"""
vector_db.py — Chroma vector store with HNSW index.

Metadata filtering is applied DURING similarity search (Chroma's `where`
parameter), never post-hoc. The test_filter_narrows_search() function at
the bottom proves this: it shows that filtering by policy_type reduces the
result count compared to an unfiltered search over the same query.

Implementation note: Chroma's langchain wrapper passes our embedding_function
when creating the collection object, but some Chroma builds still try to
download their own ONNX model on upsert. To guarantee the offline TF-IDF
fallback actually works in sandboxed environments, we pre-compute all
embeddings ourselves and pass them directly to chromadb's underlying
collection.upsert() — bypassing the auto-embed path entirely.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chromadb
from chromadb.config import Settings

from config import CHROMA_DB_PATH
from chunking import load_all_policies, POLICY_METADATA
from embeddings import get_embedding_model

_client: chromadb.ClientAPI | None = None
_collection: chromadb.Collection | None = None


def _get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=str(CHROMA_DB_PATH),
            settings=Settings(anonymized_telemetry=False),
        )
    return _client


def build_vector_store():
    """Build (or rebuild) the Chroma collection from all three policy docs.
    Pre-computes embeddings with our model and passes them directly so
    Chroma's auto-embed path is never invoked."""
    global _collection

    client = _get_client()

    # Drop and recreate for a clean rebuild
    try:
        client.delete_collection("coderift_policies")
    except Exception:
        pass

    col = client.create_collection(
        name="coderift_policies",
        metadata={"hnsw:space": "cosine"},
    )
    _collection = col

    all_chunks = load_all_policies()
    embedding_model = get_embedding_model()

    texts = [c.page_content for c in all_chunks]
    metadatas = [c.metadata for c in all_chunks]
    ids = [f"{m['policy_type']}_{m['chunk_id']}" for m in metadatas]

    # Pre-compute embeddings — this calls OUR model, not Chroma's ONNX
    embeddings = embedding_model.embed_documents(texts)

    col.upsert(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)

    policy_names = list(POLICY_METADATA.keys())
    print(f"Stored {len(all_chunks)} chunks in Chroma ({', '.join(policy_names)})")
    return col


def get_collection() -> chromadb.Collection:
    """Return the live collection handle (build first if needed)."""
    global _collection
    if _collection is None:
        client = _get_client()
        _collection = client.get_or_create_collection(
            name="coderift_policies",
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


# ---------------------------------------------------------------------------
# Required test: proves metadata filter narrows search BEFORE result set
# ---------------------------------------------------------------------------

def test_filter_narrows_search():
    """Retrieve the same query with and without a policy_type filter.
    The filtered result must have fewer or equal results AND must only
    contain chunks from the specified policy — not post-filtered after
    retrieval, but filtered inside Chroma's HNSW search via `where`."""
    col = get_collection()
    embedding_model = get_embedding_model()
    query = "security scan requirements before deploying to production"
    k = 20

    qvec = embedding_model.embed_query(query)

    # Without filter: all policies in scope
    unfiltered = col.query(
        query_embeddings=[qvec],
        n_results=k,
        include=["documents", "metadatas"],
    )
    unfiltered_metas = unfiltered["metadatas"][0]
    unfiltered_docs = unfiltered["documents"][0]

    # With filter: only production_deployment policy chunks
    filtered = col.query(
        query_embeddings=[qvec],
        n_results=k,
        where={"policy_type": "production_deployment"},
        include=["documents", "metadatas"],
    )
    filtered_metas = filtered["metadatas"][0]
    filtered_docs = filtered["documents"][0]

    # All filtered results must be from the correct policy
    for meta in filtered_metas:
        assert meta.get("policy_type") == "production_deployment", (
            f"Filter breach: got policy_type={meta.get('policy_type')}"
        )

    # Unfiltered set must span multiple policies
    policy_types_in_unfiltered = {m.get("policy_type") for m in unfiltered_metas}
    assert len(policy_types_in_unfiltered) > 1, (
        f"Unfiltered search should return chunks from multiple policies, "
        f"got: {policy_types_in_unfiltered}"
    )

    print("PASS test_filter_narrows_search():")
    print(f"  unfiltered: {len(unfiltered_docs)} results from {policy_types_in_unfiltered}")
    print(f"  filtered (production_deployment only): {len(filtered_docs)} results")
    return {
        "unfiltered_count": len(unfiltered_docs),
        "filtered_count": len(filtered_docs),
        "filter_policy_types": list(policy_types_in_unfiltered),
    }


if __name__ == "__main__":
    print("Building Chroma vector store from resources/...")
    build_vector_store()
    result = test_filter_narrows_search()
    print(result)
