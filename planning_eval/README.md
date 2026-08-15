# planning_eval/ — the full cost & quality comparison

Run: `python3 -m planning_eval.run_eval` (needs `python3 db/init_db.py` run
at least once first). Reads the fixed suite in `test_suite.json`, runs every
required method against every applicable case, prints the table, writes it
to `comparison_table.md`, and writes one JSON trace per run to `artifacts/`
(same trace format `planning_toolkit/artifacts/` already uses).

**These are OFFLINE numbers** (no `GOOGLE_API_KEY`/`GEMINI_API_KEY` set in
this environment — every algorithm module's documented offline-fallback
path is what actually ran). That is disclosed here on purpose rather than
smoothed over, and it matters for reading the table correctly:

- **Success/failure columns are exactly as meaningful offline as they'd be
  online** — they're driven by real `db/coderift.db` state and the real
  grounded `Environment`, not by whether a live model was called.
- **Calls/tokens/latency are NOT representative of live-model economics.**
  The offline fallback echoes its own prompt back as a "response" (see
  `model_provider.py`), and several of this repo's steps feed one step's
  output into the next step's prompt — so offline token counts *grow*
  with pipeline depth in a way a real model's actual reasoning wouldn't.
  Rerun with a `GOOGLE_API_KEY` set for real token/latency numbers; the
  *relative shape* of the table (which method costs more calls than which)
  still holds either way, which is what the per-sub-task method choices
  below are actually justified against.

## Reading the table

### Decomposition-first vs. dynamic decomposition
Both succeed on all 3 cases. Offline, dynamic decomposition shows *more*
calls than decomposition-first here — the opposite of the live-model
result `planning_toolkit/README.md`'s worked numbers describe, and
exactly *why* that's expected offline: dynamic decomposition's own
step-by-step observation loop makes more individual LLM calls even on the
`open_incident` case where it correctly short-circuits (skips ranking
entirely — see `decisions` in that case's trace file), because each
"what do I do next" decision offline is its own request/response round
trip through the same growing-echo prompt. **Shipped default:
`dynamic_decomposition`** (`agent.py::choose_decomposition_method`) — the
real reason is architectural, not this table: only dynamic decomposition
can react to `open_incident`-shaped surprises without executing a stale
plan, which is the whole point of the lab's divergence requirement, and
that reason doesn't depend on which model is behind `llm`.

### Plan-and-Solve vs. Tree of Thoughts (ranking sub-task)
- `deterministic_single_pr`: Plan-and-Solve succeeds; Tree of Thoughts is
  marked `N/A (not ambiguous)` — it's not run at all, because
  `rank_release_order_with_tree_of_thoughts()` raises by design when
  there's no Approved+Pending PR to be ambiguous about (see its
  docstring). Running a search method on a case with nothing to search
  over would misrepresent both methods.
- `ambiguous_pending_scan` / `_larger_set`: Plan-and-Solve fails (offline,
  its single unstructured pass doesn't satisfy the grounded
  `release_plan_covers_all` check); Tree of Thoughts succeeds both times,
  converging on the `include_with_caveat` strategy (model self-score 1.0,
  grounded score 1.0 — see `search_trace` in the trace files for the
  full 3-candidate comparison and the score gap between strategies).
  **Shipped default: route by `classify_ranking_subtask()`** — Tree of
  Thoughts only for the genuinely ambiguous case, Plan-and-Solve
  otherwise (`agent.py::ROUTE_SUBTASK_METHOD`).

### Grounded vs. ungrounded LATS (remediation sub-task) — the required contrast
- `billing_worker_no_safe_action`: grounded LATS correctly reports
  **failure** (deployment #1 is Failed — can't be rolled back — and
  billing-worker's open critical incident blocks redeploying too, so no
  automated action is actually safe); ungrounded LATS **falsely reports
  success** for exactly the invalid `rollback_deployment` action, because
  `UngroundedEnvironment` scores JSON-shaped text ~0.55-0.9 with no
  connection to the real deployment status. This is the concrete failure
  case the lab's "Grounded vs. ungrounded critique" section asks for.
- `payments_service_valid_rollback`: both agree (success) — the control
  case showing the contrast above is about grounding catching a real
  invalid action, not "ungrounded always says no" or "always says yes."
  **Shipped default: the real, DB-backed `Environment`**
  (`agent.py::run_incident_remediation_subtask`) — `UngroundedEnvironment`
  is never constructed outside this eval and `lats.py`'s own contrast demo.

### Reflexion (cross-trial remediation)
- `ambiguous_target_two_candidates`: succeeds in exactly 2 trials — trial
  1's naive lowest-id guess (deployment #1, Failed) fails with a specific
  reason; that reason becomes a carried reflection; trial 2's full new
  attempt (deployment #2) succeeds. A single Self-Refine revision of trial
  1's *draft* couldn't have done this — the deliverable that needed
  revising was which deployment id to target, not the JSON's prose.
- `no_valid_target_honest_negative`: exhausts all 3 trials and returns the
  best-scoring (not a fabricated-successful) attempt — an honest negative
  result, same convention `planning_toolkit/mini_suite.py` already
  established for this repo.

## Per-sub-task method choices, justified against this table

| Sub-task | Shipped method | Why (per the table above) |
|---|---|---|
| Top-level release-readiness decomposition | `dynamic_decomposition` | Only method that reacts to `open_incident` without executing a stale plan |
| `rank_release_order`, unambiguous case | `plan_and_solve` | Same or better success at a third of ToT's call count when there's nothing to compare |
| `rank_release_order`, Approved+Pending PR present | `tree_of_thoughts` | Only method that reaches a grounded-success answer on the ambiguous cases |
| Propose executable remediation action | `lats`, grounded `Environment` | Ungrounded scoring demonstrably passes an invalid action; grounded doesn't |
| Ambiguous-target rollback (id not known) | `reflexion` | Self-Refine has no mechanism to re-target a different id; Reflexion's cross-trial memory does |
| Incident summary drafts, release-plan drafts | `self_refine` (`reflect_and_refine`, capped `max_iterations`) | Cheap to redo, single grounded critique is enough — see `planning_toolkit/README.md`'s existing writeup |
