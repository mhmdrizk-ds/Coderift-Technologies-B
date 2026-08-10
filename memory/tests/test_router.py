"""
test_router.py — Verify the router makes the right forget/promote decisions
for Coderift domain keywords, and that it has ZERO access to SemanticStore.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from memory.episodic_store import EpisodicStore
from memory.router import PromoteOrDropRouter
from memory.short_term import Message


def _make_message(content: str, age: int = 5) -> Message:
    return Message(role="user", content=content, timestamp=0.0, age=age)


def test_critical_keywords_promote():
    store = EpisodicStore()
    router = PromoteOrDropRouter(store)

    msg = _make_message("billing-worker has had 3 consecutive failed deployments — critical incident active")
    decision, reasoning = router.decide(msg, age=5, context={})
    assert decision == "promote", f"Expected promote, got {decision}: {reasoning}"
    assert len(store) == 1
    print("PASS: critical keyword message promoted to episodic store")


def test_transient_query_dropped():
    store = EpisodicStore()
    router = PromoteOrDropRouter(store)

    # "deployment" is a critical keyword, so use a purely time-bound query
    # with no critical content — exactly what the transient branch targets.
    msg = _make_message("what time will the team lunch happen?", age=5)
    decision, reasoning = router.decide(msg, age=5, context={})
    assert decision == "forget", f"Expected forget, got {decision}"
    assert len(store) == 0
    print("PASS: transient ETA query dropped")


def test_aged_non_critical_dropped():
    store = EpisodicStore()
    router = PromoteOrDropRouter(store)

    msg = _make_message("can you check the feature flag state?", age=50)
    decision, reasoning = router.decide(msg, age=50, context={"recency_threshold": 30})
    assert decision == "forget", f"Expected forget (age > threshold), got {decision}"
    print("PASS: aged non-critical message dropped")


def test_default_below_threshold_promotes():
    store = EpisodicStore()
    router = PromoteOrDropRouter(store)

    msg = _make_message("what repositories does the Billing team own?", age=10)
    decision, reasoning = router.decide(msg, age=10, context={"recency_threshold": 30})
    assert decision == "promote", f"Expected default promote, got {decision}"
    print("PASS: below-threshold, non-transient message promoted by default")


def test_router_has_no_semantic_store_attribute():
    """STRUCTURAL CONSTRAINT: the router must have zero access to SemanticStore.
    Not just 'we don't call it' — the attribute must not exist."""
    store = EpisodicStore()
    router = PromoteOrDropRouter(store)
    assert not hasattr(router, "semantic_store"), (
        "router.semantic_store must not exist — structural zero-access constraint violated"
    )
    assert not hasattr(router, "semantic"), (
        "router.semantic must not exist — structural zero-access constraint violated"
    )
    print("PASS: router has no semantic_store attribute (structural constraint)")


def test_decision_log_populated():
    store = EpisodicStore()
    router = PromoteOrDropRouter(store)

    messages = [
        _make_message("billing-worker deployment failed", age=5),
        _make_message("when will the scan finish?", age=5),
        _make_message("check PR status", age=35),
    ]
    for msg in messages:
        router.decide(msg, age=msg.age, context={"recency_threshold": 30})

    assert len(router.decision_log) == 3
    for entry in router.decision_log:
        assert "message_preview" in entry
        assert "decision" in entry
        assert "reasoning" in entry
        assert "decided_at" in entry
    print(f"PASS: decision_log has {len(router.decision_log)} entries with required fields")


if __name__ == "__main__":
    test_critical_keywords_promote()
    test_transient_query_dropped()
    test_aged_non_critical_dropped()
    test_default_below_threshold_promotes()
    test_router_has_no_semantic_store_attribute()
    test_decision_log_populated()
    print("\nAll router tests passed.")
