from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from state_graph.contracts import NodeFailure
from state_graph.flag_rollout import make_flag_rollout_graph
from state_graph.flag_toggle_adapter import FlagToggleAdapter, SimulatedFlagToggleClient
from state_graph.store import CheckpointStore, HitlStore, TicketStore

MIGRATIONS_DIR = Path(__file__).parent.parent / "db" / "migrations"
STATE_GRAPH_MIGRATION = MIGRATIONS_DIR / "001_state_graph_and_admin_tables.sql"


@pytest.fixture()
def db_path(tmp_path):
    """Only the state-graph/admin tables (checkpoints, hitl_tasks,
    tickets) are needed here — these tests drive flag state entirely
    through FlagToggleAdapter/SimulatedFlagToggleClient, which never
    touch sqlite, so migration 002's `ALTER TABLE feature_flags` (which
    needs the base schema.sql's feature_flags table to already exist) is
    irrelevant to this file. Mirrors tests/test_incident_response.py's
    fixture, which likewise only applies the one migration its graph
    actually needs."""
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(STATE_GRAPH_MIGRATION.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    return path


def stores(db_path):
    return (CheckpointStore(db_path), HitlStore(db_path), TicketStore(db_path))


BASE_STATE = {
    "repo": "billing-worker",
    "environment": "production",
    "flag_name": "new-payment-retry-logic",
}


# ---------------------------------------------------------------------------
# Happy path: every step stays under the blast-radius threshold (50%), so
# the run completes without ever touching HITL.
# ---------------------------------------------------------------------------

def test_happy_path_low_risk_rollout_completes_without_hitl(db_path):
    ckpt, hitl, tix = stores(db_path)
    client = SimulatedFlagToggleClient()
    for _ in range(10):
        client.queue_metrics_result("healthy")
    graph = make_flag_rollout_graph(
        mcp=FlagToggleAdapter(client), checkpointer=ckpt, hitl_store=hitl, ticket_store=tix,
    )
    run_id = str(uuid.uuid4())

    result = graph.start(run_id, {
        **BASE_STATE,
        "rollout_sequence": [10, 20, 30],  # entirely below the 50% threshold
    })
    assert result["status"] == "waiting"
    assert result["node"] == "awaiting_metrics"
    assert hitl.list_pending() == []

    result = graph.resume(run_id, external_event={"metrics_result": "healthy"})  # 10% -> 20%
    assert result["status"] == "waiting"

    result = graph.resume(run_id, external_event={"metrics_result": "healthy"})  # 20% -> 30%
    assert result["status"] == "waiting"

    result = graph.resume(run_id, external_event={"metrics_result": "healthy"})  # 30% is final step
    assert result["status"] == "completed"
    assert result["state"]["rolled_out_at"] is not None
    assert result["state"]["current_rollout_pct"] == 100
    assert hitl.list_pending() == []
    assert tix.list_open() == []


# ---------------------------------------------------------------------------
# HITL pause + resume: crossing the blast-radius threshold pauses, an
# admin decision resumes it correctly and the threshold step is actually
# canaried (not skipped straight to 100%).
# ---------------------------------------------------------------------------

def test_hitl_gated_path_pauses_and_resumes_on_approval(db_path):
    ckpt, hitl, tix = stores(db_path)
    client = SimulatedFlagToggleClient()
    for _ in range(10):
        client.queue_metrics_result("healthy")
    graph = make_flag_rollout_graph(
        mcp=FlagToggleAdapter(client), checkpointer=ckpt, hitl_store=hitl, ticket_store=tix,
    )
    run_id = str(uuid.uuid4())

    result = graph.start(run_id, {**BASE_STATE, "rollout_sequence": [10, 30, 60, 100]})
    assert result["status"] == "waiting"

    # 10% healthy -> next step 30% is below threshold -> increase_pct, no HITL.
    result = graph.resume(run_id, external_event={"metrics_result": "healthy"})
    assert result["status"] == "waiting"
    assert hitl.list_pending() == []

    # 30% healthy -> next step 60% is AT/ABOVE the 50% threshold -> HITL gate.
    result = graph.resume(run_id, external_event={"metrics_result": "healthy"})
    assert result["status"] == "paused_hitl"
    assert result["node"] == "full_production_rollout"

    pending = hitl.list_pending()
    assert len(pending) == 1
    task = pending[0]
    assert "60%" in task.reason
    assert task.payload["target_rollout_pct"] == 60
    assert task.payload["blast_radius_threshold_pct"] == 50

    # Admin approves.
    hitl.decide(task.id, approved=True, decided_by="lead_sara")
    result = graph.resume(run_id, hitl_decision={"approved": True, "approver": "lead_sara"})

    # Approval must actually canary at 60% (not skip to 100%) and wait
    # for a fresh metrics signal at that percentage.
    assert result["status"] == "waiting"
    assert result["node"] == "awaiting_metrics"
    latest = ckpt.load_latest(run_id)
    assert latest.state["current_rollout_pct"] == 60

    # 60% healthy -> next step is 100, which is ALSO at/above the
    # threshold -> full_production_rollout gates again (every step at or
    # above the blast-radius threshold needs its own sign-off, not just
    # the first one that crosses it).
    result = graph.resume(run_id, external_event={"metrics_result": "healthy"})
    assert result["status"] == "paused_hitl"
    task = hitl.list_pending()[0]
    assert task.payload["target_rollout_pct"] == 100
    hitl.decide(task.id, approved=True, decided_by="lead_sara")
    result = graph.resume(run_id, hitl_decision={"approved": True, "approver": "lead_sara"})
    assert result["status"] == "waiting"

    result = graph.resume(run_id, external_event={"metrics_result": "healthy"})
    assert result["status"] == "completed"
    assert result["state"]["current_rollout_pct"] == 100


# ---------------------------------------------------------------------------
# HITL rejection: holds at current %, does not silently jump to 100%, does
# not open a ticket.
# ---------------------------------------------------------------------------

def test_hitl_rejection_holds_at_current_pct_not_a_ticket(db_path):
    ckpt, hitl, tix = stores(db_path)
    client = SimulatedFlagToggleClient()
    client.queue_metrics_result("healthy")  # for the initial 10% step
    graph = make_flag_rollout_graph(
        mcp=FlagToggleAdapter(client), checkpointer=ckpt, hitl_store=hitl, ticket_store=tix,
    )
    run_id = str(uuid.uuid4())

    graph.start(run_id, {**BASE_STATE, "rollout_sequence": [10, 60, 100]})
    result = graph.resume(run_id, external_event={"metrics_result": "healthy"})
    assert result["status"] == "paused_hitl"

    task = hitl.list_pending()[0]
    hitl.decide(task.id, approved=False, decided_by="lead_sara", reason="not ready, hold at 10%")
    result = graph.resume(run_id, hitl_decision={"approved": False, "reason": "not ready, hold at 10%"})

    # Held at 10%, waiting for a fresh signal — not jumped to 100%, no ticket.
    assert result["status"] == "waiting"
    assert result["node"] == "awaiting_metrics"
    latest = ckpt.load_latest(run_id)
    assert latest.state["current_rollout_pct"] == 10
    assert latest.state["rollout_step_index"] == 0
    assert tix.list_open() == []
    assert hitl.list_pending() == []  # the rejected task is no longer pending

    # A fresh healthy signal re-triggers the SAME gate again (still holds
    # at 10% until approved) rather than silently proceeding.
    result = graph.resume(run_id, external_event={"metrics_result": "healthy"})
    assert result["status"] == "paused_hitl"
    assert len(hitl.list_pending()) == 1


# ---------------------------------------------------------------------------
# Auto-rollback on error_spike: no human involved, correct terminal state.
# ---------------------------------------------------------------------------

def test_auto_rollback_on_error_spike_no_human_involved(db_path):
    ckpt, hitl, tix = stores(db_path)
    client = SimulatedFlagToggleClient()
    graph = make_flag_rollout_graph(
        mcp=FlagToggleAdapter(client), checkpointer=ckpt, hitl_store=hitl, ticket_store=tix,
    )
    run_id = str(uuid.uuid4())

    graph.start(run_id, {**BASE_STATE, "rollout_sequence": [10, 30, 100]})
    result = graph.resume(run_id, external_event={"metrics_result": "error_spike"})

    assert result["status"] == "completed"
    assert result["state"]["rollback_completed_at"] is not None
    assert result["state"]["current_rollout_pct"] == 0  # last_known_healthy_pct default
    assert hitl.list_pending() == []
    assert tix.list_open() == []


# ---------------------------------------------------------------------------
# Tool failure during flag-toggle -> ticket opened, distinct from HITL.
# ---------------------------------------------------------------------------

def test_flag_toggle_tool_failure_opens_ticket_distinct_from_hitl(db_path):
    ckpt, hitl, tix = stores(db_path)
    client = SimulatedFlagToggleClient()
    client.fail_next_set = True
    graph = make_flag_rollout_graph(
        mcp=FlagToggleAdapter(client), checkpointer=ckpt, hitl_store=hitl, ticket_store=tix,
    )
    run_id = str(uuid.uuid4())

    result = graph.start(run_id, {**BASE_STATE, "rollout_sequence": [10, 30, 100]})

    assert result["status"] == "ticketed"
    assert result["node"] == "canary"
    open_tickets = tix.list_open()
    assert len(open_tickets) == 1
    ticket = open_tickets[0]
    assert ticket.error_code == "FLAG_TOGGLE_TOOL_ERROR"
    assert hitl.list_pending() == []  # a ticket must never appear in the HITL inbox

    # Admin resolves; a resumed run retries the same node from checkpoint.
    tix.set_status(ticket.id, "resolved", resolution_notes="tool was flaky, retried")
    result = graph.resume(run_id)
    assert result["status"] == "waiting"
    assert result["node"] == "awaiting_metrics"


# ---------------------------------------------------------------------------
# Crash-and-resume: kill the process mid-canary-loop, restart with a fresh
# process, resume the same run_id, prove no node re-executed.
# ---------------------------------------------------------------------------

def test_crash_and_resume_no_reexecution(db_path):
    ckpt, hitl, tix = stores(db_path)
    run_id = str(uuid.uuid4())

    set_pct_calls = {"count": 0}

    def counting_adapter_factory():
        client = SimulatedFlagToggleClient()
        client.queue_metrics_result("healthy")
        client.queue_metrics_result("healthy")
        adapter = FlagToggleAdapter(client)
        original = adapter.set_flag_percentage

        def counted(*args, **kwargs):
            set_pct_calls["count"] += 1
            return original(*args, **kwargs)

        adapter.set_flag_percentage = counted
        return adapter

    # --- "process A" ---
    graph_a = make_flag_rollout_graph(
        mcp=counting_adapter_factory(), checkpointer=ckpt, hitl_store=hitl, ticket_store=tix,
    )
    result = graph_a.start(run_id, {**BASE_STATE, "rollout_sequence": [10, 30, 60, 100]})
    assert result["status"] == "waiting"
    assert set_pct_calls["count"] == 1  # canary set 10% exactly once
    del graph_a  # "the process dies"

    # --- "process B" — nothing carried over except the db file ---
    graph_b = make_flag_rollout_graph(
        mcp=counting_adapter_factory(),  # fresh adapter, fresh call counter/client
        checkpointer=CheckpointStore(db_path),
        hitl_store=HitlStore(db_path),
        ticket_store=TicketStore(db_path),
    )
    result = graph_b.resume(run_id, external_event={"metrics_result": "healthy"})
    assert result["status"] == "waiting"
    # set_pct_calls is a shared counter across both "processes" (mirrors
    # incident_response's shared diagnosis_calls dict) — process A called
    # it once (10%), process B calls it once more (30%): total 2, proving
    # process B did NOT re-execute the 10% canary step that process A
    # already completed and checkpointed.
    assert set_pct_calls["count"] == 2

    history = ckpt.history(run_id)
    node_sequence = [c.node_name for c in history]
    assert node_sequence == [
        "propose_rollout_pct", "canary", "awaiting_metrics",
        "awaiting_metrics",  # re-entered on resume (still waiting) after start()
        "increase_pct", "canary", "awaiting_metrics",
        "awaiting_metrics",  # re-entered on resume (still waiting) after process B's resume()
    ]
