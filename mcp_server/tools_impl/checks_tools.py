"""
checks_tools.py — run_pre_deploy_checks.

Genuinely multi-stage: unit tests, then integration tests, then a fresh
security scan, run sequentially with a small simulated delay per stage so
the demo can show real intermediate progress notifications instead of one
blocking response at the end. The final stage writes a new security_scans
row — this tool doesn't deploy anything, it just refreshes the scan
result a later deploy_to_production call will read.

Scan outcome is deterministic, not random, so the demo is repeatable: if
this pull request's previous latest scan was Failed, the re-run comes
back Failed again (the underlying issue hasn't been fixed by running more
tests); otherwise (Passed or Pending/no prior scan) it comes back Passed.
"""

import time

from mcp_server import db
from mcp_server.protocol import JSONRPCError, ERR_NOT_FOUND
from mcp_server.tools_impl import text_result

_STAGE_DELAY_SECONDS = 0.4


def handle_run_pre_deploy_checks(conn, session, ctx, arguments: dict) -> dict:
    pull_request_id = arguments["pull_request_id"]
    pull_request = db.get_pull_request(conn, pull_request_id)
    if pull_request is None:
        raise JSONRPCError(ERR_NOT_FOUND, f"No pull request #{pull_request_id} found.")

    stages = ["Unit tests", "Integration tests", "Security scan"]
    total = len(stages)
    results = []

    # --- Stage 1: unit tests ---
    ctx.report_progress(progress=0, total=total, message="Starting unit tests")
    time.sleep(_STAGE_DELAY_SECONDS)
    results.append({"stage": "Unit tests", "result": "Passed"})
    ctx.report_progress(progress=1, total=total, message="Unit tests passed")

    # --- Stage 2: integration tests ---
    time.sleep(_STAGE_DELAY_SECONDS)
    results.append({"stage": "Integration tests", "result": "Passed"})
    ctx.report_progress(progress=2, total=total, message="Integration tests passed")

    # --- Stage 3: security scan (deterministic outcome, see docstring) ---
    previous_scan = db.get_latest_security_scan(conn, pull_request_id)
    new_status = "Failed" if (previous_scan and previous_scan["status"] == "Failed") else "Passed"
    time.sleep(_STAGE_DELAY_SECONDS)
    cur = conn.execute(
        "INSERT INTO security_scans (pull_request_id, status, scan_type) VALUES (?, ?, ?)",
        (pull_request_id, new_status, "SAST"),
    )
    conn.commit()
    results.append({"stage": "Security scan", "result": new_status})
    ctx.report_progress(progress=3, total=total, message=f"Security scan {new_status}")

    return text_result({
        "pull_request_id": pull_request_id,
        "stages": results,
        "new_security_scan_id": cur.lastrowid,
        "final_scan_status": new_status,
    })
