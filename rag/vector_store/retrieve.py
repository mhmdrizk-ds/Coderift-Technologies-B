"""
retrieve.py — retrieve_policy_chunks() is the single retrieval API the RAG
modules call. Metadata filtering happens INSIDE Chroma's HNSW search via the
`where` parameter, not after retrieval.
"""

import sys
from pathlib import Path
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import CHROMA_DB_PATH
from embeddings import get_embedding_model
from vector_db import get_collection


@dataclass
class PolicyChunk:
    """Mimics langchain_core.documents.Document interface so RAG modules
    don't need to branch on which embedding path is active."""
    page_content: str
    metadata: dict


def retrieve_policy_chunks(
    query: str,
    policy_name: str | None = None,
    k: int = 5,
) -> list[PolicyChunk]:
    """Retrieve up to k chunks. If policy_name is given, filter by
    policy_type BEFORE scoring (Chroma `where` param) — never post-filter."""
    col = get_collection()
    embedding_model = get_embedding_model()
    qvec = embedding_model.embed_query(query)

    kwargs: dict = {
        "query_embeddings": [qvec],
        "n_results": k,
        "include": ["documents", "metadatas"],
    }
    if policy_name:
        kwargs["where"] = {"policy_type": policy_name.lower()}

    results = col.query(**kwargs)

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    return [PolicyChunk(page_content=d, metadata=m) for d, m in zip(docs, metas)]


def get_all_chunks_for_policy(policy_name: str) -> list[PolicyChunk]:
    """Return every chunk for a given policy_type — used by hybrid_rag.py to
    build its BM25 corpus over the same search space the vector side uses."""
    col = get_collection()
    raw = col.get(
        where={"policy_type": policy_name.lower()},
        include=["documents", "metadatas"],
    )
    return [
        PolicyChunk(page_content=d, metadata=m)
        for d, m in zip(raw["documents"], raw["metadatas"])
    ]
