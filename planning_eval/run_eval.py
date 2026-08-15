"""
planning_eval/run_eval.py — runs EVERY required method against EVERY
applicable case in test_suite.json (decomposition-first vs. dynamic,
Plan-and-Solve vs. Tree of Thoughts, grounded vs. ungrounded LATS,
Reflexion), measures real accuracy/task-success, LLM calls, tokens, and
latency via instrumentation.py's CountingChatModel, writes one JSON trace
per run to artifacts/ (extending the toolkit's existing trace format —
see planning_toolkit/artifacts/ for the format this reuses), and prints
the comparison table this lab's README embeds.

Run: python3 -m planning_eval.run_eval
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from planning_toolkit.model_provider import CoderiftChatModel
from planning_toolkit.planning_lab.algorithms.decomposition import decompose_goal, execute_plan, final_output
from planning_toolkit.planning_lab.algorithms.dynamic_decomposition import run_dynamic_decomposition
from planning_toolkit.planning_lab.algorithms.instrumentation import instrumented
from planning_toolkit.planning_lab.algorithms.plan_and_solve import plan_and_solve
from planning_toolkit.planning_lab.algorithms.self_refine import reflect_and_refine
from planning_toolkit.planning_lab.algorithms.tree_of_thoughts import rank_release_order_with_tree_of_thoughts
from planning_toolkit.planning_lab.algorithms.lats import propose_remediation_with_lats
from planning_toolkit.planning_lab.algorithms.reflexion import remediate_incident_with_reflexion
from planning_toolkit.planning_lab.algorithms.environment import Environment
from planning_toolkit.planning_lab.algorithms.environment_ungrounded import UngroundedEnvironment

SUITE_PATH = Path(__file__).resolve().parent / "test_suite.json"
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
ESTIMATED_COST_PER_1K_TOKENS = 0.002  # placeholder rate, stated as such in the table


def _load_suite() -> dict:
    with open(SUITE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_trace(name: str, payload: dict) -> Path:
    ARTIFACTS_DIR.mkdir(exist_ok=True)
    path = ARTIFACTS_DIR / f"{name}_{int(time.time() * 1000)}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    return path


def _row(method: str, case_id: str, success: bool, stats, latency: float) -> dict:
    summary = stats.summary()
    total_tokens = summary["total_tokens"]
    return {
        "method": method,
        "case": case_id,
        "task_success": success,
        "llm_calls": summary["llm_call_count"],
        "total_tokens": total_tokens,
        "latency_seconds": round(latency, 4),
        "est_cost_usd": round(total_tokens / 1000 * ESTIMATED_COST_PER_1K_TOKENS, 5),
    }


# ---------------------------------------------------------------------------
# 1. Decomposition-first vs. dynamic decomposition
# ---------------------------------------------------------------------------
def run_decomposition_cases(suite: dict) -> list[dict]:
    rows = []
    for case in suite["decomposition_cases"]:
        for method in ("decomposition_first", "dynamic_decomposition"):
            llm, stats = instrumented(CoderiftChatModel())
            start = time.perf_counter()
            try:
                if method == "decomposition_first":
                    plan = decompose_goal(case["goal"], llm, repository_name=case["repository_name"])
                    outputs = execute_plan(
                        plan, llm, repository_name=case["repository_name"],
                        candidate_pull_request_ids=case["candidate_pull_request_ids"],
                    )
                    output_text = final_output(plan, outputs)
                    trace = {"plan_steps": plan.topological_order(), "final_output": output_text}
                else:
                    result = run_dynamic_decomposition(
                        repository_name=case["repository_name"],
                        candidate_pull_request_ids=case["candidate_pull_request_ids"],
                        llm=llm,
                    )
                    output_text = result["final_output"]
                    trace = {
                        "plan_steps": result["plan"].topological_order(),
                        "decisions": result["decisions"],
                        "final_output": output_text,
                    }
                success = bool(output_text) and "error" not in output_text.lower()
            except Exception as exc:
                success = False
                trace = {"error": str(exc)}
            latency = time.perf_counter() - start
            _save_trace(f"decomposition__{case['id']}__{method}", {"case": case, "method": method, "trace": trace})
            rows.append(_row(method, case["id"], success, stats, latency))
    return rows


# ---------------------------------------------------------------------------
# 2. Plan-and-Solve vs. Tree of Thoughts (ranking sub-task)
# ---------------------------------------------------------------------------
def run_ranking_cases(suite: dict) -> list[dict]:
    rows = []
    for case in suite["ranking_cases"]:
        repo, pr_ids = case["repository_name"], case["candidate_pull_request_ids"]

        # Plan-and-Solve, run on every case for comparison even where ToT is
        # the routed default — the table needs both, not just the winner.
        llm, stats = instrumented(CoderiftChatModel())
        start = time.perf_counter()
        pr_mentions = ", ".join(f"#{pid}" for pid in pr_ids)
        goal = f"Rank release order for repository '{repo}', candidate PRs {pr_mentions}."
        try:
            draft = plan_and_solve(goal, llm)
            refined = reflect_and_refine(goal=goal, draft=draft, llm=llm, repository_name=repo,
                                          candidate_pull_request_ids=pr_ids)
            success = not refined.grounded_issues
            trace = {"draft": draft, "revised": refined.revised, "grounded_issues": refined.grounded_issues}
        except Exception as exc:
            success, trace = False, {"error": str(exc)}
        latency = time.perf_counter() - start
        _save_trace(f"ranking__{case['id']}__plan_and_solve", {"case": case, "trace": trace})
        rows.append(_row("plan_and_solve", case["id"], success, stats, latency))

        # Tree of Thoughts — only applicable when the case actually contains
        # the ambiguous Approved+Pending PR; rank_release_order_with_tree_of_
        # thoughts() raises ValueError by design otherwise (see routing
        # rationale in agent.py). Running it anyway would just record "N/A,
        # not applicable" as a failure, which misrepresents the method.
        if case.get("expects_method") == "tree_of_thoughts":
            llm2, stats2 = instrumented(CoderiftChatModel())
            start = time.perf_counter()
            try:
                result = rank_release_order_with_tree_of_thoughts(repo, pr_ids, llm2)
                success = bool(result["winner_grounded_success"])
                trace = {
                    "winner": result["winner"].state, "model_self_score": result["winner_model_self_score"],
                    "grounded_score": result["winner_grounded_score"], "search_trace": result["search_trace"],
                }
            except Exception as exc:
                success, trace = False, {"error": str(exc)}
            latency = time.perf_counter() - start
            _save_trace(f"ranking__{case['id']}__tree_of_thoughts", {"case": case, "trace": trace})
            rows.append(_row("tree_of_thoughts", case["id"], success, stats2, latency))
        else:
            rows.append({
                "method": "tree_of_thoughts", "case": case["id"], "task_success": "N/A (not ambiguous)",
                "llm_calls": 0, "total_tokens": 0, "latency_seconds": 0.0, "est_cost_usd": 0.0,
            })
    return rows


# ---------------------------------------------------------------------------
# 3. Grounded vs. ungrounded LATS (remediation sub-task)
# ---------------------------------------------------------------------------
def run_remediation_cases(suite: dict) -> list[dict]:
    rows = []
    for case in suite["remediation_cases"]:
        repo, dep_id = case["repository_name"], case["deployment_id"]
        for env_label, env_factory in (
            ("lats_grounded", Environment),
            ("lats_ungrounded", lambda: UngroundedEnvironment(seed=1)),
        ):
            llm, stats = instrumented(CoderiftChatModel())
            start = time.perf_counter()
            try:
                result = propose_remediation_with_lats(repo, dep_id, llm, environment=env_factory())
                success = result.success
                trace = {"output": result.output, "best_score": result.best_score,
                          "iterations": result.iterations}
            except Exception as exc:
                success, trace = False, {"error": str(exc)}
            latency = time.perf_counter() - start
            _save_trace(f"remediation__{case['id']}__{env_label}", {"case": case, "trace": trace})
            rows.append(_row(env_label, case["id"], success, stats, latency))
    return rows


# ---------------------------------------------------------------------------
# 4. Reflexion (cross-trial remediation)
# ---------------------------------------------------------------------------
def run_reflexion_cases(suite: dict) -> list[dict]:
    rows = []
    for case in suite["reflexion_cases"]:
        llm, stats = instrumented(CoderiftChatModel())
        start = time.perf_counter()
        try:
            result = remediate_incident_with_reflexion(
                case["task_description"], deployment_ids=case["deployment_ids"], llm=llm,
                max_trials=case.get("max_trials", 3),
            )
            success = result.success
            trace = {
                "output": result.output, "trial_count": len(result.trials),
                "trials": [
                    {"number": t.number, "attempt": t.attempt, "success": t.feedback.success,
                     "score": t.feedback.score, "reflection": t.reflection}
                    for t in result.trials
                ],
            }
        except Exception as exc:
            success, trace = False, {"error": str(exc)}
        latency = time.perf_counter() - start
        _save_trace(f"reflexion__{case['id']}", {"case": case, "trace": trace})
        rows.append(_row("reflexion", case["id"], success, stats, latency))
    return rows


def render_table(rows: list[dict]) -> str:
    header = f"{'method':<20} {'case':<32} {'success':<8} {'calls':<6} {'tokens':<8} {'latency(s)':<11} {'est_cost($)':<11}"
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r['method']:<20} {r['case']:<32} {str(r['task_success']):<8} {r['llm_calls']:<6} "
            f"{r['total_tokens']:<8} {r['latency_seconds']:<11} {r['est_cost_usd']:<11}"
        )
    return "\n".join(lines)


def main() -> None:
    suite = _load_suite()
    all_rows: list[dict] = []
    all_rows += run_decomposition_cases(suite)
    all_rows += run_ranking_cases(suite)
    all_rows += run_remediation_cases(suite)
    all_rows += run_reflexion_cases(suite)

    table = render_table(all_rows)
    print(table)

    out_path = Path(__file__).resolve().parent / "comparison_table.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Planning & Decomposition — Full Comparison Table\n\n")
        f.write("Generated by `python3 -m planning_eval.run_eval` against the fixed suite in "
                "`test_suite.json`. Cost is an illustrative flat estimate "
                f"(${ESTIMATED_COST_PER_1K_TOKENS}/1K tokens), not a real API bill — the real "
                "signal in this table is calls/tokens/latency/success, which don't depend on "
                "which model or pricing tier is behind `llm`.\n\n")
        f.write("```\n" + table + "\n```\n")
    print(f"\nWrote {out_path}")
    print(f"Wrote {len(list(ARTIFACTS_DIR.glob('*.json')))} trace files to {ARTIFACTS_DIR}")


if __name__ == "__main__":
    main()
