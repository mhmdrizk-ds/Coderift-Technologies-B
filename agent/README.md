# agent/ — Coderift MCP Agent

The demo client. Subprocesses `mcp_server/server.py` (stdio) and drives it
through 10 scenarios covering all 9 protocol concerns.

## Run it

```bash
python db/init_db.py           # first time, or to reset the demo state
python -m agent.client --list
python -m agent.client --all               # rebuilds the DB, runs everything in order
python -m agent.client --scenario uncontrolled_deploy
python -m agent.client --all --interactive # answer elicitation prompts live instead of scripted
```

## Files

- `mcp_client.py` — raw JSON-RPC framing over the server subprocess's
  stdin/stdout, plus the multiplexing loop that lets the SERVER call back
  into the client mid-request (`elicitation/create`,
  `sampling/createMessage`) while a `tools/call` is still in flight.
- `capabilities.py` — two client capability profiles: `FULL_CAPABILITIES`
  (declares elicitation + sampling, used by 9 of the 10 scenarios) and
  `READ_ONLY_CAPABILITIES` (declares neither — used by
  `capability_negotiation_read_only` to demo the mandated fallback path).
- `session.py` — `CoderiftAgentSession`, a thin wrapper that does the
  `initialize`/`initialized` handshake, checks the server's declared
  capabilities before relying on them, and caches `tools/list` until a
  `tools/list_changed` notification invalidates it.
- `elicitation.py` — the client-side answer to `elicitation/create`: an
  interactive terminal prompt, or a scripted fixed answer per scenario
  (see `test_inputs.json`'s `elicitation_response`) for repeatable
  automated runs.
- `sampling.py` — the client-side answer to `sampling/createMessage`: a
  live Google Gemini call if an API key is configured, otherwise a
  deterministic offline rule engine built from the same facts the server
  assembled, so the demo is repeatable without any external dependency.
- `progress.py` — renders `notifications/progress` as a text progress bar.
- `scenarios.py` — the 10 demo scenarios (see below).
- `test_inputs.json` — every fixed input the scenarios use, keyed by
  scenario name, so the demo is repeatable and doesn't rely on lucky
  random data.
- `client.py` — CLI entry point; `--all` rebuilds the database from
  `db/schema.sql` + `db/seed.sql` first so every run starts from the same
  state.

## Scenarios, in run order

| # | Scenario | Concern(s) demonstrated |
|---|---|---|
| 1 | `capability_negotiation_full` | Capability negotiation, resources, prompts |
| 2 | `capability_negotiation_read_only` | Capability negotiation — the mandated "client without elicitation" fallback path |
| 3 | `defensive_and_authorization` | Defensive tool design, authorization |
| 4 | `notifications_on_promotion` | Notifications — role promoted twice, no reconnect |
| 5 | `uncontrolled_deploy` | Elicitation rule NOT triggered (clean deploy) |
| 6 | `controlled_deploy_scan_not_passed` | Elicitation rule (a), accepted |
| 7 | `controlled_deploy_unreviewed_declined` | Elicitation rule (b), declined |
| 8 | `progress_pre_deploy_checks` | Progress tracking |
| 9 | `sampling_incident_summary` | Sampling |
| 10 | `merge_and_rollback` | Defensive tool design (second flavor) |
| 11 | `rag_policy_questions` | RAG (naive/hybrid/agentic) + Self-RAG verification |
| 12 | `memory_recall_in_session` | Memory (buffer -> router -> episodic -> semantic, in one session) |

Every scenario in this table uses the `full` capability profile except
#2, which is deliberately built with `read_only` (see
`READ_ONLY_SCENARIOS` in `scenarios.py`) to prove the "client without
elicitation support" path actually works, not just the happy path.

Scenario 12 uses a small `memory_buffer_capacity=3` (see `client.py`'s
`build_session()`) so its handful of tool calls actually overflow the
buffer and reach the router within one short scenario — a real session
uses the default capacity of 50 and overflows naturally over dozens of
turns. Scenario 12 demonstrates the pipeline *within* one session;
**cross-session persistence** (a completely separate process picking up
what a prior session consolidated) is demonstrated separately by
`demo/cross_session_memory_demo.py`, which is the flagship proof this lab
extension exists to deliver — see the top-level README's Memory & RAG
section.
