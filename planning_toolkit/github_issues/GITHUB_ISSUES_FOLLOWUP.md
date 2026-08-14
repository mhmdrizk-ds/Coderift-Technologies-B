# Follow-up Issues (closing 4 review gaps against the task brief)

فيه 4 Issues إضافية غير الأربعة الأصليين في `github_issues/` — دول بيغطوا فجوات ظهرت في مراجعة الديليفري ضد بنود الرابريك. لكل واحد rationale حقيقي ومعايير قبول قابلة للتحقق، بنفس أسلوب الأربعة الأصليين.

---

## Issue 5: Add explicit acyclicity test evidence

**Rationale**

`Plan.model_validate(...)` (`models.py`'s `validate_dag()`) already
enforces acyclicity by construction, and `DynamicRun.to_plan()` already
builds its output through this same validator — but "acyclicity enforced"
was a graded line item on its own in the task brief, and inheriting a
guarantee by import isn't the same as having test evidence it actually
holds for this module's real output shapes. `dynamic_decomposition.py` has
THREE structurally distinct branches (incident short-circuit, not-ready
short-circuit, full path), each producing a different task set — a
dedicated test per branch is needed, not one generic check.

**Acceptance criteria**

- [ ] A test feeds a deliberately cyclic task list (shaped with this
      module's own task-id vocabulary: `check_incidents` → `gather_prs` →
      `synthesize_release_plan` → back to `gather_prs`) through
      `Plan.model_validate(...)` and asserts it raises.
- [ ] A separate test asserts a task depending on itself is rejected.
- [ ] Three tests drive `run_dynamic_decomposition()` against the real
      local DB — once per real branch (incident short-circuit on
      `billing-worker`/`[5]`, full path on `payments-service`/`[1]`, and
      the not-ready short-circuit on whichever real repository/PR
      combination in the current seed data has no open incident and no
      Approved/Merged PR, found by querying the DB directly rather than
      assumed) — and assert `topological_order()` succeeds on each.
- [ ] If no real seed-data combination exists for the not-ready branch,
      that test skips with an explanation rather than fabricating a case.
- [ ] `build_dynamic_plan()` (Issue 8) has its own passing test.
- [ ] All tests pass alongside the existing `memory/tests/` suite with
      zero regressions: `python -m pytest -v`.

Closes #5 (acyclicity test evidence)

---

## Issue 6: Track context size as its own field, not inferred from token estimate

**Rationale**

The task brief asks explicitly whether the tests written for this concern
"resulted in bigger context." `instrumentation.py`'s `CallStats` tracked
call count, an estimated token count, and latency, but nothing that
directly answers a context-size question — `input_tokens` is a token-count
*estimate*, not a character-based context-size figure. A reviewer checking
this requirement against a trace JSON should find a field named for what
it measures.

**Acceptance criteria**

- [ ] `CallStats` gains `total_context_chars` (sum of every call's raw
      input character length across the run, tracked independently of the
      token estimate) and `max_single_prompt_chars` (the largest single
      call's input length).
- [ ] Both appear in `CallStats.summary()`'s dict, picked up automatically
      by every existing trace consumer (`compare_divergence.py`,
      `dynamic_decomposition.py`'s `run_and_save`, `demo_task1.py`) with
      zero call-site changes.
- [ ] `compare_divergence.py`'s `delta` dict reports
      `total_context_chars_reduction`/`_pct` alongside the existing token
      fields, not folded into them.
- [ ] `mini_suite.py`'s (Issue 7) comparison reports "context chars saved"
      as its own column.

Closes #6 (context size tracking)

---

## Issue 7: Build a fixed 3-case mini-suite to rule out a single-case fluke

**Rationale**

`compare_divergence.py` alone exercises exactly one case
(`billing-worker`/`[5]`). One case is real evidence, but can't by itself
distinguish "dynamic decomposition genuinely reacts to a blocking signal"
from "it happens to look different on this one row of seed data."

**Acceptance criteria**

- [ ] A fixed suite runs both methods against at least 3 real cases: (a) a
      clean case with no open incident and a ready PR — no divergence
      expected; (b) the original open-incident case — divergence expected;
      (c) a case with a different failing signal than an open incident (a
      real, pre-existing seed-data PR with a `Failed` scan on a repository
      with no open incident) — isolates whether the short-circuit is
      incident-specific.
- [ ] Case (c) uses real, pre-existing seed data — not the additive PR
      `#6` used by the Tree of Thoughts case, and not an inserted row.
- [ ] The suite reports, per case: step count for both methods, tokens
      saved, context chars saved (Issue 6), and whether the observed
      short-circuit behavior matched what was expected — flagged clearly
      if a case's result was a surprise.
- [ ] If case (c) does NOT diverge, that's reported as a real, documented
      scope boundary of the current short-circuit rules, not treated as a
      failed test or silently dropped from the writeup.
- [ ] All three cases logged to `planning_toolkit/artifacts/` in one
      reproducible run.

Closes #7 (mini-suite)

---

## Issue 8: Add a documented handoff entry point for Task 3 (LATS + Routing)

**Rationale**

Task 3's routing logic and LATS need to consume this module's output
programmatically. Before this issue, the only way to get a `Plan` was to
call `run_dynamic_decomposition()` and unpack `result["plan"]` from its
result dict, meaning Task 3 would need to understand the full result-dict
shape (outputs, decisions) just to get the one thing it actually needs.

**Acceptance criteria**

- [ ] `build_dynamic_plan(repository_name, candidate_pull_request_ids,
      llm) -> Plan` exists, calling `run_dynamic_decomposition()`
      internally and returning only `result["plan"]`.
- [ ] Its docstring documents `Task.id`/`.instruction`/`.depends_on`
      (including which task ids appear on which of the three branches),
      `Plan.topological_order()`/`.execution_batches()`/
      `.terminal_tasks()`/`.task(task_id)`, with a runnable example.
- [ ] `build_dynamic_plan` is exported from
      `planning_lab/algorithms/__init__.py`'s `__all__`.
- [ ] `Thought` (state/score/rationale) confirmed unchanged and reused
      as-is by `tree_of_thoughts.py`'s output — verified by reading
      `models.py` and `tree_of_thoughts.py` together, not assumed.

Closes #8 (handoff contract)
