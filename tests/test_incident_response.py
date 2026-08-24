from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
import pytest

from state_graph.contracts import NodeFailure
from state_graph.incident_response import make_incident_response_graph
from state_graph.mcp_adapter import McpAdapter
from state_graph.store import CheckpointStore, HitlStore, TicketStore

# ---------------------------------------------------------------------------
# Real database path (NOT a temp DB — uses your actual seeded db/coderift.db)
# ---------------------------------------------------------------------------

REAL_DB_PATH = Path(__file__).parent.parent / "db" / "data" / "coderift.db"

def stores():
    """Return store instances pointing to the REAL database."""
    if not REAL_DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found at {REAL_DB_PATH}. "
            f"Run: python db/init_db.py"
        )
    return (
        CheckpointStore(REAL_DB_PATH),
        HitlStore(REAL_DB_PATH),
        TicketStore(REAL_DB_PATH),
    )


@pytest.fixture(autouse=True)
def _clean_state_graph_tables():
    """These tests intentionally run against the REAL db/coderift.db
    (McpAdapter's real-MCP-call mode hardcodes that path via
    mcp_server/db.py, so a per-test tmp DB isn't an option here — see
    test_security_remediation.py for the tmp_path pattern used where it
    IS an option). But that means checkpoints/hitl_tasks/tickets rows
    left behind by one test (e.g. a HITL task that test never decided)
    used to leak into the next test's hitl.list_pending() / tix.list_open()
    assertions. Business tables (incidents, pull_requests, ...) are left
    untouched — only the three state-graph tables get cleared."""
    if REAL_DB_PATH.exists():
        conn = sqlite3.connect(str(REAL_DB_PATH))
        conn.execute("DELETE FROM checkpoints")
        conn.execute("DELETE FROM hitl_tasks")
        conn.execute("DELETE FROM tickets")
        conn.commit()
        conn.close()
    yield


# ---------------------------------------------------------------------------
# Failing MCP adapter (for ticket test only — simulates real tool failure)
# ---------------------------------------------------------------------------

class FailingMcpAdapter(McpAdapter):
    """Wraps the real MCP adapter but forces deploy_fix to fail once.
    This proves the graph turns a real tool failure into a ticket
    (not a silent retry), exactly as required."""

    def __init__(self):
        super().__init__()
        self.fail_next_deploy = True

    def deploy_fix(self, repo: str, environment: str, pr_id: int, deployed_by: str) -> dict:
        if self.fail_next_deploy:
            self.fail_next_deploy = False
            raise NodeFailure(
                "DEPLOY_FIX_TOOL_ERROR",
                "simulated tool timeout — deploy_to_production failed",
            )
        return super().deploy_fix(repo, environment, pr_id, deployed_by)


# ---------------------------------------------------------------------------
# TESTS
# ---------------------------------------------------------------------------

def test_happy_path_non_critical_incident_resolves_without_hitl():
    """Non-critical incident flows straight through, no HITL pause."""
    ckpt, hitl, tix = stores()
    graph = make_incident_response_graph(
        checkpointer=ckpt, hitl_store=hitl, ticket_store=tix
    )
    run_id = str(uuid.uuid4())

    result = graph.start(run_id, {
        "incident_id": 2,          # تم التعديل لداتا حقيقية
        "severity": "low",         # تم التعديل لداتا حقيقية
        "repo": "payments-service",# تم التعديل لداتا حقيقية
        "pr_id": 1,                # تم التعديل لداتا حقيقية
        "environment": "staging",  # تم التعديل لداتا حقيقية
    })

    assert result["status"] == "waiting"
    assert result["node"] == "awaiting_verification"
    assert hitl.list_pending() == []

    # Simulate monitoring signal: deployment is healthy
    result = graph.resume(run_id, external_event={"verification_result": "healthy"})
    assert result["status"] == "completed"
    assert result["state"]["resolved_at"] is not None


def test_critical_incident_pauses_for_hitl_and_resumes_on_approval():
    """Critical incident requires lead sign-off before deploy.
    After approval, graph continues; if verification fails, it reopens."""
    ckpt, hitl, tix = stores()
    graph = make_incident_response_graph(
        checkpointer=ckpt, hitl_store=hitl, ticket_store=tix
    )
    run_id = str(uuid.uuid4())

    result = graph.start(run_id, {
        "incident_id": 1,          # تم التعديل لداتا حقيقية
        "severity": "critical",
        "repo": "checkout-web",    # تم التعديل لداتا حقيقية
        "pr_id": 2,                # تم التعديل لداتا حقيقية
        "environment": "production",
    })
    assert result["status"] == "paused_hitl"
    assert result["node"] == "hitl_lead_signoff"

    pending = hitl.list_pending()
    assert len(pending) == 1
    task = pending[0]
    assert task.reason  # real human-readable reason, not placeholder
    assert task.payload["incident_id"] == 1 # تم تعديل الـ assert ليتطابق مع الـ ID الجديد

    # Admin approves through the platform (real DB update)
    hitl.decide(task.id, approved=True, decided_by="lead_jane")
    result = graph.resume(run_id, hitl_decision={"approved": True, "approver": "lead_jane"})

    assert result["status"] == "waiting"
    assert result["node"] == "awaiting_verification"

    # Verification fails -> reopened -> loops back, hits HITL again
    result = graph.resume(run_id, external_event={"verification_result": "unhealthy"})
    assert result["status"] == "paused_hitl"
    assert ckpt.load_latest(run_id).state["reopen_count"] == 1


def test_hitl_rejection_loops_back_to_diagnosis_not_a_ticket():
    """Rejected HITL loops back to diagnosis — does NOT open a ticket."""
    ckpt, hitl, tix = stores()
    graph = make_incident_response_graph(
        checkpointer=ckpt, hitl_store=hitl, ticket_store=tix
    )
    run_id = str(uuid.uuid4())

    graph.start(run_id, {
        "incident_id": 1,          # تم التعديل لداتا حقيقية
        "severity": "critical",
        "repo": "billing-worker",
        "pr_id": 3,                # تم التعديل لداتا حقيقية
        "environment": "production",
    })
    task = hitl.list_pending()[0]
    hitl.decide(task.id, approved=False, decided_by="lead_jane", reason="wrong fix")
    result = graph.resume(run_id, hitl_decision={"approved": False, "reason": "wrong fix"})

    # Rejected -> back to awaiting_diagnosis -> propose_fix -> HITL again
    assert result["status"] == "paused_hitl"
    assert tix.list_open() == []


def test_deploy_tool_failure_opens_ticket_distinct_from_hitl():
    """Mid-node deploy failure opens a ticket (NOT HITL).
    Admin resolves ticket, graph resumes from checkpoint."""
    ckpt, hitl, tix = stores()
    graph = make_incident_response_graph(
        mcp=FailingMcpAdapter(),
        checkpointer=ckpt,
        hitl_store=hitl,
        ticket_store=tix,
    )
    run_id = str(uuid.uuid4())

    result = graph.start(run_id, {
        "incident_id": 2,          # تم التعديل لداتا حقيقية
        "severity": "medium",
        "repo": "payments-service",# تم التعديل لداتا حقيقية
        "pr_id": 4,                # تم التعديل لداتا حقيقية
        "environment": "production",
    })

    assert result["status"] == "ticketed"
    assert result["node"] == "deploy_fix"

    open_tickets = tix.list_open()
    assert len(open_tickets) == 1
    ticket = open_tickets[0]
    assert ticket.error_code == "DEPLOY_FIX_TOOL_ERROR"
    assert ticket.state_snapshot["incident_id"] == 2 # تم تعديل الـ assert ليتطابق مع الـ ID الجديد
    # Ticket must NEVER appear in HITL inbox
    assert hitl.list_pending() == []

    # Admin resolves the ticket, graph resumes from that exact checkpoint
    tix.set_status(ticket.id, "resolved", resolution_notes="tool was flaky, retried")
    result = graph.resume(run_id)
    assert result["status"] == "waiting"
    assert result["node"] == "awaiting_verification"


def test_crash_and_resume_no_reexecution():
    """Process dies mid-run. New process resumes SAME run_id from last checkpoint.
    Already-completed steps are NOT re-executed."""
    ckpt, hitl, tix = stores()
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

    # --- "Process A" ---
    graph_a = make_incident_response_graph(
        mcp=counting_mcp_factory(),
        checkpointer=ckpt,
        hitl_store=hitl,
        ticket_store=tix,
    )
    result = graph_a.start(run_id, {
        "incident_id": 1,          # تم التعديل لداتا حقيقية
        "severity": "critical",
        "repo": "checkout-web",    # تم التعديل لداتا حقيقية
        "pr_id": 6,                # تم التعديل لداتا حقيقية
        "environment": "production",
    })
    assert result["status"] == "paused_hitl"
    assert diagnosis_calls["count"] == 1
    del graph_a  # "the process dies"

    # --- "Process B" — NOTHING carried over except the sqlite file on disk ---
    task = hitl.list_pending()[0]
    hitl.decide(task.id, approved=True, decided_by="lead_bob")

    graph_b = make_incident_response_graph(
        mcp=counting_mcp_factory(),  # fresh adapter, fresh counter
        checkpointer=CheckpointStore(REAL_DB_PATH),
        hitl_store=HitlStore(REAL_DB_PATH),
        ticket_store=TicketStore(REAL_DB_PATH),
    )
    result = graph_b.resume(run_id, hitl_decision={"approved": True, "approver": "lead_bob"})

    assert result["status"] == "waiting"
    assert result["node"] == "awaiting_verification"
    # draft_incident_summary was called exactly ONCE — proof of no re-execution
    assert diagnosis_calls["count"] == 1

    # Full checkpoint history proves each node ran exactly once in order
    history = ckpt.history(run_id)
    node_sequence = [c.node_name for c in history]
    assert node_sequence == [
        "triage", "awaiting_diagnosis", "propose_fix", "hitl_lead_signoff",
        "hitl_lead_signoff",  # re-entered on resume, not restarted from top
        "deploy_fix", "awaiting_verification", "awaiting_verification",
    ]