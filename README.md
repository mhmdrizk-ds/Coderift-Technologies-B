# Coderift Technologies — MCP Server Lab

## The company and the problem

Coderift Technologies is a software company. Internally, engineers deploy
code to production, manage feature flags, and handle security
vulnerability patches through an internal CLI/dashboard with full,
unscoped database access. There is no LLM-safe layer in front of any of
this.

The real risk: an engineer — or an LLM agent acting on an engineer's
behalf with the same unscoped access — could push straight to production,
skip a required security review, or roll back a deployment other
engineers depend on, with no audit trail and no human-in-the-loop check
on the riskiest actions. The seeded database already contains one
consequence of exactly this kind of gap: `billing-worker`'s production
deployment #1 failed after shipping an unreviewed caching change, and
produced an active, unresolved **critical** incident (`incidents` #1).

The fix built here: a real database with an ERD (`db/`), and an MCP
server (`mcp_server/`) sitting in front of it that a model talks to
instead of the database directly — enforcing role-based tool visibility,
mandatory human sign-off on the riskiest action, and scoped, validated
writes.

## Database / ERD

See `db/schema.sql` for the full DDL and `db/ERD.mmd` for the Mermaid
source. Eight tables: `engineers`, `repositories`, `pull_requests`,
`environments`, `deployments`, `security_scans`, `feature_flags`,
`incidents`. `db/seed.sql` includes the required edge cases: a PR with a
**Failed** security scan, a PR with a **Pending** scan, a **Failed**
deployment that produced an **open, critical** incident, an engineer at
every role level, and one **inactive** engineer (revoked access).

```mermaid
erDiagram
    ENGINEERS ||--o{ PULL_REQUESTS : "authors"
    ENGINEERS ||--o{ DEPLOYMENTS : "deploys"
    REPOSITORIES ||--o{ PULL_REQUESTS : "has"
    REPOSITORIES ||--o{ ENVIRONMENTS : "has"
    REPOSITORIES ||--o{ DEPLOYMENTS : "deployed for"
    ENVIRONMENTS ||--o{ DEPLOYMENTS : "target of"
    PULL_REQUESTS ||--o{ DEPLOYMENTS : "ships"
    PULL_REQUESTS ||--o{ SECURITY_SCANS : "scanned by"
    REPOSITORIES ||--o{ FEATURE_FLAGS : "defines"
    ENVIRONMENTS ||--o{ FEATURE_FLAGS : "scoped to"
    DEPLOYMENTS ||--o{ INCIDENTS : "may cause"
```

(Full attribute lists are in `db/ERD.mmd`; this block is a summary for
quick orientation.)

## Setup

```bash
pip install -r requirements.txt      # only needed for the HTTP transport
python db/init_db.py                 # builds db/coderift.db from schema+seed
python db/apply_migration.py         # adds the final-project tables (checkpoints, hitl_tasks, tickets, ...)
python -m agent.client --all         # runs all 10 demo scenarios, stdio transport
```

**Note:** `agent.client --all` rebuilds `db/coderift.db` from scratch
before running (`rebuild_database()` in `agent/client.py`, "so every
full demo run starts from the same fixed seed"), and that rebuild only
runs `init_db.py` — it does not re-apply migrations. If you run any
final-project demo (`demo/incident_response_demo.py`,
`demo/crash_resume_demo.py`, the admin/user platforms, etc.) after
running `agent.client --all`, re-run `python db/apply_migration.py`
first, or you'll hit `sqlite3.OperationalError: no such table:
checkpoints`. This is a real, reproducible gap between the original
MCP-lab demo runner and the final-project schema — flagged here rather
than silently worked around, since fixing `rebuild_database()` itself
belongs to whoever owns `agent/client.py`'s scope.

`python -m mcp_server.server_http --port 8000` runs the Streamable HTTP
transport separately (see `mcp_server/server_http.py` and
`db/README.md` / `mcp_server/README.md` for details).

For Docker: `docker-compose up --build` starts all three services
(`mcp_server`, `admin_platform`, `user_platform`) behind a one-shot
`db-init` step that builds and migrates the database exactly once on
the shared volume before anything else starts. Once the stack is up,
`python scripts/docker_integration_test.py` drives a full check against
the live containers — MCP protocol round trip, all five agents reachable
through the user platform, a complete `flag_rollout` run through HITL
over real HTTP, a real RAG answer through the platform, and the
`planning_toolkit` env-var regression re-verified specifically inside
the running container via `docker-compose exec`. See that script's
module docstring for exactly what each check does and does not prove;
it currently reports one expected failure (flag_rollout's simulated vs.
real MCP client gap, documented above) by design, not by accident.

## The 9 protocol concerns, tied to a specific tool/trigger

1. **Capability negotiation.** `server.py: handle_initialize()` stores
   whatever capabilities the client declares; `SERVER_CAPABILITIES`
   promises `tools.listChanged: true`. `_tool_visible()` hides
   `deploy_to_production` and `draft_incident_summary` entirely from a
   client that never declared `elicitation`/`sampling` — demoed in both
   directions by `agent/scenarios.py`'s scenario 1 (full client) and
   scenario 2 (read-only client, using `check_deployment_status` as the
   mandated fallback, and getting a clean `ERR_CAPABILITY_UNSUPPORTED` if
   it calls `deploy_to_production` anyway).

2. **Notifications.** An engineer's role isn't fixed for the life of a
   connection — `authenticate` can be called again mid-session with a
   different access code (no reconnect), and each successful call fires
   `notifications/tools/list_changed`. Scenario 4 authenticates as junior,
   then promotes to senior on the same connection, and shows the tool set
   changing live both times.

3. **Elicitation.** `deploy_to_production` (`tools_impl/deploy_tools.py`)
   pauses via `elicitation/create` when either the target is production
   with a scan that isn't `Passed`, or the PR hasn't been through review
   (`status != Approved`) — the exact rule from
   `resources/production_deployment_policy.md` §3. Scenario 5 shows the
   clean/no-elicitation path; scenarios 6 and 7 show both outcomes of the
   pause (accepted, then declined).

4. **Sampling.** `draft_incident_summary`
   (`tools_impl/incident_tools.py`) assembles an incident's linked
   deployment and PR context, then asks the CLIENT's model via
   `sampling/createMessage` to draft a plain-language summary — the
   server never runs its own model. Demoed in scenario 9.

5. **Resources.** The Production Deployment Policy is exposed via
   `resources/list`/`resources/read`
   (`resources/production_deployment_policy.md`, wired in
   `mcp_server/resources.py`) — read once, reasoned over, not re-fetched
   as a function call on every deploy decision.

6. **Prompts.** Two parameterized templates via `prompts/list`/
   `prompts/get` (`mcp_server/prompts.py`): `draft_rollback_plan(deployment_id)`
   and `draft_incident_postmortem(incident_id)`, both filled from live DB
   lookups so two different ids produce two different, factually-grounded
   prompts.

7. **Transport (both).** `mcp_server/server.py` (stdio) was built and
   tested first; `mcp_server/server_http.py` (Streamable HTTP) was added
   afterward, reusing the same `dispatch()` function, because multiple
   Coderift engineers need concurrent remote access to one server process
   — a stdio server is inherently single-client, subprocessed by exactly
   one caller. See the git log for these as separate, real commits.

8. **Progress tracking.** `run_pre_deploy_checks`
   (`tools_impl/checks_tools.py`) runs unit tests, then integration
   tests, then a fresh security scan, sequentially, reporting real
   intermediate progress via `ctx.report_progress()` at each stage rather
   than one blocking response. Demoed in scenario 8.

9. **Defensive tool design.** `deploy_to_production`'s schema
   (`mcp_server/schemas.py`) is fully typed with `required` and
   `additionalProperties: false`. Its handler
   (`tools_impl/deploy_tools.py`) independently verifies the named
   environment actually belongs to the named repository, that the PR
   belongs to that repository, and that no deployment is already
   Pending/InProgress for that repo+environment — none of which a JSON
   Schema can express. Authorization is re-checked in the handler against
   a fresh database fetch of the engineer's role, not inferred from the
   schema or from what `tools/list` happened to show. Scenario 3 exercises
   the negative cases (missing field, disallowed extra field,
   unauthenticated write, unauthorized junior write, inactive access
   code); scenario 10 exercises a second flavor of the same discipline on
   `merge_pull_request`/`rollback_deployment`.

## Tools: read-only vs. write, and capability requirements

| Tool | Read/Write | Role required | Capability required | If capability missing |
|---|---|---|---|---|
| `authenticate` | — | none | — | always available |
| `check_deployment_status` | read | none | — | always available (the mandated fallback) |
| `get_pull_request` | read | none | — | always available |
| `list_active_incidents` | read | any authenticated | — | always available once authenticated |
| `list_feature_flags` | read | senior, lead | — | hidden below senior |
| `run_pre_deploy_checks` | write (scan row) | any authenticated | — | always available once authenticated |
| `draft_incident_summary` | read | any authenticated | sampling | **hidden from `tools/list`**; direct call raises `ERR_CAPABILITY_UNSUPPORTED` |
| `deploy_to_production` | write | senior, lead | elicitation | **hidden from `tools/list`**; direct call raises `ERR_CAPABILITY_UNSUPPORTED` |
| `merge_pull_request` | write | senior, lead | — | hidden below senior |
| `rollback_deployment` | write | senior, lead | — | hidden below senior |

Why `deploy_to_production` specifically needs `elicitation`: it's the one
tool that can silently ship an unreviewed or security-failing change to
production if nothing stops to ask a human. Every other write tool
(`merge_pull_request`, `rollback_deployment`) only requires a role — their
own defensive validation (PR must be Approved + Passed to merge; a
deployment must be Succeeded/InProgress to roll back) is enough to make
them safe without a human pause.

## Memory & RAG (Session 3 extension)

Two real problems emerge once engineers actually use this agent across
shifts, beyond what the MCP server's tools alone can fix:

**Problem 1 — Memory.** Engineers lose all session context when a
conversation ends. If Engineer A's session establishes that
`billing-worker` has had 3 consecutive failed deployments and an active
critical incident, Engineer B starting a fresh session has no memory of
this — the agent treats everything as brand new. `memory/` fixes this: a
rolling short-term buffer hands evicted messages to a promote-or-drop
router (`memory/router.py`, with zero structural access to semantic
memory), promoted messages become episodes, and a periodic — never
write-time — consolidation pass turns episodes into versioned, expirable
semantic facts (`memory/consolidation.py`, `memory/semantic_store.py`).
Semantic facts persist to disk (`memory/data/semantic_facts.json`) so a
**new `MemorySystem()` instance in a completely separate process** — a
different engineer's session — loads them on construction.
`demo/cross_session_memory_demo.py` proves this end to end: it runs two
genuinely separate sessions and confirms Engineer B's brand-new session
correctly refuses to treat `billing-worker` as safe to deploy to, without
ever having lived through Engineer A's conversation.

**Problem 2 — Knowledge.** Engineers ask questions the database was never
built to answer — "what's the required approval chain for a hotfix
deployment during an active incident?" — that only live in the company's
internal policy documents. `rag/` fixes this: three expanded policy
documents (`resources/production_deployment_policy.md`,
`security_review_policy.md`, `incident_response_runbook.md`, 40+
statements each) are chunked, embedded, and indexed in Chroma with
metadata filtering applied **during** HNSW search, not after. Three
retrieval architectures are implemented and evaluated against the same
12-question set (see `retrieval_eval/README.md` for the full comparison):
naive (baseline), hybrid (vector + BM25 via Reciprocal Rank Fusion — wins
on exact-identifier questions like "what does Section 4.2 say?"), and
agentic (a retrieve-observe-decide loop that combines multiple policies —
wins on multi-part questions naive/hybrid can only partially answer). A
bonus NetworkX-based Graph RAG (`rag/graph_rag.py`) is also implemented and
evaluated honestly against the same set — it does not beat agentic RAG on
this evaluation, and `rag/GRAPH_RAG_README.md` explains why rather than
hiding the result.

Every RAG answer and every memory recall passes through Self-RAG
verification (`rag/self_rag.py`) before being trusted — `check_relevance()`
and `check_support()`, each with a live-model path and a deterministic
offline fallback — so a recalled fact or a generated answer is never
presented with more confidence than the evidence behind it supports.

Context window management is a related but separate concern: a real
Coderift session involves dozens of tool calls, and an early critical
detail can get buried under tool JSON noise before the final question is
asked. `context_eval/` benchmarks four pruning strategies against 11
transcripts (40-turn base + 10 variations spanning different lengths and
critical-detail positions) and finds `observation_masking` wins — 100%
accuracy, fastest of the two 100%-accuracy strategies — because it targets
Coderift's actual bloat source (tool output, not dialogue). Full
justification and real numbers in `context_eval/README.md`.

```bash
# Build the vector store (once, or after editing a policy doc)
python3 rag/vector_store/vector_db.py

# Run the memory + RAG test suite
python3 -m pytest memory/tests/ -q

# Run the flagship cross-session memory demo
python3 demo/cross_session_memory_demo.py

# Run the context-window-management benchmark
cd context_eval && python3 benchmark.py

# Run the retrieval architecture evaluation
cd retrieval_eval && python3 run_eval.py

# Run the full agent demo including RAG + memory scenarios (12 total)
python3 -m agent.client --all
```

## Decomposition & Planning (Week 4 extension)

**The problem.** Two real requests neither the memory/RAG agent nor a
single MCP tool call can safely resolve on their own:

1. *"Prepare repository X for a production release."* Which candidate PRs
   are actually ready depends on scan status, an open-incident check, and
   sometimes a genuinely ambiguous case (a human-Approved PR whose
   security scan is still `Pending`, not `Failed` and not `Passed` — no
   single correct answer, only defensible strategies).
2. *"An incident is open on deployment X — what do we actually do?"*
   Proposing a concrete remediation action (rollback vs. redeploy) is
   real database state, not prose — a wrong guess is expensive to unwind,
   and there's real external ground truth (the deployment's actual
   status, whether an incident is genuinely open) to check the proposal
   against before it ships.

**The agent.** `planning_toolkit/` — the Release Readiness & Incident
Remediation Planning Agent, `planning_lab/agent.py` as its single routing
entry point — sits next to `memory/`/`rag/`, reuses the same
`mcp_server/`/`db/` everything else here uses, and never touches the
memory/RAG agent's code path. It implements, against real data, both
required decomposition methods (decomposition-first, dynamic/interleaved
— acyclicity enforced, a real divergence case), all three planning
algorithms routed by sub-task shape (Plan-and-Solve, Tree of Thoughts,
LATS), both self-correction scopes (Self-Refine, Reflexion), and a real
grounded `Environment` (plus a deliberately fake one kept only for the
required grounded-vs-ungrounded contrast).

See `planning_toolkit/README.md` for the full writeup, `planning_eval/`
for the complete cost & quality comparison table across every method
against a fixed real-request test suite, and
`planning_eval/DEMO_TRANSCRIPT.md` for a walkthrough of every required
demo element with real captured output.

```bash
python3 db/init_db.py
python3 -m planning_toolkit.compare_divergence
python3 -m planning_eval.run_eval
python3 -m pytest planning_toolkit/tests/ -v
```

## Feature Flag Rollout & Rollback Governance (Final Project — Person C)

Three real problems this graph solves, matching the same "real wait / real
branch / real failure" shape as Person A's Incident Response graph:

1. **Real wait.** A rollout percentage change doesn't show its true blast
   radius instantly — error-rate metrics need an observation window at
   the new traffic level. `awaiting_metrics` uses the same `WAIT_KEY`
   pattern as `awaiting_verification`: the graph genuinely pauses and
   waits for an external `metrics_result` event, it does not poll in a
   loop.
2. **Real branch.** Any step that reaches or crosses a **named
   blast-radius threshold of 50%** (`state_graph/rollout_lats.py:
   BLAST_RADIUS_THRESHOLD_PCT`) requires a human's sign-off before that
   percentage is actually set — `full_production_rollout` raises
   `Interrupt` with the flag name, repo, current %, target %, and the
   threshold itself in the payload, the same shape `hitl_lead_signoff`
   uses. Crucially, approval does **not** skip straight to 100%: it
   advances exactly one step through the normal `increase_pct -> canary`
   path, so the threshold-crossing percentage is actually canaried and
   metrics-checked like every other step.
3. **Real failure.** A flag-toggle tool call (`set_flag_percentage`) can
   fail for a real infrastructure reason (timeout, malformed response).
   `canary`/`auto_rollback` never catch this themselves —
   `FlagToggleAdapter` raises `NodeFailure` with `error_code =
   "FLAG_TOGGLE_TOOL_ERROR"`, which the shared `StateGraph.resume()` loop
   turns into a ticket, exactly like `deploy_fix`. `tests/
   test_flag_rollout.py::test_flag_toggle_tool_failure_opens_ticket_distinct_from_hitl`
   asserts `hitl_store.list_pending()` stays empty for that run.

### LATS: real numbers, not a single example

`state_graph/rollout_lats.py` searches three canonical rollout-percentage
orderings — `aggressive` `[5,25,50,100]`, `standard` `[10,30,60,100]`,
`conservative` `[1,5,15,40,100]` — scored by `score_sequence()`, a
deterministic function with two real, independently-computed terms:

- `jump_penalty`: sum of `(step_size/100)^2 x (1 + 20 x baseline_error_rate)`
  over consecutive steps — squaring means one big jump costs more than
  the same distance spread over several smaller jumps, and the repo's
  real DB-derived `baseline_error_rate` (from
  `mcp_server/db.py:get_historical_baseline_error_rate`, itself computed
  from actual high/critical incident counts against that repository —
  never a guess) amplifies every jump for a repo with a rockier incident
  history.
- `threshold_overshoot_penalty`: +0.15 for any single step that crosses
  the 50% blast-radius line in one jump of more than 30 percentage
  points (e.g. `conservative`'s final `40 -> 100` step) — independent of
  the raw jump-size penalty above.

Real numbers across three repositories in the seeded database:

| Repository | baseline_error_rate | aggressive | standard | conservative | Selected |
|---|---|---|---|---|---|
| `payments-service` | 0.01 | 0.426 | **0.36** | 0.671 | standard |
| `billing-worker` | 0.025 (1 critical incident on record) | 0.5325 | **0.45** | 0.8013 | standard |
| `checkout-web` | 0.01 | 0.426 | **0.36** | 0.671 | standard |

`standard` wins in all three seeded cases here, but the margin narrows as
`baseline_error_rate` rises — a repo with a worse incident history
doesn't flip the ranking on this particular candidate set, but it does
compress the gap, which is the real, checkable effect of the DB-grounded
penalty term. This is a one-level tree (root + one child per named
candidate), not an open-ended search — rollout-percentage orderings have
a small real catalog, unlike Person A's Task 2 LATS over open-ended
remediation actions.

### Constrained ReAct: the flag-toggle whitelist

`state_graph/flag_toggle_adapter.py`'s `ALLOWED_TOOLS = {"set_flag_percentage",
"get_error_rate_metrics"}` is the only surface `canary`
and `auto_rollback` can reach — enforced structurally in
`FlagToggleAdapter._call()`, which raises `NodeFailure` with
`error_code="FLAG_TOGGLE_TOOL_NOT_WHITELISTED"` for anything outside the
set. This matters because an unconstrained ReAct loop here could toggle
production traffic percentages in ways the graph never modeled.

### `mcp_server/` — no changes to gating

This branch adds two new tools (`set_flag_percentage`,
`get_error_rate_metrics`) and their `HANDLERS` entries only. The
per-agent tool gating in `server.py` (`_tool_visible`, `handle_tools_call`,
`TOOL_REGISTRY`, keyed off `session.agent_id`) — already built and
tested by Person A in `tests/test_tool_registry_enforcement.py` — is
untouched by this branch.

## Security Remediation & Admin Platform (Final Project — Person B)

Three real problems this graph solves, matching the same "real wait / real
branch / real failure" shape as Person A's Incident Response graph and
Person C's Feature Flag Rollout graph:

1. **Real wait.** After a patch brings a PR's security scan back to
   `Passed`, code review is a genuine multi-turn wait — a human reviewer
   may take days. `awaiting_code_review` uses the same `WAIT_KEY` pattern
   as `awaiting_verification` / `awaiting_metrics`: the graph pauses for a
   real external `review_result` event instead of polling.
2. **Real branch.** Per `resources/security_review_policy.md` §4.1, only a
   `lead`-role engineer may authorize deploying a PR whose scan is still
   `Failed` after a patch attempt — the agent is never allowed to decide
   this alone. `hitl_lead_signoff` raises `Interrupt` with the PR id, scan
   status, attempt number, and the selected remediation strategy in the
   payload. A rejected review and a rejected override are both real
   cycles back to `propose_remediation` for a *different* strategy
   (`attempt_number` increments, `previous_selected_id` is carried so Tree
   of Thoughts deprioritizes whatever already failed) — not a fresh run.
3. **Real failure.** `run_pre_deploy_checks` interrupted mid-run is a
   named failure mode under policy §6.2 ("may leave the `security_scans`
   table in an inconsistent state") that a silent retry cannot safely
   paper over. `patch_pr` never catches this itself — `McpAdapter` raises
   `NodeFailure` with `error_code = "PRE_DEPLOY_CHECKS_TOOL_ERROR"`, which
   `StateGraph.resume()` turns into a ticket, the same shape
   `deploy_fix`'s and `canary`'s tool failures use.
   `tests/test_security_remediation.py::test_pre_deploy_checks_tool_failure_opens_ticket_distinct_from_hitl`
   asserts `hitl_store.list_pending()` stays empty for that run.

```
scan_flag -> propose_remediation -> patch_pr -> [conditional on the refreshed scan]
    Passed -> awaiting_code_review -> [conditional on reviewer]
        approved -> deploy_patch -> resolved
        rejected -> propose_remediation                  (real cycle #1)
    Failed -> hitl_lead_signoff -> [conditional on the lead's decision]
        approved -> deploy_patch_override -> resolved
        rejected -> propose_remediation                  (real cycle #2)
```

### Tree of Thoughts: scoring more than one remediation strategy

`state_graph/remediation_strategy.py::select_remediation_strategy`, called
from `propose_remediation`, scores at least three candidate responses to a
`Failed` scan per policy §7.3–7.4 (upgrade the dependency / patch in
place / add a compensating control) before one is picked — a wrong first
guess wastes a real fix window, so the candidates and the selection
reasoning are both carried in graph state (`strategy_candidates`,
`selected_strategy_id`, `selection_reasoning`) rather than discarded once
a choice is made.

### Constrained ReAct: the per-node tool whitelist

`state_graph/security_remediation.py::ALLOWED_TOOLS_BY_NODE` restricts
which MCP tools each node may call — `patch_pr` may only call
`run_pre_deploy_checks`; `deploy_patch` may only call
`record_review_approval` and `merge_pull_request`; `deploy_patch_override`
may only call `deploy_to_production_override`. `_call_whitelisted_tool`
enforces this in code, raising `ConstrainedToolViolation` (a distinct
`NodeFailure` subtype) before the adapter is ever invoked if a node
attempts a tool outside its allowlist — a real production merge/deploy
action is too costly to leave to an unconstrained tool call.
`tests/test_security_remediation.py::test_constrained_react_blocks_non_whitelisted_tool_call`
proves the rejected call never reaches the adapter.

### `mcp_server/` — the one new tool this branch required

`merge_pull_request` only ever *read* `pull_requests.status == 'Approved'`;
nothing could write it. `record_review_approval`
(`mcp_server/tools_impl/release_tools.py`) is the write side of that
check — `senior`/`lead` only, sets `status = 'Approved'` and stamps
`reviewer_id`, registered in `schemas.py` and `server.py` alongside the
other release tools. No other tool gating changed.

### Ticket & HITL admin UI (shared across all three graphs)

`admin_platform/` exposes `/api/tickets`, `/api/hitl-tasks`, and
`/api/checkpoints` — list, filter, inspect, and resolve/decide — backed by
the same `TicketStore`/`HitlStore`/`CheckpointStore` classes
`incident_response.py`, `security_remediation.py`, and `flag_rollout.py`
all share. Resolving a ticket or deciding a HITL task through this UI
resumes the exact graph run from its last checkpoint; nothing is
re-executed.

### RAG document management (admin platform)

`admin_platform/` also exposes add/remove for the RAG corpus
(`resources/*.md` — the production deployment, security review, and
incident response policy documents indexed by the Memory & RAG extension
described earlier in this README). Adding or removing a document triggers a synchronous reindex, so
the very next `naive_rag` query reflects the change — there is no stale
window between an admin edit and the agent seeing it.

### `.env` / secrets guardrail

No `.env` file has ever been committed (checked across the full git
history, not just the working tree); `.env.example` documents every
variable name with a placeholder value only. `admin_platform/` and
`user_platform/` were both grepped for hardcoded keys — none found.



## `mcp_server/` audit (Final Project — Person C)

The final-project brief calls this "load-bearing infrastructure — no
partial credit for new work sitting next to a broken server," so this is
a fresh audit run directly against a live server (`server_http.py` on a
loopback port), not a re-statement of the original MCP Server Lab
feedback. Every finding below was reproduced with a real JSON-RPC call
over real HTTP, shown alongside what was checked so it can be re-run.

**Capability negotiation, role gating, and runtime tool
register/de-register all work correctly end-to-end over HTTP.** Checked
by declaring `elicitation`+`sampling` in `initialize`, authenticating as
a senior engineer, and calling `tools/list` in the same batched request —
all 12 role/capability-appropriate tools appeared, including
`deploy_to_production` and `draft_incident_summary`. Runtime
de-registration was checked by disabling `rollback_deployment` for one
agent via `ToolRegistry.deregister()` and confirming, over the same live
server, that the tool both disappeared from that agent's `tools/list`
*and* a direct `tools/call` for it was rejected with a clean error — not
silently allowed through. This matches what
`tests/test_tool_registry_enforcement.py` already covers (6/6 passing);
this audit re-confirmed it live rather than trusting the tests alone.

**Authorization is re-checked in the handler against a fresh DB read, not
just at login.** Checked by authenticating a session as a senior
engineer, then flipping that engineer's `active` flag to 0 directly in
the database mid-session (simulating an admin revoking access without
the client's session object knowing), then calling
`deploy_to_production` on the same session. The call was correctly
rejected — the handler re-fetches the engineer record rather than
trusting the cached session role — confirming this isn't a login-time-only
check.

**Elicitation gating on `deploy_to_production` is correctly conditional
and correctly enforced when it does apply.** A clean, already-approved,
already-passing-scan deploy to staging succeeds with no elicitation
required, exactly as designed — that's not a gap, it's the intended
"skip the human step when nothing's actually risky" path. Deploying a PR
with a pending security scan to *production* (which does need
confirmation) with no `elicitation` capability declared correctly
returns a clean `-32005` `ERR_CAPABILITY_UNSUPPORTED` error rather than
either crashing or silently deploying anyway.

**Finding: `notifications/progress` and `notifications/tools/list_changed`
are not actually delivered to the client over the HTTP transport.**
`protocol.send_message()` (used by both `ToolContext.report_progress()`
and `notifications.send_tools_list_changed()`) writes unconditionally to
`sys.stdout` — correct for the stdio transport, where stdout *is* the
client's input stream, but over HTTP that's the server process's own
log, not the HTTP response. Reproduced by calling `run_pre_deploy_checks`
(which fires 3 progress notifications) with a `progressToken` over HTTP:
the final tool result came back correctly in the HTTP response, but all
three progress notifications, plus the `tools/list_changed` notification
from the preceding `authenticate` call, only ever appeared in the
server's own stdout log — never reached the client. This is a real gap
in the Notifications / Progress Tracking protocol concerns specifically
for the HTTP transport; both work correctly over stdio, which is what
`agent/client.py`'s demo scenarios exercise, so the existing 12/12 clean
demo run doesn't surface it. `server_http.py`'s docstring already flags
the same root cause for elicitation/sampling (per-request sessions can't
hold a blocking round-trip open); this is the same limitation showing up
for the two fire-and-forget notification types instead. Not fixed as
part of this pass — fixing it properly means giving the HTTP transport a
way to stream or queue notifications per-session (e.g. an SSE channel or
an in-memory per-session outbox the client polls), which is a transport
design change, not a one-line patch, so it's recorded here rather than
patched hastily.

**Conclusion:** the server's actual gating logic — capability checks,
role checks, tool registry enforcement, handler-level re-authorization —
is sound and verified live. The one real gap found is scoped and
specific (notification delivery over HTTP only), not a symptom of
broken core mechanics.

## Repository layout

```
db/               schema.sql, seed.sql, ERD.mmd, init_db.py, README.md
mcp_server/       server code — see mcp_server/README.md for the concern-by-concern index
resources/        Policy documents (RAG corpus) — production deployment, security review, incident response
prompts/          draft_rollback_plan / draft_incident_postmortem (prompt templates)
agent/            demo client (agent/README.md) + planning_client.py (planning agent CLI)
memory/           short-term buffer, router, episodic/semantic stores, consolidation, scheduler — memory/api.py is the only import surface — see docs/rag_memory_audit.md
rag/              naive/hybrid/agentic/graph RAG, Self-RAG, vector store — see rag/README.md, docs/rag_memory_audit.md
admin_platform/   ticket/HITL admin UI + RAG document management + tool registration (Person B/C) — /api/tickets, /api/hitl-tasks, /api/checkpoints, /api/rag-docs
context_eval/     context-window pruning strategy benchmark — see context_eval/README.md
retrieval_eval/   RAG architecture comparison — see retrieval_eval/README.md
demo/             DEMO_TRANSCRIPT.md (MCP lab, all 9 concerns) + cross_session_memory_demo.py (Session 3 flagship demo)
planning_toolkit/ Release Readiness & Incident Remediation Planning Agent — decomposition, PS/ToT/LATS, Self-Refine/Reflexion — see planning_toolkit/README.md
planning_eval/    Full cost/quality comparison table + fixed test suite + demo transcript for the planning agent
state_graph/      shared engine + incident_response.py + security_remediation.py + flag_rollout.py, flag_toggle_adapter.py, rollout_lats.py
user_platform/    the User Platform (project brief 2.3) — FastAPI backend + static chat UI, switches between all five live agents
```