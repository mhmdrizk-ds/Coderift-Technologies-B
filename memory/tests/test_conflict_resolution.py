"""
test_conflict_resolution.py — Real Coderift conflict example:

A deployment for billing-worker shows "Succeeded" in Episode A (engineer A's
session: the deploy tool returned OK). Then a health check fails immediately
after and a second engineer initiates a rollback — Episode B says the same
deployment is now "Failed".

Both episodes are correct observations of the same deployment at different
points in time. The consolidation pass must keep both versions, append to
conflict_history, set human_review_needed=True, and set status=CONFLICT_RESOLVED.
It must never silently overwrite.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from memory.episodic_store import EpisodicStore
from memory.semantic_store import SemanticStore
from memory.consolidation import SemanticConsolidation


def test_deployment_status_conflict_billing_worker():
    episodic = EpisodicStore()
    semantic = SemanticStore()
    consolidation = SemanticConsolidation(episodic, semantic, stale_after_seconds=9999)

    T0 = 1_000_000.0

    # Episode A (session A): deployment succeeded
    episodic.add_episode({
        "type": "promoted_from_buffer",
        "content": "billing-worker deployment #1 to production: status Succeeded",
        "source": "router",
        "metadata": {"entity": "billing-worker"},
        "timestamp": T0,
    })
    # First consolidation pass — creates the fact
    summary1 = consolidation.run_consolidation(now=T0 + 10)
    assert summary1["episodes_processed"] == 1

    fact_after_first = semantic.get_fact("billing-worker")
    assert fact_after_first is not None, "Fact should have been created"
    assert fact_after_first["version"] == 1
    assert fact_after_first["status"] == "active"
    print(f"After first pass: version={fact_after_first['version']}, "
          f"status={fact_after_first['status']}")

    # Episode B (session B): health check fails, same deployment now Failed
    episodic.add_episode({
        "type": "promoted_from_buffer",
        "content": "billing-worker deployment #1 post-deploy health check failed: status now Failed",
        "source": "router",
        "metadata": {"entity": "billing-worker"},
        "timestamp": T0 + 300,
    })
    # Second consolidation pass — must detect contradiction and resolve
    summary2 = consolidation.run_consolidation(now=T0 + 310)
    assert summary2["episodes_processed"] == 1

    fact_after_conflict = semantic.get_fact("billing-worker")
    assert fact_after_conflict["version"] == 2, (
        f"Expected version=2, got {fact_after_conflict['version']}"
    )
    assert fact_after_conflict["status"] == "CONFLICT_RESOLVED", (
        f"Expected CONFLICT_RESOLVED, got {fact_after_conflict['status']}"
    )
    assert fact_after_conflict.get("human_review_needed") is None  # set inside conflict_history
    assert len(fact_after_conflict.get("conflict_history", [])) == 1, (
        "Expected one conflict_history entry"
    )

    conflict = fact_after_conflict["conflict_history"][0]
    assert conflict["human_review_needed"] is True
    assert len(conflict["versions"]) == 2
    assert conflict["versions"][0]["status"] == "superseded"
    assert conflict["versions"][1]["status"] == "current"
    assert len(conflict["contradiction_details"]) > 0

    # Both versions' content is preserved in conflict_history
    all_statements = (
        " ".join(conflict["versions"][0]["statements"]) +
        " ".join(conflict["versions"][1]["statements"])
    )
    assert "succeeded" in all_statements.lower(), "Superseded 'Succeeded' version must be retained"
    assert "failed" in all_statements.lower(), "Current 'Failed' version must be present"

    print(f"PASS: conflict detected and resolved at version={fact_after_conflict['version']}, "
          f"status={fact_after_conflict['status']}, human_review_needed=True")
    print(f"      Both versions retained in conflict_history:")
    print(f"      v1 (superseded): {conflict['versions'][0]['statements']}")
    print(f"      v2 (current):    {conflict['versions'][1]['statements']}")


def test_no_false_conflict_on_clean_update():
    """A non-contradictory update (e.g. incident severity downgraded) should
    produce a clean version bump without triggering CONFLICT_RESOLVED."""
    episodic = EpisodicStore()
    semantic = SemanticStore()
    consolidation = SemanticConsolidation(episodic, semantic, stale_after_seconds=9999)

    T0 = 2_000_000.0

    episodic.add_episode({
        "type": "promoted_from_buffer",
        "content": "checkout-web deployment #2 to staging: status Succeeded",
        "source": "router",
        "metadata": {"entity": "checkout-web"},
        "timestamp": T0,
    })
    consolidation.run_consolidation(now=T0 + 5)

    episodic.add_episode({
        "type": "promoted_from_buffer",
        "content": "checkout-web deployment #2 post-deploy: all health checks passed, resolved",
        "source": "router",
        "metadata": {"entity": "checkout-web"},
        "timestamp": T0 + 300,
    })
    consolidation.run_consolidation(now=T0 + 310)

    fact = semantic.get_fact("checkout-web")
    assert fact["version"] == 2
    assert fact["status"] != "CONFLICT_RESOLVED", (
        f"False conflict on clean update — got status={fact['status']}"
    )
    assert "conflict_history" not in fact or len(fact["conflict_history"]) == 0
    print(f"PASS: clean update produced version=2 without CONFLICT_RESOLVED "
          f"(status={fact['status']})")


if __name__ == "__main__":
    test_deployment_status_conflict_billing_worker()
    test_no_false_conflict_on_clean_update()
    print("\nAll conflict resolution tests passed.")
