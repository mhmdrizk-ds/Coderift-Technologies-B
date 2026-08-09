"""
capabilities.py — what the Coderift agent declares to the server during
initialize.

This is one half of the Capability Negotiation concern (the server-side
half — SERVER_CAPABILITIES and session.supports() — lives in
mcp_server/server.py and mcp_server/auth.py). Two profiles are defined
here to demo both required paths:

  * FULL_CAPABILITIES — a client that actually implements elicitation and
    sampling (see elicitation.py, sampling.py), so it declares both.
    Declaring a capability you don't back up would defeat the entire
    point of negotiation.
  * READ_ONLY_CAPABILITIES — a client that implements neither. Connecting
    with this profile means deploy_to_production and draft_incident_summary
    never even appear in tools/list (see server.py's _tool_visible()); the
    client is expected to use check_deployment_status instead, per the
    assignment's own worked example.

session.py checks what the SERVER declared back (e.g.
capabilities.tools.listChanged) before the agent ever relies on
notifications actually arriving, instead of just assuming it.
"""

PROTOCOL_VERSION = "2025-06-18"

FULL_CLIENT_INFO = {"name": "coderift-agent-full", "version": "1.0.0"}
READ_ONLY_CLIENT_INFO = {"name": "coderift-agent-read-only", "version": "1.0.0"}

FULL_CAPABILITIES = {
    "elicitation": {},
    "sampling": {},
}

READ_ONLY_CAPABILITIES = {}
