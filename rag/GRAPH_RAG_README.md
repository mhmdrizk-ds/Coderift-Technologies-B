# Graph RAG (bonus) — `rag/graph_rag.py`

## Why a graph applies here

Coderift's policy corpus isn't a flat pile of unrelated passages — it has
real causal relationships worth modeling explicitly:

- A **failed security scan** triggers a **security review**, which can
  **block a deployment** (or require an **elicitation override**, which
  itself requires **lead authorization** and an **audit log** entry).
- An **active critical incident** can trigger a **deployment halt**, which
  still **allows a rollback** during the halt.
- A **critical incident** requires both **paging** and, later, a
  **postmortem** — and a **rollback** feeds into that postmortem as input.
- **3 consecutive failed deployments** can cause a **deployment-instability
  flag**, which *should* trigger a **deployment halt** even without a
  currently-open incident.

`CONCEPT_EDGES` in `graph_rag.py` encodes 19 such trigger relationships as
a directed graph (NetworkX `DiGraph`), each tagged with the specific policy
section it comes from. `answer_graph(query)` finds entry concepts by
keyword match, does a 2-hop traversal (both successors and predecessors) to
pull in causally-connected concepts the query didn't explicitly name, and
uses the expanded concept set to build a richer vector search query.

## Evaluation against the same 12-question set

| Architecture | Overall | General | Exact-ID | Multi-part | Avg tokens/query | Avg latency/query |
|---|---|---|---|---|---|---|
| naive | 58% | 75% | 100% | 0% | 860 | 0.054s |
| hybrid | 58% | 75% | 100% | 0% | 866 | 0.008s |
| agentic | 67% | 75% | 100% | 25% | 1343 | 0.007s |
| **graph** | **58%** | **75%** | **100%** | **0%** | 985 | 0.062s |

Real numbers from a real run against `retrieval_eval/questions.json`.

## Honest finding: graph RAG does not beat agentic here

Graph RAG ties naive/hybrid overall (58%) and, notably, does **not** win
on multi-part questions in this evaluation — it scores 0%, same as
naive/hybrid, and worse than agentic's 25%. This is worth reporting
honestly rather than glossing over:

- On q9 (the question agentic *does* get right), graph RAG's concept
  traversal correctly identifies `consecutive_failed_deployments`,
  `active_critical_incident`, `deployment_halt`, `paging`, and
  `postmortem` as relevant concepts — the traversal itself is working as
  designed. But feeding all of those expanded terms into a single vector
  query dilutes the search compared to agentic's approach of retrieving
  from each policy *separately* with the original question, then combining
  results after an explicit relevance check per retrieval round.
- Graph RAG's real strength — surfacing the *causal chain* a question
  touches, with traceable source sections (see `source_sections` in
  `answer_graph()`'s return value) — is a genuine capability naive, hybrid,
  and agentic don't have. But for this specific evaluation's accuracy
  metric (keyword overlap in the final generated answer), that structural
  advantage doesn't translate into a better score, because the bottleneck
  is the same offline extractive `generate_answer()` every strategy shares,
  which caps the answer to only 3-6 sentences.

## What this means for a ship decision

Given this evaluation, Coderift would **not** ship graph RAG in place of
agentic RAG for its production query-answering path — agentic wins on the
metric that matters (accuracy on the query pattern Coderift actually has).
Graph RAG's traversal output (`traversed_concepts`, `source_sections`) is
still valuable as a **debugging and policy-authoring tool**: an engineer
maintaining the policy documents could use `answer_graph()`'s concept
traversal to check "if I change the failed-scan override rule, what
else in the causal chain does that touch?" — a question none of naive,
hybrid, or agentic RAG can answer at all, since none of them model
relationships between concepts, only similarity between text chunks.
