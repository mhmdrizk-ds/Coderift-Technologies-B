"""
short_term.py — Rolling short-term message buffer.

Real need this solves for Coderift Technologies:
An MCP session is one engineer's shift at the keyboard. A session checking PR
status, running pre-deploy checks (3 stages of JSON output), reading security
scan history, and finally asking "is billing-worker safe to deploy to?" can
easily span 40+ turns. We keep only the most recent N turns in full detail so
prompts stay small — but we must never silently lose the thing the engineer is
actively working on. That's why scratchpad.py is a deliberately separate
object: pruning this buffer must never be able to touch it.
"""

from collections import deque
from dataclasses import dataclass
from time import time
from typing import Optional


@dataclass
class Message:
    role: str
    content: str
    timestamp: float
    age: int = 0


class ShortTermBuffer:
    def __init__(self, capacity: int = 50):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self.messages: deque[Message] = deque(maxlen=capacity)
        self._evicted: list[Message] = []  # staged hand-off for the promote-or-drop router

    def add_message(self, role: str, content: str, timestamp: Optional[float] = None) -> Message:
        """Add a message. If the buffer is already full, the oldest message is
        evicted and staged in `_evicted` so the caller can hand it to the
        promote-or-drop router before it disappears for good."""
        was_full = self.is_full()
        oldest_before = self.messages[0] if was_full else None

        msg = Message(role=role, content=content, timestamp=timestamp if timestamp is not None else time())
        self.messages.append(msg)

        if was_full and oldest_before is not None:
            self._evicted.append(oldest_before)

        return msg

    def age_all_messages(self) -> None:
        """Increment age for every message still resident. Call once per turn
        so the router has a real 'how many turns old' signal to reason over."""
        for msg in self.messages:
            msg.age += 1

    def is_full(self) -> bool:
        return len(self.messages) == self.messages.maxlen

    def get_oldest_message(self) -> Optional[Message]:
        return self.messages[0] if self.messages else None

    def pop_evicted(self) -> list[Message]:
        """Drain and return messages that fell off the buffer since the last
        call. The buffer never deletes information outright — it only hands it
        off to the router, which is the only component allowed to decide
        forget vs promote."""
        drained, self._evicted = self._evicted, []
        return drained

    def get_last_n(self, n: int) -> list[Message]:
        return list(self.messages)[-n:]

    def __len__(self) -> int:
        return len(self.messages)
