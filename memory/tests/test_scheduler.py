"""
test_scheduler.py — Proves consolidation is genuinely periodic.

run_n_cycles(4) must produce 4 distinct timestamped run records, spaced
interval_seconds apart, and each processing only the episodes that arrived
since the previous run (not re-processing the whole episodic store).
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from memory.episodic_store import EpisodicStore
from memory.semantic_store import SemanticStore
from memory.consolidation import SemanticConsolidation
from memory.scheduler import ConsolidationScheduler


def test_four_distinct_periodic_runs():
    episodic = EpisodicStore()
    semantic = SemanticStore()
    consolidation = SemanticConsolidation(episodic, semantic, stale_after_seconds=9999)
    scheduler = ConsolidationScheduler(consolidation, interval_seconds=300)

    T0 = 1_700_000_000.0  # fixed simulated start

    # Add one episode before starting — should be processed in run 1 only
    episodic.add_episode({
        "content": "billing-worker: 3 consecutive failed deployments detected",
        "source": "router",
        "metadata": {"entity": "billing-worker"},
        "timestamp": T0,
    })

    history = scheduler.run_n_cycles(4, simulated_start=T0)

    assert len(history) == 4, f"Expected 4 run records, got {len(history)}"

    timestamps = [r["ran_at"] for r in history]
    expected_gap = 300.0
    for i in range(1, 4):
        gap = timestamps[i] - timestamps[i - 1]
        assert abs(gap - expected_gap) < 0.001, (
            f"Expected gap of {expected_gap}s between runs {i-1} and {i}, got {gap}"
        )

    # All 4 timestamps must be distinct
    assert len(set(timestamps)) == 4, "All 4 run timestamps must be distinct"

    # Only run 1 should have processed episodes (the rest are unconsolidated=False)
    assert history[0]["episodes_processed"] == 1, (
        f"Run 1 should process 1 episode, processed {history[0]['episodes_processed']}"
    )
    for i in range(1, 4):
        assert history[i]["episodes_processed"] == 0, (
            f"Run {i+1} should process 0 new episodes, processed {history[i]['episodes_processed']}"
        )

    print(f"PASS: 4 distinct runs at timestamps {[round(t - T0) for t in timestamps]}")
    print(f"      Run 1 processed 1 episode; runs 2-4 processed 0 (no re-processing)")

    # Also verify the live start/stop API doesn't crash
    scheduler2 = ConsolidationScheduler(consolidation, interval_seconds=3600)
    scheduler2.start()
    scheduler2.stop()
    print("PASS: start()/stop() for live use executes without error")


if __name__ == "__main__":
    test_four_distinct_periodic_runs()
    print("\nAll scheduler tests passed.")
