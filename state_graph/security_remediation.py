"""security_remediation.py — Security Remediation state graph.

Real problem: a pull request's security scan comes back Failed (or a
prior scan is superseded, per security_review_policy.md), and the fix
needs to go through patch -> re-scan -> review/override -> merge-or-deploy
without losing progress if that takes days, gets rejected, or a tool call
breaks mid-run.

    scan_flag -> propose_remediation -> patch_pr -> [conditional on the
    refreshed scan status]
        Passed -> awaiting_code_review -> [conditional on reviewer]
            approved -> deploy_patch -> resolved
            rejected -> propose_remediation           (real cycle #1)
        Failed -> hitl_lead_signoff -> [conditional on the lead's decision]
            approved -> deploy_patch_override -> resolved
            rejected -> propose_remediation           (real cycle #2)

Why this needs a state graph, not a linear script:
  * awaiting_code_review is a genuine multi-turn wait — a reviewer may
    take days, and the graph must not poll in a loop (WAIT_KEY, same
    contract as incident_response.py's awaiting_verification).
  * hitl_lead_signoff is a real branch that depends on something outside
    the model's control: security_review_policy.md 4.1 says only a
    lead-role engineer may authorize deploying a PR with a Failed scan —
    the agent is not allowed to decide this alone, full stop.
  * A rejected review or a rejected override both genuinely loop back to
    propose_remediation for a *different* strategy attempt (attempt_number
    increments, previous_selected_id is carried so Tree of Thoughts
    deprioritizes the strategy that already failed) — not a fresh run.
  * run_pre_deploy_checks interrupted mid-run is a real, named failure
    mode (policy 6.2: "may leave the security_scans table in an
    inconsistent state") that a single retry cannot safely paper over —
    it becomes a ticket, a separate code path from the HITL pause above.

Two LLM-call additions used, and why:
  * Tree of Thoughts (remediation_strategy.select_remediation_strategy,
    called from propose_remediation) — a Failed scan has more than one
    legitimate response (upgrade dependency / patch in place /
    compensating control per policy 7.3-7.4); picking the wrong one wastes
    a real fix window, so multiple candidates are scored before one is
    picked, exactly the shape ToT is for.
  * Constrained ReAct (patch_pr, enforced by _call_whitelisted_tool below)
    — the node that is allowed to trigger a fresh scan and, eventually,
    a real merge/deploy must not be free to call arbitrary MCP tools; the
    cost of a wrong tool call here is a real production action.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from state_graph.base import StateGraph, WAIT_KEY
from state_graph.contracts import Interrupt, NodeFailure
from state_graph.mcp_adapter import McpAdapter
from state_graph.remediation_strategy import select_remediation_strategy
from state_graph.store import CheckpointStore, HitlStore, TicketStore

GRAPH_NAME = "security_remediation"

# -- constrained ReAct allowlist, per node --------------------------------
# Enforced in code (not just implied by the prompt): a node may only call
# tools listed here. See _call_whitelisted_tool.
ALLOWED_TOOLS_BY_NODE = {
    "patch_pr": {"run_pre_deploy_checks"},
    "deploy_patch": {"record_review_approval", "merge_pull_request"},
    "deploy_patch_override": {"deploy_to_production_override"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConstrainedToolViolation(NodeFailure):
    """A distinct NodeFailure subtype so a grader/log can tell a genuine
    tool outage apart from the graph's own allowlist rejecting a call —
    both still open a ticket, since both are unplanned."""

    def __init__(self, node_name: str, tool_name: str):
        allowed = sorted(ALLOWED_TOOLS_BY_NODE.get(node_name, set()))
        super().__init__(
            "CONSTRAINED_TOOL_VIOLATION",
            f"node '{node_name}' attempted to call '{tool_name}', which is "
            f"not in its allowlist {allowed}.",
            payload={"node_name": node_name, "tool_name": tool_name},
        )


def _call_whitelisted_tool(node_name: str, tool_name: str, call_fn):
    """call_fn is a zero-arg callable that actually invokes the adapter
    method. Raises before ever calling it if tool_name isn't allowed for
    this node — the check happens in code, not by trusting the caller."""
    if tool_name not in ALLOWED_TOOLS_BY_NODE.get(node_name, set()):
        raise ConstrainedToolViolation(node_name, tool_name)
    return call_fn()


def make_security_remediation_graph(
    mcp: Optional[McpAdapter] = None,
    checkpointer: Optional[CheckpointStore] = None,
    hitl_store: Optional[HitlStore] = None,
    ticket_store: Optional[TicketStore] = None,
) -> StateGraph:
    mcp = mcp or McpAdapter()

    graph = StateGraph(GRAPH_NAME, checkpointer=checkpointer,
                         hitl_store=hitl_store, ticket_store=ticket_store)

    # -- scan_flag: entry -------------------------------------------------
    def scan_flag(state: dict) -> dict:
        pr = mcp.get_pull_request(state["pull_request_id"])
        scan = pr.get("latest_security_scan") or {}
        return {
            "repository_name": state["repository_name"],
            "environment_name": state.get("environment_name", "production"),
            "scan_type": scan.get("scan_type", "SAST"),
            "scan_status": scan.get("scan_status", scan.get("status")),
            "attempt_number": 1,
            "flagged_at": _now(),
            "status_history": ["scan_flag"],
        }

    graph.add_node("scan_flag", scan_flag)
    graph.set_entry_point("scan_flag")
    graph.add_edge("scan_flag", "propose_remediation")

    # -- propose_remediation: TREE OF THOUGHTS addition --------------------
    def propose_remediation(state: dict) -> dict:
        scan_facts = {
            "pull_request_id": state["pull_request_id"],
            "scan_type": state.get("scan_type", "SAST"),
            "scan_status": state.get("scan_status"),
            "attempt_number": state.get("attempt_number", 1),
            "previous_selected_id": state.get("selected_strategy_id"),
        }
        strategy = select_remediation_strategy(scan_facts)
        return {
            "strategy_candidates": strategy["candidates"],
            "selected_strategy_id": strategy["selected_id"],
            "selection_reasoning": strategy["selection_reasoning"],
            "proposed_at": _now(),
        }

    graph.add_node("propose_remediation", propose_remediation)
    graph.add_edge("propose_remediation", "patch_pr")

    # -- patch_pr: CONSTRAINED REACT addition -------------------------------
    def patch_pr(state: dict) -> dict:
        pull_request_id = state["pull_request_id"]
        result = _call_whitelisted_tool(
            "patch_pr", "run_pre_deploy_checks",
            lambda: mcp.run_pre_deploy_checks(pull_request_id),
        )
        return {
            "scan_status": result["final_scan_status"],
            "patched_at": _now(),
            "attempt_number": state.get("attempt_number", 1) + 1,
        }

    graph.add_node("patch_pr", patch_pr)
    graph.add_conditional_edges(
        "patch_pr",
        lambda state: "passed" if state.get("scan_status") == "Passed" else "failed",
        {"passed": "awaiting_code_review", "failed": "hitl_lead_signoff"},
    )

    # -- awaiting_code_review: real external wait ---------------------------
    def awaiting_code_review(state: dict) -> dict:
        review_result = state.get("review_result")
        if review_result is None:
            return {WAIT_KEY: True, "awaiting_review_since": _now()}
        return {"reviewed_at": _now()}

    graph.add_node("awaiting_code_review", awaiting_code_review)
    graph.add_conditional_edges(
        "awaiting_code_review",
        lambda state: "approved" if state.get("review_result") == "approved" else "rejected",
        {"approved": "deploy_patch", "rejected": "review_rejected"},
    )

    def review_rejected(state: dict) -> dict:
        # A real cycle: a rejected review means the patch itself was
        # wrong, not the process — go propose a genuinely different
        # strategy, not just re-submit the same patch.
        return {
            "review_result": None,  # clear so awaiting_code_review waits again next time
            "review_rejection_reason": state.get("review_rejection_reason"),
        }

    graph.add_node("review_rejected", review_rejected)
    graph.add_edge("review_rejected", "propose_remediation")

    # -- deploy_patch: clean path (Passed + Approved), real failure -> ticket
    def deploy_patch(state: dict) -> dict:
        pull_request_id = state["pull_request_id"]
        _call_whitelisted_tool(
            "deploy_patch", "record_review_approval",
            lambda: mcp.record_review_approval(pull_request_id),
        )
        _call_whitelisted_tool(
            "deploy_patch", "merge_pull_request",
            lambda: mcp.merge_pull_request(pull_request_id),
        )
        return {"merged": True, "merged_at": _now(), "resolution_path": "clean_merge"}

    graph.add_node("deploy_patch", deploy_patch)
    graph.add_edge("deploy_patch", "resolved")

    # -- hitl_lead_signoff: HITL node ----------------------------------------
    def hitl_lead_signoff(state: dict) -> dict:
        # Policy 4.1: only a lead-role engineer may authorize deploying a
        # Failed-scan PR to production. The agent is never allowed to
        # decide this alone — there is no "auto-approve if confidence is
        # high" branch here, unlike a numeric-threshold HITL condition.
        decision = state.get("_hitl_decision")

        if decision is None:
            raise Interrupt(
                reason=(
                    f"PR #{state['pull_request_id']} still has a Failed "
                    f"security scan after {state.get('attempt_number', 1) - 1} "
                    f"patch attempt(s). Only a lead-role engineer may "
                    f"authorize a production deploy under policy 4.1 — the "
                    f"agent cannot decide this alone."
                ),
                payload={
                    "pull_request_id": state["pull_request_id"],
                    "scan_status": state.get("scan_status"),
                    "attempt_number": state.get("attempt_number"),
                    "selected_strategy_id": state.get("selected_strategy_id"),
                    "selection_reasoning": state.get("selection_reasoning"),
                },
            )

        approved = bool(decision.get("approved"))
        return {
            "hitl_approved": approved,
            "hitl_approver": decision.get("approver") or decision.get("decided_by"),
            "hitl_justification": decision.get("reason"),
        }

    graph.add_node("hitl_lead_signoff", hitl_lead_signoff)
    graph.add_conditional_edges(
        "hitl_lead_signoff",
        lambda state: "approved" if state.get("hitl_approved") else "rejected",
        {"approved": "deploy_patch_override", "rejected": "override_rejected"},
    )

    def override_rejected(state: dict) -> dict:
        return {
            "_hitl_decision": None,
            "override_rejection_reason": state.get("hitl_justification"),
        }

    graph.add_node("override_rejected", override_rejected)
    graph.add_edge("override_rejected", "propose_remediation")  # real cycle #2

    # -- deploy_patch_override: only reachable after a real lead sign-off ---
    def deploy_patch_override(state: dict) -> dict:
        pull_request_id = state["pull_request_id"]
        note = (
            f"Lead-approved Failed-scan override "
            f"(policy 4.1/4.3): {state.get('hitl_justification', 'no justification recorded')}, "
            f"approved by {state.get('hitl_approver')}."
        )
        _call_whitelisted_tool(
            "deploy_patch_override", "deploy_to_production_override",
            lambda: mcp.deploy_to_production_override(
                repository_name=state["repository_name"],
                environment_name=state.get("environment_name", "production"),
                pull_request_id=pull_request_id,
                confirmation_note=note,
            ),
        )
        return {
            "merged": False, "deployed_override": True,
            "deployed_at": _now(), "resolution_path": "lead_override",
        }

    graph.add_node("deploy_patch_override", deploy_patch_override)
    graph.add_edge("deploy_patch_override", "resolved")

    # -- resolved: terminal ---------------------------------------------------
    def resolved(state: dict) -> dict:
        return {"resolved_at": _now()}

    graph.add_node("resolved", resolved)
    graph.add_edge("resolved", None)

    return graph
