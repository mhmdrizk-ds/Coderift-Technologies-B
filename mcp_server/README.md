# mcp_server/ — MCP Server Core

No third-party packages are required for the stdio server or schema
validation (`jsonschema` etc. are hand-rolled against the actual spec —
see `validate.py`'s docstring for why, and how to swap in the real
`jsonschema` package later with a one-line change). `fastapi` + `uvicorn`
are only needed for `server_http.py` (Streamable HTTP transport).

## Run it

```bash
python -m mcp_server.server          # stdio transport
python -m mcp_server.server_http     # Streamable HTTP transport, :8000
```

It expects the database at `db/data/coderift.db` to already exist
(`python db/init_db.py` first if not).

## Where each protocol concern lives

| Concern | File | What to look at |
|---|---|---|
| **Capability negotiation** | `server.py` | `handle_initialize()` stores the client's declared capabilities on the session; `SERVER_CAPABILITIES` is what we declare back. `_tool_visible()` and `context.py`'s `elicit()`/`sample()` both check `session.supports(...)` before relying on a capability. |
| **Notifications** | `tools_impl/session_tools.py` + `server.py` + `notifications.py` | `authenticate` sets `session.role` from the `engineers` table (never from client input). `handle_tools_call()` in `server.py` fires `notifications.send_tools_list_changed()` right after a successful `authenticate` — including a second time on a mid-session re-authenticate that changes role. |
| **Elicitation** | `context.py: ToolContext.elicit()`, used in `tools_impl/deploy_tools.py: handle_deploy_to_production()` | The exact business rule (production + scan not Passed, OR PR not Approved) lives in the handler, sourced from `resources/production_deployment_policy.md`. |
| **Sampling** | `context.py: ToolContext.sample()`, used in `tools_impl/incident_tools.py` | |
| **Resources** | `resources.py` | `policy://production-deployment` — read once, reasoned over, not re-fetched per call. |
| **Prompts** | `prompts.py` | `draft_rollback_plan`, `draft_incident_postmortem` — parameterized, filled from live DB lookups, not static text. |
| **Transport (both)** | `server.py` (stdio) + `server_http.py` (Streamable HTTP) | Same `dispatch()` function in `server.py`, two different framings. See the git history for the stdio -> HTTP transition as separate commits. |
| **Progress tracking** | `context.py: ToolContext.report_progress()`, used in `tools_impl/checks_tools.py` | Three real sequential stages (unit tests, integration tests, security scan), each with its own `notifications/progress` push. |
| **Defensive tool design** | `schemas.py` (schema-level) + `validate.py` (schema-level enforcement) + every handler in `tools_impl/` (business-rule level) | `handle_tools_call()` runs `validate.validate()` before ever calling a handler. Each handler then does its own DB-backed checks — see especially `deploy_tools.py`. |
| **Authorization** | `auth.py` (`Session.require_role`), called at the top of every restricted handler, plus a fresh DB re-fetch in `deploy_tools.py` | Never inferred from `tools/list` — a client that calls a tool it wasn't shown still gets a clean `ERR_UNAUTHORIZED`/`ERR_UNAUTHENTICATED` error, not a crash or a silent bypass. |

## Tools at a glance

| Tool | Role required | Capability required | Write? | Notes |
|---|---|---|---|---|
| `authenticate` | none | — | no | Sets session role; triggers `tools/list_changed`. |
| `check_deployment_status` | none | — | no | The mandated fallback for a client without elicitation support. |
| `get_pull_request` | none | — | no | |
| `list_active_incidents` | any authenticated | — | no | |
| `list_feature_flags` | senior, lead | — | no | Role-gated read, distinct from the write tools. |
| `run_pre_deploy_checks` | any authenticated | — | writes a scan row | Long-running; reports progress per stage. |
| `draft_incident_summary` | any authenticated | sampling | no | Hidden from clients without sampling support. |
| `deploy_to_production` | senior, lead | elicitation | **yes** | Hidden entirely from clients without elicitation support. |
| `merge_pull_request` | senior, lead | — | **yes** | |
| `rollback_deployment` | senior, lead | — | **yes** | |

## What happens if a client connects without a needed capability

- No `elicitation` capability: `deploy_to_production` is not offered in
  `tools/list` at all — `check_deployment_status` is still there as the
  mandated read-only fallback. If a client calls `deploy_to_production`
  anyway, `ToolContext.elicit()` raises `ERR_CAPABILITY_UNSUPPORTED`
  instead of silently proceeding or silently failing (see Scenario 2 in
  `agent/scenarios.py`).
- No `sampling` capability: same treatment for `draft_incident_summary`.
