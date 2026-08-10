"""
hybrid_rag.py — vector similarity + BM25 keyword scoring, fused via RRF.

Why this exists: dense embeddings blur exact identifiers. "Section 4.2" or
"ENG-LEAD-01" doesn't embed distinctively — cosine similarity retrieves a
semantically-related-but-wrong chunk. BM25 scores the literal tokens, so
combining the two signals recovers exact-identifier questions that naive RAG
misses, without giving up semantic recall for general questions.

Fusion: Reciprocal Rank Fusion (RRF, K=60). Chosen over weighted-score sum
because vector distance and BM25 live on completely different, non-comparable
scales. RRF only needs ranking order, so there's no brittle normalization.
"""

import re
import sys
import time
from pathlib import Path

from rank_bm25 import BM25Okapi

sys.path.insert(0, str(Path(__file__).resolve().parent / "vector_store"))

from retrieve import retrieve_policy_chunks, get_all_chunks_for_policy
from vector_db import get_collection
from embeddings import get_embedding_model
from rag.llm import generate_answer

RRF_K = 60


def _tokenize(text: str) -> list[str]:
    """Decimal section numbers ('4.2') must survive as ONE token, not split
    into '4' and '2' at the period — that's exactly the exact-identifier
    signal BM25 exists to recover for questions like 'what does Section 4.2
    say?'. Match decimal numbers first, then fall back to word/int tokens."""
    return re.findall(r"\d+\.\d+|[a-z0-9]+", text.lower())


def _chunk_id(metadata: dict) -> str:
    return f"{metadata['policy_type']}_{metadata['chunk_id']}"


def answer_hybrid(query: str, policy_name: str | None = None, k: int = 5,
                   vector_k: int = 8, bm25_k: int = 8) -> dict:
    start = time.perf_counter()

    # Determine corpus scope: one policy or all
    if policy_name:
        corpus_chunks = get_all_chunks_for_policy(policy_name)
    else:
        # All policies: retrieve broadly, then BM25 over all chunks
        from vector_store.retrieve import retrieve_policy_chunks as rpc
        # Get all chunks without filter for BM25
        col = get_collection()
        raw = col.get(include=["documents", "metadatas"])
        from retrieve import PolicyChunk
        corpus_chunks = [
            PolicyChunk(page_content=d, metadata=m)
            for d, m in zip(raw["documents"], raw["metadatas"])
        ]

    corpus = {_chunk_id(c.metadata): c for c in corpus_chunks}
    corpus_ids = list(corpus.keys())

    # --- vector ranking -------------------------------------------------
    col = get_collection()
    embedding_model = get_embedding_model()
    qvec = embedding_model.embed_query(query)
    kwargs = {"query_embeddings": [qvec], "n_results": min(vector_k, len(corpus_ids)),
              "include": ["documents", "metadatas"]}
    if policy_name:
        kwargs["where"] = {"policy_type": policy_name.lower()}
    vector_results = col.query(**kwargs)
    vector_rank = [
        _chunk_id(m) for m in vector_results["metadatas"][0]
    ]

    # --- BM25 keyword ranking -------------------------------------------
    tokenized_corpus = [_tokenize(corpus[cid].page_content) for cid in corpus_ids]
    bm25 = BM25Okapi(tokenized_corpus)
    scores = bm25.get_scores(_tokenize(query))
    bm25_rank = [
        cid for cid, _ in sorted(zip(corpus_ids, scores), key=lambda x: x[1], reverse=True)
    ][:bm25_k]

    # --- Reciprocal Rank Fusion -----------------------------------------
    fused: dict[str, float] = {}
    for rank_list in (vector_rank, bm25_rank):
        for rank, cid in enumerate(rank_list):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)

    ranked_ids = sorted(fused, key=lambda cid: fused[cid], reverse=True)[:k]
    top_chunks = [corpus[cid] for cid in ranked_ids]

    result = generate_answer(query, [c.page_content for c in top_chunks])
    latency = time.perf_counter() - start

    return {
        "strategy": "hybrid",
        "query": query,
        "policy_name": policy_name,
        "answer": result["answer"],
        "source_chunks": [{"content": c.page_content, "metadata": c.metadata} for c in top_chunks],
        "fusion_scores": {cid: fused[cid] for cid in ranked_ids},
        "used_live_model": result["used_live_model"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "latency_seconds": latency,
    }
