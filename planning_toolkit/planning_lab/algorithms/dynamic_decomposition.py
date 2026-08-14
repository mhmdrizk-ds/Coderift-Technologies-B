"""
dynamic_decomposition.py — dynamic / interleaved decomposition for the
Release Readiness & Rollout Planning Agent.

Contrast with decomposition.py (decomposition-first): that module commits
to its full 6-task DAG shape upfront, then runs all four tool tasks in
parallel regardless of what any of them turn out to say, and always runs a
full rank_release_order reasoning pass over all four results. This module
chooses each next step only after observing the real result of the
previous one — check_incidents runs first and alone, and what it reports
genuinely changes what happens next:

  * If it reveals an open high/critical incident for the repository, the
    release is blocked outright — there is no reason to still fetch
    feature-flag state or current deployment status, and no reason to run
    a full multi-factor ranking pass over facts that no longer matter once
    the release can't proceed. This path instead runs a short,
    incident-focused synthesis step directly.
  * If gather_prs then reveals every candidate PR is already not
    release-ready (rejected/failed scan, no incident involved), checking
    feature flags is similarly pointless — nothing is shipping regardless
    of flag state — so that step is skipped too.
  * Only when there's a genuine live question to answer (no incident, at
    least one plausibly-ready PR) does this module do the full gather ->
    rank -> synthesize work decomposition-first always does.

Every fact this module reasons over comes from the same four real MCP tool
handlers decomposition.py uses (imported directly, not re-implemented) and
the same grounded Environment for the terminal synthesis step's Self-Refine
pass. The resulting plan is represented as a validated models.Plan (built
incrementally as a growing list of Task objects, then wrapped into a Plan
at the end for its DAG guarantees and topological_order()/
execution_batches() — Plan itself is an immutable pydantic model, so it
can't literally mutate turn by turn; what's dynamic is which Task objects
get appended and in what order, which depends on real observations, not a
plan committed to before any observation existed).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from mcp_server import db
from mcp_server.auth import Session
from mcp_server.tools_impl.query_tools import (
    handle_check_deployment_status,
    handle_get_pull_request,
    handle_list_active_incidents,
    handle_list_feature_flags,
)

from ..models import Plan, Task
from .environment import Environment
from .instrumentation import CallStats, instrumented
from .self_refine import reflect_and_refine

ARTIFACTS_DIR = Path(__file__).resolve().parents[3] / "artifacts"

SEVERITIES_THAT_BLOCK = {"high", "critical"}

SHORT_CIRCUIT_SYSTEM = """You are writing a short, direct release-blocking notice
for a Coderift Technologies repository. State plainly that the release cannot
proceed, name the incident and its severity, name every candidate pull request by
id, and say the release should be revisited once the incident is resolved. Do not
speculate about the incident's cause or timeline — only state what was given."""

FULL_SYNTHESIS_SYSTEM = """You are ranking pull requests for a Coderift Technologies
production release. Using ONLY the given facts, decide which PRs are release-ready
(Approved/Merged status AND a Passed latest security scan), the release order, and
a BLOCKED list for anything not ready. Reference every candidate PR by id."""


def _lead_session(conn) -> Session:
    session = Session()
    session.login(db.get_engineer_by_id(conn, 4))
    return session


class DynamicRun:
    """Accumulates the incrementally-decided steps and their real outputs
    for one dynamic-decomposition run, then produces a validated Plan plus
    a decomposition.py-compatible trace dict."""

    def __init__(self, goal: str):
        self.goal = goal
        self.tasks: list[Task] = []
        self.outputs: dict[str, str] = {}
        self.decisions: list[dict] = []  # human-readable record of each branch taken

    def add_step(self, task_id: str, instruction: str, depends_on: list[str], output: str) -> None:
        self.tasks.append(Task(id=task_id, instruction=instruction, depends_on=depends_on))
        self.outputs[task_id] = output

    def record_decision(self, after: str, observed: str, chose: str) -> None:
        self.decisions.append({"after_observing": after, "observation_summary": observed, "next_step_chosen": chose})

    def to_plan(self) -> Plan:
        return Plan.model_validate({"goal": self.goal, "tasks": [t.model_dump() for t in self.tasks]})


def run_dynamic_decomposition(
    repository_name: str,
    candidate_pull_request_ids: list[int],
    llm: BaseChatModel,
    max_refine_iterations: int = 2,
) -> dict[str, Any]:
    """Run one dynamic-decomposition pass for the given repository/PRs.
    Returns a dict with plan, outputs, final_output, decisions, and (if the
    llm was wrapped via instrumentation.instrumented()) llm stats — the
    same shape run_and_save() below turns into an artifacts/ trace."""
    goal = f"Prepare repository '{repository_name}' for a production release."
    run = DynamicRun(goal)

    conn = db.get_connection()
    session = _lead_session(conn)
    try:
        # ---- Step 1: ALWAYS check incidents first, alone --------------------
        # This is the core interleaving decision point: everything after this
        # depends on what comes back, so it cannot be batched with the other
        # three the way decomposition-first batches all four upfront.
        incidents_result = json.loads(
            handle_list_active_incidents(conn, session, None, {})["content"][0]["text"]
        )
        matching_incidents = [
            i for i in incidents_result["active_incidents"]
            if i["repository_name"] == repository_name and i["severity"] in SEVERITIES_THAT_BLOCK
        ]
        run.add_step(
            "check_incidents",
            "List open incidents for the repository via list_active_incidents.",
            [],
            json.dumps({"open_incident": matching_incidents[0] if matching_incidents else None}),
        )

        if matching_incidents:
            incident = matching_incidents[0]
            run.record_decision(
                after="check_incidents",
                observed=f"open {incident['severity']} incident #{incident['incident_id']} "
                         f"('{incident['title']}')",
                chose="gather_prs (to name the blocked PRs), then short-circuit directly to "
                      "flag_blocked_by_incident — SKIPPING check_flags and check_deploy_status "
                      "(irrelevant once the release is blocked outright) and SKIPPING the full "
                      "rank_release_order reasoning pass (nothing left to rank).",
            )

            # Still need to know WHICH PRs we're blocking, by id — that's a
            # cheap, deterministic tool call, not a reasoning cost.
            prs = [
                json.loads(handle_get_pull_request(conn, session, None, {"pull_request_id": pid})["content"][0]["text"])
                for pid in candidate_pull_request_ids
            ]
            run.add_step(
                "gather_prs",
                "Fetch every candidate pull request's status and latest security scan via get_pull_request.",
                [],
                json.dumps({"pull_requests": prs}),
            )

            facts = {
                "repository_name": repository_name,
                "incident": incident,
                "candidate_pull_requests": prs,
            }
            prompt = (
                f"Repository: {repository_name}\nOpen blocking incident and candidate PR facts "
                f"(from the real Coderift database): {json.dumps(facts)}\n\nWrite the short "
                f"release-blocking notice."
            )
            response = llm.invoke(
                [("system", SHORT_CIRCUIT_SYSTEM), ("human", prompt)], temperature=0.1,
            )
            draft = response.content.strip()
            run.add_step(
                "flag_blocked_by_incident",
                "Given the open incident, write a short release-blocking notice naming the "
                "blocked PR(s) — no full multi-factor ranking needed.",
                ["check_incidents", "gather_prs"],
                draft,
            )

            refined = reflect_and_refine(goal=goal, draft=draft, llm=llm, max_iterations=max_refine_iterations)
            run.add_step(
                "synthesize_release_plan",
                "Run the blocking notice through a grounded Self-Refine pass before returning it.",
                ["flag_blocked_by_incident"],
                refined.revised,
            )
            final = refined.revised

        else:
            # ---- No blocking incident: proceed, but still interleaved -------
            run.record_decision(
                after="check_incidents",
                observed="no open high/critical incident for this repository",
                chose="gather_prs next, to find out whether any candidate PR is even "
                      "plausibly ready before deciding whether checking feature flags is worth it.",
            )

            prs = [
                json.loads(handle_get_pull_request(conn, session, None, {"pull_request_id": pid})["content"][0]["text"])
                for pid in candidate_pull_request_ids
            ]
            run.add_step(
                "gather_prs",
                "Fetch every candidate pull request's status and latest security scan via get_pull_request.",
                [],
                json.dumps({"pull_requests": prs}),
            )

            any_plausibly_ready = any(
                pr["status"] in ("Approved", "Merged") for pr in prs
            )

            if not any_plausibly_ready:
                run.record_decision(
                    after="gather_prs",
                    observed="no candidate PR is Approved or Merged",
                    chose="skip check_flags and check_deploy_status (nothing is shipping "
                          "regardless of flag/deploy state) and go straight to a short "
                          "not-ready synthesis.",
                )
                facts = {"repository_name": repository_name, "candidate_pull_requests": prs}
                prompt = (
                    f"Repository: {repository_name}\nCandidate PR facts (from the real "
                    f"Coderift database): {json.dumps(facts)}\n\nNone are Approved or Merged. "
                    f"Write a short notice explaining the release cannot proceed and why, "
                    f"naming every candidate PR by id."
                )
                response = llm.invoke(
                    [("system", SHORT_CIRCUIT_SYSTEM), ("human", prompt)], temperature=0.1,
                )
                draft = response.content.strip()
                run.add_step(
                    "flag_not_ready",
                    "Given that no candidate PR is Approved/Merged, write a short not-ready "
                    "notice rather than continuing to gather irrelevant facts.",
                    ["gather_prs"],
                    draft,
                )
                refined = reflect_and_refine(goal=goal, draft=draft, llm=llm, max_iterations=max_refine_iterations)
                run.add_step(
                    "synthesize_release_plan",
                    "Run the not-ready notice through a grounded Self-Refine pass before returning it.",
                    ["flag_not_ready"],
                    refined.revised,
                )
                final = refined.revised

            else:
                run.record_decision(
                    after="gather_prs",
                    observed="at least one candidate PR is Approved or Merged — a genuine "
                             "release decision is still live",
                    chose="check_flags, then check_deploy_status, then run the full ranking "
                          "pass — the same work decomposition-first always does, because here "
                          "there is genuinely nothing to short-circuit.",
                )

                flags_result = handle_list_feature_flags(
                    conn, session, None, {"repository_name": repository_name}
                )["content"][0]["text"]
                run.add_step(
                    "check_flags",
                    "List feature flags for the repository via list_feature_flags.",
                    ["gather_prs"],
                    flags_result,
                )

                deploy_status_result = handle_check_deployment_status(
                    conn, session, None,
                    {"repository_name": repository_name, "environment_name": "production"},
                )["content"][0]["text"]
                run.add_step(
                    "check_deploy_status",
                    "Check the current production deployment status via check_deployment_status.",
                    ["gather_prs"],
                    deploy_status_result,
                )

                facts = {
                    "repository_name": repository_name,
                    "gather_prs": {"pull_requests": prs},
                    "check_incidents": {"open_incident": None},
                    "check_flags": json.loads(flags_result),
                    "check_deploy_status": json.loads(deploy_status_result),
                }
                question = (
                    f"Repository: {repository_name}\nGathered facts (from the real Coderift "
                    f"database): {json.dumps(facts)}\n\nDecide which PRs are release-ready "
                    f"(Approved/Merged status AND a Passed latest security scan), whether "
                    f"an open incident should pause the release, and produce the release "
                    f"order plus a BLOCKED list."
                )
                response = llm.invoke(
                    [("system", FULL_SYNTHESIS_SYSTEM), ("human", question)], temperature=0.2,
                )
                draft = response.content.strip()
                run.add_step(
                    "rank_release_order",
                    "Given the gathered facts, decide the release order and flag anything not ready.",
                    ["gather_prs", "check_incidents", "check_flags", "check_deploy_status"],
                    draft,
                )

                refined = reflect_and_refine(goal=goal, draft=draft, llm=llm, max_iterations=max_refine_iterations)
                run.add_step(
                    "synthesize_release_plan",
                    "Write the final release plan and run it through a grounded Self-Refine pass before returning it.",
                    ["rank_release_order"],
                    refined.revised,
                )
                final = refined.revised
    finally:
        conn.close()

    plan = run.to_plan()
    return {
        "plan": plan,
        "outputs": run.outputs,
        "decisions": run.decisions,
        "final_output": final,
    }


def run_and_save(
    repository_name: str,
    candidate_pull_request_ids: list[int],
    llm: BaseChatModel | None = None,
    stats: CallStats | None = None,
) -> dict[str, Any]:
    """Run dynamic decomposition and write an artifacts/ trace in the same
    format demo_task1.py uses for decomposition-first, with
    method='dynamic_decomposition' so the two are diffable. If llm/stats
    are not given, wraps a fresh CoderiftChatModel with instrumentation so
    call/token/latency numbers are always captured."""
    from planning_toolkit.model_provider import CoderiftChatModel

    if llm is None or stats is None:
        llm, stats = instrumented(CoderiftChatModel())

    start = time.perf_counter()
    result = run_dynamic_decomposition(repository_name, candidate_pull_request_ids, llm)
    wall_seconds = time.perf_counter() - start

    plan = result["plan"]
    trace = {
        "agent": "release_readiness_planning",
        "method": "dynamic_decomposition",
        "repository_name": repository_name,
        "candidate_pull_request_ids": candidate_pull_request_ids,
        "plan": plan.model_dump(),
        "topological_order": plan.topological_order(),
        "execution_batches": plan.execution_batches(),
        "node_outputs": result["outputs"],
        "decisions": result["decisions"],
        "final_output": result["final_output"],
        "llm_stats": stats.summary(),
        "wall_clock_seconds": round(wall_seconds, 4),
        "timestamp": time.time(),
    }

    ARTIFACTS_DIR.mkdir(exist_ok=True)
    path = ARTIFACTS_DIR / f"dynamic_decomposition_{repository_name}_{int(time.time())}.json"
    path.write_text(json.dumps(trace, indent=2, default=str))
    trace["_path"] = str(path)
    return trace


def build_dynamic_plan(
    repository_name: str,
    candidate_pull_request_ids: list[int],
    llm: BaseChatModel,
) -> Plan:
    """Handoff-contract entry point for Task 3 (LATS + Routing + Reflexion).

    Runs run_dynamic_decomposition end to end and returns ONLY the
    validated Plan — no outputs dict, no decisions log, no trace-writing
    concerns. This is the one function Task 3's routing logic and LATS
    should import and call; it should never need to call
    run_dynamic_decomposition directly or unpack its result dict itself.

    Returned Plan.tasks each expose:
      - task.id            : str  — stable id. One of "check_incidents"
                              (always first), "gather_prs",
                              "flag_blocked_by_incident" OR
                              "check_flags"/"check_deploy_status"/
                              "rank_release_order" (mutually exclusive
                              branches — see dynamic_decomposition.py's
                              module docstring for which branch fires
                              when), and always exactly one terminal
                              "synthesize_release_plan".
      - task.instruction    : str  — natural-language description of what
                              this node does; routing logic can pattern-
                              match on this the same way it would on
                              decomposition.py's node instructions.
      - task.depends_on     : list[str] — same dependency semantics as
                              decomposition-first's Plan; use
                              plan.topological_order() or
                              plan.execution_batches() for scheduling,
                              plan.terminal_tasks() to find the synthesis
                              node (guaranteed exactly one — see
                              test_dynamic_decomposition_acyclicity.py).
                              Plan.task(task_id) looks up a single Task by
                              id.

    Grounded scoring for whatever LATS builds on top of this plan should
    go through Environment().evaluate(...) (environment.py) — the same
    grounded class this module already uses via reflect_and_refine and
    tree_of_thoughts.py already uses via score_strategy. Do not add a
    second scoring path.

    Example:
        from planning_toolkit.model_provider import CoderiftChatModel
        from planning_toolkit.planning_lab.algorithms.dynamic_decomposition import (
            build_dynamic_plan,
        )

        plan = build_dynamic_plan(
            repository_name="billing-worker",
            candidate_pull_request_ids=[5],
            llm=CoderiftChatModel(),
        )
        for task_id in plan.topological_order():
            task = plan.task(task_id)
            ...  # Task 3's router decides PS / ToT / LATS per task here
    """
    result = run_dynamic_decomposition(
        repository_name=repository_name,
        candidate_pull_request_ids=candidate_pull_request_ids,
        llm=llm,
    )
    return result["plan"]


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    for repo, pr_ids in (("billing-worker", [5]), ("payments-service", [1])):
        print(f"\n{'=' * 70}\nDynamic decomposition: {repo} / {pr_ids}\n{'=' * 70}")
        trace = run_and_save(repo, pr_ids)
        print(f"Steps taken ({len(trace['topological_order'])}): {trace['topological_order']}")
        print(f"Decisions: {json.dumps(trace['decisions'], indent=2)}")
        print(f"\nFINAL:\n{trace['final_output']}")
        print(f"\nLLM stats: {trace['llm_stats']}")
        print(f"Trace saved to {trace['_path']}")
