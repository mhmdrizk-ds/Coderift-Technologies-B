from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from state_graph.contracts import NodeFailure
from state_graph.incident_response import make_incident_response_graph
from state_graph.mcp_adapter import McpAdapter
from state_graph.store import CheckpointStore, HitlStore, TicketStore

MIGRATION_SQL = (Path(__file__).parent.parent / "db" / "migrations" /
                  "001_state_graph_and_admin_tables.sql")


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(MIGRATION_SQL.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    return path


def stores(db_path):
    return (CheckpointStore(db_path), HitlStore(db_path), TicketStore(db_path))


class FailingMcpAdapter(McpAdapter):
    def __init__(self):
        super().__init__()
        self.fail_next_deploy = True

    def deploy_fix(self, **kwargs):
        if self.fail_next_deploy:
            self.fail_next_deploy = False
            raise NodeFailure("DEPLOY_FIX_TOOL_ERROR", "simulated tool timeout")
        return super().deploy_fix(**kwargs)


# ---------------------------------------------------------------------------

def test_happy_path_non_critical_incident_resolves_without_hitl(db_path):
    ckpt, hitl, tix = stores(db_path)
    graph = make_incident_response_graph(checkpointer=ckpt, hitl_store=hitl,
                                            ticket_store=tix)
    run_id = str(uuid.uuid4())

    result = graph.start(run_id, {
        "incident_id": 1, "severity": "medium", "repo": "billing-worker",
        "pr_id": 42, "environment": "production",
    })

    assert result["status"] == "waiting"
    assert result["node"] == "awaiting_verification"
    assert hitl.list_pending() == []

    # Simulate the monitoring signal arriving healthy.
    result = graph.resume(run_id, external_event={"verification_result": "healthy"})
    assert result["status"] == "completed"
    assert result["state"]["resolved_at"] is not None


def test_critical_incident_pauses_for_hitl_and_resumes_on_approval(db_path):
    ckpt, hitl, tix = stores(db_path)
    graph = make_incident_response_graph(checkpointer=ckpt, hitl_store=hitl,
                                            ticket_store=tix)
    run_id = str(uuid.uuid4())

    result = graph.start(run_id, {
        "incident_id": 2, "severity": "critical", "repo": "billing-worker",
        "pr_id": 43, "environment": "production",
    })
    assert result["status"] == "paused_hitl"
    assert result["node"] == "hitl_lead_signoff"

    pending = hitl.list_pending()
    assert len(pending) == 1
    task = pending[0]
    assert task.reason  # a real human-readable reason, not a placeholder
    assert task.payload["incident_id"] == 2

    # Admin approves through the platform.
    hitl.decide(task.id, approved=True, decided_by="lead_jane")
    result = graph.resume(run_id, hitl_decision={"approved": True, "approver": "lead_jane"})

    assert result["status"] == "waiting"
    assert result["node"] == "awaiting_verification"

    # Verification fails -> reopened -> loops back into awaiting_diagnosis,
    # which will re-run propose_fix and hit HITL again.
    result = graph.resume(run_id, external_event={"verification_result": "unhealthy"})
    assert result["status"] == "paused_hitl"  # critical incident, HITL again
    assert ckpt.load_latest(run_id).state["reopen_count"] == 1


def test_hitl_rejection_loops_back_to_diagnosis_not_a_ticket(db_path):
    ckpt, hitl, tix = stores(db_path)
    graph = make_incident_response_graph(checkpointer=ckpt, hitl_store=hitl,
                                            ticket_store=tix)
    run_id = str(uuid.uuid4())

    graph.start(run_id, {
        "incident_id": 3, "severity": "critical", "repo": "billing-worker",
        "pr_id": 44, "environment": "production",
    })
    task = hitl.list_pending()[0]
    hitl.decide(task.id, approved=False, decided_by="lead_jane", reason="wrong fix")
    result = graph.resume(run_id, hitl_decision={"approved": False, "reason": "wrong fix"})

    # Rejected -> back to awaiting_diagnosis -> propose_fix -> hitl again,
    # not deploy_fix, and definitely not a ticket.
    assert result["status"] == "paused_hitl"
    assert tix.list_open() == []


def test_deploy_tool_failure_opens_ticket_distinct_from_hitl(db_path):
    ckpt, hitl, tix = stores(db_path)
    graph = make_incident_response_graph(
        mcp=FailingMcpAdapter(), checkpointer=ckpt, hitl_store=hitl, ticket_store=tix,
    )
    run_id = str(uuid.uuid4())

    result = graph.start(run_id, {
        "incident_id": 4, "severity": "medium", "repo": "billing-worker",
        "pr_id": 45, "environment": "production",
    })

    assert result["status"] == "ticketed"
    assert result["node"] == "deploy_fix"
    open_tickets = tix.list_open()
    assert len(open_tickets) == 1
    ticket = open_tickets[0]
    assert ticket.error_code == "DEPLOY_FIX_TOOL_ERROR"
    assert ticket.state_snapshot["incident_id"] == 4
    # a ticket must never appear in the HITL inbox
    assert hitl.list_pending() == []

    # Admin resolves the ticket, then the run is resumed from that exact
    # checkpoint (not restarted).
    tix.set_status(ticket.id, "resolved", resolution_notes="tool was flaky, retried")
    result = graph.resume(run_id)
    assert result["status"] == "waiting"  # deploy_fix succeeded on the retry
    assert result["node"] == "awaiting_verification"


def test_crash_and_resume_no_reexecution(db_path):
    """Simulates: process A starts a run and gets partway, then the process
    dies. A brand-new process (fresh graph object, fresh adapters — nothing
    shared with process A except the sqlite file on disk) resumes the SAME
    run_id and must continue from the last checkpoint with no re-execution
    of the steps already completed."""
    ckpt, hitl, tix = stores(db_path)
    run_id = str(uuid.uuid4())

    diagnosis_calls = {"count": 0}

    def counting_mcp_factory():
        adapter = McpAdapter()
        original = adapter.draft_incident_summary
        def counted(incident_id):
            diagnosis_calls["count"] += 1
            return original(incident_id)
        adapter.draft_incident_summary = counted
        return adapter

    # --- "process A" ---
    graph_a = make_incident_response_graph(
        mcp=counting_mcp_factory(), checkpointer=ckpt, hitl_store=hitl, ticket_store=tix,
    )
    result = graph_a.start(run_id, {
        "incident_id": 5, "severity": "critical", "repo": "billing-worker",
        "pr_id": 46, "environment": "production",
    })
    assert result["status"] == "paused_hitl"
    assert diagnosis_calls["count"] == 1
    del graph_a  # "the process dies"

    # --- "process B" — nothing carried over except the db file ---
    task = hitl.list_pending()[0]
    hitl.decide(task.id, approved=True, decided_by="lead_bob")

    graph_b = make_incident_response_graph(
        mcp=counting_mcp_factory(),  # a fresh adapter, fresh call counter
        checkpointer=CheckpointStore(db_path),  # fresh store instances too
        hitl_store=HitlStore(db_path),
        ticket_store=TicketStore(db_path),
    )
    result = graph_b.resume(run_id, hitl_decision={"approved": True, "approver": "lead_bob"})

    assert result["status"] == "waiting"
    assert result["node"] == "awaiting_verification"
    assert diagnosis_calls["count"] == 1

    # Full checkpoint history proves each node ran exactly once in order,
    # with no duplicate re-execution.
    history = ckpt.history(run_id)
    node_sequence = [c.node_name for c in history]
    assert node_sequence == [
        "triage", "awaiting_diagnosis", "propose_fix", "hitl_lead_signoff",
        "hitl_lead_signoff",  # re-entered on resume, not re-run from the top
        "deploy_fix", "awaiting_verification", "awaiting_verification",
    ]