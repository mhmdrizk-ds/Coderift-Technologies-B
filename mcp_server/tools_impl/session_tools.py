"""
session_tools.py — authenticate.

This is the trigger for the Notifications concern. Before authenticate
succeeds, a session is anonymous: tools/list only shows the public
read-only tools + authenticate itself. The moment authenticate succeeds,
the session's role is set server-side (never trusted from client input —
see auth.Session.login, which reads the role out of the `engineers` table
by access_code, not out of anything the client asserted), and the server
pushes notifications/tools/list_changed so the client knows to re-fetch
tools/list without polling or reconnecting. The actual push call lives in
server.py right after this handler returns, so server.py stays the single
place that owns "when do we tell the client the tool set changed."

Re-authenticating mid-session (a different access_code on an already-
open connection) is allowed and is exactly how the demo shows a role
promotion with no reconnect: a junior engineer logs in, sees read-only
tools, then a senior engineer authenticates on the SAME connection and
write tools appear live.
"""

from mcp_server import db
from mcp_server.protocol import JSONRPCError, ERR_NOT_FOUND
from mcp_server.tools_impl import text_result


def handle_authenticate(conn, session, ctx, arguments: dict) -> dict:
    access_code = arguments["access_code"]

    engineer = db.get_engineer_by_access_code(conn, access_code)
    if engineer is None:
        raise JSONRPCError(ERR_NOT_FOUND, f"No engineer found with access_code '{access_code}'.")
    if not engineer["active"]:
        raise JSONRPCError(ERR_NOT_FOUND, f"Access code '{access_code}' belongs to an inactive engineer.")

    session.login(engineer)

    return text_result({
        "authenticated": True,
        "name": session.name,
        "role": session.role,
        "note": "Tool set updated for this role — tools/list_changed sent.",
    })
