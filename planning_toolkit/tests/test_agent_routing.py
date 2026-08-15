"""Regression tests for the Week 4 extension: ToT/LATS/Reflexion wired to
real Coderift data, the grounded-vs-ungrounded contrast, and agent.py's
routing table. Run: python3 -m pytest planning_toolkit/tests/test_agent_routing.py -q
(requires db/coderift.db — run `python3 db/init_db.py` first)."""
import pytest

from planning_toolkit.model_provider import CoderiftChatModel
from planning_toolkit.planning_lab.agent import (
    classify_ranking_subtask, SubtaskShape,
    run_release_ranking_subtask, run_incident_remediation_subtask,
)
from planning_toolkit.planning_lab.algorithms.reflexion import remediate_incident_with_reflexion
from planning_toolkit.planning_lab.algorithms.lats import propose_remediation_with_lats
from planning_toolkit.planning_lab.algorithms.environment import Environment
from planning_toolkit.planning_lab.algorithms.environment_ungrounded import UngroundedEnvironment

pytestmark = pytest.mark.db


@pytest.fixture
def llm():
    return CoderiftChatModel()


def test_routing_picks_plan_and_solve_when_unambiguous():
    assert classify_ranking_subtask("payments-service", [1]) == SubtaskShape.DETERMINISTIC


def test_routing_picks_tree_of_thoughts_when_ambiguous():
    assert classify_ranking_subtask("checkout-web", [2, 6]) == SubtaskShape.AMBIGUOUS_RANKING


def test_deterministic_ranking_routes_to_plan_and_solve(llm):
    result = run_release_ranking_subtask("payments-service", [1], llm)
    assert result.method_used == "plan_and_solve"


def test_ambiguous_ranking_routes_to_tree_of_thoughts_and_prefers_include_with_caveat(llm):
    result = run_release_ranking_subtask("checkout-web", [2, 6], llm)
    assert result.method_used == "tree_of_thoughts"
    assert "include_with_caveat" in result.output


def test_lats_grounded_rejects_invalid_rollback_of_failed_deployment(llm):
    result = propose_remediation_with_lats(
        "billing-worker", 1, llm, environment=Environment(),
    )
    assert result.success is False, (
        "Grounded LATS must not report success for rolling back a Failed deployment "
        "while an open incident also blocks redeploying."
    )


def test_lats_ungrounded_falsely_accepts_the_same_invalid_action(llm):
    result = propose_remediation_with_lats(
        "billing-worker", 1, llm, environment=UngroundedEnvironment(seed=1),
    )
    assert result.success is True, (
        "This is the required contrast: the deliberately fake evaluator has no "
        "connection to the real DB, so it should NOT catch the invalid rollback "
        "the grounded Environment correctly rejects above."
    )


def test_lats_grounded_accepts_valid_rollback(llm):
    result = propose_remediation_with_lats(
        "payments-service", 2, llm, environment=Environment(),
    )
    assert result.success is True


def test_incident_remediation_subtask_always_routes_to_lats(llm):
    result = run_incident_remediation_subtask("payments-service", 2, llm)
    assert result.method_used == "lats"
    assert result.success is True


def test_reflexion_needs_a_second_full_trial_to_find_a_valid_target(llm):
    result = remediate_incident_with_reflexion(
        "An on-call engineer needs to roll back a deployment for further verification.",
        deployment_ids=[1, 2], llm=llm,
    )
    assert result.success is True
    assert len(result.trials) == 2, "Should fail once (deployment #1, Failed) before succeeding on #2."
    assert result.trials[0].feedback.success is False
    assert result.trials[0].reflection is not None


def test_reflexion_returns_honest_negative_when_no_valid_target_exists(llm):
    result = remediate_incident_with_reflexion(
        "Roll back the billing-worker deployment tied to the open incident.",
        deployment_ids=[1], llm=llm, max_trials=3,
    )
    assert result.success is False
    assert len(result.trials) == 3
