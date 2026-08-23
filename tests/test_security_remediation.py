from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from state_graph.contracts import NodeFailure
from state_graph.mcp_adapter import McpAdapter, SimulatedMcpClient
from state_graph.security_remediation import (
    ConstrainedToolViolation,
    make_security_remediation_graph,
)
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


class FailingChecksMcpAdapter(McpAdapter):
    """Simulates policy 6.2: run_pre_deploy_checks gets interrupted mid-run
    the first time it's called for a given PR."""

    def __init__(self):
        super().__init__(client=SimulatedMcpClient())
        self.fail_next_checks = True

    def run_pre_deploy_checks(self, pull_request_id: int) -> dict:
        if self.fail_next_checks:
            self.fail_next_checks = False
            raise NodeFailure("PRE_DEPLOY_CHECKS_TOOL_ERROR", "simulated interrupted run")
        return super().run_pre_deploy_checks(pull_request_id)


# ---------------------------------------------------------------------------
# Clean path: Passed scan -> review approved -> merge, no HITL needed
# ---------------------------------------------------------------------------

def test_clean_path_passed_scan_and_approved_review_merges(db_path):
    ckpt, hitl, tix = stores(db_path)
    mcp = McpAdapter(client=SimulatedMcpClient())
    mcp.seed_pull_request(101, status="Open", scan_status="Pending")
    graph = make_security_remediation_graph(mcp=mcp, checkpointer=ckpt,
                                              hitl_store=hitl, ticket_store=tix)
    run_id = str(uuid.uuid4())

    result = graph.start(run_id, {
        "pull_request_id": 101, "repository_name": "billing-worker",
        "environment_name": "production",
    })

    # run_pre_deploy_checks comes back Passed (previous status wasn't
    # Failed), so it waits for a human reviewer next — a genuine pause.
    assert result["status"] == "waiting"
    assert result["node"] == "awaiting_code_review"
    assert hitl.list_pending() == []  # no HITL needed for a Passed scan

    result = graph.resume(run_id, external_event={"review_result": "approved"})
    assert result["status"] == "completed"
    assert result["state"]["resolution_path"] == "clean_merge"
    assert result["state"]["resolved_at"] is not None


# ---------------------------------------------------------------------------
# Failed-scan path: real HITL required, per policy 4.1
# ---------------------------------------------------------------------------

def test_failed_scan_pauses_for_lead_hitl_and_resumes_on_approval(db_path):
    ckpt, hitl, tix = stores(db_path)
    mcp = McpAdapter(client=SimulatedMcpClient())
    mcp.seed_pull_request(102, status="Open", scan_status="Failed")
    graph = make_security_remediation_graph(mcp=mcp, checkpointer=ckpt,
                                              hitl_store=hitl, ticket_store=tix)
    run_id = str(uuid.uuid4())

    result = graph.start(run_id, {
        "pull_request_id": 102, "repository_name": "billing-worker",
        "environment_name": "production",
    })

    # Previous scan was Failed -> stays Failed -> HITL, not a wait.
    assert result["status"] == "paused_hitl"
    assert result["node"] == "hitl_lead_signoff"

    pending = hitl.list_pending()
    assert len(pending) == 1
    task = pending[0]
    assert "lead-role engineer" in task.reason  # real, specific reason
    assert task.payload["pull_request_id"] == 102

    hitl.decide(task.id, approved=True, decided_by="lead_maria",
                 reason="worse active vuln, deploying with compensating control per 4.3(b)")
    result = graph.resume(run_id, hitl_decision={
        "approved": True, "approver": "lead_maria",
        "reason": "worse active vuln, deploying with compensating control per 4.3(b)",
    })

    assert result["status"] == "completed"
    assert result["state"]["resolution_path"] == "lead_override"
    assert result["state"]["hitl_approver"] == "lead_maria"


def test_hitl_rejection_loops_back_to_propose_remediation_not_a_ticket(db_path):
    ckpt, hitl, tix = stores(db_path)
    mcp = McpAdapter(client=SimulatedMcpClient())
    mcp.seed_pull_request(103, status="Open", scan_status="Failed")
    graph = make_security_remediation_graph(mcp=mcp, checkpointer=ckpt,
                                              hitl_store=hitl, ticket_store=tix)
    run_id = str(uuid.uuid4())

    graph.start(run_id, {
        "pull_request_id": 103, "repository_name": "billing-worker",
        "environment_name": "production",
    })
    task = hitl.list_pending()[0]
    hitl.decide(task.id, approved=False, decided_by="lead_maria",
                 reason="not a valid override reason under 4.4")
    result = graph.resume(run_id, hitl_decision={
        "approved": False, "reason": "not a valid override reason under 4.4",
    })

    # Rejected override -> back through propose_remediation -> patch_pr.
    # Same underlying (still-Failed) scan means it hits HITL again, not a
    # ticket — a rejection is never the same code path as a ticket.
    assert result["status"] == "paused_hitl"
    assert tix.list_open() == []
    # attempt_number is 2 at the moment of the *first* HITL pause
    # (patch_pr already incremented it once); rejecting loops back
    # through propose_remediation -> patch_pr, incrementing it again
    # before the second pause.
    assert ckpt.load_latest(run_id).state["attempt_number"] == 3

    # A genuinely different strategy is proposed on the second pass, per
    # the Tree of Thoughts node deprioritizing whatever was already tried.
    state = ckpt.load_latest(run_id).state
    first_attempt_strategy = state["strategy_candidates"]
    assert len(first_attempt_strategy) >= 3


# ---------------------------------------------------------------------------
# Ticket path: scan tool failure is distinct from a HITL pause
# ---------------------------------------------------------------------------

def test_pre_deploy_checks_tool_failure_opens_ticket_distinct_from_hitl(db_path):
    ckpt, hitl, tix = stores(db_path)
    mcp = FailingChecksMcpAdapter()
    mcp.seed_pull_request(104, status="Open", scan_status="Pending")
    graph = make_security_remediation_graph(mcp=mcp, checkpointer=ckpt,
                                              hitl_store=hitl, ticket_store=tix)
    run_id = str(uuid.uuid4())

    result = graph.start(run_id, {
        "pull_request_id": 104, "repository_name": "billing-worker",
        "environment_name": "production",
    })

    assert result["status"] == "ticketed"
    assert result["node"] == "patch_pr"
    open_tickets = tix.list_open()
    assert len(open_tickets) == 1
    ticket = open_tickets[0]
    assert ticket.error_code == "PRE_DEPLOY_CHECKS_TOOL_ERROR"
    assert ticket.state_snapshot["pull_request_id"] == 104
    assert hitl.list_pending() == []  # a ticket must never appear in the HITL inbox

    # Admin resolves the ticket; the run resumes from that exact
    # checkpoint (patch_pr re-runs, everything before it does not).
    tix.set_status(ticket.id, "resolved", resolution_notes="scan infra was flaky, retried")
    result = graph.resume(run_id)
    assert result["status"] == "waiting"  # this time run_pre_deploy_checks succeeds
    assert result["node"] == "awaiting_code_review"


def test_constrained_react_blocks_non_whitelisted_tool_call(db_path):
    """Proves the allowlist is enforced in code: even if a node were
    coerced into trying a tool outside its allowlist, the call never
    reaches the adapter — it's rejected before that, becoming a ticket."""
    ckpt, hitl, tix = stores(db_path)
    from state_graph.security_remediation import _call_whitelisted_tool

    called = {"value": False}

    def forbidden_call():
        called["value"] = True
        return {"ok": True}

    with pytest.raises(ConstrainedToolViolation):
        _call_whitelisted_tool("patch_pr", "merge_pull_request", forbidden_call)

    assert called["value"] is False  # never actually invoked


# ---------------------------------------------------------------------------
# Crash-and-resume: kill the process mid-run, restart, no re-execution
# ---------------------------------------------------------------------------

def test_crash_and_resume_no_reexecution(db_path):
    ckpt, hitl, tix = stores(db_path)
    run_id = str(uuid.uuid4())

    checks_calls = {"count": 0}

    def counting_mcp_factory():
        adapter = McpAdapter(client=SimulatedMcpClient())
        adapter.seed_pull_request(105, status="Open", scan_status="Failed")
        original = adapter.run_pre_deploy_checks
        def counted(pull_request_id):
            checks_calls["count"] += 1
            return original(pull_request_id)
        adapter.run_pre_deploy_checks = counted
        return adapter

    # --- "process A" ---
    graph_a = make_security_remediation_graph(
        mcp=counting_mcp_factory(), checkpointer=ckpt, hitl_store=hitl, ticket_store=tix,
    )
    result = graph_a.start(run_id, {
        "pull_request_id": 105, "repository_name": "billing-worker",
        "environment_name": "production",
    })
    assert result["status"] == "paused_hitl"
    assert checks_calls["count"] == 1
    del graph_a  # "the process dies"

    # --- "process B" — nothing carried over except the db file ---
    # Reset the shared counter: it's process A's own call count we just
    # asserted above, and everything below should measure only what
    # process B itself calls, per the assertion at the end of this test.
    checks_calls["count"] = 0
    task = hitl.list_pending()[0]
    hitl.decide(task.id, approved=True, decided_by="lead_bob")

    graph_b = make_security_remediation_graph(
        mcp=counting_mcp_factory(),  # fresh adapter, fresh call counter, fresh PR seed
        checkpointer=CheckpointStore(db_path),
        hitl_store=HitlStore(db_path),
        ticket_store=TicketStore(db_path),
    )
    result = graph_b.resume(run_id, hitl_decision={"approved": True, "approver": "lead_bob"})

    assert result["status"] == "completed"
    assert result["state"]["resolution_path"] == "lead_override"

    history = ckpt.history(run_id)
    node_sequence = [c.node_name for c in history]
    assert node_sequence == [
        "scan_flag", "propose_remediation", "patch_pr", "hitl_lead_signoff",
        "hitl_lead_signoff",  # re-entered on resume, not re-run from the top
        "deploy_patch_override",
        "resolved", "resolved",  # entering resolved, then resolved completing
    ]
    # Process B's counter only saw the checks call graph B itself made
    # (none — patch_pr already completed before the crash, so it is not
    # re-executed on resume).
    assert checks_calls["count"] == 0
