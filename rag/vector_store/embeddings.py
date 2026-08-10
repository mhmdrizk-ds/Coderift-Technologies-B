"""
embeddings.py — sentence-transformers/all-MiniLM-L6-v2 for production
(if the HuggingFace Hub is reachable); TF-IDF offline fallback otherwise.

The TF-IDF fallback is pickled to disk after fitting on the corpus so that
query() calls use the EXACT same vocabulary as the indexed documents —
dimension mismatch between build-time and query-time would make Chroma reject
the query vector.

Both paths implement embed_documents(list[str]) and embed_query(str) so the
rest of the stack is unaffected by which path is active.
"""

import math
import pickle
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import CHROMA_DB_PATH

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
TFIDF_PICKLE_PATH = CHROMA_DB_PATH / "tfidf_model.pkl"

_model = None


# ---------------------------------------------------------------------------
# Offline TF-IDF fallback — vocab is persisted to match build vs query dim
# ---------------------------------------------------------------------------

class _TFIDFEmbeddings:
    """Deterministic, zero-network embedding using TF-IDF vectors.
    Vocab is fixed after the first fit; subsequent calls are consistent."""

    def __init__(self, max_features: int = 512):
        self.max_features = max_features
        self._vocab: dict[str, int] = {}
        self._idf: dict[str, float] = {}
        self._fitted = False

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def _fit(self, texts: list[str]) -> None:
        from collections import Counter
        df: Counter = Counter()
        all_tfs = []
        for text in texts:
            tokens = self._tokenize(text)
            tf = Counter(tokens)
            all_tfs.append(tf)
            df.update(tf.keys())
        n = max(len(texts), 1)
        top_terms = [t for t, _ in df.most_common(self.max_features)]
        self._vocab = {t: i for i, t in enumerate(top_terms)}
        self._idf = {
            t: math.log((n + 1) / (df[t] + 1)) + 1.0
            for t in top_terms
        }
        self._fitted = True
        _save_tfidf(self)

    def _vectorize(self, text: str) -> list[float]:
        from collections import Counter
        tokens = self._tokenize(text)
        tf = Counter(tokens)
        total = max(sum(tf.values()), 1)
        dim = len(self._vocab)
        if dim == 0:
            return [0.0]
        vec = [0.0] * dim
        for term, idx in self._vocab.items():
            tfidf = (tf.get(term, 0) / total) * self._idf.get(term, 1.0)
            vec[idx] = tfidf
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not self._fitted:
            self._fit(texts)
        return [self._vectorize(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        if not self._fitted:
            raise RuntimeError(
                "TF-IDF model not fitted. Call embed_documents() on the full "
                "corpus before querying, or load a saved model."
            )
        return self._vectorize(text)


def _save_tfidf(model: _TFIDFEmbeddings) -> None:
    CHROMA_DB_PATH.mkdir(parents=True, exist_ok=True)
    with open(TFIDF_PICKLE_PATH, "wb") as f:
        pickle.dump(model, f)


def _load_tfidf() -> _TFIDFEmbeddings | None:
    if not TFIDF_PICKLE_PATH.exists():
        return None
    try:
        with open(TFIDF_PICKLE_PATH, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API: try sentence-transformers, fall back to persisted TF-IDF
# ---------------------------------------------------------------------------

def get_embedding_model():
    """Return a sentence-transformers model if available, otherwise the
    persisted TF-IDF offline fallback. The return type is duck-typed
    (both have embed_documents/embed_query) so callers don't need to branch."""
    global _model
    if _model is not None:
        return _model
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
        candidate = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL_NAME,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        candidate.embed_query("test")
        _model = candidate
        print(f"[embeddings] Using sentence-transformers/{EMBEDDING_MODEL_NAME}")
    except Exception:
        # Try to load a previously fitted and saved TF-IDF model first so
        # query() calls match the build-time vocabulary exactly.
        saved = _load_tfidf()
        if saved is not None:
            _model = saved
            print("[embeddings] Loaded persisted TF-IDF model from disk")
        else:
            _model = _TFIDFEmbeddings(max_features=512)
            print("[embeddings] TF-IDF offline fallback (not yet fitted)")
    return _model
