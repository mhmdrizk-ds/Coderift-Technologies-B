"""
semantic_store.py — Final storage for stable, consolidated facts.

Only ever written to by consolidation.py (never directly by the router,
and never at message-write time). Facts are versioned, dated, and —
critically — expirable: an operational fact like "billing-worker has active
critical incident" has no long-term value once the incident closes, and
keeping it "active" forever in semantic memory is exactly the kind of
staleness that causes wrong answers ("is it safe to deploy?" "No." — but the
incident was resolved 3 weeks ago).

Persistence: save()/load() write/read a JSON file so that semantic facts
survive past a single MemorySystem instance's lifetime. This is what
actually solves Coderift's cross-session problem: Engineer A's session
consolidates "billing-worker has had 3 consecutive failed deployments"
into semantic memory and saves it; Engineer B's session, a completely new
process with a new MemorySystem() instance, loads the same file on
__init__ and sees the fact immediately — without ever having lived through
Engineer A's conversation.
"""

import json
from pathlib import Path
from time import time
from typing import Optional

DEFAULT_STORE_PATH = Path(__file__).resolve().parent / "data" / "semantic_facts.json"


class SemanticStore:
    def __init__(self):
        self.facts: dict[str, dict] = {}  # topic -> fact record

    def add_fact(self, fact: dict) -> None:
        self.facts[fact["topic"]] = fact

    def get_fact(self, topic: str) -> Optional[dict]:
        return self.facts.get(topic)

    def update_fact(self, topic: str, updates: dict) -> None:
        if topic in self.facts:
            self.facts[topic].update(updates)

    def get_active_facts(self) -> dict[str, dict]:
        """Non-expired facts only — this is what recall()/RAG grounding
        should be reading from, never self.facts directly."""
        return {
            topic: fact for topic, fact in self.facts.items()
            if fact.get("status") not in ("expired",)
        }

    def expire_fact(self, topic: str, reason: str) -> bool:
        """Mark a fact expired. Never deletes it — the record (and its
        conflict/version history) stays for audit, just excluded from
        get_active_facts() going forward."""
        if topic not in self.facts:
            return False
        self.facts[topic]["status"] = "expired"
        self.facts[topic]["expired_at"] = time()
        self.facts[topic]["expiration_reason"] = reason
        return True

    def facts_eligible_for_expiration(self, now: float, stale_after_seconds: float,
                                       topics_referenced_since: dict[str, float]) -> list[str]:
        """Return topics whose fact is resolved (status resolved or was a
        successful deployment) AND hasn't been referenced (recalled) recently.
        This is the check the periodic consolidation pass runs before calling
        expire_fact — expiration is a decision, not just a TTL."""
        eligible = []
        for topic, fact in self.facts.items():
            if fact.get("status") == "expired":
                continue
            if not fact.get("resolved", False):
                continue
            last_ref = topics_referenced_since.get(
                topic, fact.get("updated_at", fact.get("created_at", 0))
            )
            if now - last_ref >= stale_after_seconds:
                eligible.append(topic)
        return eligible

    # ---- Cross-session persistence -----------------------------------------
    def save(self, path: Path = DEFAULT_STORE_PATH) -> None:
        """Write all facts (including expired ones — the audit trail
        matters) to disk as JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.facts, f, indent=2, default=str)

    def load(self, path: Path = DEFAULT_STORE_PATH) -> int:
        """Load facts from disk, merging into (and overwriting duplicates
        in) the current in-memory store. Returns the number of facts
        loaded. Missing file is not an error — a brand new deployment of
        this system has no history yet."""
        if not path.exists():
            return 0
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        self.facts.update(loaded)
        return len(loaded)
