# Task Decomposition & Planning Lab (Mistral)

This executable lab turns the Week 4 concepts into a compact agent pipeline:

- **Decomposition-first:** Mistral produces a structured task DAG.
- **DAG validation:** Pydantic validates ids and dependencies; topological sorting rejects cycles.
- **Parallel scheduling:** independent nodes run in the same dependency-safe batch.
- **Dynamic decomposition:** `--mode dynamic` interleaves planning and observations.
- **Plan-and-Solve:** `--mode ps` uses an explicit plan phase followed by a solution phase.
- **Tree of Thoughts:** `--mode tot` performs bounded generate/evaluate/beam-search.
- **Reflection:** DAG mode uses an independent critic plus deterministic grounding checks before revision.
- **Reflexion:** `--mode reflexion` retries the full task and carries bounded verbal memory across trials.
- **LATS:** `--mode lats` runs a compact MCTS loop with action generation, a value function,
  external environment feedback, branch reflection, UCT selection, and value backpropagation.

Structured model outputs are enforced with Pydantic schemas through LangChain's maintained
`ChatMistralAI.with_structured_output(..., method="json_schema")` integration. Task-graph
validation, topological ordering, parallel generations, and terminal-node discovery use NetworkX
instead of local graph algorithms.

## Code layout

Each algorithm has a focused implementation module:

- `algorithms/decomposition.py` — DAG generation, scheduling, and execution
- `algorithms/dynamic_decomposition.py` — interleaved planning and execution
- `algorithms/plan_and_solve.py` — Plan-and-Solve prompting
- `algorithms/tree_of_thoughts.py` — candidate generation, evaluation, and beam search
- `algorithms/self_refine.py` — one-draft critique and revision
- `algorithms/reflexion.py` — multi-trial episodic reflection
- `algorithms/lats.py` — LATS/MCTS search
- `algorithms/environment.py` — swappable external feedback interface

## Setup

The supplied environment is `venv`, and `.env` must contain `MISTRAL_API_KEY`.

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run

Run these commands from this directory:

```powershell
# Full decomposition-first DAG, execution, grounded critique, and refinement
.\venv\Scripts\python.exe -m planning_lab.cli "Design a 60-minute phishing-awareness workshop for new employees"

# Dynamic/interleaved decomposition
.\venv\Scripts\python.exe -m planning_lab.cli "Investigate why customer onboarding completion fell" --mode dynamic

# One-call Plan-and-Solve
.\venv\Scripts\python.exe -m planning_lab.cli "A project has 3 developers for 10 days. Estimate capacity at 6 focused hours per day." --mode ps

# Bounded Tree-of-Thoughts search (more API calls)
.\venv\Scripts\python.exe -m planning_lab.cli "Propose a low-cost launch strategy for a student productivity app" --mode tot --depth 2 --beam-width 2

# Reflexion: retry the entire task with episodic memory from failed trials
.\venv\Scripts\python.exe -m planning_lab.cli "Create a structured security checklist for a small API" --mode reflexion --max-trials 3 --memory-size 2

# LATS: MCTS-guided candidates scored by a randomized external environment
.\venv\Scripts\python.exe -m planning_lab.cli "Create a structured security checklist for a small API" --mode lats --iterations 2 --n-actions 2
```

Each run prints its result and saves a JSON trace in `artifacts/`, making plans, node outputs,
critic feedback, episodic memories, MCTS visits, external scores, and branch reflections inspectable.
To compare cost, note that PS uses one call; DAG mode uses one planning call plus one per node
and up to two reflection calls; ToT grows with depth and beam width; LATS adds a value and,
on failure, a reflection call for every real environment interaction.

`Environment` is a deliberately simple randomized evaluator outside the model. Scores
come from a beta distribution biased toward favorable evaluations; `--success-threshold` controls
the cutoff. A seeded `random.Random` can be injected for reproducible runs. For a code lab, replace
its `evaluate()` method with a pytest runner; for an API or web lab, return `EnvironmentFeedback`
from the actual tool result. The algorithms depend only on this small environment protocol.

## Test

Tests use a deterministic fake model and never spend API credits:

```powershell
.\venv\Scripts\python.exe -m pytest -q
```

## Important 
This repo is built on top of langchain and tested on the mistral API, changing either should take about 10 minutes with small modifications.

## Suggested exercises

1. Introduce a cycle in a test plan and observe validation fail before execution.
2. Compare sequential execution with the reported parallel batches.
3. Make an early dynamic-decomposition observation fail and inspect how the next task changes.
4. Compare PS with ToT on a problem that needs lookahead, then count API calls.
5. Remove the deterministic checks and compare the critic's behavior with grounded reflection.
6. Raise `--success-threshold` and inspect how more failed Reflexion trials change episodic memory.
7. Compare ToT's model-only scores with LATS's environment scores and UCT visit counts in the artifacts.
