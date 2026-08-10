"""
Zone-Based Pruning Context Strategy

Splits context into logical zones and removes low-priority information
while preserving important facts.

Purpose:
- Keep critical information.
- Preserve system instructions.
- Reduce unnecessary context.
"""

from typing import List, Dict, Any

# Coderift domain: zone 2 critical-information keywords.
CRITICAL_KEYWORDS = [
    # Deployment stability
    "consecutive failed", "deployment-unstable", "unstable", "3 consecutive",

    # Incidents
    "critical incident", "active incident", "open incident", "incident #",
    "critical severity", "high severity",

    # Security
    "failed scan", "security scan failed", "sast failed", "cve-",
    "sql injection", "vulnerability", "hardcoded secret",

    # Deployment/release decisions
    "cannot be released", "must not deploy", "should not deploy",
    "blocked", "deployment blocked", "halt all deployments",
    "rollback", "rolled back", "not safe",

    # Authorization
    "unauthorized", "not authorized", "requires lead", "requires senior",

    # Elicitation / overrides
    "elicitation", "override", "human confirmation", "confirm:",

    # Risk assessment
    "high risk", "manual review required", "escalate", "escalation",
]


def apply_zone_based_pruning(
    messages: List[Dict[str, Any]],
    keep_system_prompt: bool = True,
    keep_scratchpad: bool = True
) -> List[Dict[str, Any]]:
    """Apply zone-based context pruning.

    Args:
        messages: Full transcript messages.
        keep_system_prompt: Keep system messages.
        keep_scratchpad: Keep important stored facts.

    Returns:
        Reduced messages.
    """

    pruned_messages = []

    for message in messages:
        role = message.get("role")
        content = str(message.get("content", "")).lower()

        # Zone 1: System messages
        if keep_system_prompt and role == "system":
            pruned_messages.append(message)
            continue

        # Zone 2: Critical information
        if any(keyword in content for keyword in CRITICAL_KEYWORDS):
            pruned_messages.append(message)
            continue

        # Zone 3: Recent messages (last ~5 turns worth, by absolute turn number
        # from the end of the transcript)
        max_turn = max((m.get("turn", 0) for m in messages), default=0)
        if message.get("turn", 0) >= max_turn - 5:
            pruned_messages.append(message)
            continue

    return pruned_messages


def get_strategy_name() -> str:
    return "zone_based_pruning"
