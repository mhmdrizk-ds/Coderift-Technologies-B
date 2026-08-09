"""
query_tools.py — read-only tools. check_deployment_status and
get_pull_request are public (no role required, session may be anonymous)
so these handlers do NOT call session.require_role() for those two.
list_active_incidents requires any authenticated role; list_feature_flags
is restricted to senior/lead — see schemas.py for why. All four still
fully validate their inputs against the database (an unknown repository
name is a clean 404, not a stack trace).
"""

from mcp_server import db
from mcp_server.protocol import JSONRPCError, ERR_NOT_FOUND
from mcp_server.tools_impl import text_result


def handle_check_deployment_status(conn, session, ctx, arguments: dict) -> dict:
    repository_name = arguments["repository_name"]
    environment_name = arguments["environment_name"]

    repository = db.get_repository_by_name(conn, repository_name)
    if repository is None:
        raise JSONRPCError(ERR_NOT_FOUND, f"No repository '{repository_name}' found.")

    environment = db.get_environment(conn, repository["id"], environment_name)
    if environment is None:
        raise JSONRPCError(
            ERR_NOT_FOUND,
            f"Repository '{repository_name}' has no '{environment_name}' environment.",
        )

    row = conn.execute(
        """
        SELECT d.id, d.status, d.created_at, d.notes,
               eng.name AS deployed_by_name, pr.title AS pull_request_title
        FROM deployments d
        JOIN engineers eng ON eng.id = d.deployed_by
        JOIN pull_requests pr ON pr.id = d.pull_request_id
        WHERE d.repository_id = ? AND d.environment_id = ?
        ORDER BY d.created_at DESC, d.id DESC
        LIMIT 1
        """,
        (repository["id"], environment["id"]),
    ).fetchone()

    if row is None:
        return text_result({
            "repository_name": repository_name,
            "environment_name": environment_name,
            "deployment": None,
            "message": "No deployment recorded for this repository/environment yet.",
        })

    return text_result({
        "repository_name": repository_name,
        "environment_name": environment_name,
        "deployment": {
            "deployment_id": row["id"],
            "status": row["status"],
            "deployed_by": row["deployed_by_name"],
            "pull_request_title": row["pull_request_title"],
            "created_at": row["created_at"],
            "notes": row["notes"],
        },
    })


def handle_get_pull_request(conn, session, ctx, arguments: dict) -> dict:
    pull_request_id = arguments["pull_request_id"]

    pr = db.get_pull_request(conn, pull_request_id)
    if pr is None:
        raise JSONRPCError(ERR_NOT_FOUND, f"No pull request #{pull_request_id} found.")

    scan = db.get_latest_security_scan(conn, pull_request_id)

    return text_result({
        "pull_request_id": pr["id"],
        "repository_name": pr["repository_name"],
        "title": pr["title"],
        "description": pr["description"],
        "author": pr["author_name"],
        "status": pr["status"],
        "latest_security_scan": {
            "status": scan["status"],
            "scan_type": scan["scan_type"],
            "created_at": scan["created_at"],
        } if scan else None,
        "created_at": pr["created_at"],
    })


def handle_list_active_incidents(conn, session, ctx, arguments: dict) -> dict:
    rows = conn.execute(
        """
        SELECT i.id, i.title, i.severity, i.created_at,
               d.id AS deployment_id, r.name AS repository_name, e.name AS environment_name
        FROM incidents i
        LEFT JOIN deployments d ON d.id = i.deployment_id
        LEFT JOIN repositories r ON r.id = d.repository_id
        LEFT JOIN environments e ON e.id = d.environment_id
        WHERE i.status = 'open'
        ORDER BY i.severity DESC, i.created_at
        """
    ).fetchall()

    return text_result({
        "active_incidents": [
            {
                "incident_id": r["id"],
                "title": r["title"],
                "severity": r["severity"],
                "created_at": r["created_at"],
                "deployment_id": r["deployment_id"],
                "repository_name": r["repository_name"],
                "environment_name": r["environment_name"],
            }
            for r in rows
        ]
    })


def handle_list_feature_flags(conn, session, ctx, arguments: dict) -> dict:
    session.require_role("senior", "lead")

    repository_name = arguments["repository_name"]
    repository = db.get_repository_by_name(conn, repository_name)
    if repository is None:
        raise JSONRPCError(ERR_NOT_FOUND, f"No repository '{repository_name}' found.")

    rows = conn.execute(
        """
        SELECT f.id, f.name, f.enabled, e.name AS environment_name
        FROM feature_flags f
        JOIN environments e ON e.id = f.environment_id
        WHERE f.repository_id = ?
        ORDER BY e.name, f.name
        """,
        (repository["id"],),
    ).fetchall()

    return text_result({
        "repository_name": repository_name,
        "feature_flags": [
            {"name": r["name"], "environment_name": r["environment_name"], "enabled": bool(r["enabled"])}
            for r in rows
        ],
    })
