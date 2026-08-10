"""
Observation Masking Context Strategy

Masks old tool outputs while keeping the conversation structure.

Purpose:
- Reduce context size.
- Keep user/assistant messages visible.
- Test if important tool observations are preserved.
"""

from typing import List, Dict, Any


def apply_observation_masking(
    messages: List[Dict[str, Any]],
    keep_recent_tool_outputs: int,
    mask_text: str
) -> List[Dict[str, Any]]:

    tool_indexes = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    keep_indexes = set(tool_indexes[-keep_recent_tool_outputs:])

    masked_messages = []
    for index, message in enumerate(messages):
        new_message = message.copy()
        if message.get("role") == "tool" and index not in keep_indexes:
            new_message["content"] = mask_text
        masked_messages.append(new_message)

    return masked_messages


def get_strategy_name() -> str:
    return "observation_masking"
