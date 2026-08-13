"""
mini_suite.py — the fixed mini test suite for the dynamic-decomposition vs.
decomposition-first divergence concern.

compare_divergence.py alone exercises ONE case (billing-worker/[5]) — real
and correctly measured, but one case can't by itself distinguish "dynamic
decomposition genuinely reacts to a blocking signal" from "dynamic
decomposition happens to look different on this one row of seed data."
This file runs BOTH methods against three real cases with three different
signals, so the divergence claim rests on more than a single data point:

  1. payments-service / [1] — the clean case. PR #1 is Approved+Passed, no
     open incident on payments-service. Expected: no short-circuit; both
     methods run the full path. This is the control case.
  2. billing-worker / [5] — the incident case (same as
     compare_divergence.py, re-run here so all three cases are in one
     comparison). PR #5 is Merged+Passed, but billing-worker has an open
     critical incident in db/seed.sql. Expected: dynamic decomposition
     short-circuits on check_incidents; decomposition-first does not.
  3. checkout-web / [2] — the failed-scan case, deliberately NOT an
     incident case, to isolate whether a blocking signal OTHER than an
     open incident also produces a divergence. PR #2 on checkout-web is
     Approved+Failed (real seed data, not an inserted row), and
     checkout-web has no open incident. Under the current implementation,
     this does NOT hit the not-ready short circuit either, because PR #2's
     status is still "Approved" (the not-ready branch only fires when NO
     candidate PR is Approved/Merged — see dynamic_decomposition.py's
     `any_plausibly_ready` check) — a Failed scan alone doesn't change
     that check. So this case is expected to run dynamic decomposition's
     FULL path, same as case 1, and rank_release_order itself is what's
     responsible for catching the Failed scan and excluding PR #2 from
     the release order — not the decomposition method. That is itself a
     meaningful, honest finding about the current implementation's scope,
     not a gap in this suite.

Run: python -m planning_toolkit.mini_suite
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from planning_toolkit.model_provider import CoderiftChatModel
from planning_toolkit.planning_lab.algorithms.decomposition import (
    decompose_goal,
    execute_plan,
    final_output as decomposition_first_final_output,
)
from planning_toolkit.planning_lab.algorithms.instrumentation import instrumented
from planning_toolkit.planning_lab.algorithms.dynamic_decomposition import (
    run_dynamic_decomposition,
)

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"

CASES = [
    {
        "case_id": "clean_control",
        "repository_name": "payments-service",
        "candidate_pull_request_ids": [1],
        "expected_signal": "none (Approved+Passed PR, no open incident)",
        "expect_short_circuit": False,
    },
    {
        "case_id": "open_incident",
        "repository_name": "billing-worker",
        "candidate_pull_request_ids": [5],
        "expected_signal": "open critical incident",
        "expect_short_circuit": True,
    },
    {
        "case_id": "failed_scan_no_incident",
        "repository_name": "checkout-web",
        "candidate_pull_request_ids": [2],
        "expected_signal": "Failed security scan on an Approved PR, no open incident "
                            "(current short-circuit rules don't fire on this signal alone "
                            "— see module docstring)",
        "expect_short_circuit": False,
    },
]


def _run_decomposition_first(repository_name: str, candidate_pull_request_ids: list[int]) -> dict:
    llm, stats = instrumented(CoderiftChatModel())
    goal = f"Prepare repository '{repository_name}' for a production release."
    plan = decompose_goal(goal, llm, repository_name=repository_name)
    outputs = execute_plan(
        plan, llm, repository_name=repository_name,
        candidate_pull_request_ids=candidate_pull_request_ids,
    )
    result = decomposition_first_final_output(plan, outputs)
    return {
        "method": "decomposition_first",
        "total_step_count": len(plan.tasks),
        "final_output": result,
        "stats": stats.summary(),
    }


def _run_dynamic_decomposition(repository_name: str, candidate_pull_request_ids: list[int]) -> dict:
    llm, stats = instrumented(CoderiftChatModel())
    result = run_dynamic_decomposition(
        repository_name=repository_name,
        candidate_pull_request_ids=candidate_pull_request_ids,
        llm=llm,
    )
    plan = result["plan"]
    short_circuited = len(plan.tasks) < 6  # decomposition-first is always exactly 6 tasks
    return {
        "method": "dynamic_decomposition",
        "total_step_count": len(plan.tasks),
        "topological_order": plan.topological_order(),
        "short_circuited": short_circuited,
        "decisions": result["decisions"],
        "final_output": result["final_output"],
        "stats": stats.summary(),
    }


def run() -> None:
    ARTIFACTS_DIR.mkdir(exist_ok=True)
    stamp = int(time.time())
    suite_results = []

    for case in CASES:
        print(f"\n=== Case: {case['case_id']} "
              f"({case['repository_name']}/{case['candidate_pull_request_ids']}) ===")
        print(f"Expected signal: {case['expected_signal']}")

        df = _run_decomposition_first(case["repository_name"], case["candidate_pull_request_ids"])
        dd = _run_dynamic_decomposition(case["repository_name"], case["candidate_pull_request_ids"])

        matched_expectation = dd["short_circuited"] == case["expect_short_circuit"]

        result = {
            "case_id": case["case_id"],
            "repository_name": case["repository_name"],
            "candidate_pull_request_ids": case["candidate_pull_request_ids"],
            "expected_signal": case["expected_signal"],
            "expected_short_circuit": case["expect_short_circuit"],
            "actual_short_circuit": dd["short_circuited"],
            "matched_expectation": matched_expectation,
            "decomposition_first": {
                "total_step_count": df["total_step_count"],
                **df["stats"],
            },
            "dynamic_decomposition": {
                "total_step_count": dd["total_step_count"],
                "topological_order": dd["topological_order"],
                "decisions": dd["decisions"],
                **dd["stats"],
            },
            "step_count_delta": df["total_step_count"] - dd["total_step_count"],
            "token_delta": df["stats"]["total_tokens"] - dd["stats"]["total_tokens"],
            "context_chars_delta": (
                df["stats"]["total_context_chars"] - dd["stats"]["total_context_chars"]
            ),
        }
        suite_results.append(result)

        flag = "OK" if matched_expectation else "** UNEXPECTED **"
        print(f"  decomposition_first : {df['total_step_count']} steps, "
              f"{df['stats']['total_tokens']} tokens, "
              f"{df['stats']['total_context_chars']} context chars")
        print(f"  dynamic_decomposition: {dd['total_step_count']} steps, "
              f"{dd['stats']['total_tokens']} tokens, "
              f"{dd['stats']['total_context_chars']} context chars, "
              f"short_circuited={dd['short_circuited']} [{flag}]")

    suite_path = ARTIFACTS_DIR / f"mini_suite_{stamp}.json"
    suite_path.write_text(json.dumps({"cases": suite_results, "timestamp": stamp}, indent=2, default=str))

    print(f"\nFull suite trace: {suite_path}")
    print("\nSUITE SUMMARY")
    print("=============")
    for r in suite_results:
        flag = "OK" if r["matched_expectation"] else "** UNEXPECTED — investigate **"
        print(f"  {r['case_id']:28s} short_circuit={r['actual_short_circuit']!s:5s} "
              f"(expected {r['expected_short_circuit']!s:5s}) [{flag}]  "
              f"steps saved={r['step_count_delta']:+d}  tokens saved={r['token_delta']:+d}  "
              f"context chars saved={r['context_chars_delta']:+d}")

    any_unexpected = any(not r["matched_expectation"] for r in suite_results)
    if any_unexpected:
        print("\nAt least one case did not match its expected short-circuit "
              "behavior — see the flagged row(s) above before treating the "
              "divergence claim as settled.")


if __name__ == "__main__":
    run()
