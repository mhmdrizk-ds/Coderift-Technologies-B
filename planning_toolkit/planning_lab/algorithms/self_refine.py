from dataclasses import dataclass
import json

from langchain_core.language_models.chat_models import BaseChatModel

from .environment import Environment


def deterministic_checks(
    goal: str, draft: str,
    repository_name: str | None = None,
    candidate_pull_request_ids: list[int] | None = None,
) -> list[str]:
    if repository_name and candidate_pull_request_ids:
        return _grounded_release_plan_checks(repository_name, candidate_pull_request_ids, draft)
    return _generic_heuristic_checks(goal, draft)


def _generic_heuristic_checks(goal: str, draft: str) -> list[str]:
    import re
    issues: list[str] = []
    if len(draft.split()) < 80:
        issues.append("The deliverable is under 80 words and is probably incomplete.")
    goal_terms = {
        word.lower()
        for word in re.findall(r"[A-Za-z]{5,}", goal)
        if word.lower() not in {"create", "design", "write", "build", "about", "using"}
    }
    represented = [term for term in goal_terms if term in draft.lower()]
    if goal_terms and not represented:
        issues.append("The output contains none of the goal's significant terms.")
    if not re.search(r"(^|\n)(#{1,3}\s+|\d+[.)]\s+|[-*]\s+)", draft):
        issues.append("The deliverable has no visible structure (headings or list items).")
    return issues


def _grounded_release_plan_checks(
    repository_name: str, candidate_pull_request_ids: list[int], draft: str,
) -> list[str]:
    state = json.dumps({
        "action": "release_plan_covers_all",
        "repository_name": repository_name,
        "candidate_pull_request_ids": candidate_pull_request_ids,
        "draft": draft,
    })
    feedback = Environment().evaluate(state)
    return feedback.details


@dataclass
class ReflectionRound:
    draft: str
    critique: str
    revised: str
    grounded_issues: list[str]


@dataclass
class ReflectionResult:
    draft: str
    critique: str
    revised: str
    grounded_issues: list[str]
    rounds: list[ReflectionRound]

    @property
    def iterations(self) -> int:
        return len(self.rounds)


def _one_round(
    goal: str, draft: str, llm: BaseChatModel,
    repository_name: str | None, candidate_pull_request_ids: list[int] | None,
) -> ReflectionRound:
    grounded = deterministic_checks(goal, draft, repository_name, candidate_pull_request_ids)
    grounded_report = "\n".join(f"- {issue}" for issue in grounded) or "- Grounded checks passed."
    critique_response = llm.invoke([
        ("system", "You are a separate critic. Judge against the rubric; do not rewrite the draft."),
        ("human", f"""Goal: {goal}
Rubric: correctness, completeness, internal consistency, and instruction adherence.
External grounded checks (from the real Coderift database):
{grounded_report}

Draft:
{draft}

List concrete issues. If there are none, respond exactly PASS."""),
    ], temperature=0.2)
    critique = critique_response.content
    if not isinstance(critique, str) or not critique.strip():
        raise RuntimeError("The chat model returned an empty or unsupported response")
    critique = critique.strip()
    if critique.strip().upper() == "PASS" and not grounded:
        revised = draft
    else:
        response = llm.invoke([
            ("system", "Revise a deliverable using both the grounded database checks and "
                       "an independent critique. Never drop a fact the grounded checks "
                       "require mentioning."),
            ("human", f"Goal: {goal}\n\nDraft:\n{draft}\n\nGrounded checks:\n{grounded_report}"
                      f"\n\nCritique:\n{critique}\n\nReturn only the improved deliverable."),
        ], temperature=0.2)
        revised = response.content
        if not isinstance(revised, str) or not revised.strip():
            raise RuntimeError("The chat model returned an empty or unsupported response")
        revised = revised.strip()
    return ReflectionRound(draft=draft, critique=critique, revised=revised, grounded_issues=grounded)


def reflect_and_refine(
    goal: str, draft: str, llm: BaseChatModel,
    repository_name: str | None = None,
    candidate_pull_request_ids: list[int] | None = None,
    max_iterations: int = 1,
) -> ReflectionResult:
    """One draft, one grounded+model critique, one revision — repeated up to
    `max_iterations` times, stopping early the moment a round both passes the
    grounded checks AND the independent critic says PASS. `max_iterations=1`
    (the default) reproduces the original single-pass Self-Refine behavior
    exactly; sub-tasks that are cheap to redo (e.g. an incident summary
    draft) can afford `max_iterations>1` to converge further."""
    if max_iterations < 1:
        raise ValueError("max_iterations must be positive")
    rounds: list[ReflectionRound] = []
    current_draft = draft
    for _ in range(max_iterations):
        round_result = _one_round(goal, current_draft, llm, repository_name, candidate_pull_request_ids)
        rounds.append(round_result)
        current_draft = round_result.revised
        converged = (
            not round_result.grounded_issues
            and round_result.critique.strip().upper() == "PASS"
        )
        if converged:
            break
    last = rounds[-1]
    return ReflectionResult(
        draft=draft, critique=last.critique, revised=last.revised,
        grounded_issues=last.grounded_issues, rounds=rounds,
    )