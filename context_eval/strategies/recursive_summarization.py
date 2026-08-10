"""
Recursive Summarization Context Strategy

Compresses old conversation history into a summary while keeping recent
messages unchanged.

Purpose:
- Reduce context size.
- Preserve important historical facts.
- Test whether summarization maintains critical decisions.
"""

import re
from typing import List, Dict, Any

CONTAINER_ID_PATTERN = re.compile(r"\bPR\s*#?\d+\b|\bdeployment\s*#?\d+\b|\bincident\s*#?\d+\b", re.IGNORECASE)

# Coderift domain: terms that signal an operationally important fact worth
# keeping in a compressed summary rather than discarding.
IMPORTANCE_MARKERS = [
    "consecutive failed", "unstable", "critical incident", "critical", "high severity",
    "failed", "failed scan", "failed deployment", "security scan", "sast", "cve",
    "vulnerability", "sql injection", "rollback", "rolled back", "not safe",
    "cannot be released", "must not", "should not", "halt", "block", "blocked",
    "override", "elicitation", "unauthorized", "unapproved", "pending scan",
    "open incident", "active incident", "escalate", "page", "on-call",
]


def apply_recursive_summarization(
    messages: List[Dict[str, Any]],
    summary_max_tokens: int,
    keep_recent_messages: int
) -> List[Dict[str, Any]]:
    """Replace old messages with a generated summary."""

    if len(messages) <= keep_recent_messages:
        return messages

    old_messages = messages[:-keep_recent_messages]
    recent_messages = messages[-keep_recent_messages:]

    summary = create_summary(old_messages, summary_max_tokens)

    summary_message = {
        "turn": old_messages[-1].get("turn"),
        "role": "system",
        "content": summary,
    }

    return [summary_message] + recent_messages


def create_summary(messages: List[Dict[str, Any]], max_tokens: int) -> str:
    """Creates a compact summary from old messages.

    This is a simplified implementation. In production, an LLM would
    generate this summary (see rag/llm.py's generate_answer for the pattern
    used elsewhere in this project — the same live/offline-fallback split
    applies conceptually)."""

    important_points = []

    for message in messages:
        content = str(message.get("content", ""))
        lowered = content.lower()

        is_important = CONTAINER_ID_PATTERN.search(content) is not None

        if not is_important:
            for marker in IMPORTANCE_MARKERS:
                if marker in lowered:
                    is_important = True
                    break

        if is_important:
            important_points.append(content)

    summary = "\n".join(important_points[:10])
    return summary[:max_tokens]


def get_strategy_name() -> str:
    return "recursive_summarization"
