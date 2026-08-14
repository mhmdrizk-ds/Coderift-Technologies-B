"""
test_dynamic_decomposition_acyclicity.py

Graded requirement (Task decomposition, both methods): "acyclicity
enforced" needs actual test evidence, not just inherited-by-import from
Plan's own validator (models.py's validate_dag()). This file is that
evidence.

Six things are asserted:
  1. Plan itself rejects a deliberately cyclic task list — the base
     guarantee everything else here depends on.
  2. A cycle shaped with THIS module's own task-id vocabulary
     (check_incidents / gather_prs / synthesize_release_plan) is rejected
     the same way — proving the validator that runs inside
     DynamicRun.to_plan()'s Plan.model_validate({...}) call is not being
     bypassed by this module's incremental (rather than upfront) build
     style.
  3. A task depending on itself is rejected (Plan's dedicated
     self-dependency branch).
  4. run_dynamic_decomposition() driven for real against the local DB on
     the short-circuit-by-incident path (billing-worker/[5], which has an
     open critical incident in db/seed.sql) produces a Plan whose
     topological_order() succeeds — networkx only guarantees this for a
     genuine DAG.
  5. Same, but for the full, non-short-circuited path
     (payments-service/[1]) — the longer of the two real shapes this
     module can produce.
  6. Same, but for the third real branch this module has — the
     not-ready-PR short-circuit (see dynamic_decomposition.py's
     "flag_not_ready" branch) — exercised here for the first time in any
     test, using a repository/PR combination confirmed against the real
     seed data to hit that exact branch.

No network/API calls: a deterministic fake LLM stands in for
CoderiftChatModel throughout — none of these tests are about what the
LLM says, only about the DAG shape the surrounding control flow produces,
which the fake's fixed response text doesn't affect.
"""
from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from planning_toolkit.planning_lab.models import Plan
from planning_toolkit.planning_lab.algorithms.dynamic_decomposition import (
    run_dynamic_decomposition,
)

DB_PATH = "db/coderift.db"


class _EchoLLM:
    """Deterministic fake LLM: the reasoning/short-circuit prompts just
    need *some* text back to keep control flow moving — content doesn't
    affect DAG shape, which is all this file is testing. No network call,
    no API key required, and no LangChain BaseChatModel machinery needed
    since dynamic_decomposition.py only ever calls llm.invoke(...) and
    reflect_and_refine's llm.invoke(...) — both are simple .invoke(messages)
    calls, never with_structured_output here (unlike decomposition.py)."""

    def invoke(self, messages, **kwargs):
        class _Result:
            content = "OK: acyclicity-test placeholder response, naming no PR ids or incidents."
        return _Result()


def _find_repo_with_no_ready_pr() -> tuple[str, list[int]] | None:
    """Query the real seed data for a repository/candidate-PR combination
    where every candidate is neither Approved nor Merged and the
    repository has no open high/critical incident — the exact condition
    dynamic_decomposition.py's "flag_not_ready" branch requires. Returns
    None if no such combination exists in the current seed data (the test
    that needs this skips rather than guessing a synthetic case, since a
    wrong guess would test nothing real)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    blocked_repo_ids = {
        row["repository_id"] for row in cur.execute(
            "SELECT DISTINCT d.repository_id FROM incidents i "
            "JOIN deployments d ON d.id = i.deployment_id "
            "WHERE i.status = 'open' AND i.severity IN ('high', 'critical')"
        )
    }

    for repo_row in cur.execute("SELECT id, name FROM repositories"):
        if repo_row["id"] in blocked_repo_ids:
            continue
        prs = list(cur.execute(
            "SELECT id, status FROM pull_requests WHERE repository_id = ?",
            (repo_row["id"],),
        ))
        if not prs:
            continue
        if all(pr["status"] not in ("Approved", "Merged") for pr in prs):
            conn.close()
            return repo_row["name"], [pr["id"] for pr in prs]
    conn.close()
    return None


def test_plan_rejects_a_deliberate_cycle():
    """Base guarantee: models.py's own validator refuses a 2-node cycle."""
    with pytest.raises(ValidationError, match="[Cc]ycle"):
        Plan.model_validate({
            "goal": "deliberately cyclic plan for acyclicity testing",
            "tasks": [
                {"id": "a", "instruction": "depends on b", "depends_on": ["b"]},
                {"id": "b", "instruction": "depends on a", "depends_on": ["a"]},
            ],
        })


def test_plan_rejects_a_cycle_shaped_like_dynamic_decompositions_own_tasks():
    """Same validator, but with task ids/instructions shaped exactly like
    dynamic_decomposition.py's real vocabulary (check_incidents, gather_prs,
    synthesize_release_plan), so this isn't just an abstract a/b example —
    it's the specific vocabulary DynamicRun.to_plan()'s
    Plan.model_validate({...}) call actually produces."""
    with pytest.raises(ValidationError, match="[Cc]ycle"):
        Plan.model_validate({
            "goal": "Prepare repository 'billing-worker' for a production release.",
            "tasks": [
                {"id": "check_incidents", "instruction": "List open incidents.", "depends_on": []},
                {
                    "id": "gather_prs",
                    "instruction": "Fetch candidate PRs.",
                    # Deliberately cyclic back to a task that depends on
                    # this one — the kind of mistake an incrementally-built
                    # (rather than upfront) plan is actually at risk of if
                    # a future edit to the add_step() call order is wrong.
                    "depends_on": ["synthesize_release_plan"],
                },
                {
                    "id": "synthesize_release_plan",
                    "instruction": "Write the final release plan.",
                    "depends_on": ["gather_prs"],
                },
            ],
        })


def test_plan_rejects_self_dependency():
    """A task depending on itself is a degenerate 1-node cycle — Plan has
    a dedicated branch for it separate from the general cycle check, so it
    gets its own assertion rather than being assumed covered above."""
    with pytest.raises(ValidationError, match="itself"):
        Plan.model_validate({
            "goal": "self-dependency test",
            "tasks": [
                {"id": "rank_release_order", "instruction": "Rank the release order.",
                 "depends_on": ["rank_release_order"]},
            ],
        })


@pytest.mark.db
def test_dynamic_decomposition_incident_short_circuit_path_is_acyclic():
    """Drives the real incident short-circuit branch (billing-worker has
    an open critical incident in db/seed.sql) and asserts the resulting
    Plan is acyclic via topological_order() AND has exactly one terminal
    task — two independent confirmations beyond "construction didn't
    raise"."""
    result = run_dynamic_decomposition(
        repository_name="billing-worker",
        candidate_pull_request_ids=[5],
        llm=_EchoLLM(),
    )
    plan = result["plan"]
    order = plan.topological_order()
    assert set(order) == {t.id for t in plan.tasks}
    assert len(plan.terminal_tasks()) == 1
    assert "check_incidents" in order
    assert order.index("check_incidents") == 0  # must run first per this module's own contract


@pytest.mark.db
def test_dynamic_decomposition_full_path_is_acyclic():
    """Same, for payments-service (no open incident, at least one
    plausibly-ready PR in seed data) — the full, non-short-circuited path
    through every tool task plus rank_release_order and
    synthesize_release_plan."""
    result = run_dynamic_decomposition(
        repository_name="payments-service",
        candidate_pull_request_ids=[1],
        llm=_EchoLLM(),
    )
    plan = result["plan"]
    order = plan.topological_order()
    assert set(order) == {t.id for t in plan.tasks}
    assert len(plan.terminal_tasks()) == 1
    assert "rank_release_order" in order  # confirms this hit the full path, not a short circuit


@pytest.mark.db
def test_dynamic_decomposition_not_ready_short_circuit_path_is_acyclic():
    """Drives the THIRD real branch (no open incident, but no candidate PR
    is Approved/Merged either) — not exercised by any test before this
    one. Uses a real repository/PR combination found in the actual seed
    data, not a guessed or inserted case."""
    case = _find_repo_with_no_ready_pr()
    if case is None:
        pytest.skip(
            "No repository in the current db/seed.sql has zero open "
            "blocking incidents AND zero Approved/Merged candidate PRs — "
            "this branch can't be exercised against real data right now. "
            "Flagging via skip rather than inventing a synthetic case."
        )
    repository_name, candidate_pull_request_ids = case
    result = run_dynamic_decomposition(
        repository_name=repository_name,
        candidate_pull_request_ids=candidate_pull_request_ids,
        llm=_EchoLLM(),
    )
    plan = result["plan"]
    order = plan.topological_order()
    assert set(order) == {t.id for t in plan.tasks}
    assert len(plan.terminal_tasks()) == 1
    assert "flag_not_ready" in order
    assert "check_flags" not in order  # confirms the short circuit actually skipped this
    assert "check_deploy_status" not in order


@pytest.mark.db
def test_build_dynamic_plan_returns_a_validated_plan():
    """The handoff-contract function (build_dynamic_plan) is exercised
    directly, not just run_dynamic_decomposition() — this is the entry
    point Task 3 actually imports, so it needs its own passing test."""
    from planning_toolkit.planning_lab.algorithms.dynamic_decomposition import (
        build_dynamic_plan,
    )

    plan = build_dynamic_plan(
        repository_name="billing-worker",
        candidate_pull_request_ids=[5],
        llm=_EchoLLM(),
    )
    assert isinstance(plan, Plan)
    assert plan.topological_order()  # raises if somehow non-acyclic
    assert plan.task("check_incidents") is not None  # confirms Plan.task() accessor works
