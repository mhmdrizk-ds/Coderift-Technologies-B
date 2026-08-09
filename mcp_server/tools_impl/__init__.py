"""
tools_impl/ — one module per family of tools, split by what they touch:

  session_tools.py    authenticate                              (Notifications)
  query_tools.py       check_deployment_status, get_pull_request,
                        list_active_incidents, list_feature_flags  (read-only)
  deploy_tools.py       deploy_to_production                      (Defensive design,
                                                                     Authorization,
                                                                     Elicitation call-site)
  release_tools.py      merge_pull_request, rollback_deployment    (Defensive design,
                                                                     Authorization)
  checks_tools.py       run_pre_deploy_checks                      (Progress tracking)
  incident_tools.py     draft_incident_summary                     (Sampling call-site)

Every handler has the same signature:

    handler(conn, session, ctx, arguments) -> dict

`conn` is a live sqlite3 connection, `session` is the auth.Session for this
connection, `ctx` is a context.ToolContext (elicit / sample / progress),
`arguments` is the already-schema-validated dict from tools/call.

Handlers still re-validate business rules themselves (existence,
state transitions, whether a deployment is already in progress) — the
JSON Schema only checked shape, never checked "does this repository
exist" or "is this deployment already running." That's the point of
Defensive Tool Design: schema validation and business validation are two
separate steps, and both are enforced before anything touches the
database.
"""

import json


def text_result(payload: dict) -> dict:
    """Wrap a plain dict as an MCP tools/call result content block."""
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}]}
