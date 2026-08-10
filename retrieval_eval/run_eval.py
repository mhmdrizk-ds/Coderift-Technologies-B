"""
run_eval.py — runs naive, hybrid, and agentic RAG against every question in
questions.json and produces the comparison table the README cites.

Usage:
    cd retrieval_eval
    python3 run_eval.py

Writes:
    results.json          — full per-question, per-architecture output
    comparison_table.md   — the summary table (paste straight into README)

Accuracy grading: offline (no separate LLM judge call, to keep the eval
cheap to re-run) — each question lists expected_keywords; a question is
scored correct if at least 60% of its expected_keywords show up
(case-insensitive) in the returned answer.
"""

import json
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.naive_rag import answer_naive
from rag.hybrid_rag import answer_hybrid
from rag.agentic_rag import answer_agentic

QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.json"
RESULTS_PATH = Path(__file__).resolve().parent / "results.json"
TABLE_PATH = Path(__file__).resolve().parent / "comparison_table.md"

STRATEGIES = {
    "naive": lambda q: answer_naive(q["question"], q["policy_name"]),
    "hybrid": lambda q: answer_hybrid(q["question"], q["policy_name"]),
    "agentic": lambda q: answer_agentic(q["question"]),
}


def _grade(answer: str, expected_keywords: list[str], threshold: float = 0.6) -> dict:
    answer_lower = answer.lower()
    hits = [kw for kw in expected_keywords if kw.lower() in answer_lower]
    score = len(hits) / len(expected_keywords) if expected_keywords else 0.0
    return {"score": score, "correct": score >= threshold, "hits": hits}


def run() -> dict:
    questions = json.loads(QUESTIONS_PATH.read_text())["questions"]
    results = {name: [] for name in STRATEGIES}

    for q in questions:
        print(f"[{q['id']}] {q['category']}: {q['question'][:70]}...")
        for name, fn in STRATEGIES.items():
            out = fn(q)
            grade = _grade(out["answer"], q["expected_keywords"])
            record = {
                "question_id": q["id"],
                "category": q["category"],
                "question": q["question"],
                "answer": out["answer"],
                "correct": grade["correct"],
                "score": grade["score"],
                "input_tokens": out["input_tokens"],
                "output_tokens": out["output_tokens"],
                "latency_seconds": out["latency_seconds"],
                "used_live_model": out["used_live_model"],
            }
            results[name].append(record)
            print(f"    {name:8s} correct={grade['correct']!s:5} "
                  f"tokens={out['input_tokens']+out['output_tokens']:4d} "
                  f"latency={out['latency_seconds']:.3f}s")

    return results


def summarize(results: dict) -> dict:
    summary = {}
    for name, records in results.items():
        overall_acc = mean(1 if r["correct"] else 0 for r in records)
        by_category = {}
        for cat in ("general", "exact_id", "multi_part"):
            cat_records = [r for r in records if r["category"] == cat]
            by_category[cat] = mean(1 if r["correct"] else 0 for r in cat_records) if cat_records else None
        summary[name] = {
            "overall_accuracy": overall_acc,
            "accuracy_by_category": by_category,
            "avg_tokens": mean(r["input_tokens"] + r["output_tokens"] for r in records),
            "avg_latency_seconds": mean(r["latency_seconds"] for r in records),
        }
    return summary


def write_table(summary: dict) -> str:
    lines = [
        "| Architecture | Overall accuracy (12 q) | General | Exact-ID | Multi-part | Avg tokens/query | Avg latency/query |",
        "|---|---|---|---|---|---|---|",
    ]
    for name in ("naive", "hybrid", "agentic"):
        s = summary[name]
        cat = s["accuracy_by_category"]
        lines.append(
            f"| {name} | {s['overall_accuracy']*100:.0f}% "
            f"| {cat['general']*100:.0f}% "
            f"| {cat['exact_id']*100:.0f}% "
            f"| {cat['multi_part']*100:.0f}% "
            f"| {s['avg_tokens']:.0f} "
            f"| {s['avg_latency_seconds']:.3f}s |"
        )
    table = "\n".join(lines)
    TABLE_PATH.write_text(table + "\n")
    return table


if __name__ == "__main__":
    results = run()
    RESULTS_PATH.write_text(json.dumps(results, indent=2))
    summary = summarize(results)
    table = write_table(summary)
    print("\n" + table)
    print(f"\nWrote {RESULTS_PATH.name} and {TABLE_PATH.name}")
