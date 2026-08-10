"""
naive_rag.py — the baseline pipeline: retrieve -> stuff context -> generate.

Deliberately the "dumb" baseline every other strategy gets compared against.
Will handle general questions well, struggle on exact-identifier questions
(TF-IDF/embeddings don't represent "Section 4.2" distinctively), and miss
multi-policy questions (one retrieval round, no re-querying).
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vector_store"))

from retrieve import retrieve_policy_chunks
from rag.llm import generate_answer


def answer_naive(query: str, policy_name: str | None = None, k: int = 5) -> dict:
    start = time.perf_counter()
    docs = retrieve_policy_chunks(query=query, policy_name=policy_name, k=k)
    chunks = [d.page_content for d in docs]
    result = generate_answer(query, chunks)
    latency = time.perf_counter() - start
    return {
        "strategy": "naive",
        "query": query,
        "policy_name": policy_name,
        "answer": result["answer"],
        "source_chunks": [{"content": d.page_content, "metadata": d.metadata} for d in docs],
        "used_live_model": result["used_live_model"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "latency_seconds": latency,
    }
