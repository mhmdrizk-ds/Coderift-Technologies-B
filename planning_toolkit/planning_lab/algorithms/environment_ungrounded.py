"""
environment_ungrounded.py — a deliberately UNGROUNDED evaluator, kept
strictly for the lab's required grounded-vs-ungrounded contrast demo.

This is the reference toolkit's original `environment.py` behavior (a
randomized/self-referential score with no connection to the real database)
kept alive under its own name, on purpose, rather than deleted, so the
comparison the lab asks for — "an ungrounded LATS is expensive theater" —
can be run and measured rather than just asserted in prose.

NEVER used as this system's shipped default. `agent.py` (the routing
layer) only ever constructs the real, DB-backed `Environment` from
`environment.py` for actual remediation runs; this class is imported
exclusively by `planning_eval/run_eval.py`'s grounded-vs-ungrounded LATS
comparison row and by `lats.py`'s own `__main__` contrast demo.
"""

from __future__ import annotations

import random

from ..models import EnvironmentFeedback


class UngroundedEnvironment:
    """Same `.evaluate(state) -> EnvironmentFeedback` interface as the real
    `Environment`, but the score has no connection to the real Coderift
    database — it's a pseudo-random function of the state text's own
    length/hash, exactly the kind of "does the model like its own output"
    signal the lab brief calls out as unsafe to ship. `seed` is fixed so a
    given state string always gets the same fake score in a given process,
    which makes it possible to demonstrate reproducibly that this evaluator
    ALWAYS passes some malformed/unsafe action (the concrete failure case
    the real Environment catches — see planning_eval/README.md)."""

    def __init__(self, seed: int = 0):
        self._seed = seed

    def evaluate(self, state: str) -> EnvironmentFeedback:
        if not isinstance(state, str):
            raise TypeError("state must be a string")
        rng = random.Random(self._seed ^ hash(state) & 0xFFFFFFFF)
        # Deliberately biased toward "looks fine" — length and punctuation
        # density correlate with nothing about whether the action is
        # actually safe to run against the real deployments/PRs/incidents
        # tables, which is exactly the point: this is what "the model
        # judging its own output" degrades to once you write it down as a
        # function instead of describing it in prose.
        base = 0.55 + 0.35 * rng.random()
        looks_structured = state.strip().startswith("{") and state.strip().endswith("}")
        score = min(1.0, base + (0.1 if looks_structured else 0.0))
        success = score >= 0.6
        details = [] if success else ["[ungrounded] state did not resemble a JSON action payload"]
        return EnvironmentFeedback(success=success, score=round(score, 4), details=details)
