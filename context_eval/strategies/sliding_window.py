"""
Sliding Window Context Strategy

Keeps only the latest N messages from the transcript.

Purpose:
- Reduce context size.
- Simulate limited context memory.
- Evaluate whether old critical facts are lost.
"""

from typing import List, Dict, Any


def apply_sliding_window(
    messages: List[Dict[str, Any]],
    window_size: int = 20
) -> List[Dict[str, Any]]:

    if window_size <= 0:
        return []

    system_messages = [m for m in messages if m.get("role") == "system"]
    non_system_messages = [m for m in messages if m.get("role") != "system"]

    recent_messages = non_system_messages[-window_size:]

    return system_messages + recent_messages


def get_strategy_name() -> str:
    return "sliding_window"
