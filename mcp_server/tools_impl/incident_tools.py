"""
incident_tools.py — draft_incident_summary.

Why this genuinely needs sampling instead of the server just computing an
answer: turning "deployment #1 Failed, PR was 'Add caching layer for
invoice queries', severity critical" into a readable plain-language
summary for a non-engineer stakeholder is exactly the kind of open-ended
language generation the assignment says belongs to the CLIENT's model via
sampling/createMessage, not a server-side model — the server's job is to
assemble accurate facts, not to have its own opinion about how to phrase
them.
"""

from mcp_server import db
from mcp_server.protocol import JSONRPCError, ERR_NOT_FOUND
from mcp_server.tools_impl import text_result


def handle_draft_incident_summary(conn, session, ctx, arguments: dict) -> dict:
    incident_id = arguments["incident_id"]

    incident = conn.execute(
        """
        SELECT i.id, i.title, i.severity, i.status, i.created_at, i.resolved_at,
               d.id AS deployment_id, d.status AS deployment_status, d.notes AS deployment_notes,
               r.name AS repository_name, e.name AS environment_name,
               pr.title AS pull_request_title, pr.description AS pull_request_description
        FROM incidents i
        LEFT JOIN deployments d ON d.id = i.deployment_id
        LEFT JOIN repositories r ON r.id = d.repository_id
        LEFT JOIN environments e ON e.id = d.environment_id
        LEFT JOIN pull_requests pr ON pr.id = d.pull_request_id
        WHERE i.id = ?
        """,
        (incident_id,),
    ).fetchone()
    if incident is None:
        raise JSONRPCError(ERR_NOT_FOUND, f"No incident #{incident_id} found.")

    facts = {
        "incident_id": incident["id"],
        "title": incident["title"],
        "severity": incident["severity"],
        "status": incident["status"],
        "created_at": incident["created_at"],
        "resolved_at": incident["resolved_at"],
        "deployment_id": incident["deployment_id"],
        "deployment_status": incident["deployment_status"],
        "deployment_notes": incident["deployment_notes"],
        "repository_name": incident["repository_name"],
        "environment_name": incident["environment_name"],
        "pull_request_title": incident["pull_request_title"],
        "pull_request_description": incident["pull_request_description"],
    }

    sampling_result = ctx.sample(
        messages=[
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        "Draft a short, plain-language incident summary (3-5 sentences) "
                        "for a non-engineer stakeholder, using only the facts below. "
                        "Note severity and whether it's resolved.\n\nIncident facts:\n"
                        f"{facts}"
                    ),
                },
            }
        ],
        system_prompt=(
            "You are drafting incident summaries for Coderift Technologies. "
            "Be factual and concise; do not invent details not present in the facts given."
        ),
        max_tokens=400,
    )

    return text_result({
        "incident_facts": facts,
        "model_summary": sampling_result,
    })
