"""
benchmark.py — runs all four context strategies against all 11 transcripts
(base + 10 variations), measures accuracy, tokens, and latency, and writes
results.csv / summary.csv.

Accuracy check: did the critical detail (billing-worker has 3 consecutive
failed deployments, flagged deployment-unstable, active critical incident)
survive pruning and reach the final answer? Grading is keyword-based against
`grading_keywords` in each transcript file — the same check used in the
lab's own accuracy definition.
"""

import csv
import json
import time
from pathlib import Path
from typing import List, Dict, Any

from config import TRANSCRIPTS_DIR, GRADING_KEYWORDS_KEY

from strategies.sliding_window import apply_sliding_window
from strategies.observation_masking import apply_observation_masking
from strategies.recursive_summarization import apply_recursive_summarization
from strategies.zone_based_pruning import apply_zone_based_pruning


BASE_DIR = Path(__file__).resolve().parent
RESULTS_FULL_FILE = BASE_DIR / "results.csv"
SUMMARY_FILE = BASE_DIR / "summary.csv"

LATENCY_REPEATS = 200  # repeats per (transcript, strategy) for a stable latency mean

STRATEGIES = {
    "sliding_window": lambda messages: apply_sliding_window(messages, window_size=20),
    "observation_masking": lambda messages: apply_observation_masking(
        messages, keep_recent_tool_outputs=3, mask_text="[tool output omitted]"
    ),
    "recursive_summarization": lambda messages: apply_recursive_summarization(
        messages, summary_max_tokens=300, keep_recent_messages=20
    ),
    "zone_based_pruning": lambda messages: apply_zone_based_pruning(
        messages, keep_system_prompt=True, keep_scratchpad=True
    ),
}


def approx_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, round(len(text) / 4))


def serialize_messages(messages: List[Dict[str, Any]]) -> str:
    return "\n".join(str(m.get("content", "")) for m in messages)


def extract_context_text(messages: List[Dict[str, Any]]) -> str:
    return "\n".join(str(m.get("content", "")) for m in messages).lower()


def check_critical_facts(messages: List[Dict[str, Any]], required_keywords: List[str]) -> Dict[str, Any]:
    context_text = extract_context_text(messages)
    missing = [kw for kw in required_keywords if kw.lower() not in context_text]
    return {"passed": len(missing) == 0, "missing_keywords": missing}


def synthesize_answer(messages: List[Dict[str, Any]], required_keywords: List[str]) -> str:
    """Stand-in for a real LLM call over the pruned context (see rag/llm.py
    for the live/offline-fallback pattern used elsewhere in this project).
    Whether the synthesized answer is correct depends entirely on whether
    the pruned context still contains the critical detail — this is the
    same test the assignment's worked example uses."""
    result = check_critical_facts(messages, required_keywords)
    if result["passed"]:
        return (
            "Based on the available context: billing-worker has had 3 consecutive "
            "failed deployments and an active critical incident — it is NOT safe to "
            "deploy to billing-worker production right now. Recommend holding all "
            "deployments to this repository until the incident is resolved."
        )
    return (
        "I don't have enough information in the current context to confirm "
        "billing-worker's deployment stability. Missing: "
        + ", ".join(result["missing_keywords"])
        + ". Recommend re-querying deployment history and incident status before proceeding."
    )


def measure_latency_ms(fn, messages: List[Dict[str, Any]], repeats: int = LATENCY_REPEATS) -> float:
    start = time.perf_counter()
    for _ in range(repeats):
        fn(messages)
    elapsed = time.perf_counter() - start
    return (elapsed / repeats) * 1000.0


def load_transcript(file_path: Path) -> Dict[str, Any]:
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_benchmark():
    full_rows = []
    transcript_files = sorted(TRANSCRIPTS_DIR.glob("*.json"))

    for transcript_file in transcript_files:
        transcript = load_transcript(transcript_file)
        messages = transcript["messages"]
        required_keywords = transcript[GRADING_KEYWORDS_KEY]

        original_input_tokens = approx_tokens(serialize_messages(messages))

        for strategy_name, strategy_fn in STRATEGIES.items():
            pruned_messages = strategy_fn(messages)

            accuracy = check_critical_facts(pruned_messages, required_keywords)
            answer = synthesize_answer(pruned_messages, required_keywords)

            input_tokens = approx_tokens(serialize_messages(pruned_messages))
            output_tokens = approx_tokens(answer)
            latency_ms = measure_latency_ms(strategy_fn, messages)

            compression_pct = (
                round(100 * (1 - input_tokens / original_input_tokens), 1)
                if original_input_tokens else 0.0
            )

            full_rows.append({
                "transcript": transcript_file.name,
                "strategy": strategy_name,
                "passed": accuracy["passed"],
                "missing_keywords": ",".join(accuracy["missing_keywords"]),
                "original_input_tokens": original_input_tokens,
                "pruned_input_tokens": input_tokens,
                "compression_pct": compression_pct,
                "output_tokens": output_tokens,
                "latency_ms": round(latency_ms, 4),
            })

    save_full_results(full_rows)
    summary_rows = build_summary(full_rows)
    save_summary(summary_rows)
    print_comparison_table(summary_rows)

    return full_rows, summary_rows


def save_full_results(rows: List[Dict[str, Any]]):
    fieldnames = [
        "transcript", "strategy", "passed", "missing_keywords",
        "original_input_tokens", "pruned_input_tokens", "compression_pct",
        "output_tokens", "latency_ms",
    ]
    with open(RESULTS_FULL_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_summary(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_strategy: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_strategy.setdefault(row["strategy"], []).append(row)

    summary = []
    for strategy_name, strategy_rows in by_strategy.items():
        n = len(strategy_rows)
        passed_count = sum(1 for r in strategy_rows if r["passed"])
        avg_input_tokens = sum(r["pruned_input_tokens"] for r in strategy_rows) / n
        avg_output_tokens = sum(r["output_tokens"] for r in strategy_rows) / n
        avg_latency_ms = sum(r["latency_ms"] for r in strategy_rows) / n
        avg_compression = sum(r["compression_pct"] for r in strategy_rows) / n

        summary.append({
            "strategy": strategy_name,
            "accuracy_pct": round(100 * passed_count / n, 1),
            "passed": passed_count,
            "total": n,
            "avg_input_tokens": round(avg_input_tokens, 1),
            "avg_output_tokens": round(avg_output_tokens, 1),
            "avg_compression_pct": round(avg_compression, 1),
            "avg_latency_ms": round(avg_latency_ms, 4),
        })

    summary.sort(key=lambda r: (-r["accuracy_pct"], r["avg_input_tokens"]))
    return summary


def save_summary(rows: List[Dict[str, Any]]):
    fieldnames = [
        "strategy", "accuracy_pct", "passed", "total",
        "avg_input_tokens", "avg_output_tokens", "avg_compression_pct", "avg_latency_ms",
    ]
    with open(SUMMARY_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_comparison_table(summary_rows: List[Dict[str, Any]]):
    header = (
        f"{'Strategy':<26}{'Accuracy':>10}{'Avg In Tok':>12}"
        f"{'Avg Out Tok':>13}{'Compression':>13}{'Latency (ms)':>14}"
    )
    print(header)
    print("-" * len(header))
    for row in summary_rows:
        print(
            f"{row['strategy']:<26}"
            f"{row['accuracy_pct']:>9}%"
            f"{row['avg_input_tokens']:>12}"
            f"{row['avg_output_tokens']:>13}"
            f"{row['avg_compression_pct']:>12}%"
            f"{row['avg_latency_ms']:>14}"
        )


if __name__ == "__main__":
    run_benchmark()
    print("\nBenchmark completed. See results.csv and summary.csv.")
