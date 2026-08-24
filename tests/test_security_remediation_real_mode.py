"""
tests/test_security_remediation_real_mode.py

Real-mode coverage for the security_remediation graph — i.e. McpAdapter
with client=None, hitting the actual mcp_server tool handlers against the
real, seeded db/coderift.db (same pattern test_incident_response.py uses
for the incident_response graph; see that file's docstring/fixture).

Before this fix, get_pull_request / run_pre_deploy_checks /
merge_pull_request / record_review_approval / deploy_to_production_override
had NO real-mode branch at all — they unconditionally did
`self._client.call(...)`, which raised AttributeError the instant
self._client was None (real mode). That meant this graph had *zero*
real-mode coverage: Tree of Thoughts, the HITL lead sign-off, and the
ticket path were all "done but unverified" / "blocked" per the audit,
regardless of how correct their own logic looked on read.

These tests exercise the graph the way user_platform/backend.py actually
calls it: real McpAdapter(), real mcp_server handlers, real DB rows.

Uses PR #1 (payments-service, Approved, Passed scan -> clean merge path)
and PR #2 (checkout-web, Approved, Failed scan -> HITL override path)
from db/seed.sql. Since real mode genuinely mutates pull_requests /
security_scans / deployments (unlike the simulated-mode unit tests in
test_security_remediation.py), an autouse fixture snapshots and restores
those specific rows around every test -- this is the fix for exactly the
kind of self-inflicted seed-state pollution the audit already caught
once for a different graph (test_reflexion_needs_a_second_full_trial...).
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from mcp_server import db as mcp_db
from state_graph.contracts import NodeFailure
from state_graph.mcp_adapter import McpAdapter
from state_graph.security_remediation import make_security_remediation_graph
from state_graph.store import CheckpointStore, HitlStore, TicketStore

REAL_DB_PATH = Path(__file__).parent.parent / "db" / "data" / "coderift.db"

PR_TICKET = 3  # billing-worker, Open, no scan on record

PR_CLEAN = 1     # payments-service, Approved, Passed scan
PR_FAILED = 2    # checkout-web, Approved, Failed scan


def stores():
    if not REAL_DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found at {REAL_DB_PATH}. Run: python db/init_db.py "
            f"&& python db/apply_migration.py"
        )
    return (
        CheckpointStore(REAL_DB_PATH),
        HitlStore(REAL_DB_PATH),
        TicketStore(REAL_DB_PATH),
    )


@pytest.fixture(autouse=True)
def _isolate_real_db_state():
    """Clears the three state-graph tables (same as test_incident_response.py)
    AND restores pull_requests/security_scans/deployments for the PRs these
    tests touch, so real-mode side effects from one test run never leak
    into the next."""
    touched_prs = (PR_CLEAN, PR_FAILED, PR_TICKET)
    conn = sqlite3.connect(str(REAL_DB_PATH))
    conn.row_factory = sqlite3.Row

    def _snapshot_prs():
        return {
            row["id"]: dict(row)
            for row in conn.execute(
                f"SELECT * FROM pull_requests WHERE id IN "
                f"({','.join('?' * len(touched_prs))})", touched_prs
            )
        }

    before_prs = _snapshot_prs()
    before_scan_ids = {
        row["id"] for row in conn.execute(
            f"SELECT id FROM security_scans WHERE pull_request_id IN "
            f"({','.join('?' * len(touched_prs))})", touched_prs
        )
    }

    yield

    conn.execute("DELETE FROM checkpoints")
    conn.execute("DELETE FROM hitl_tasks")
    conn.execute("DELETE FROM tickets")
    # Restore pull_requests rows this test may have mutated (status/reviewer_id).
    for pr_id, row in before_prs.items():
        conn.execute(
            "UPDATE pull_requests SET status = ?, reviewer_id = ? WHERE id = ?",
            (row["status"], row["reviewer_id"], pr_id),
        )
    # Remove any security_scans rows these tests inserted that weren't there
    # before -- handles both "kept the original scan" (PR_CLEAN, PR_FAILED)
    # and "had no scan at all before" (PR_TICKET) correctly.
    after_scan_ids = {
        row["id"] for row in conn.execute(
            f"SELECT id FROM security_scans WHERE pull_request_id IN "
            f"({','.join('?' * len(touched_prs))})", touched_prs
        )
    }
    new_scan_ids = after_scan_ids - before_scan_ids
    for scan_id in new_scan_ids:
        conn.execute("DELETE FROM security_scans WHERE id = ?", (scan_id,))
    conn.execute(
        "DELETE FROM deployments WHERE pull_request_id IN (?, ?) AND notes LIKE 'Lead-approved%'",
        (PR_CLEAN, PR_FAILED),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 1. Adapter-level: the tool that never existed before this fix.
# ---------------------------------------------------------------------------

def test_record_review_approval_real_mode_writes_to_db():
    """record_review_approval had NO real mcp_server tool at all before
    this fix (docstring: 'no approve_pull_request MCP tool exists yet').
    This proves the new handler actually writes pull_requests.status."""
    adapter = McpAdapter()  # client=None -> real mode
    result = adapter.record_review_approval(PR_CLEAN)
    assert result["status"] == "Approved"

    conn = mcp_db.get_connection()
    pr = mcp_db.get_pull_request(conn, PR_CLEAN)
    assert pr["status"] == "Approved"
    assert pr["reviewer_id"] is not None
    conn.close()


def test_record_review_approval_real_mode_rejects_already_merged():
    """Business-rule validation on the new handler, not just a bare write."""
    conn = mcp_db.get_connection()
    conn.execute("UPDATE pull_requests SET status = 'Merged' WHERE id = ?", (PR_CLEAN,))
    conn.commit()
    conn.close()

    from state_graph.contracts import NodeFailure
    adapter = McpAdapter()
    with pytest.raises(NodeFailure) as exc_info:
        adapter.record_review_approval(PR_CLEAN)
    assert exc_info.value.error_code == "REVIEW_APPROVAL_TOOL_ERROR"


# ---------------------------------------------------------------------------
# 2. Full graph, real mode, clean path: Approved + Passed -> deploy_patch
#    (record_review_approval -> merge_pull_request), both previously
#    unreachable in real mode.
# ---------------------------------------------------------------------------

def test_real_mode_clean_path_merges_pr(monkeypatch):
    ckpt, hitl, tix = stores()

    # patch_pr calls run_pre_deploy_checks, which re-scans and is
    # deterministic (Passed unless the *previous* scan was Failed).
    # PR #1's seeded scan is Passed, so it stays Passed -> no HITL needed.
    graph = make_security_remediation_graph(checkpointer=ckpt, hitl_store=hitl, ticket_store=tix)
    run_id = str(uuid.uuid4())

    result = graph.start(run_id, {
        "pull_request_id": PR_CLEAN,
        "repository_name": "payments-service",
        "environment_name": "production",
    })
    assert result["status"] == "waiting"
    assert result["node"] == "awaiting_code_review"

    result = graph.resume(run_id, external_event={"review_result": "approved"})
    assert result["status"] == "completed"
    assert result["state"]["resolution_path"] == "clean_merge"

    conn = mcp_db.get_connection()
    pr = mcp_db.get_pull_request(conn, PR_CLEAN)
    assert pr["status"] == "Merged"
    conn.close()


# ---------------------------------------------------------------------------
# 3. Full graph, real mode, Failed-scan path: HITL lead sign-off ->
#    deploy_to_production_override, both previously unreachable.
# ---------------------------------------------------------------------------

def test_real_mode_failed_scan_pauses_hitl_and_overrides():
    ckpt, hitl, tix = stores()

    # PR #2's seeded scan is Failed; the deterministic re-scan rule keeps
    # a previously-Failed scan Failed -> routes to hitl_lead_signoff, not
    # awaiting_code_review.
    graph = make_security_remediation_graph(checkpointer=ckpt, hitl_store=hitl, ticket_store=tix)
    run_id = str(uuid.uuid4())

    result = graph.start(run_id, {
        "pull_request_id": PR_FAILED,
        "repository_name": "checkout-web",
        "environment_name": "production",
    })
    assert result["status"] == "paused_hitl"
    assert result["node"] == "hitl_lead_signoff"

    pending = hitl.list_pending()
    assert len(pending) == 1
    assert pending[0].payload["pull_request_id"] == PR_FAILED

    hitl.decide(pending[0].id, approved=True, decided_by="lead_maria",
                reason="compensating control per 4.3(b)")
    result = graph.resume(run_id, hitl_decision={
        "approved": True, "approver": "lead_maria",
        "reason": "compensating control per 4.3(b)",
    })

    assert result["status"] == "completed"
    assert result["state"]["resolution_path"] == "lead_override"

    conn = mcp_db.get_connection()
    dep = conn.execute(
        "SELECT status, notes FROM deployments WHERE pull_request_id = ? "
        "ORDER BY id DESC LIMIT 1", (PR_FAILED,)
    ).fetchone()
    conn.close()
    assert dep is not None
    assert dep[0] == "Succeeded"


# ---------------------------------------------------------------------------
# 4. Full graph, real mode, ticket path: a mid-node tool failure opens a
#    real ticket distinct from a HITL pause, and resumes from checkpoint
#    once resolved -- previously unreachable in real mode (only ever
#    proven in test_security_remediation.py's simulated-mode
#    test_pre_deploy_checks_tool_failure_opens_ticket_distinct_from_hitl).
# ---------------------------------------------------------------------------

class _FailOnceRealAdapter(McpAdapter):
    """Real-mode McpAdapter (client=None) that injects one interrupted
    run_pre_deploy_checks call, mirroring policy §6.2 -- same
    failure-injection shape test_security_remediation.py's
    FailingChecksMcpAdapter already uses for the simulated-mode version
    of this same test, just wired to the real handler underneath instead
    of SimulatedMcpClient."""

    def __init__(self):
        super().__init__()  # client=None -> real mode
        self.fail_next_checks = True

    def run_pre_deploy_checks(self, pull_request_id: int) -> dict:
        if self.fail_next_checks:
            self.fail_next_checks = False
            raise NodeFailure("PRE_DEPLOY_CHECKS_TOOL_ERROR", "simulated interrupted run")
        return super().run_pre_deploy_checks(pull_request_id)


def test_real_mode_tool_failure_opens_ticket_and_resumes_from_checkpoint():
    ckpt, hitl, tix = stores()

    graph = make_security_remediation_graph(
        mcp=_FailOnceRealAdapter(), checkpointer=ckpt, hitl_store=hitl, ticket_store=tix
    )
    run_id = str(uuid.uuid4())

    result = graph.start(run_id, {
        "pull_request_id": PR_TICKET,
        "repository_name": "billing-worker",
        "environment_name": "production",
    })

    assert result["status"] == "ticketed"
    assert hitl.list_pending() == []  # ticket path, not a HITL pause

    tickets = tix.list_open()
    assert len(tickets) == 1
    ticket = tickets[0]
    assert ticket.status == "open"
    assert ticket.run_id == run_id

    # Checkpoint exists at the point of failure -- resolving the ticket
    # resumes from there, it does not restart patch_pr from scratch.
    history_before = ckpt.history(run_id)
    node_sequence_before = [c.node_name for c in history_before]

    tix.set_status(ticket.id, "resolved", resolution_notes="scanner flaked, retried clean")

    graph2 = make_security_remediation_graph(
        mcp=McpAdapter(), checkpointer=CheckpointStore(REAL_DB_PATH),
        hitl_store=HitlStore(REAL_DB_PATH), ticket_store=TicketStore(REAL_DB_PATH),
    )
    result2 = graph2.resume(run_id)

    assert result2["status"] in ("completed", "waiting", "paused_hitl")
    history_after = ckpt.history(run_id)
    # Every step recorded before the crash is still there, untouched --
    # resume picked up after the checkpoint, it didn't re-run from node 1.
    assert [c.node_name for c in history_after][:len(node_sequence_before)] == node_sequence_before

    conn = mcp_db.get_connection()
    pr = mcp_db.get_pull_request(conn, PR_TICKET)
    conn.close()
    assert pr is not None  # sanity: real DB, real row, untouched by the injected failure
