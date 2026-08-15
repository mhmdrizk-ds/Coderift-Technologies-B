from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel
from ..models import EnvironmentFeedback
from .environment import Environment


@dataclass
class ReflexionTrial:
    number: int
    attempt: str
    feedback: EnvironmentFeedback
    reflection: str | None = None


@dataclass
class ReflexionResult:
    success: bool
    output: str
    trials: list[ReflexionTrial]
    memory: list[str]


def reflexion(
    task: str,
    llm: BaseChatModel,
    environment: Environment,
    max_trials: int = 3,
    memory_size: int = 3,
) -> ReflexionResult:
    if max_trials < 1 or memory_size < 1:
        raise ValueError("max_trials and memory_size must be positive")
    memory: list[str] = []
    trials: list[ReflexionTrial] = []
    best_attempt = ""
    best_score = -1.0
    for number in range(1, max_trials + 1):
        recalled = "\n".join(f"- {item}" for item in memory[-memory_size:]) or "- No prior trials."
        response = llm.invoke([
            ("system", "You are the acting agent in a Reflexion loop. Attempt the entire task again."),
            ("human", f"""Task: {task}
Episodic memory from previous failed trials:
{recalled}

Produce the complete deliverable. Apply remembered lessons without discussing them."""),
        ], temperature=0.2)
        attempt = response.content
        if not isinstance(attempt, str) or not attempt.strip():
            raise RuntimeError("The chat model returned an empty or unsupported response")
        attempt = attempt.strip()
        feedback = environment.evaluate(attempt)
        trial = ReflexionTrial(number=number, attempt=attempt, feedback=feedback)
        if feedback.score > best_score:
            best_attempt, best_score = attempt, feedback.score
        if feedback.success:
            trials.append(trial)
            return ReflexionResult(True, attempt, trials, memory[-memory_size:])
        response = llm.invoke([
            ("system", "Generate a concise first-person Reflexion memory, not a revised answer."),
            ("human", f"""Task: {task}
Failed attempt:
{attempt}

External environment feedback (score {feedback.score}):
{chr(10).join('- ' + item for item in feedback.details)}

State what I did wrong and the specific strategy I should use next trial. Start with 'I'."""),
        ], temperature=0.2)
        reflection = response.content
        if not isinstance(reflection, str) or not reflection.strip():
            raise RuntimeError("The chat model returned an empty or unsupported response")
        reflection = reflection.strip()
        trial.reflection = reflection
        trials.append(trial)
        memory.append(reflection)
    return ReflexionResult(False, best_attempt, trials, memory[-memory_size:])


# ---------------------------------------------------------------------------
# Coderift-specific wiring: remediate_incident — full-task retry (not
# branch search; see LATS for that) for a request an engineer would
# genuinely send as-is: "roll back whichever deployment is safe, I don't
# remember the exact id." A single Self-Refine pass revises ONE fixed
# draft; this needs the whole action re-proposed against a different
# target after learning why the first guess was wrong, which is exactly
# Reflexion's scope, not Self-Refine's. See planning_eval/README.md for
# why this specific request needs Reflexion and not a single retry.
# ---------------------------------------------------------------------------

import json

from mcp_server import db
from planning_toolkit.model_provider import is_live_model_configured


def _gather_rollback_candidates(deployment_ids: list[int]) -> list[dict]:
    conn = db.get_connection()
    try:
        rows = []
        for did in deployment_ids:
            row = conn.execute(
                """
                SELECT d.id, d.status, r.name AS repository_name, e.name AS environment_name
                FROM deployments d
                JOIN repositories r ON r.id = d.repository_id
                JOIN environments e ON e.id = d.environment_id
                WHERE d.id = ?
                """,
                (did,),
            ).fetchone()
            if row is not None:
                rows.append(dict(row))
        return rows
    finally:
        conn.close()


def remediate_incident_with_reflexion(
    task_description: str,
    deployment_ids: list[int],
    llm: BaseChatModel,
    environment: Environment | None = None,
    max_trials: int = 3,
    memory_size: int = 3,
) -> ReflexionResult:
    """Propose a `rollback_deployment` action for an ambiguous request that
    only names CANDIDATE deployment ids, not which one is actually valid —
    the real request shape an on-call engineer sends when they remember
    "a deployment needs rolling back for further verification" but not the
    exact id. Requires a full new attempt (a different deployment_id) after
    learning why the previous guess failed, carried via the episodic
    memory buffer, not a revision of the same draft — Self-Refine's scope.

    Uses the real generic `reflexion()` loop when a live model is
    configured. Offline (no GOOGLE_API_KEY/GEMINI_API_KEY — checked
    explicitly here because reflexion()'s plain .invoke() degrades to a
    labeled echo rather than raising, so exception-catching can't detect
    "no live model" the way decompose_goal()/tree_of_thoughts.py's
    structured-output paths do), falls back to a deterministic-but-real
    "naive, one-lesson-per-trial" agent: trial 1 always guesses the
    lowest-id candidate without checking status; the grounded environment
    rejects it with a concrete reason; the reflection captures that
    specific reason; trial 2 applies exactly that lesson (filter by
    status) against the real candidate list and succeeds if a valid one
    exists, or the best-scoring trial is returned honestly if none does.
    """
    environment = environment or Environment()
    candidates = _gather_rollback_candidates(deployment_ids)
    task = (
        f"{task_description}\n\nCandidate deployment ids (in the order the engineer "
        f"mentioned them, NOT necessarily in a safe-to-pick order): {deployment_ids}.\n"
        f"Return ONLY a compact JSON object: "
        f'{{"action": "rollback_deployment", "deployment_id": <int>}}'
    )

    if is_live_model_configured():
        return reflexion(task, llm, environment, max_trials=max_trials, memory_size=memory_size)
    return _offline_remediate_incident(task, candidates, environment, max_trials, memory_size)


def _offline_remediate_incident(
    task: str, candidates: list[dict], environment: Environment,
    max_trials: int, memory_size: int,
) -> ReflexionResult:
    memory: list[str] = []
    trials: list[ReflexionTrial] = []
    best_attempt = ""
    best_score = -1.0
    tried_ids: set[int] = set()

    for number in range(1, max_trials + 1):
        # "Naive, one-lesson-per-trial" candidate selection: on trial 1, pick
        # the lowest-id candidate with no filtering. On later trials, apply
        # every lesson learned so far — filter to candidates whose status is
        # Succeeded/InProgress once that lesson has been reflected on, and
        # skip any id already tried.
        learned_status_filter = any("status" in item.lower() for item in memory)
        pool = [c for c in candidates if c["id"] not in tried_ids] or candidates
        if learned_status_filter:
            filtered = [c for c in pool if c["status"] in ("Succeeded", "InProgress")]
            pool = filtered or pool
        choice = min(pool, key=lambda c: c["id"])
        tried_ids.add(choice["id"])

        attempt = json.dumps({"action": "rollback_deployment", "deployment_id": choice["id"]})
        feedback = environment.evaluate(attempt)
        trial = ReflexionTrial(number=number, attempt=attempt, feedback=feedback)
        if feedback.score > best_score:
            best_attempt, best_score = attempt, feedback.score
        if feedback.success:
            trials.append(trial)
            return ReflexionResult(True, attempt, trials, memory[-memory_size:])

        reflection = (
            f"I picked deployment #{choice['id']} (status '{choice['status']}') without "
            f"checking whether its status allows a rollback. External feedback: "
            f"{'; '.join(feedback.details)}. Next trial, I should only propose a "
            f"deployment whose status is Succeeded or InProgress."
        )
        trial.reflection = reflection
        trials.append(trial)
        memory.append(reflection)

    return ReflexionResult(False, best_attempt, trials, memory[-memory_size:])
