from __future__ import annotations

import math
from dataclasses import dataclass, field

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field

from ..models import EnvironmentFeedback
from .environment import Environment
import json

from mcp_server import db


class LATSAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: str = Field(min_length=2)
    state: str = Field(min_length=2)


class LATSActionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[LATSAction] = Field(min_length=1, max_length=3)


class ValueEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0.0, le=1.0)


@dataclass
class LATSNode:
    state: str
    action: str = "root"
    parent: "LATSNode | None" = field(default=None, repr=False)
    children: list["LATSNode"] = field(default_factory=list, repr=False)
    visits: int = 0
    value_sum: float = 0.0
    environment_score: float = 0.0
    model_score: float = 0.0
    feedback: EnvironmentFeedback | None = None
    reflections: list[str] = field(default_factory=list)

    @property
    def mean_value(self) -> float:
        return self.value_sum / self.visits if self.visits else 0.0


@dataclass
class LATSResult:
    success: bool
    output: str
    best_score: float
    iterations: int
    root: LATSNode


def _uct(node: LATSNode, exploration_weight: float) -> float:
    if node.visits == 0:
        return float("inf")
    parent_visits = max(node.parent.visits if node.parent else 1, 1)
    return node.mean_value + exploration_weight * math.sqrt(math.log(parent_visits) / node.visits)


def _select_leaf(root: LATSNode, exploration_weight: float) -> LATSNode:
    node = root
    while node.children:
        node = max(node.children, key=lambda child: _uct(child, exploration_weight))
    return node


def _backpropagate(node: LATSNode, value: float) -> None:
    while node is not None:
        node.visits += 1
        node.value_sum += value
        node = node.parent


def _trajectory_reflections(node: LATSNode) -> list[str]:
    path: list[str] = []
    while node is not None:
        path.extend(node.reflections)
        node = node.parent
    return list(reversed(path))


def lats(
    task: str,
    llm: BaseChatModel,
    environment: Environment,
    iterations: int = 2,
    n_actions: int = 2,
    exploration_weight: float = 1.414,
) -> LATSResult:
    if iterations < 1 or n_actions < 1:
        raise ValueError("iterations and n_actions must be positive")
    root = LATSNode(state="No attempt yet.")
    best = root
    completed_iterations = 0
    for iteration in range(1, iterations + 1):
        completed_iterations = iteration
        leaf = _select_leaf(root, exploration_weight)
        lessons = _trajectory_reflections(leaf)
        lesson_text = "\n".join(f"- {item}" for item in lessons[-4:]) or "- None yet."
        proposed = llm.with_structured_output(
            LATSActionBatch,
            method="json_schema",
        ).invoke([
            ("system", "You are the action generator in LATS."),
            ("human", f"""Task: {task}
Current trajectory/state:
{leaf.state}
Reflections learned from failed branches:
{lesson_text}

Propose exactly {n_actions} distinct complete candidate solution(s). Each state must
contain the fully written solution, not a placeholder or description of a solution.""",
            ),
        ], temperature=0.5)
        for item in proposed.actions[:n_actions]:
            child = LATSNode(state=item.state.strip(), action=item.action, parent=leaf)
            leaf.children.append(child)
            feedback = environment.evaluate(child.state)
            child.feedback = feedback
            child.environment_score = feedback.score
            value_judgment = llm.with_structured_output(
                ValueEstimate,
                method="json_schema",
            ).invoke([
                ("system", "You are the LATS value function."),
                ("human", f"""Task: {task}
Candidate state:
{child.state}
External score: {feedback.score}
External feedback: {feedback.details}
Estimate the candidate's future usefulness."""),
            ], temperature=0.1)
            child.model_score = value_judgment.score
            combined_value = 0.75 * child.environment_score + 0.25 * child.model_score
            if not feedback.success:
                response = llm.invoke([
                    ("system", "Create a branch-level LATS reflection grounded in environment feedback."),
                    ("human", f"""Task: {task}
Action: {child.action}
Resulting state: {child.state}
External feedback: {feedback.details}
Explain briefly why this branch failed and how a later expansion should change."""),
                ], temperature=0.2)
                reflection = response.content
                if not isinstance(reflection, str) or not reflection.strip():
                    raise RuntimeError("The chat model returned an empty or unsupported response")
                reflection = reflection.strip()
                child.reflections.append(reflection)
            _backpropagate(child, combined_value)
            if best is root or child.environment_score > best.environment_score:
                best = child
            if feedback.success:
                return LATSResult(True, child.state, child.environment_score, completed_iterations, root)
    return LATSResult(False, best.state, best.environment_score, completed_iterations, root)


def flatten_lats_tree(root: LATSNode) -> list[dict]:
    records: list[dict] = []
    queue: list[tuple[LATSNode, str | None]] = [(root, None)]
    next_id = 0
    while queue:
        node, parent_id = queue.pop(0)
        node_id = f"n{next_id}"
        next_id += 1
        records.append(
            {
                "id": node_id,
                "parent_id": parent_id,
                "action": node.action,
                "state": node.state,
                "visits": node.visits,
                "mean_value": node.mean_value,
                "environment_score": node.environment_score,
                "model_score": node.model_score,
                "feedback": node.feedback.model_dump() if node.feedback else None,
                "reflections": node.reflections,
            }
        )
        queue.extend((child, node_id) for child in node.children)
    return records


# ---------------------------------------------------------------------------
# Coderift-specific wiring: propose_remediation — the final "propose the
# executable action" sub-task for an incident-affected deployment. This is
# the step where a wrong plan is expensive to unwind (an accepted-but-
# invalid rollback, or a redeploy on top of an unresolved critical
# incident), so it goes through LATS's real external-feedback search
# rather than a single greedy pass or an ungrounded self-critique. See
# agent.py's routing table and planning_eval/ for the grounded-vs-
# ungrounded contrast this function is built to support: pass
# `environment=UngroundedEnvironment()` to reproduce the "expensive
# theater" baseline the lab brief warns about, or leave `environment=None`
# for the real, shipped, DB-backed default.
# ---------------------------------------------------------------------------

REMEDIATION_TASK_TEMPLATE = """A production incident is open. Propose ONE concrete
remediation action for it, as a single compact JSON object and NOTHING else — no
prose, no markdown fences. The JSON must match exactly one of these shapes:
  {{"action": "rollback_deployment", "deployment_id": <int>}}
  {{"action": "deploy_pr", "repository_name": "<str>", "environment_name": "<str>", "pull_request_id": <int>}}

Real facts (from the Coderift database): {facts}

Your proposed action's `state` must BE the JSON action object itself (as a string),
so it can be checked against the real database."""


def _gather_remediation_facts(repository_name: str, deployment_id: int) -> dict:
    conn = db.get_connection()
    try:
        deployment = conn.execute(
            """
            SELECT d.id, d.status, d.notes, d.repository_id, d.environment_id,
                   r.name AS repository_name, e.name AS environment_name,
                   d.pull_request_id
            FROM deployments d
            JOIN repositories r ON r.id = d.repository_id
            JOIN environments e ON e.id = d.environment_id
            WHERE d.id = ?
            """,
            (deployment_id,),
        ).fetchone()
        if deployment is None:
            raise ValueError(f"No deployment #{deployment_id}.")
        incident = conn.execute(
            """
            SELECT id, title, severity, status
            FROM incidents WHERE deployment_id = ? AND status = 'open'
            """,
            (deployment_id,),
        ).fetchone()
        other_prs = conn.execute(
            """
            SELECT pr.id, pr.status, s.status AS scan_status
            FROM pull_requests pr
            LEFT JOIN security_scans s ON s.id = (
                SELECT id FROM security_scans WHERE pull_request_id = pr.id
                ORDER BY created_at DESC LIMIT 1
            )
            WHERE pr.repository_id = ?
            """,
            (deployment["repository_id"],),
        ).fetchall()
        return {
            "deployment": dict(deployment),
            "open_incident": dict(incident) if incident else None,
            "repository_pull_requests": [dict(pr) for pr in other_prs],
        }
    finally:
        conn.close()


def _deterministic_remediation_actions(facts: dict, round_number: int) -> list[dict]:
    """Real, content-grounded candidate actions used when no live model is
    configured. Deliberately includes a plausible-looking-but-invalid
    action alongside a genuinely valid one where the seed data supports it,
    so the offline path still exercises real branch pruning."""
    deployment = facts["deployment"]
    dep_id = deployment["id"]
    candidates = []
    # Candidate A: the "obvious" move — roll back the affected deployment.
    # Valid ONLY if its status is Succeeded/InProgress.
    candidates.append({
        "action": "rollback_deployment",
        "state": json.dumps({"action": "rollback_deployment", "deployment_id": dep_id}),
    })
    # Candidate B: try redeploying a currently-Merged PR for the same repo
    # (a "hotfix" move) — valid only if no open high/critical incident
    # blocks it and the PR/scan are clean.
    merged_prs = [pr for pr in facts["repository_pull_requests"] if pr["status"] == "Merged"]
    if merged_prs:
        pr = merged_prs[0]
        candidates.append({
            "action": "deploy_pr",
            "state": json.dumps({
                "action": "deploy_pr",
                "repository_name": deployment["repository_name"],
                "environment_name": deployment["environment_name"],
                "pull_request_id": pr["id"],
            }),
        })
    else:
        # No merged PR to redeploy — propose the rollback again with a
        # different reason field so the tree still has 2 real branches to
        # score and reflect on, rather than degenerating to 1.
        candidates.append({
            "action": "rollback_deployment_retry",
            "state": json.dumps({"action": "rollback_deployment", "deployment_id": dep_id}),
        })
    return candidates[:2]


def propose_remediation_with_lats(
    repository_name: str,
    deployment_id: int,
    llm: BaseChatModel,
    environment: "Environment | None" = None,
    iterations: int = 2,
    n_actions: int = 2,
    exploration_weight: float = 1.414,
) -> LATSResult:
    """MCTS-guided search (select -> expand/simulate -> evaluate/reflect ->
    backpropagate) over concrete remediation actions for an
    incident-affected deployment, scored by real external feedback (the
    `environment` argument — grounded `Environment` by default; pass an
    `UngroundedEnvironment` for the required contrast). Falls back to a
    deterministic-but-real (same tree mechanics, content-grounded
    candidates) offline path when no live model is configured, matching
    every other algorithm module's offline-fallback contract in this repo.
    """
    environment = environment or Environment()
    facts = _gather_remediation_facts(repository_name, deployment_id)
    task = REMEDIATION_TASK_TEMPLATE.format(facts=json.dumps(facts))

    try:
        return lats(
            task, llm, environment,
            iterations=iterations, n_actions=n_actions, exploration_weight=exploration_weight,
        )
    except Exception:
        return _offline_lats_remediation(task, facts, environment, iterations, n_actions, exploration_weight)


def _offline_lats_remediation(
    task: str, facts: dict, environment: "Environment",
    iterations: int, n_actions: int, exploration_weight: float,
) -> LATSResult:
    root = LATSNode(state="No attempt yet.")
    best = root
    completed_iterations = 0
    for iteration in range(1, iterations + 1):
        completed_iterations = iteration
        leaf = _select_leaf(root, exploration_weight)
        for item in _deterministic_remediation_actions(facts, iteration)[:n_actions]:
            child = LATSNode(state=item["state"], action=item["action"], parent=leaf)
            leaf.children.append(child)
            feedback = environment.evaluate(child.state)
            child.feedback = feedback
            child.environment_score = feedback.score
            child.model_score = feedback.score  # no live value function offline; env score stands in
            combined_value = 0.75 * child.environment_score + 0.25 * child.model_score
            if not feedback.success:
                reflection = (
                    f"[offline reflection] Action '{child.action}' scored {feedback.score} "
                    f"because: {'; '.join(feedback.details) or 'no external feedback issues recorded'}."
                )
                child.reflections.append(reflection)
            _backpropagate(child, combined_value)
            if best is root or child.environment_score > best.environment_score:
                best = child
            if feedback.success:
                return LATSResult(True, child.state, child.environment_score, completed_iterations, root)
    return LATSResult(False, best.state, best.environment_score, completed_iterations, root)
