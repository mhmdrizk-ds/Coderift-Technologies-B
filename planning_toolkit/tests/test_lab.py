import json
from types import SimpleNamespace

import pytest

from planning_toolkit.model_provider import (
    CoderiftChatModel,
    NoLiveModelConfigured,
)
from planning_toolkit.planning_lab.algorithms.environment import Environment
from planning_toolkit.planning_lab.algorithms.decomposition import (
    GeneratedPlan,
    decompose_goal,
)
from planning_toolkit.planning_lab.algorithms.plan_and_solve import plan_and_solve
from planning_toolkit.planning_lab.algorithms.self_refine import (
    deterministic_checks,
    reflect_and_refine,
)
from planning_toolkit.planning_lab.models import Plan


# ============================================================
# Grounded Environment
# ============================================================

def test_environment_accepts_valid_deploy_state():
    environment = Environment()

    result = environment.evaluate(json.dumps({
        "action": "deploy_pr",
        "repository_name": "payments-service",
        "environment_name": "staging",
        "pull_request_id": 1,
    }))

    assert result.success is True
    assert result.score == 1.0
    assert result.details == []


def test_environment_rejects_deploy_with_open_incident():
    environment = Environment()

    result = environment.evaluate(json.dumps({
        "action": "deploy_pr",
        "repository_name": "billing-worker",
        "environment_name": "production",
        "pull_request_id": 5,
    }))

    assert result.success is False
    assert result.score < 1.0
    assert any("incident" in detail.lower() for detail in result.details)


def test_environment_rejects_invalid_json():
    environment = Environment()

    result = environment.evaluate("not valid json")

    assert result.success is False
    assert result.score == 0.0
    assert "not valid json" in result.details[0].lower()


def test_environment_rejects_unknown_action():
    environment = Environment()

    result = environment.evaluate(json.dumps({
        "action": "something_unknown",
    }))

    assert result.success is False
    assert result.score == 0.0
    assert "unknown action type" in result.details[0].lower()


def test_environment_rejects_missing_required_field():
    environment = Environment()

    result = environment.evaluate(json.dumps({
        "action": "deploy_pr",
        "repository_name": "payments-service",
    }))

    assert result.success is False
    assert result.score == 0.0
    assert "missing required field" in result.details[0].lower()


# ============================================================
# Decomposition-first
# ============================================================

def test_decomposition_plan_is_acyclic_and_topologically_valid():
    plan = Plan.model_validate({
        "goal": "Prepare repository for release",
        "tasks": [
            {
                "id": "gather_prs",
                "instruction": "Fetch candidate pull requests",
                "depends_on": [],
            },
            {
                "id": "check_incidents",
                "instruction": "Check active incidents",
                "depends_on": [],
            },
            {
                "id": "rank_release_order",
                "instruction": "Rank release order",
                "depends_on": [
                    "gather_prs",
                    "check_incidents",
                ],
            },
            {
                "id": "synthesize_release_plan",
                "instruction": "Write final release plan",
                "depends_on": ["rank_release_order"],
            },
        ],
    })

    assert plan.topological_order() == [
        "gather_prs",
        "check_incidents",
        "rank_release_order",
        "synthesize_release_plan",
    ]

    assert [set(batch) for batch in plan.execution_batches()] == [
        {"gather_prs", "check_incidents"},
        {"rank_release_order"},
        {"synthesize_release_plan"},
    ]


def test_decomposition_rejects_cycle():
    with pytest.raises(ValueError, match="Cycle detected"):
        Plan.model_validate({
            "goal": "Reject cyclic release plan",
            "tasks": [
                {
                    "id": "a",
                    "instruction": "Task A",
                    "depends_on": ["b"],
                },
                {
                    "id": "b",
                    "instruction": "Task B",
                    "depends_on": ["a"],
                },
            ],
        })


class FailingStructuredLLM:
    def with_structured_output(self, schema, *, method):
        raise RuntimeError("structured generation failed")


def test_decompose_goal_uses_safe_fallback_plan():
    llm = FailingStructuredLLM()

    plan = decompose_goal(
        "Prepare payments-service for production",
        llm,
        repository_name="payments-service",
    )

    task_ids = [task.id for task in plan.tasks]

    assert task_ids == [
        "gather_prs",
        "check_incidents",
        "check_flags",
        "check_deploy_status",
        "rank_release_order",
        "synthesize_release_plan",
    ]

    assert [set(batch) for batch in plan.execution_batches()] == [
        {
            "gather_prs",
            "check_incidents",
            "check_flags",
            "check_deploy_status",
        },
        {"rank_release_order"},
        {"synthesize_release_plan"},
    ]

# ============================================================
# Plan-and-Solve
# ============================================================

class FakePlanLLM:
    def invoke(self, messages, **kwargs):
        assert "Plan-and-Solve" in messages[0][1]
        assert "First understand the problem" in messages[-1][1]

        return SimpleNamespace(
            content=(
                "PLAN:\n"
                "1. Inspect the release state.\n"
                "2. Identify blocked PRs.\n"
                "3. Produce the release order.\n\n"
                "SOLUTION:\n"
                "PR #1 is ready and PR #5 is blocked by the active incident."
            )
        )


def test_plan_and_solve_returns_plan_and_solution():
    result = plan_and_solve(
        "Decide whether the repository is ready for release.",
        FakePlanLLM(),
    )

    assert "PLAN:" in result
    assert "SOLUTION:" in result
    assert "PR #1" in result


class EmptyLLM:
    def invoke(self, messages, **kwargs):
        return SimpleNamespace(content="")


def test_plan_and_solve_rejects_empty_model_response():
    with pytest.raises(RuntimeError, match="empty"):
        plan_and_solve(
            "Prepare a release plan.",
            EmptyLLM(),
        )


# ============================================================
# Self-Refine
# ============================================================

def test_deterministic_checks_detect_short_unstructured_output():
    issues = deterministic_checks(
        "Create a structured security checklist",
        "Too short",
    )

    assert len(issues) >= 2
    assert any("80 words" in issue for issue in issues)
    assert any("structure" in issue.lower() for issue in issues)


def test_deterministic_checks_pass_good_deliverable():
    draft = (
        "# Security Checklist\n"
        + "- security controls and verification steps\n" * 30
    )

    issues = deterministic_checks(
        "Create a structured security checklist",
        draft,
    )

    assert issues == []


class SelfRefineLLM:
    def __init__(self):
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append(messages)

        system = messages[0][1]

        if "separate critic" in system:
            return SimpleNamespace(
                content="The draft is too short and lacks a concrete checklist."
            )

        return SimpleNamespace(
            content=(
                "# Security Checklist\n"
                + "- security controls and verification steps\n" * 30
            )
        )


def test_self_refine_performs_critique_then_revision():
    llm = SelfRefineLLM()

    draft = "Too short"

    result = reflect_and_refine(
        goal="Create a structured security checklist",
        draft=draft,
        llm=llm,
    )

    assert result.draft == draft
    assert result.critique
    assert result.revised != draft
    assert len(llm.calls) == 2

    assert "security controls" in result.revised.lower()


# ============================================================
# CoderiftChatModel
# ============================================================

def test_coderift_chat_model_has_correct_llm_type():
    chat = CoderiftChatModel()

    assert chat._llm_type == "coderift-gemini-or-offline"


def test_coderift_chat_model_offline_fallback(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    chat = CoderiftChatModel()

    response = chat.invoke([
        ("system", "You are a release assistant."),
        ("human", "Check PR #1 for release readiness."),
    ])

    assert response.content
    assert "offline fallback" in response.content
    assert "PR #1" in response.content


def test_coderift_structured_output_requires_live_model(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    class ExampleSchema(__import__("pydantic").BaseModel):
        value: str

    chat = CoderiftChatModel()

    runnable = chat.with_structured_output(
        ExampleSchema,
        method="json_schema",
    )

    with pytest.raises(NoLiveModelConfigured):
        runnable.invoke([
            ("human", "Return a value."),
        ])


def test_generated_plan_schema_rejects_extra_fields():
    with pytest.raises(Exception):
        GeneratedPlan.model_validate({
            "goal": "Prepare release",
            "unexpected": "not allowed",
            "tasks": [],
        })
