# Follow-up Issues (closing the remaining Decomposition & Planning Lab gaps)

Five more issues beyond `GITHUB_ISSUES_FOLLOWUP.md`'s original five — these
close the gaps between what `planning_toolkit/README.md` described and what
was actually wired into real code as of that document's last revision:
Tree of Thoughts was documented but not actually connected to
`rank_release_order`, LATS/Reflexion existed only as the toolkit's generic
functions, there was no grounded-vs-ungrounded contrast, and there was no
`planning_eval/` comparison table or CLI entry point. Same rationale +
acceptance-criteria format as the originals.

---

## Issue 9: Wire Tree of Thoughts to the real ambiguous-ranking sub-task

**Rationale**

`tree_of_thoughts.py` shipped as the reference toolkit's generic
generate/evaluate/prune demo, unconnected to `rank_release_order` or any
real Coderift data, despite the README already describing (in the past
tense) a `checkout-web`/PR `#6` walkthrough that didn't exist in code.
Documentation ahead of implementation reads as done work that isn't —
closing this gap for real, not just correcting the tense.

**Acceptance criteria**

- [ ] `rank_release_order_with_tree_of_thoughts()` gathers real facts via
      the same `handle_get_pull_request` MCP tool handler the rest of this
      repo uses (not a parallel query path).
- [ ] Generates ≥2 distinct candidate strategies, self-evaluates each,
      keeps the best, runs ≥1 refinement round.
- [ ] Raises (not silently degrades) when called on a case with no
      Approved+Pending PR — ToT is reserved for the genuinely ambiguous
      shape.
- [ ] Also reports the grounded `Environment` score of the winner, purely
      for comparison against LATS's environment score — not used as the
      search criterion itself.
- [ ] `db/seed.sql`'s PR `#6` (the case this needs) actually exists and
      `python3 db/init_db.py` produces it — verified by
      `planning_toolkit/tests/test_agent_routing.py`.

Closes #9 (Tree of Thoughts wiring)

---

## Issue 10: Wire LATS to a real remediation-action sub-task, with a genuine grounded-vs-ungrounded contrast

**Rationale**

The lab brief requires "a shown failure case the grounded version catches
that the ungrounded version missed" — a requirement that's unmet by
stating it in prose. `environment.py`'s randomized default was already
replaced by a real DB-backed evaluator, which is correct as the shipped
default, but that alone doesn't produce the required contrast; something
deliberately fake needs to exist too, used only for this one demo.

**Acceptance criteria**

- [ ] `propose_remediation_with_lats()` runs real MCTS (select, expand &
      simulate, evaluate/reflect, backpropagate) over concrete
      `rollback_deployment`/`deploy_pr` action proposals against real
      deployment/PR/incident rows.
- [ ] `environment_ungrounded.py::UngroundedEnvironment` implements the
      same `.evaluate()` interface with a score that has no connection to
      the real database, clearly documented as demo-only, never
      constructed by `agent.py`'s shipped routing path.
- [ ] At least one real case (found by querying seed data, not
      constructed) where the grounded environment reports failure and the
      ungrounded one reports success for the same proposed action.
- [ ] At least one control case where both agree, so the contrast isn't
      "ungrounded always says no/yes."
- [ ] Both cases have passing regression tests
      (`test_lats_grounded_rejects_invalid_rollback_of_failed_deployment`,
      `test_lats_ungrounded_falsely_accepts_the_same_invalid_action`).

Closes #10 (LATS wiring + grounded/ungrounded contrast)

---

## Issue 11: Wire Reflexion to a request that genuinely needs cross-trial memory, not a single retry

**Rationale**

Self-Refine and Reflexion have different scopes (revise a fixed draft vs.
retry the whole task with carried memory), and the lab brief explicitly
asks for a test case where a single retry isn't enough. Without a
domain-specific wiring, that distinction stays theoretical — needed: a
real request where the SECOND attempt has to be a materially different
whole answer, not a patched version of the first.

**Acceptance criteria**

- [ ] `remediate_incident_with_reflexion()` proposes a
      `rollback_deployment` action for an ambiguous multi-candidate
      request (exact target id not given).
- [ ] A real case where trial 1's naive guess fails for a specific,
      stated reason, that reason is carried into trial 2 as an episodic
      memory entry, and trial 2 — a full new attempt targeting a
      different id — succeeds.
- [ ] A real honest-negative case (only one candidate, genuinely never
      valid) that exhausts all trials and returns the best-scoring
      attempt rather than fabricating success.
- [ ] Both covered by passing regression tests.

Closes #11 (Reflexion wiring)

---

## Issue 12: `planning_eval/` — run every required method against a fixed real test suite, produce the full comparison table

**Rationale**

The lab brief requires one comparison table covering decomposition-first
vs. dynamic, Plan-and-Solve vs. Tree of Thoughts vs. LATS, and Self-Refine
vs. Reflexion, scored on accuracy/success, LLM calls, tokens, and latency
— against a FIXED suite, not cherry-picked ad hoc runs. No such harness or
folder existed.

**Acceptance criteria**

- [ ] `planning_eval/test_suite.json` is fixed (not modified once
      evaluation starts) and covers: a decomposition-first-favoring case,
      a dynamic-favoring case, a ToT-needing ambiguous case, and a
      Reflexion-needing (single-retry-insufficient) case, per the brief's
      explicit list.
- [ ] `planning_eval/run_eval.py` runs every method against every
      applicable case (skipping, not faking, inapplicable combinations —
      e.g. ToT on an unambiguous case), using `instrumentation.py`'s
      `CountingChatModel` for real call/token/latency numbers.
- [ ] Writes one JSON trace per run to `planning_eval/artifacts/`,
      extending (not duplicating) the existing trace format.
- [ ] Produces `comparison_table.md` and a `planning_eval/README.md` that
      justifies each shipped per-sub-task method choice against the
      table's actual numbers, not just against which method sounds more
      sophisticated.

Closes #12 (evaluation harness + comparison table)

---

## Issue 13: `agent/planning_client.py` — a CLI entry point for the planning agent

**Rationale**

`planning_toolkit/README.md` already documented CLI invocations for the
planning agent before the CLI existed, mirroring `agent/client.py`'s
style for the MCP lab's agent. Graders and teammates need one place to run
any planning sub-task without importing internals directly.

**Acceptance criteria**

- [ ] `python3 -m agent.planning_client` supports `--method`, `--rank`,
      `--remediate`, `--reflexion`, and `--compare-divergence`, each
      calling real code in `planning_lab/agent.py` (no canned output).
- [ ] Every subcommand prints real JSON computed against the real
      `db/coderift.db`.
- [ ] README's documented example invocations actually run as written.

Closes #13 (planning agent CLI)
