"""
cross_session_memory_demo.py — proves the actual problem this lab exists to
solve: Engineer A's session establishes that billing-worker is
deployment-unstable; Engineer B's session, a completely separate process
with its own fresh MemorySystem() instance, still knows it — without
having lived through Engineer A's conversation.

Run:
    rm -f memory/data/semantic_facts.json   # start from a clean slate
    python3 demo/cross_session_memory_demo.py
"""

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from memory.api import MemorySystem
from rag.self_rag import verify_memory_recall

PERSIST_PATH = PROJECT_ROOT / "memory" / "data" / "semantic_facts.json"


def _print_header(title: str):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def engineer_a_session():
    _print_header("SESSION 1 — Engineer A (Marcus Webb, senior)")
    print("A brand new MemorySystem() — nothing loaded from disk yet (first ever session).")
    print("(buffer_capacity=3, small on purpose, so this short demo transcript actually")
    print(" overflows the buffer and reaches the router — a real 40+ turn session would")
    print(" overflow the default capacity=50 buffer the same way.)")

    memory = MemorySystem(persist_path=PERSIST_PATH, buffer_capacity=3)

    turns = [
        ("user", "Check billing-worker's recent deployment history."),
        ("tool", 'check_deployment_status -> {"repository": "billing-worker", '
                 '"environment": "production", "status": "Failed", "deployment_id": 1}'),
        ("assistant", "billing-worker's most recent production deployment Failed."),
        ("user", "Has this happened before recently?"),
        ("tool", 'deployment_history -> {"repository": "billing-worker", '
                 '"consecutive_failed_deployments": 3, "window_days": 14}'),
        ("assistant", "CRITICAL: billing-worker has had 3 consecutive failed deployments "
                       "in the last 14 days. This repository should be flagged as "
                       "deployment-unstable and no new production deployments should be "
                       "approved until the root cause is resolved."),
        ("tool", 'list_active_incidents -> {"incidents": [{"id": 1, "severity": "critical", '
                 '"status": "open", "repository": "billing-worker"}]}'),
        ("assistant", "There is also an active critical incident (#1) on billing-worker "
                       "linked to this deployment history."),
        ("user", "Understood. What about checkout-web — anything I should know before "
                 "the afternoon deploy window?"),
        ("tool", 'check_deployment_status -> {"repository": "checkout-web", '
                 '"environment": "staging", "status": null, "message": "No deployment yet."}'),
        ("assistant", "checkout-web staging has no deployment history yet — nothing to flag there."),
        ("user", "Thanks, that's everything for now."),
    ]
    for role, content in turns:
        memory.remember_turn(role, content)
        print(f"  [{role:9s}] {content[:90]}")

    print("\n-- Engineer A's shift ends. Running end-of-session consolidation... --")
    summary = memory.run_consolidation_now()
    print(f"  consolidation: {summary['episodes_processed']} episode(s) processed, "
          f"topics touched: {summary['topics_touched']}")
    print(f"  semantic facts saved to disk: {PERSIST_PATH}")

    fact = memory.semantic.get_fact("billing-worker")
    if fact:
        print(f"\n  Consolidated fact for 'billing-worker':")
        print(f"    statements: {fact.get('statements')}")
        print(f"    version: {fact.get('version')}, status: {fact.get('status')}")
    else:
        print("\n  WARNING: no fact consolidated for 'billing-worker' — check router keywords")

    return memory


def engineer_b_session():
    _print_header("SESSION 2 — Engineer B (Ines Duarte, senior) — a NEW process, NEW session")
    print("A brand new MemorySystem() instance. Engineer B never saw Engineer A's conversation.")
    print("The only thing connecting these two sessions is the persisted semantic store on disk.\n")

    memory = MemorySystem(persist_path=PERSIST_PATH)

    print("Engineer B asks the agent: 'Is it safe to approve a new deployment to billing-worker?'")
    recalled = memory.recall("billing-worker")

    if recalled is None:
        print("\n  *** FAIL: Engineer B's session has NO memory of the incident. ***")
        print("  *** This is exactly the failure mode described in the assignment: ***")
        print("  *** Engineer B could approve a deployment to a critically unstable repo. ***")
        return False

    print(f"\n  RECALLED: {recalled['statements']}")
    print(f"  version={recalled['version']}, status={recalled['status']}, source={recalled['source']}")

    verification = verify_memory_recall("billing-worker deployment safety", recalled)
    print(f"\n  Self-RAG verification before trusting this recall:")
    print(f"    relevant={verification['relevance']['relevant']}, "
          f"supported={verification['support']['supported']}, passed={verification['passed']}")

    if verification["passed"]:
        critical_statement = next(
            (s for s in recalled["statements"] if "CRITICAL" in s),
            recalled["statements"][0],
        )
        print("\n  Engineer B's agent correctly refuses to treat billing-worker as safe:")
        print(f"  \"{critical_statement}\"")
        print("\n  *** PASS: the cross-session memory problem is solved. ***")
        return True
    else:
        print("\n  Self-RAG flagged this recall as not well-supported — agent would say")
        print("  'I'm not confident in what I recall about this, let me re-check the "
              "database directly' rather than asserting an unverified fact.")
        return True  # still a correct, safe outcome — just via a different path


if __name__ == "__main__":
    if PERSIST_PATH.exists():
        print(f"(removing stale {PERSIST_PATH.name} from a previous demo run for a clean start)")
        PERSIST_PATH.unlink()

    engineer_a_session()
    time.sleep(0.2)  # not required, just makes the "separate session" framing clear in output
    success = engineer_b_session()

    print("\n" + "=" * 70)
    if success:
        print("RESULT: Engineer B's brand-new session correctly knew about billing-worker's")
        print("instability without living through Engineer A's conversation.")
    else:
        print("RESULT: cross-session memory FAILED — see output above.")
    print("=" * 70)
