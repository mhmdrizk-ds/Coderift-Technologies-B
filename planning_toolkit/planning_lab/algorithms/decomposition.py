from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, ConfigDict

from mcp_server import db
from mcp_server.auth import Session
from mcp_server.tools_impl.query_tools import (
    handle_check_deployment_status,
    handle_get_pull_request,
    handle_list_active_incidents,
    handle_list_feature_flags,
)

from ..models import Plan
from .plan_and_solve import plan_and_solve
from .self_refine import reflect_and_refine


PLANNER_SYSTEM = """You are a careful task-decomposition planner for a software
release-readiness agent at Coderift Technologies. Produce a small executable DAG,
not a prose checklist. Available deterministic tool tasks: gather_prs,
check_incidents, check_flags, check_deploy_status — each maps to a real MCP tool
call and takes no reasoning. The DAG must also include exactly one reasoning task
(id: rank_release_order) depending on all four tool tasks, and exactly one
terminal synthesis task (id: synthesize_release_plan) depending only on
rank_release_order."""


class PlannedTask(BaseModel):

    model_config = ConfigDict(extra="forbid")

    id: str
    instruction: str
    depends_on: list[str]


class GeneratedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str
    tasks: list[PlannedTask]

_FIXED_TASKS = [
    {"id": "gather_prs", "instruction": "Fetch every candidate pull request's status "
        "and latest security scan via get_pull_request.", "depends_on": []},
    {"id": "check_incidents", "instruction": "List open incidents for the repository "
        "via list_active_incidents.", "depends_on": []},
    {"id": "check_flags", "instruction": "List feature flags for the repository via "
        "list_feature_flags.", "depends_on": []},
    {"id": "check_deploy_status", "instruction": "Check the current production "
        "deployment status via check_deployment_status.", "depends_on": []},
    {"id": "rank_release_order", "instruction": "Given the gathered facts, decide the "
        "release order and flag anything not ready.",
     "depends_on": ["gather_prs", "check_incidents", "check_flags", "check_deploy_status"]},
    {"id": "synthesize_release_plan", "instruction": "Write the final release plan and "
        "run it through a grounded Self-Refine pass before returning it.",
     "depends_on": ["rank_release_order"]},
]

TOOL_TASK_IDS = {"gather_prs", "check_incidents", "check_flags", "check_deploy_status"}


def _fixed_plan(repository_name: str) -> Plan:
    return Plan.model_validate({
        "goal": f"Prepare repository '{repository_name}' for a production release.",
        "tasks": _FIXED_TASKS,
    })


def decompose_goal(goal: str, llm: BaseChatModel, repository_name: str | None = None) -> Plan:
    repository_name = repository_name or "the target repository"
    try:
        generated = llm.with_structured_output(
            GeneratedPlan,
            method="json_schema",
        ).invoke([
            ("system", PLANNER_SYSTEM),
            ("human", f"""Goal: {goal!r}. Use the exact task ids listed in the system
prompt — this plan will be executed against real tools that only recognize those ids."""),
        ], temperature=0.1)
        payload = generated.model_dump()
        payload["goal"] = goal
        return Plan.model_validate(payload)
    except Exception:
        return _fixed_plan(repository_name)


def _lead_session(conn) -> Session:
    session = Session()
    session.login(db.get_engineer_by_id(conn, 4))
    return session


def _run_tool_task(
    task_id: str, conn, session, repository_name: str, candidate_pull_request_ids: list[int],
) -> str:
    if task_id == "gather_prs":
        prs = [
            json.loads(handle_get_pull_request(conn, session, None, {"pull_request_id": pid})["content"][0]["text"])
            for pid in candidate_pull_request_ids
        ]
        return json.dumps({"pull_requests": prs})
    if task_id == "check_incidents":
        result = json.loads(handle_list_active_incidents(conn, session, None, {})["content"][0]["text"])
        matching = [i for i in result["active_incidents"] if i["repository_name"] == repository_name]
        return json.dumps({"open_incident": matching[0] if matching else None})
    if task_id == "check_flags":
        return handle_list_feature_flags(
            conn, session, None, {"repository_name": repository_name}
        )["content"][0]["text"]
    if task_id == "check_deploy_status":
        return handle_check_deployment_status(
            conn, session, None,
            {"repository_name": repository_name, "environment_name": "production"},
        )["content"][0]["text"]
    raise ValueError(f"'{task_id}' is not a registered tool task")


def execute_plan(
    plan: Plan, llm: BaseChatModel,
    repository_name: str | None = None,
    candidate_pull_request_ids: list[int] | None = None,
    max_workers: int = 4,
) -> dict[str, str]:
    domain_mode = repository_name is not None and candidate_pull_request_ids is not None
    outputs: dict[str, str] = {}
    conn = None
    session = None
    try:
        for batch in plan.execution_batches():
            reasoning_jobs: dict[str, callable] = {}
            for task_id in batch:
                if domain_mode and task_id in TOOL_TASK_IDS:
                    if conn is None:
                        conn = db.get_connection()
                        session = _lead_session(conn)
                    outputs[task_id] = _run_tool_task(
                        task_id, conn, session, repository_name, candidate_pull_request_ids,
                    )
                elif domain_mode and task_id == "rank_release_order":
                    facts = {
                        "repository_name": repository_name,
                        "gather_prs": json.loads(outputs["gather_prs"]),
                        "check_incidents": json.loads(outputs["check_incidents"]),
                        "check_flags": json.loads(outputs["check_flags"]),
                        "check_deploy_status": json.loads(outputs["check_deploy_status"]),
                    }
                    question = (
                        f"Repository: {repository_name}\nGathered facts (from the real "
                        f"Coderift database): {json.dumps(facts)}\n\nDecide which PRs are "
                        f"release-ready (Approved/Merged status AND a Passed latest "
                        f"security scan), whether an open incident should pause the "
                        f"release, and produce the release order plus a BLOCKED list."
                    )
                    reasoning_jobs[task_id] = lambda q=question: plan_and_solve(q, llm)
                elif domain_mode and task_id == "synthesize_release_plan":
                    draft = outputs["rank_release_order"]
                    reasoning_jobs[task_id] = lambda d=draft: reflect_and_refine(
                    goal=plan.goal,
                    draft=d,
                    llm=llm,
                ).revised
                else:
                    task = plan.task(task_id)
                    context = "\n\n".join(
                        f"OUTPUT FROM {dependency}:\n{outputs[dependency]}"
                        for dependency in task.depends_on
                    ) or "No prerequisite outputs."
                    prompt = f"""Overall goal: {plan.goal}
                        Current task: {task.instruction}
                        Prerequisite outputs:
                        {context}
                        Complete only the current task. Be concrete and concise. Do not
                        invent sources."""
                    reasoning_jobs[task_id] = lambda p=prompt: llm.invoke(
                        [("system", "You execute one node in a validated task DAG."),
                         ("human", p)], temperature=0.2,
                    ).content.strip()
            if not reasoning_jobs:
                continue
            with ThreadPoolExecutor(max_workers=min(max_workers, len(reasoning_jobs))) as pool:
                futures = {pool.submit(job): task_id for task_id, job in reasoning_jobs.items()}
                for future in as_completed(futures):
                    content = future.result()
                    if not isinstance(content, str) or not content.strip():
                        raise RuntimeError("The chat model returned an empty or unsupported response")
                    outputs[futures[future]] = content.strip()
    finally:
        if conn is not None:
            conn.close()
    return outputs


def final_output(plan: Plan, outputs: dict[str, str]) -> str:
    terminals = plan.terminal_tasks()
    if len(terminals) != 1:
        raise ValueError(f"Expected exactly one terminal synthesis task, found {terminals}")
    return outputs[terminals[0]]
    
