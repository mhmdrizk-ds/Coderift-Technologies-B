# rag/ — Retrieval-Augmented Generation for Coderift Technologies

## The problem this solves

Engineers ask the MCP agent questions the database was never built to
answer: "what's the required approval chain for a hotfix deployment during
an active incident?", "what does our security review policy say about
deploying with a Pending scan?" — answers that only live in the company's
internal policy documents (`resources/*.md`), not in `db/coderift.db`.

## Corpus

Three policy documents in `resources/`, each expanded to 40+ meaningful
statements: `production_deployment_policy.md`, `security_review_policy.md`,
`incident_response_runbook.md`. Chunked by section header with overlap
(`rag/vector_store/chunking.py`), tagged with `policy_type`, `section`,
`last_reviewed_date`, and `severity_applies_to` metadata.

## Build the vector store

```bash
python3 rag/vector_store/vector_db.py
```

Builds/rebuilds the Chroma collection from all three policy docs and runs
`test_filter_narrows_search()`, proving metadata filtering happens
**during** HNSW search (Chroma's `where` parameter), not as a post-filter.

Embeddings: `sentence-transformers/all-MiniLM-L6-v2` if the HuggingFace Hub
is reachable, otherwise a deterministic TF-IDF fallback persisted to disk
(`rag/vector_store/embeddings.py`) so the whole pipeline runs offline with
no network dependency — a real constraint that came up building this in a
sandboxed environment, not a shortcut.

## Files

| File | Concern |
|---|---|
| `vector_store/chunking.py` | Section-header chunking + metadata tagging |
| `vector_store/embeddings.py` | Embedding model, live + offline fallback |
| `vector_store/vector_db.py` | Chroma HNSW index build + the required filter test |
| `vector_store/retrieve.py` | `retrieve_policy_chunks()` — single retrieval API |
| `llm.py` | The one place every module calls a model — live Gemini + offline extractive fallback |
| `naive_rag.py` | Baseline: retrieve → generate |
| `hybrid_rag.py` | Vector + BM25 fused via Reciprocal Rank Fusion |
| `agentic_rag.py` | Retrieve → observe → decide whether to retrieve again |
| `graph_rag.py` | Bonus: NetworkX concept graph, multi-hop traversal |
| `self_rag.py` | Relevance + support verification for RAG answers AND memory recalls |

## Evaluation

See `retrieval_eval/README.md` for the full 12-question comparison across
naive/hybrid/agentic (and `rag/GRAPH_RAG_README.md` for the bonus graph
architecture) with real accuracy/token/latency numbers, and
`context_eval/README.md` for the separate context-window-management
evaluation (which strategy the agent should use to keep long tool-heavy
sessions from losing critical early details).

## Self-RAG verification

Every RAG answer and every memory recall passes through
`self_rag.check_relevance()` and `self_rag.check_support()` before it's
trusted (see `agent/session.py`'s integration). `rag/self_rag.py`'s
`demo_relevance_failure()` and `demo_support_failure()` show real cases
where an unsupported or off-topic answer gets caught and flagged, not
just a description of the mechanism.
