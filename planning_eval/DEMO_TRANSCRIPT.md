# Demo Transcript — Release Readiness & Incident Remediation Planning Agent

Every output below is real: captured from `python3 -m planning_toolkit.compare_divergence`
and `python3 -m planning_eval.run_eval` against the real `db/coderift.db` seed
data, offline (no live model configured — see `planning_eval/README.md` for
what that does and doesn't affect). Full JSON traces for everything here are
in `planning_eval/artifacts/` and `artifacts/`.

## 1. Decomposition-first vs. dynamic decomposition, divergence visible

Case: `repository='billing-worker'`, candidate PR `[5]` — billing-worker has
a seeded **open critical incident**.

```
decomposition-first: 6 steps ['gather_prs', 'check_incidents', 'check_flags',
  'check_deploy_status', 'rank_release_order', 'synthesize_release_plan']
  llm_call_count: 3, total_tokens: 4293

dynamic_decomposition: 4 steps ['check_incidents', 'gather_prs',
  'flag_blocked_by_incident', 'synthesize_release_plan']
  llm_call_count: 5, total_tokens: 7517

DELTA: step_count_reduction: 2 (33.3%)
```

**The divergence**: decomposition-first queues all 6 steps up front and runs
`rank_release_order`, `check_flags`, and `check_deploy_status` regardless.
Dynamic decomposition observes `check_incidents` FIRST, sees the open
critical incident, and reshapes its own plan to `flag_blocked_by_incident`
— it never generates or runs `rank_release_order`, `check_flags`, or
`check_deploy_status` at all, because none of them can change the outcome
once a critical incident is open. That's a real early-surprise reshaping a
fixed up-front plan can't do.

*(Offline, dynamic decomposition's own step-by-step LLM round trips cost
more total tokens than decomposition-first's 3 calls despite doing less
real work — see `planning_eval/README.md`'s note on why offline-echo token
counts don't reflect live-model economics. `planning_eval/comparison_table.md`
has the full numbers including the `clean_control` case where no
short-circuit should fire, as a baseline.)*

## 2. Plan-and-Solve solving a sub-task

Case: rank release order for `payments-service`, PR `#1` (Approved+Passed,
nothing ambiguous — routed to Plan-and-Solve per `agent.py::ROUTE_SUBTASK_METHOD`).

```
$ python3 -m agent.planning_client --rank --repository payments-service --candidate-pr-ids 1
{
  "method_used": "plan_and_solve",
  ...
}
```
Grounded check (`release_plan_covers_all`) passes on the first Self-Refine
round — one explicit plan, one pass, no branching needed because there's
nothing to branch over.

## 3. Tree of Thoughts solving a sub-task

Case: rank release order for `checkout-web`, PRs `#2` (Approved, scan
Failed) and `#6` (Approved, scan **Pending** — the genuinely ambiguous one).

```
$ python3 -m agent.planning_client --rank --repository checkout-web --candidate-pr-ids 2 6
{
  "method_used": "tree_of_thoughts",
  "output": "RELEASE ORDER for checkout-web\nStrategy: include_with_caveat\n
    PR #6 ... Approved by a human reviewer; its security scan is still
    Pending, not Failed. Include PR #6 in this release, but hold the deploy
    until the scan resolves ...\n
    PR #2 (Approved, scan Failed) is BLOCKED — not release-ready."
}
```

Full search trace (`planning_eval/artifacts/ranking__ambiguous_pending_scan__tree_of_thoughts_*.json`):

| Candidate strategy | Self-eval score | Why |
|---|---|---|
| `include_with_caveat` | **1.0** | Commits to a decision, leverages the human reviewer's sign-off |
| `exclude_until_resolved` | 0.9 | Complete, but discards the Approved sign-off entirely |
| `escalate_for_manual_review` | 0.8 | Defers the decision rather than committing |

`include_with_caveat` wins the search, gets one refinement round (adds an
escalation note), and its grounded `Environment` score is **1.0 / success:
true** — confirmed independently of the model's own self-score.

## 4. LATS solving a sub-task, and the grounded-vs-ungrounded contrast

Case: propose a remediation action for `billing-worker`, deployment `#1`
(**Failed** status, tied to an **open critical incident**).

```
$ python3 -m agent.planning_client --remediate --repository billing-worker --deployment-id 1
{
  "method_used": "lats",
  "success": false,
  "output": "{\"action\": \"deploy_pr\", \"repository_name\": \"billing-worker\",
              \"environment_name\": \"production\", \"pull_request_id\": 5}"
}
```

Grounded LATS correctly finds **no safe automated action**: `rollback_deployment`
on #1 fails ("only Succeeded/InProgress can be rolled back"); `deploy_pr` of
the last merged PR fails too (open critical incident blocks it). Best score
reached: 0.75 — an honest partial result, not a fabricated success.

**The same case, `UngroundedEnvironment` instead of the real one**
(`propose_remediation_with_lats(..., environment=UngroundedEnvironment(seed=1))`):

```
success: true
output: {"action": "rollback_deployment", "deployment_id": 1}
best_score: 0.9365
```

**This is the concrete failure case the lab requires**: the ungrounded
evaluator has no connection to `db/coderift.db` and scores the JSON shape
of the text alone, so it reports success for rolling back a deployment
that is literally not in a rollback-eligible state. The grounded
`Environment` catches this; the ungrounded one doesn't.

Control case confirming this isn't just "ungrounded always says yes":
`payments-service` deployment `#2` (Succeeded, no incident) — **both**
grounded and ungrounded report success, because a genuinely valid action
exists there.

## 5. A Self-Refine revision

From `run_release_ranking_subtask("payments-service", [1], llm)`'s internal
`reflect_and_refine(...)` call — the grounded `release_plan_covers_all`
check runs against the real draft, reports concrete issues if the draft
doesn't mention PR `#1` by id or use a `READY`/`BLOCKED` marker, and one
revision round is generated addressing exactly that grounded report (see
`planning_toolkit/README.md`'s existing writeup on `reflect_and_refine` for
the mechanism; `self_refine.py::_one_round` now supports up to
`max_iterations` rounds, stopping the moment both the grounded checks pass
AND an independent critic says `PASS`).

## 6. A Reflexion run carrying a reflection across trials

Case: "An on-call engineer needs to roll back a deployment for further
verification" — candidates `[1, 2]`, exact id not given.

```
$ python3 -m agent.planning_client --reflexion --deployment-ids 1 2
{
  "success": true,
  "output": "{\"action\": \"rollback_deployment\", \"deployment_id\": 2}",
  "trial_count": 2,
  "trials": [
    {
      "number": 1,
      "attempt": "{\"action\": \"rollback_deployment\", \"deployment_id\": 1}",
      "success": false,
      "score": 0.0,
      "reflection": "I picked deployment #1 (status 'Failed') without checking
        whether its status allows a rollback. External feedback: Deployment #1
        is 'Failed'; only a Succeeded or InProgress deployment can be rolled
        back.. Next trial, I should only propose a deployment whose status is
        Succeeded or InProgress."
    },
    {
      "number": 2,
      "attempt": "{\"action\": \"rollback_deployment\", \"deployment_id\": 2}",
      "success": true,
      "score": 1.0,
      "reflection": null
    }
  ]
}
```

Trial 1's failure produces a concrete, carried reflection; trial 2 is a
**brand-new full attempt** (a different `deployment_id`, not a revision of
trial 1's text) that applies it and succeeds. This is Reflexion's scope,
distinct from Self-Refine: the thing that needed fixing was *which*
deployment to target, not the wording of a draft.

The honest-negative companion case (only deployment `#1`, no valid target
exists at all) exhausts all 3 trials and correctly returns `success: false`
with the best-scoring attempt — see `planning_eval/artifacts/reflexion__no_valid_target_honest_negative_*.json`.

## Reproduce this transcript

```bash
python3 db/init_db.py
python3 -m planning_toolkit.compare_divergence      # section 1
python3 -m planning_eval.run_eval                   # sections 2-6, all at once
python3 -m agent.planning_client --rank --repository payments-service --candidate-pr-ids 1
python3 -m agent.planning_client --rank --repository checkout-web --candidate-pr-ids 2 6
python3 -m agent.planning_client --remediate --repository billing-worker --deployment-id 1
python3 -m agent.planning_client --reflexion --deployment-ids 1 2
```
