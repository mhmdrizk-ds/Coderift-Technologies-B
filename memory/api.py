"""
api.py — The single public surface the rest of the project imports from memory/.

agent/session.py should only ever import MemorySystem from here — never reach
into router.py/consolidation.py/etc. directly. This keeps the memory internals
free to change without breaking the agent loop.

Typical usage from the agent loop:

    memory = MemorySystem()
    memory.remember_turn("user", "Is billing-worker safe to deploy to?")
    ...
    grounded = memory.recall("billing-worker")
    # grounded == {"topic": ..., "statements": [...], "version": ..., "source": "semantic"}
    # or None if nothing is known — the agent MUST NOT fabricate an answer.
"""

import time
from pathlib import Path
from typing import Optional

from memory.consolidation import SemanticConsolidation
from memory.episodic_store import EpisodicStore
from memory.router import PromoteOrDropRouter
from memory.scheduler import ConsolidationScheduler
from memory.scratchpad import Scratchpad
from memory.semantic_store import SemanticStore, DEFAULT_STORE_PATH
from memory.short_term import ShortTermBuffer


class MemorySystem:
    def __init__(self, buffer_capacity: int = 50,
                 consolidation_interval_seconds: float = 300,
                 critical_keywords: Optional[list[str]] = None,
                 persist_path: Optional[Path] = DEFAULT_STORE_PATH):
        """persist_path controls cross-session semantic memory: if set (the
        default), semantic facts are loaded from disk on construction and
        saved back after every consolidation pass, so a brand new
        MemorySystem() in a later process — a different engineer's session
        — sees what a previous session already consolidated. Pass None to
        run fully in-memory (e.g. for isolated unit tests)."""
        self.buffer = ShortTermBuffer(capacity=buffer_capacity)
        self.scratchpad = Scratchpad()
        self.episodic = EpisodicStore()
        self.semantic = SemanticStore()
        self.router = PromoteOrDropRouter(self.episodic)
        self.consolidation = SemanticConsolidation(self.episodic, self.semantic)
        self.scheduler = ConsolidationScheduler(
            self.consolidation, interval_seconds=consolidation_interval_seconds
        )
        self._critical_keywords = critical_keywords
        self._turn = 0
        self.persist_path = persist_path

        if self.persist_path is not None:
            loaded_count = self.semantic.load(self.persist_path)
            if loaded_count:
                print(f"[memory] loaded {loaded_count} semantic fact(s) from a previous session "
                      f"({self.persist_path.name})")

    # -- write path: agent calls this after every message exchange ---------

    def remember_turn(self, role: str, content: str) -> None:
        self._turn += 1
        self.buffer.add_message(role, content)
        self.buffer.age_all_messages()

        for evicted in self.buffer.pop_evicted():
            self.router.decide(
                evicted,
                age=evicted.age,
                context={"critical_keywords": self._critical_keywords}
                        if self._critical_keywords else {},
            )

    # -- read path: agent calls this before building a prompt --------------

    def recall(self, topic: str) -> Optional[dict]:
        """Returns a grounded fact with provenance for Self-RAG verification,
        or None. Callers MUST treat None as 'nothing known' — never fill the
        gap with a guess."""
        fact = self.semantic.get_fact(topic)
        if fact is None or fact.get("status") == "expired":
            return None
        self.consolidation.note_reference(topic)
        return {
            "topic": topic,
            "statements": fact["statements"],
            "version": fact["version"],
            "status": fact["status"],
            "source": "semantic",
        }

    def context_for_prompt(self, last_n_messages: int = 10) -> dict:
        """What the agent injects into its next LLM call: recent transcript
        + the untouched scratchpad — never the full buffer."""
        return {
            "recent_messages": [
                {"role": m.role, "content": m.content}
                for m in self.buffer.get_last_n(last_n_messages)
            ],
            "scratchpad": self.scratchpad.snapshot(),
        }

    # -- maintenance ------------------------------------------------------

    def run_consolidation_now(self) -> dict:
        summary = self.consolidation.run_consolidation()
        if self.persist_path is not None:
            self.semantic.save(self.persist_path)
        return summary

    def start_background_consolidation(self) -> None:
        self.scheduler.start()

    def stop_background_consolidation(self) -> None:
        self.scheduler.stop()

    def save_logs(self, router_path: str, consolidation_path: str) -> None:
        self.router.save_log(router_path)
        self.consolidation.save_log(consolidation_path)
