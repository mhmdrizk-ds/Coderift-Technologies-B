"""
release_tools.py — record_review_approval, merge_pull_request, and
rollback_deployment.

All three are senior/lead write tools with their own independent-of-schema
business validation and their own session.require_role() call — none
piggybacks on deploy_to_production's checks, since a client can call
any of these directly regardless of what tools/list happened to show.
"""

from mcp_server import db
from mcp_server.protocol import JSONRPCError, ERR_NOT_FOUND, ERR_CONFLICT
from mcp_server.tools_impl import text_result


def handle_record_review_approval(conn, session, ctx, arguments: dict) -> dict:
    """Records code-review approval for a pull request: sets status to
    'Approved' and stamps reviewer_id with the approving engineer.

    This tool did not previously exist — merge_pull_request only ever
    read pull_requests.status, it never had a write path to set it to
    'Approved' in the first place. Mirrors merge_pull_request's shape:
    senior/lead only, its own independent-of-schema validation.
    """
    session.require_role("senior", "lead")

    pull_request_id = arguments["pull_request_id"]
    pr = db.get_pull_request(conn, pull_request_id)
    if pr is None:
        raise JSONRPCError(ERR_NOT_FOUND, f"No pull request #{pull_request_id} found.")

    if pr["status"] == "Merged":
        raise JSONRPCError(
            ERR_CONFLICT,
            f"Pull request #{pull_request_id} is already Merged; cannot record "
            f"a review approval for it.",
        )

    conn.execute(
        "UPDATE pull_requests SET status = 'Approved', reviewer_id = ? WHERE id = ?",
        (session.engineer_id, pull_request_id),
    )
    conn.commit()

    return text_result({
        "pull_request_id": pull_request_id,
        "status": "Approved",
        "reviewer_id": session.engineer_id,
        "message": f"Pull request #{pull_request_id} approved.",
    })


def handle_merge_pull_request(conn, session, ctx, arguments: dict) -> dict:
    session.require_role("senior", "lead")

    pull_request_id = arguments["pull_request_id"]
    pr = db.get_pull_request(conn, pull_request_id)
    if pr is None:
        raise JSONRPCError(ERR_NOT_FOUND, f"No pull request #{pull_request_id} found.")

    if pr["status"] != "Approved":
        raise JSONRPCError(
            ERR_CONFLICT,
            f"Pull request #{pull_request_id} is '{pr['status']}', not Approved. "
            f"Cannot merge an Open, Rejected, or already-Merged pull request.",
        )

    scan = db.get_latest_security_scan(conn, pull_request_id)
    if scan is None or scan["status"] != "Passed":
        raise JSONRPCError(
            ERR_CONFLICT,
            f"Pull request #{pull_request_id}'s latest security scan is "
            f"'{scan['status'] if scan else 'missing'}', not Passed. Cannot merge.",
        )

    conn.execute("UPDATE pull_requests SET status = 'Merged' WHERE id = ?", (pull_request_id,))
    conn.commit()

    return text_result({
        "pull_request_id": pull_request_id,
        "status": "Merged",
        "message": f"Pull request #{pull_request_id} merged.",
    })


def handle_rollback_deployment(conn, session, ctx, arguments: dict) -> dict:
    session.require_role("senior", "lead")

    deployment_id = arguments["deployment_id"]
    reason = arguments["reason"]

    deployment = conn.execute(
        """
        SELECT d.id, d.status, r.name AS repository_name, e.name AS environment_name
        FROM deployments d
        JOIN repositories r ON r.id = d.repository_id
        JOIN environments e ON e.id = d.environment_id
        WHERE d.id = ?
        """,
        (deployment_id,),
    ).fetchone()
    if deployment is None:
        raise JSONRPCError(ERR_NOT_FOUND, f"No deployment #{deployment_id} found.")

    if deployment["status"] not in ("Succeeded", "InProgress"):
        raise JSONRPCError(
            ERR_CONFLICT,
            f"Deployment #{deployment_id} is '{deployment['status']}'; only a Succeeded "
            f"or InProgress deployment can be rolled back.",
        )

    conn.execute(
        """
        UPDATE deployments
        SET status = 'RolledBack',
            notes = COALESCE(notes, '') || ' | rolled back: ' || ?
        WHERE id = ?
        """,
        (reason, deployment_id),
    )
    conn.commit()

    return text_result({
        "deployment_id": deployment_id,
        "status": "RolledBack",
        "repository_name": deployment["repository_name"],
        "environment_name": deployment["environment_name"],
        "message": f"Deployment #{deployment_id} rolled back: {reason}",
    })
