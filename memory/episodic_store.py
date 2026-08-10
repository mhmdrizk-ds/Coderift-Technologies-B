"""
episodic_store.py — Timestamped, append-only store of promoted events.

Written to by exactly one thing: the promote-or-drop router (router.py).
Consolidation (consolidation.py) only ever *reads* from here to build
semantic facts — it never writes episodes. The `consolidated` flag per
episode prevents repeated consolidation passes from reprocessing the same
episode endlessly.
"""

from dataclasses import dataclass, field
from time import time
from typing import Any, Optional


@dataclass
class Episode:
    id: int
    type: str          # "promoted_from_buffer" | "action" | "decision"
    content: str
    timestamp: float
    source: str        # "router" | "direct"
    metadata: dict = field(default_factory=dict)
    consolidated: bool = False  # flips True once a consolidation pass has read it


class EpisodicStore:
    def __init__(self):
        self._episodes: list[Episode] = []
        self._next_id = 0

    def add_episode(self, episode: dict) -> Episode:
        ep = Episode(
            id=self._next_id,
            type=episode.get("type", "event"),
            content=episode["content"],
            timestamp=episode.get("timestamp", time()),
            source=episode.get("source", "router"),
            metadata=episode.get("metadata", {}),
        )
        self._episodes.append(ep)
        self._next_id += 1
        return ep

    def get_all_episodes(self) -> list[Episode]:
        return list(self._episodes)

    def get_unconsolidated(self) -> list[Episode]:
        """What a periodic consolidation pass should process — episodes it
        hasn't seen yet, not the whole store every time."""
        return [ep for ep in self._episodes if not ep.consolidated]

    def mark_consolidated(self, episode_ids: list[int]) -> None:
        ids = set(episode_ids)
        for ep in self._episodes:
            if ep.id in ids:
                ep.consolidated = True

    def get_episodes_since(self, timestamp: float) -> list[Episode]:
        return [ep for ep in self._episodes if ep.timestamp >= timestamp]

    def search_episodes(self, query: str) -> list[Episode]:
        q = query.lower()
        return [ep for ep in self._episodes
                if q in ep.content.lower() or q in str(ep.metadata).lower()]

    def __len__(self) -> int:
        return len(self._episodes)
