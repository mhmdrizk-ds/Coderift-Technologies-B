"""Public algorithm API. Was empty before this addition — kept minimal and
additive (no re-exports of existing modules changed) so nothing that
already imports from planning_lab.algorithms.<module> directly is
affected. This only adds one new, explicit export for Task 3's handoff
entry point."""

from .dynamic_decomposition import build_dynamic_plan

__all__ = ["build_dynamic_plan"]
