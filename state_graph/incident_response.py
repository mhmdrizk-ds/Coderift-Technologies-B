from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from state_graph.base import StateGraph, WAIT_KEY
from state_graph.contracts import Interrupt, NodeFailure
from state_graph.llm_stub import LlmClient
from state_graph.mcp_adapter import McpAdapter
from state_graph.rag_lookup import lookup_runbook_guidance
from state_graph.store import CheckpointStore, HitlStore, TicketStore
from state_graph.incident_decomposition import decompose_remediation

GRAPH_NAME = "incident_response"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def make_incident_response_graph(
    mcp: Optional[McpAdapter] = None,
    llm: Optional[LlmClient] = None,
    checkpointer: Optional[CheckpointStore] = None,
    hitl_store: Optional[HitlStore] = None,
    ticket_store: Optional[TicketStore] = None,
) -> StateGraph:
    llm = llm or LlmClient()
    mcp = mcp or McpAdapter(llm=llm)

    graph = StateGraph(GRAPH_NAME, checkpointer=checkpointer,
                         hitl_store=hitl_store, ticket_store=ticket_store)

    # -- triage --------------------------------------------------------
    def triage(state: dict) -> dict:
        severity = state.get("severity", "medium")
        return {"severity": severity, "triaged_at": _now(), "status_history": ["triage"]}

    graph.add_node("triage", triage)
    graph.set_entry_point("triage")
    graph.add_edge("triage", "awaiting_diagnosis")

    # -- awaiting_diagnosis: TASK DECOMPOSITION addition ----------------
    def awaiting_diagnosis(state: dict) -> dict:
        incident_id = state["incident_id"]
        summary = mcp.draft_incident_summary(incident_id)
        incident_facts = {
            "incident_id": incident_id,
            "severity": state.get("severity"),
            "title": summary,
            "deployment_id": state.get("deployment_id"),
            "deployment_status": state.get("deployment_status"),
            "repository_name": state.get("repo"),
        }
        plan = decompose_remediation(incident_facts)
        remediation_steps = [
            f"{task.id}: {task.instruction}"
            + (f" (depends on: {', '.join(task.depends_on)})" if task.depends_on else "")
            for task in plan.tasks
        ]
        return {
            "incident_summary": summary,
            "remediation_steps": remediation_steps,
            "diagnosed_at": _now(),
        }

    graph.add_node("awaiting_diagnosis", awaiting_diagnosis)
    graph.add_edge("awaiting_diagnosis", "propose_fix")

    # -- propose_fix: RAG addition --------------------------------------
    def propose_fix(state: dict) -> dict:
        query = (
            f"remediation guidance for a {state.get('severity')} severity "
            f"incident: {state.get('incident_summary', '')}"
        )
        rag_result = lookup_runbook_guidance(query)
        proposed_fix = (
            f"Proposed fix based on runbook guidance "
            f"({rag_result['source']}): {rag_result['guidance'][:500]}"
        )
        return {
            "proposed_fix": proposed_fix,
            "runbook_citations": rag_result["citations"],
            "proposed_at": _now(),
        }

    graph.add_node("propose_fix", propose_fix)
    graph.add_edge("propose_fix", "hitl_lead_signoff")

    # -- hitl_lead_signoff: HITL node -----------------------------------
    def hitl_lead_signoff(state: dict) -> dict:
        decision = state.get("_hitl_decision")

        if state.get("severity") != "critical":
            return {"hitl_required": False, "hitl_approved": True}

        if decision is None:
            raise Interrupt(
                reason=(
                    f"Critical incident {state['incident_id']} needs lead "
                    f"sign-off before deploying a prod fix (policy threshold)."
                ),
                payload={
                    "incident_id": state["incident_id"],
                    "severity": state["severity"],
                    "proposed_fix": state.get("proposed_fix"),
                    "runbook_citations": state.get("runbook_citations"),
                },
            )

        return {
            "hitl_required": True,
            "hitl_approved": bool(decision.get("approved")),
            "hitl_approver": decision.get("approver") or decision.get("decided_by"),
            "hitl_rejection_reason": decision.get("reason") if not decision.get("approved") else None,
        }

    graph.add_node("hitl_lead_signoff", hitl_lead_signoff)
    graph.add_conditional_edges(
        "hitl_lead_signoff",
        lambda state: "approved" if state.get("hitl_approved") else "rejected",
        {"approved": "deploy_fix", "rejected": "awaiting_diagnosis"},
    )

    # -- deploy_fix: real failure -> ticket path -------------------------
    def deploy_fix(state: dict) -> dict:
        if state.get("fix_deployed"):
            return {}

        result = mcp.deploy_fix(
            repo=state["repo"],
            environment=state.get("environment", "production"),
            pr_id=state["pr_id"],
            deployed_by=state.get("hitl_approver", "auto"),
        )
        return {
            "fix_deployed": True,
            "deployment_id": result["deployment_id"],
            "deployed_at": _now(),
        }

    graph.add_node("deploy_fix", deploy_fix)
    graph.add_edge("deploy_fix", "awaiting_verification")

    # -- awaiting_verification: real external wait -----------------------
    def awaiting_verification(state: dict) -> dict:
        verification_result = state.get("verification_result")
        if verification_result is None:
            return {WAIT_KEY: True, "awaiting_verification_since": _now()}
        return {"verified_at": _now()}

    graph.add_node("awaiting_verification", awaiting_verification)
    graph.add_conditional_edges(
        "awaiting_verification",
        lambda state: "healthy" if state.get("verification_result") == "healthy" else "unhealthy",
        {"healthy": "resolved", "unhealthy": "reopened"},
    )

    # -- terminal / loop nodes --------------------------------------------
    def resolved(state: dict) -> dict:
        return {"resolved_at": _now()}

    graph.add_node("resolved", resolved)
    graph.add_edge("resolved", None)

    def reopened(state: dict) -> dict:
        return {
            "reopened_at": _now(),
            "reopen_count": state.get("reopen_count", 0) + 1,
            "verification_result": None,  # clear so awaiting_verification waits again
            "fix_deployed": False,         # allow a fresh fix to be deployed
        }

    graph.add_node("reopened", reopened)
    graph.add_edge("reopened", "awaiting_diagnosis")  # the real cycle

    return graph