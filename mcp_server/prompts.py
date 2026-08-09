"""
prompts.py — prompts/list and prompts/get.

Parameterization: each prompt declares real arguments in its catalog
entry. get_prompt() requires those arguments, looks up the relevant
deployment/incident data from the database, and fills {{token}}
placeholders in the template with real values — so calling
draft_rollback_plan for two different deployments returns two different,
factually-grounded prompts instead of the same static file.
"""

from pathlib import Path

from mcp_server.protocol import JSONRPCError, ERR_NOT_FOUND, INVALID_PARAMS
from mcp_server import db

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# name -> (filename, title, required argument names)
_CATALOG = {
    "draft_rollback_plan": (
        "draft_rollback_plan_prompt.md",
        "Rollback Plan",
        ["deployment_id"],
    ),
    "draft_incident_postmortem": (
        "draft_incident_postmortem_prompt.md",
        "Incident Postmortem",
        ["incident_id"],
    ),
}


def list_prompts() -> dict:
    prompts = []
    for name, (fname, title, required_args) in _CATALOG.items():
        prompts.append({
            "name": name,
            "title": title,
            "arguments": [
                {"name": arg, "required": True}
                for arg in required_args
            ],
        })
    return {"prompts": prompts}


def _lookup_deployment_context(deployment_id: int) -> dict:
    conn = db.get_connection()
    try:
        row = conn.execute(
            """
            SELECT d.id, d.status AS deployment_status, d.created_at,
                   r.name AS repository_name, e.name AS environment_name,
                   eng.name AS deployed_by_name, pr.title AS pull_request_title
            FROM deployments d
            JOIN repositories r ON r.id = d.repository_id
            JOIN environments e ON e.id = d.environment_id
            JOIN engineers eng ON eng.id = d.deployed_by
            JOIN pull_requests pr ON pr.id = d.pull_request_id
            WHERE d.id = ?
            """,
            (deployment_id,),
        ).fetchone()
        if row is None:
            raise JSONRPCError(ERR_NOT_FOUND, f"No deployment #{deployment_id} found.")
        return {
            "deployment_id": row["id"],
            "repository_name": row["repository_name"],
            "environment_name": row["environment_name"],
            "deployment_status": row["deployment_status"],
            "pull_request_title": row["pull_request_title"],
            "deployed_by_name": row["deployed_by_name"],
            "created_at": row["created_at"],
        }
    finally:
        conn.close()


def _lookup_incident_context(incident_id: int) -> dict:
    conn = db.get_connection()
    try:
        row = conn.execute(
            """
            SELECT i.id, i.title, i.severity, i.status, i.created_at, i.resolved_at,
                   d.status AS deployment_status,
                   r.name AS deployment_repository, e.name AS deployment_environment,
                   pr.title AS pull_request_title
            FROM incidents i
            LEFT JOIN deployments d ON d.id = i.deployment_id
            LEFT JOIN repositories r ON r.id = d.repository_id
            LEFT JOIN environments e ON e.id = d.environment_id
            LEFT JOIN pull_requests pr ON pr.id = d.pull_request_id
            WHERE i.id = ?
            """,
            (incident_id,),
        ).fetchone()
        if row is None:
            raise JSONRPCError(ERR_NOT_FOUND, f"No incident #{incident_id} found.")
        return {
            "incident_id": row["id"],
            "title": row["title"],
            "severity": row["severity"],
            "status": row["status"],
            "created_at": row["created_at"],
            "resolved_at": row["resolved_at"] or "not yet resolved",
            "deployment_status": row["deployment_status"] or "N/A (no linked deployment)",
            "deployment_repository": row["deployment_repository"] or "N/A",
            "deployment_environment": row["deployment_environment"] or "N/A",
            "pull_request_title": row["pull_request_title"] or "N/A",
        }
    finally:
        conn.close()


def _resolve_arguments(name: str, arguments: dict) -> dict:
    if name == "draft_rollback_plan":
        return _lookup_deployment_context(arguments["deployment_id"])
    if name == "draft_incident_postmortem":
        return _lookup_incident_context(arguments["incident_id"])
    return dict(arguments)


def _fill_template(text: str, context: dict) -> str:
    for key, value in context.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


def get_prompt(name: str, arguments: dict | None = None) -> dict:
    entry = _CATALOG.get(name)
    if entry is None:
        raise JSONRPCError(ERR_NOT_FOUND, f"No prompt named '{name}'.")
    fname, title, required_args = entry

    arguments = arguments or {}
    missing = [a for a in required_args if not arguments.get(a)]
    if missing:
        raise JSONRPCError(
            INVALID_PARAMS,
            f"Prompt '{name}' missing required argument(s): {', '.join(missing)}.",
        )

    path = PROMPTS_DIR / fname
    if not path.exists():
        raise JSONRPCError(ERR_NOT_FOUND, f"Prompt file '{fname}' missing on disk.")

    text = path.read_text(encoding="utf-8")
    context = _resolve_arguments(name, arguments)
    text = _fill_template(text, context)

    return {
        "description": title,
        "messages": [
            {"role": "user", "content": {"type": "text", "text": text}}
        ],
    }
