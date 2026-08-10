from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DOCUMENTS_PATH = PROJECT_ROOT / "resources"
CHROMA_DB_PATH = PROJECT_ROOT / "rag" / "vector_store" / "chroma_db"
CHUNK_SIZE = 600
CHUNK_OVERLAP = 100
