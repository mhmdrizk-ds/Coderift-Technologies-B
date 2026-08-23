from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from state_graph.base import StateGraph, WAIT_KEY
from state_graph.contracts import Interrupt
from state_graph.flag_toggle_adapter import FlagToggleAdapter
from state_graph.rollout_lats import (
    BLAST_RADIUS_THRESHOLD_PCT,
    propose_rollout_sequence,
)
from state_graph.store import CheckpointStore, HitlStore, TicketStore

GRAPH_NAME = "flag_rollout"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_flag_rollout_graph(
    mcp: Optional[FlagToggleAdapter] = None,
    llm=None,
    checkpointer: Optional[CheckpointStore] = None,
    hitl_store: Optional[HitlStore] = None,
    ticket_store: Optional[TicketStore] = None,
) -> StateGraph:
    mcp = mcp or FlagToggleAdapter()

    graph = StateGraph(GRAPH_NAME, checkpointer=checkpointer,
                         hitl_store=hitl_store, ticket_store=ticket_store)

    # -- propose_rollout_pct: LATS addition ------------------------------
    def propose_rollout_pct(state: dict) -> dict:
        """Search over the fixed catalog of canonical rollout-percentage
        orderings (state_graph.rollout_lats.CANDIDATE_SEQUENCES) and
        select the lowest-penalty sequence for this repository, scored by
        rollout_lats.score_sequence — a real, deterministic function over
        jump sizes and this repo's DB-derived historical incident
        baseline, never model opinion. See rollout_lats.py's module
        docstring for why this is a one-level LATS tree (root + one child
        per candidate) rather than an open-ended search: rollout
        percentage orderings have a small real catalog, unlike Person A's
        Task 2 remediation-action LATS, which searches genuinely
        open-ended LLM-proposed actions.

        A sequence the operator already supplied in `initial_state`
        (`rollout_sequence`) is respected as-is (e.g. a test fixing the
        exact steps) — LATS only runs when no sequence was pre-supplied,
        the same "don't re-decide something the caller already decided"
        convention state_graph.incident_response's triage() node uses for
        `severity`.
        """
        if state.get("rollout_sequence"):
            return {
                "rollout_sequence": state["rollout_sequence"],
                "rollout_step_index": 0,
                "lats_candidates": None,
                "proposed_at": _now(),
            }

        result = propose_rollout_sequence(state["repo"])
        return {
            "rollout_sequence": result.best.sequence,
            "rollout_step_index": 0,
            "lats_candidates": [
                {"name": c.name, "sequence": c.sequence, "score": c.score,
                 "penalty_breakdown": c.penalty_breakdown}
                for c in result.all_candidates
            ],
            "lats_baseline_error_rate": result.baseline_error_rate,
            "proposed_at": _now(),
        }

    graph.add_node("propose_rollout_pct", propose_rollout_pct)
    graph.set_entry_point("propose_rollout_pct")
    graph.add_edge("propose_rollout_pct", "canary")

    # -- canary: constrained ReAct node -----------------------------------
    def canary(state: dict) -> dict:
        sequence = state["rollout_sequence"]
        step_index = state.get("rollout_step_index", 0)
        target_pct = sequence[step_index]

        mcp.set_flag_percentage(
            repo=state["repo"], environment=state.get("environment", "production"),
            flag_name=state["flag_name"], rollout_pct=target_pct,
        )
        return {
            "current_rollout_pct": target_pct,
            "metrics_result": None,  # clear so awaiting_metrics waits for a fresh signal
            "canary_set_at": _now(),
        }

    graph.add_node("canary", canary)
    graph.add_edge("canary", "awaiting_metrics")

    # -- awaiting_metrics: real external wait -----------------------------
    def awaiting_metrics(state: dict) -> dict:
        metrics_result = state.get("metrics_result")
        if metrics_result is None:
            return {WAIT_KEY: True, "awaiting_metrics_since": _now()}
        return {"metrics_checked_at": _now()}

    def _route_after_metrics(state: dict) -> str:
        metrics_result = state["metrics_result"]
        if metrics_result == "error_spike":
            return "error_spike"

        sequence = state["rollout_sequence"]
        step_index = state["rollout_step_index"]

        if step_index >= len(sequence) - 1:
            # Already sitting at the final step (100%) and metrics are
            # healthy — the rollout is done.
            return "complete"

        next_target = sequence[step_index + 1]
        if next_target >= BLAST_RADIUS_THRESHOLD_PCT:
            return "needs_full_rollout_gate"
        return "healthy_below_threshold"

    graph.add_node("awaiting_metrics", awaiting_metrics)
    graph.add_conditional_edges(
        "awaiting_metrics", _route_after_metrics,
        {
            "error_spike": "auto_rollback",
            "healthy_below_threshold": "increase_pct",
            "needs_full_rollout_gate": "full_production_rollout",
            "complete": "rolled_out",
        },
    )

    # -- increase_pct: loop back into canary at the next step -------------
    def increase_pct(state: dict) -> dict:
        return {"rollout_step_index": state["rollout_step_index"] + 1}

    graph.add_node("increase_pct", increase_pct)
    graph.add_edge("increase_pct", "canary")

    # -- full_production_rollout: HITL node, gated by the named
    # blast-radius threshold. Approval does NOT jump straight to 100% —
    # it advances exactly one step (increase_pct -> canary), the same
    # step that triggered the gate, so that percentage is actually
    # canaried and metrics-checked like every other step, rather than
    # skipped. If that step happens to BE 100 (the sequence's final
    # value), the normal awaiting_metrics healthy path reaches
    # `rolled_out` on its own next pass — this node never sets the flag
    # itself and never claims completion directly. ----------------------
    def full_production_rollout(state: dict) -> dict:
        decision = state.get("_hitl_decision")
        sequence = state["rollout_sequence"]
        step_index = state["rollout_step_index"]
        next_target = sequence[step_index + 1]

        if decision is None:
            raise Interrupt(
                reason=(
                    f"Rollout of flag '{state['flag_name']}' on '{state['repo']}' "
                    f"wants to move from {state['current_rollout_pct']}% to "
                    f"{next_target}%, which is at or above the "
                    f"{BLAST_RADIUS_THRESHOLD_PCT}% blast-radius threshold. "
                    f"Full-production rollout beyond this threshold requires "
                    f"human sign-off (policy threshold)."
                ),
                payload={
                    "repo": state["repo"],
                    "flag_name": state["flag_name"],
                    "current_rollout_pct": state["current_rollout_pct"],
                    "target_rollout_pct": next_target,
                    "blast_radius_threshold_pct": BLAST_RADIUS_THRESHOLD_PCT,
                },
            )

        approved = bool(decision.get("approved"))
        if not approved:
            return {
                "hitl_required": True,
                "hitl_approved": False,
                "hitl_rejection_reason": decision.get("reason"),
                # Hold at the current %, and clear metrics_result so the
                # next awaiting_metrics pass genuinely WAITS for a fresh
                # external signal rather than immediately re-routing back
                # into this same Interrupt on stale state — a rejection
                # must not silently re-trigger the identical gate in a
                # tight loop.
                "metrics_result": None,
            }

        return {
            "hitl_required": True,
            "hitl_approved": True,
            "hitl_approver": decision.get("approver") or decision.get("decided_by"),
            "hitl_rejection_reason": None,
        }

    graph.add_node("full_production_rollout", full_production_rollout)
    graph.add_conditional_edges(
        "full_production_rollout",
        lambda state: "approved" if state.get("hitl_approved") else "rejected",
        # Approval: exactly one step forward, through the same
        # increase_pct -> canary path every other step takes — the
        # threshold-crossing percentage gets set and metrics-checked for
        # real, it is never skipped straight to 100%.
        # Rejection: back to awaiting_metrics, HOLDING at the current %
        # (rollout_step_index unchanged) — not silently advancing and not
        # opening a ticket. full_production_rollout already cleared
        # metrics_result on rejection (see above), so awaiting_metrics
        # genuinely waits for a fresh signal rather than immediately
        # re-raising the same Interrupt on stale state.
        {"approved": "increase_pct", "rejected": "awaiting_metrics"},
    )

    # -- auto_rollback: automatic safety action, no human needed ----------
    def auto_rollback(state: dict) -> dict:
        safe_pct = state.get("last_known_healthy_pct", 0)
        mcp.set_flag_percentage(
            repo=state["repo"], environment=state.get("environment", "production"),
            flag_name=state["flag_name"], rollout_pct=safe_pct,
        )
        return {
            "current_rollout_pct": safe_pct,
            "rolled_back_at": _now(),
            "rollback_reason": "error_spike detected during awaiting_metrics",
        }

    graph.add_node("auto_rollback", auto_rollback)
    graph.add_edge("auto_rollback", "rolled_back")

    # -- terminal nodes ----------------------------------------------------
    def rolled_out(state: dict) -> dict:
        return {"rolled_out_at": _now(), "current_rollout_pct": 100}

    graph.add_node("rolled_out", rolled_out)
    graph.add_edge("rolled_out", None)

    def rolled_back(state: dict) -> dict:
        return {"rollback_completed_at": _now()}

    graph.add_node("rolled_back", rolled_back)
    graph.add_edge("rolled_back", None)

    return graph
