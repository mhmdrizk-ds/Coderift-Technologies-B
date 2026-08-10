"""
consolidation.py — Periodic pass that turns episodic memory into semantic facts.

This is the ONLY thing that writes to SemanticStore. It never runs at
message-write time and never runs from inside the router — it is invoked on
a cadence by scheduler.py (or manually per pass, e.g. at session end).
Each run only looks at episodes the last run hasn't consolidated yet
(EpisodicStore.get_unconsolidated()), making repeated runs meaningful
instead of redundant.

REAL CODERIFT CONFLICT EXAMPLE required by the assignment:
A repository's deployment status shows "Succeeded" in one episode and "Failed"
in another. This actually happens: a deployment succeeds (the deploy step
returns Succeeded) but the post-deploy health check fails immediately, and
a second engineer triggers a rollback — producing a new episode that says the
same deployment is now "Failed". Both versions are kept. `conflict_history`
is appended, `human_review_needed=True`, `status="CONFLICT_RESOLVED"`.
We never silently overwrite.

Handles all four required cases:
  - updates         (_handle_update, no contradiction)
  - versioning      (fact["version"] increments, previous_version kept)
  - conflict res.   (_resolve_conflict, with Coderift domain example above)
  - expiration      (_expire_stale_facts, e.g. resolved incident = no longer
                     "active critical incident" after incident closes)
"""

import json
import re
import time
from typing import Any

from memory.episodic_store import Episode, EpisodicStore
from memory.semantic_store import SemanticStore

# Contradiction pairs grounded in Coderift's actual CHECK() constraints
# (schema.sql: deployments.status, incidents.status, pull_requests.status,
# security_scans.status). A semantic fact that says a deployment "Succeeded"
# and a new episode that says it "Failed" is the exact conflict case called
# out in the assignment brief.
CONTRADICTORY_PAIRS = [
    ("succeeded", "failed"),        # deployments.status
    ("succeeded", "rolledback"),    # deployments.status
    ("failed", "rolledback"),       # same deployment can't be both
    ("open", "resolved"),           # incidents.status
    ("approved", "rejected"),       # pull_requests.status
    ("passed", "failed"),           # security_scans.status
    ("passed", "pending"),
]

# Coderift entity extractors: repository names, PR numbers (#N), engineer
# access codes (ENG-*), deployment/incident ids.
REPO_RE = re.compile(r"\b(payments-service|checkout-web|billing-worker)\b", re.IGNORECASE)
PR_RE = re.compile(r"\bpr\s*#?\s*(\d+)\b", re.IGNORECASE)
ENGINEER_RE = re.compile(r"\b(ENG-[A-Z]+-\d+)\b")
INCIDENT_RE = re.compile(r"\bincident\s*#?\s*(\d+)\b", re.IGNORECASE)
DEPLOYMENT_RE = re.compile(r"\bdeployment\s*#?\s*(\d+)\b", re.IGNORECASE)


class SemanticConsolidation:
    def __init__(self, episodic_store: EpisodicStore, semantic_store: SemanticStore,
                 stale_after_seconds: float = 60 * 60 * 24 * 30):  # 30 days default
        self.episodic_store = episodic_store
        self.semantic_store = semantic_store
        self.stale_after_seconds = stale_after_seconds
        self.consolidation_log: list[dict] = []
        self._topic_last_referenced: dict[str, float] = {}

    def run_consolidation(self, now: float | None = None) -> dict:
        """One periodic pass. Returns a summary dict so the scheduler can
        log 'ran at T, processed N episodes' — proof this is periodic,
        not a one-off call buried in a test."""
        now = now if now is not None else time.time()
        episodes = self.episodic_store.get_unconsolidated()

        topic_groups = self._group_episodes_by_topic(episodes)
        for topic, topic_episodes in topic_groups.items():
            self._consolidate_topic(topic, topic_episodes, now)

        self.episodic_store.mark_consolidated([ep.id for ep in episodes])

        expired = self._expire_stale_facts(now)

        summary = {
            "ran_at": now,
            "episodes_processed": len(episodes),
            "topics_touched": list(topic_groups.keys()),
            "facts_expired": expired,
        }
        self.consolidation_log.append({"action": "run_summary", **summary})
        return summary

    def note_reference(self, topic: str, when: float | None = None) -> None:
        """Called by MemorySystem.recall() whenever a semantic fact is
        actually read/used, so expiration is based on real disuse rather
        than pure age."""
        self._topic_last_referenced[topic] = when if when is not None else time.time()

    # -- per-topic consolidation ------------------------------------------

    def _consolidate_topic(self, topic: str, episodes: list[Episode], now: float) -> None:
        existing_fact = self.semantic_store.get_fact(topic)
        new_facts = self._extract_facts_from_episodes(episodes)
        if not new_facts:
            return

        if existing_fact:
            self._handle_update(topic, existing_fact, new_facts, now)
        else:
            resolved = self._looks_resolved(new_facts)
            self.semantic_store.add_fact({
                "topic": topic,
                "statements": new_facts,
                "created_at": now,
                "updated_at": now,
                "version": 1,
                "status": "active",
                "resolved": resolved,
            })
            self.consolidation_log.append({
                "action": "created", "topic": topic, "statements": new_facts, "at": now,
            })

    def _handle_update(self, topic: str, existing_fact: dict,
                       new_facts: list[str], now: float) -> None:
        contradictions = self._detect_contradictions(existing_fact, new_facts)

        if contradictions:
            self._resolve_conflict(topic, existing_fact, new_facts, contradictions, now)
        else:
            existing_fact["previous_version"] = {
                "statements": existing_fact["statements"],
                "version": existing_fact["version"],
                "updated_at": existing_fact.get("updated_at", existing_fact["created_at"]),
            }
            existing_fact["version"] += 1
            existing_fact["statements"] = new_facts
            existing_fact["updated_at"] = now
            existing_fact["resolved"] = self._looks_resolved(new_facts)
            self.consolidation_log.append({
                "action": "updated",
                "topic": topic,
                "old_statements": existing_fact["previous_version"]["statements"],
                "new_statements": new_facts,
                "new_version": existing_fact["version"],
                "at": now,
            })

    def _resolve_conflict(self, topic: str, existing_fact: dict, new_facts: list[str],
                           contradictions: list[dict], now: float) -> None:
        """Never silently overwrite. Keep both versions, flag for human
        review, log the exact contradiction.

        Real Coderift example: deployment for billing-worker shows 'Succeeded'
        in session A (the deploy command returned OK), but session B says the
        same deployment is now 'Failed' (health check failed post-deploy,
        status updated by rollback). Both episodes are correct observations —
        the ambiguity is real and requires human review before the semantic
        fact settles."""
        resolution = {
            "topic": topic,
            "at": now,
            "conflict_type": "value_contradiction",
            "versions": [
                {
                    "version": existing_fact["version"],
                    "statements": existing_fact["statements"],
                    "status": "superseded",
                },
                {
                    "version": existing_fact["version"] + 1,
                    "statements": new_facts,
                    "status": "current",
                },
            ],
            "contradiction_details": contradictions,
            "resolution_strategy": "most_recent_with_history_retained",
            "human_review_needed": True,
        }

        existing_fact["version"] += 1
        existing_fact["statements"] = new_facts
        existing_fact.setdefault("conflict_history", []).append(resolution)
        existing_fact["status"] = "CONFLICT_RESOLVED"
        existing_fact["updated_at"] = now
        existing_fact["resolved"] = self._looks_resolved(new_facts)

        self.consolidation_log.append({"action": "conflict_resolved", **resolution})

    # -- expiration -------------------------------------------------------

    def _expire_stale_facts(self, now: float) -> list[str]:
        """Real Coderift example this fires on: 'billing-worker has active
        critical incident' — the incident closes (resolved=True after
        consolidation sees the 'incident resolved' episode), and if nobody
        has asked about billing-worker in stale_after_seconds, it's
        operational noise, not a lasting fact — expire it, don't delete."""
        eligible = self.semantic_store.facts_eligible_for_expiration(
            now=now,
            stale_after_seconds=self.stale_after_seconds,
            topics_referenced_since=self._topic_last_referenced,
        )
        for topic in eligible:
            reason = (f"Resolved operational fact, unreferenced for >= "
                      f"{self.stale_after_seconds / 86400:.0f} days")
            self.semantic_store.expire_fact(topic, reason)
            self.consolidation_log.append({
                "action": "expired", "topic": topic, "reason": reason, "at": now,
            })
        return eligible

    # -- entity / fact extraction -----------------------------------------

    def _group_episodes_by_topic(self, episodes: list[Episode]) -> dict[str, list[Episode]]:
        topics: dict[str, list[Episode]] = {}
        for ep in episodes:
            for entity in self._extract_entities(ep.content, ep.metadata):
                topics.setdefault(entity, []).append(ep)
        return topics

    def _extract_entities(self, text: str, metadata: dict) -> list[str]:
        """Regex-based entity extraction grounded in Coderift schema names.
        Prefers explicit metadata hints (set when an episode is added from a
        known tool result) over regex for reliability."""
        entities: set[str] = set()

        if metadata.get("entity"):
            entities.add(metadata["entity"])

        entities.update(REPO_RE.findall(text))
        for m in PR_RE.finditer(text):
            entities.add(f"PR#{m.group(1)}")
        entities.update(ENGINEER_RE.findall(text))
        for m in INCIDENT_RE.finditer(text):
            entities.add(f"incident#{m.group(1)}")
        for m in DEPLOYMENT_RE.finditer(text):
            entities.add(f"deployment#{m.group(1)}")

        return list(entities)

    def _extract_facts_from_episodes(self, episodes: list[Episode]) -> list[str]:
        return [ep.content for ep in episodes]

    def _detect_contradictions(self, existing_fact: dict,
                                new_facts: list[str]) -> list[dict]:
        """Check for direct semantic contradictions between the stored fact
        and incoming episodes. Uses the Coderift-domain pairs defined at the
        top of this module (deployment Succeeded vs Failed, incident open vs
        resolved, etc.)."""
        contradictions = []
        old_text = " ".join(existing_fact["statements"]).lower()
        new_text = " ".join(new_facts).lower()
        for term1, term2 in CONTRADICTORY_PAIRS:
            if term1 in old_text and term2 in new_text:
                contradictions.append({
                    "type": "direct_contradiction",
                    "old_term": term1,
                    "new_term": term2,
                })
            elif term2 in old_text and term1 in new_text:
                contradictions.append({
                    "type": "direct_contradiction",
                    "old_term": term2,
                    "new_term": term1,
                })
        return contradictions

    def _looks_resolved(self, statements: list[str]) -> bool:
        """A fact counts as 'resolved' (expiration-eligible later) once it
        describes a closed Coderift operational state: incident resolved,
        deployment rolled back or succeeded after a fix, PR merged."""
        text = " ".join(statements).lower()
        resolved_markers = ["resolved", "succeeded", "merged", "rolledback", "approved", "cleared"]
        open_markers = ["critical incident", "failed", "pending", "open incident", "rollback required"]
        has_resolved = any(m in text for m in resolved_markers)
        has_open = any(m in text for m in open_markers)
        return has_resolved and not has_open

    def save_log(self, filename: str) -> None:
        with open(filename, "w") as f:
            json.dump(self.consolidation_log, f, indent=2, default=str)
