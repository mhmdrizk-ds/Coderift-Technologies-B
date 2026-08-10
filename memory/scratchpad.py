"""
scratchpad.py — Working state, deliberately separate from ShortTermBuffer.

Real Coderift example:
    scratchpad.update_goal("Deploy payments-service PR #1 to staging")
    scratchpad.add_sub_goal("Check PR approval status")
    scratchpad.add_sub_goal("Run pre-deploy checks")
    scratchpad.add_sub_goal("Confirm no in-flight deployment")
    scratchpad.gather_data("pr_status", "Approved")
    scratchpad.gather_data("scan_status", "Passed")
    scratchpad.mark_sub_goal_done(0)
    scratchpad.next_step = "Run deploy_to_production tool"

No matter how the short-term buffer is pruned, sliding-windowed, or
summarized, this object is untouched — the transcript is disposable working
memory, this is the plan. reset() is only called when the engineer's goal
fully resolves (deployment succeeded or was explicitly cancelled).
"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class SubGoal:
    goal: str
    status: str = "pending"  # pending | done | blocked


class Scratchpad:
    def __init__(self):
        self.current_goal: Optional[str] = None
        self.sub_goals: list[SubGoal] = []
        self.data_gathered: dict[str, Any] = {}
        self.next_step: Optional[str] = None

    def update_goal(self, goal: str) -> None:
        self.current_goal = goal

    def add_sub_goal(self, goal: str, status: str = "pending") -> int:
        self.sub_goals.append(SubGoal(goal=goal, status=status))
        return len(self.sub_goals) - 1

    def mark_sub_goal_done(self, goal_idx: int) -> None:
        self.sub_goals[goal_idx].status = "done"

    def mark_sub_goal_blocked(self, goal_idx: int, reason: str) -> None:
        self.sub_goals[goal_idx].status = f"blocked: {reason}"

    def gather_data(self, key: str, value: Any) -> None:
        self.data_gathered[key] = value

    def snapshot(self) -> dict:
        """Deep copy so callers can compare before/after without aliasing
        bugs — this is what test_pruning.py asserts equality against."""
        return deepcopy({
            "goal": self.current_goal,
            "sub_goals": [{"goal": sg.goal, "status": sg.status} for sg in self.sub_goals],
            "data": self.data_gathered,
            "next": self.next_step,
        })

    def reset(self) -> None:
        """Only called explicitly when a goal is fully resolved — NEVER as a
        side effect of buffer pruning."""
        self.current_goal = None
        self.sub_goals = []
        self.data_gathered = {}
        self.next_step = None
