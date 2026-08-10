"""
router.py — Promote-or-drop routing for Coderift Technologies.

Fires whenever ShortTermBuffer evicts a message. Decides "forget" or
"promote" (to episodic — never directly to semantic), and logs the reasoning
so a grader (or a teammate debugging a wrong answer) can see exactly why
something was kept or dropped.

Real Coderift failure this protects against: Engineer A's session established
that `billing-worker` had 3 consecutive failed deployments and an active
critical incident. That exchange scrolls off the buffer 40 turns later.
Engineer B starts a new session with no memory of this and approves a
production deploy to `billing-worker` — exactly the "critical deployment
to an unstable repository" risk the MCP server's elicitation gate exists to
prevent. Memory has to preserve that fact past the session boundary.

STRUCTURAL CONSTRAINT: This class has ZERO reference to SemanticStore.
It may only call episodic_store.add_episode(). Semantic facts are built
exclusively by the periodic consolidation pass in consolidation.py.
"""

import json
import time
from typing import Any

from memory.episodic_store import EpisodicStore
from memory.short_term import Message

# Coderift domain: terms that signal an operationally important event worth
# keeping past the session boundary.
CRITICAL_KEYWORDS_DEFAULT = [
    "deployment", "incident", "failed", "security", "approved",
    "rejected", "rollback", "critical", "scan", "vulnerability",
    "override", "production", "billing-worker", "checkout-web",
    "payments-service",
]

TRANSIENT_PATTERNS = ["what time", "when will", "eta", "right now", "today", "tomorrow"]


class PromoteOrDropRouter:
    def __init__(self, episodic_store: EpisodicStore):
        self.episodic_store = episodic_store
        self.decision_log: list[dict] = []
        # SemanticStore is intentionally NOT an attribute here.
        # Zero structural access, not just "we won't call it" — the
        # attribute doesn't exist so a future developer can't add a call
        # without the type checker complaining.

    def decide(self, message: Message, age: int, context: dict) -> tuple[str, str]:
        """
        Returns (decision, reasoning). decision is "forget" or "promote".
        This method NEVER touches semantic memory — only episodic, via
        self.episodic_store.add_episode(). Semantic facts only ever get
        built later, by a separate consolidation pass.
        """
        recency_threshold = context.get("recency_threshold", 30)

        if self._is_critical_info(message, context):
            decision = "promote"
            reasoning = (f"Contains operationally critical Coderift terms: "
                         f"{self._matched_keywords(message, context)}")
        elif self._is_transient(message):
            decision = "forget"
            reasoning = "Time-bound query with no lasting value (ETA/status-right-now question)"
        elif age > recency_threshold:
            decision = "forget"
            reasoning = (f"Aged beyond retention threshold ({age} > {recency_threshold} "
                         f"turns) with no critical content")
        else:
            decision = "promote"
            reasoning = ("Default: below aging threshold and not clearly transient — "
                         "retain for episodic review")

        self.decision_log.append({
            "message_preview": message.content[:80],
            "role": message.role,
            "age": age,
            "decision": decision,
            "reasoning": reasoning,
            "decided_at": time.time(),
        })

        if decision == "promote":
            self.episodic_store.add_episode({
                "type": "promoted_from_buffer",
                "content": message.content,
                "source": "router",
                "metadata": {
                    "role": message.role,
                    "original_age": age,
                    "reasoning": reasoning,
                },
            })

        return decision, reasoning

    def save_log(self, filename: str) -> None:
        with open(filename, "w") as f:
            json.dump(self.decision_log, f, indent=2, default=str)

    # -- internals --------------------------------------------------------

    def _is_critical_info(self, message: Message, context: dict) -> bool:
        keywords = context.get("critical_keywords", CRITICAL_KEYWORDS_DEFAULT)
        content_lower = message.content.lower()
        return any(kw.lower() in content_lower for kw in keywords)

    def _is_transient(self, message: Message) -> bool:
        content_lower = message.content.lower()
        return any(p in content_lower for p in TRANSIENT_PATTERNS)

    def _matched_keywords(self, message: Message, context: dict) -> list[str]:
        keywords = context.get("critical_keywords", CRITICAL_KEYWORDS_DEFAULT)
        content_lower = message.content.lower()
        return [kw for kw in keywords if kw.lower() in content_lower]
