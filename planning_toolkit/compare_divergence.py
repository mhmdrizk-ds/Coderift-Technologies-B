"""
compare_divergence.py — runs decomposition-first (Teammate 1's,
unmodified) and dynamic decomposition against the SAME case
(billing-worker / candidate PR #5, which has a seeded open critical
incident) and logs real, instrumented LLM call/token/latency numbers for
both, plus the step-count divergence, to artifacts/.

This is the graded deliverable comparison — run it, don't just describe
the expected result:

    python3 -m planning_toolkit.compare_divergence
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
    final_output,
)
from planning_toolkit.planning_lab.algorithms.dynamic_decomposition import run_and_save
from planning_toolkit.planning_lab.algorithms.instrumentation import instrumented

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"

REPOSITORY_NAME = "billing-worker"
CANDIDATE_PULL_REQUEST_IDS = [5]


def run_decomposition_first_instrumented() -> dict:
    llm, stats = instrumented(CoderiftChatModel())
    goal = f"Prepare repository '{REPOSITORY_NAME}' for a production release."

    start = time.perf_counter()
    plan = decompose_goal(goal, llm, repository_name=REPOSITORY_NAME)
    outputs = execute_plan(
        plan, llm, repository_name=REPOSITORY_NAME,
        candidate_pull_request_ids=CANDIDATE_PULL_REQUEST_IDS,
    )
    result = final_output(plan, outputs)
    wall_seconds = time.perf_counter() - start

    trace = {
        "agent": "release_readiness_planning",
        "method": "decomposition_first",
        "repository_name": REPOSITORY_NAME,
        "candidate_pull_request_ids": CANDIDATE_PULL_REQUEST_IDS,
        "plan": plan.model_dump(),
        "topological_order": plan.topological_order(),
        "execution_batches": plan.execution_batches(),
        "node_outputs": outputs,
        "final_output": result,
        "llm_stats": stats.summary(),
        "wall_clock_seconds": round(wall_seconds, 4),
        "timestamp": time.time(),
    }
    ARTIFACTS_DIR.mkdir(exist_ok=True)
    path = ARTIFACTS_DIR / f"decomposition_first_INSTRUMENTED_{REPOSITORY_NAME}_{int(time.time())}.json"
    path.write_text(json.dumps(trace, indent=2, default=str))
    trace["_path"] = str(path)
    return trace


def run_dynamic_instrumented() -> dict:
    llm, stats = instrumented(CoderiftChatModel())
    return run_and_save(REPOSITORY_NAME, CANDIDATE_PULL_REQUEST_IDS, llm=llm, stats=stats)


def main():
    print(f"Divergence case: repository='{REPOSITORY_NAME}', "
          f"candidate_pull_request_ids={CANDIDATE_PULL_REQUEST_IDS} "
          f"(seeded with an open critical incident)\n")

    print("Running decomposition-first (Teammate 1's, unmodified)...")
    df_trace = run_decomposition_first_instrumented()

    print("Running dynamic decomposition...")
    dd_trace = run_dynamic_instrumented()

    df_steps = df_trace["topological_order"]
    dd_steps = dd_trace["topological_order"]
    df_stats = df_trace["llm_stats"]
    dd_stats = dd_trace["llm_stats"]

    comparison = {
        "case": {"repository_name": REPOSITORY_NAME, "candidate_pull_request_ids": CANDIDATE_PULL_REQUEST_IDS},
        "decomposition_first": {
            "total_steps": len(df_steps), "steps": df_steps,
            "execution_batches": df_trace["execution_batches"],
            **df_stats,
            "wall_clock_seconds": df_trace["wall_clock_seconds"],
        },
        "dynamic_decomposition": {
            "total_steps": len(dd_steps), "steps": dd_steps,
            "execution_batches": dd_trace["execution_batches"],
            "decisions": dd_trace["decisions"],
            **dd_stats,
            "wall_clock_seconds": dd_trace["wall_clock_seconds"],
        },
        "delta": {
            "step_count_reduction": len(df_steps) - len(dd_steps),
            "step_count_reduction_pct": round(
                100 * (len(df_steps) - len(dd_steps)) / len(df_steps), 1
            ),
            "llm_call_count_reduction": df_stats["llm_call_count"] - dd_stats["llm_call_count"],
            "total_tokens_reduction": df_stats["total_tokens"] - dd_stats["total_tokens"],
            "total_tokens_reduction_pct": round(
                100 * (df_stats["total_tokens"] - dd_stats["total_tokens"]) / df_stats["total_tokens"], 1
            ) if df_stats["total_tokens"] else 0.0,
            "total_context_chars_reduction": (
                df_stats["total_context_chars"] - dd_stats["total_context_chars"]
            ),
            "total_context_chars_reduction_pct": round(
                100 * (df_stats["total_context_chars"] - dd_stats["total_context_chars"])
                / df_stats["total_context_chars"], 1
            ) if df_stats["total_context_chars"] else 0.0,
        },
    }

    print("\n" + "=" * 70)
    print("DIVERGENCE COMPARISON — same case, both methods")
    print("=" * 70)
    print(f"decomposition-first: {len(df_steps)} steps {df_steps}")
    print(f"  execution_batches: {df_trace['execution_batches']}")
    print(f"  llm_stats: {df_stats}")
    print()
    print(f"dynamic_decomposition: {len(dd_steps)} steps {dd_steps}")
    print(f"  execution_batches: {dd_trace['execution_batches']}")
    print(f"  llm_stats: {dd_stats}")
    print()
    print(f"DELTA: {comparison['delta']}")

    ARTIFACTS_DIR.mkdir(exist_ok=True)
    path = ARTIFACTS_DIR / f"divergence_comparison_{REPOSITORY_NAME}_{int(time.time())}.json"
    path.write_text(json.dumps(comparison, indent=2, default=str))
    print(f"\nComparison saved to {path}")
    print(f"decomposition-first trace: {df_trace['_path']}")
    print(f"dynamic_decomposition trace: {dd_trace['_path']}")
    return comparison


if __name__ == "__main__":
    main()
