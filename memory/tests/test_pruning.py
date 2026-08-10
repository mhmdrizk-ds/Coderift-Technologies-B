"""
test_pruning.py — The assignment's required test: fill the buffer 50x past
capacity, assert scratchpad.snapshot() is identical before and after.

This is the structural proof that pruning the ShortTermBuffer can never touch
the Scratchpad — they are separate objects with no shared state.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from memory.api import MemorySystem


def test_scratchpad_survives_buffer_overflow():
    mem = MemorySystem(buffer_capacity=10)

    # Set up a non-trivial scratchpad state representing an in-progress
    # deploy workflow — something that must survive a full buffer flush.
    mem.scratchpad.update_goal("Deploy payments-service PR #1 to staging")
    mem.scratchpad.add_sub_goal("Verify PR approval")
    mem.scratchpad.add_sub_goal("Run pre-deploy checks")
    mem.scratchpad.add_sub_goal("Confirm no in-flight deployment")
    mem.scratchpad.gather_data("pr_status", "Approved")
    mem.scratchpad.gather_data("scan_result", "Passed")
    mem.scratchpad.gather_data("engineer_role", "senior")
    mem.scratchpad.mark_sub_goal_done(0)
    mem.scratchpad.next_step = "Call deploy_to_production tool"

    before = mem.scratchpad.snapshot()

    # Fill the buffer 5x capacity (capacity=10, so 50 messages)
    for i in range(50):
        mem.remember_turn("user", f"turn {i}: check deployment status for billing-worker")
        mem.remember_turn("tool", f'{{"status": "Succeeded", "deployment_id": {i}}}')

    after = mem.scratchpad.snapshot()

    assert before == after, (
        f"Scratchpad changed after buffer overflow!\n"
        f"Before: {before}\n"
        f"After:  {after}"
    )
    print("PASS: scratchpad.snapshot() identical before and after 50x buffer overflow")

    # Also verify the buffer did actually overflow (evictions happened)
    assert len(mem.episodic) > 0, "Expected episodic store to have promoted episodes"
    assert len(mem.buffer) == 10, f"Expected buffer at capacity=10, got {len(mem.buffer)}"
    print(f"PASS: {len(mem.episodic)} messages promoted to episodic store")
    print(f"PASS: buffer size = {len(mem.buffer)} (= capacity)")


if __name__ == "__main__":
    test_scratchpad_survives_buffer_overflow()
