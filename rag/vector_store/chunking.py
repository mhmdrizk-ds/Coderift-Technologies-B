"""
chunking.py — Chunk each policy document by section headers, with overlap.
Tags chunks with metadata: policy_type, section, last_reviewed_date,
severity_applies_to.

Why section-header splitting matters: the retrieval eval has exact-ID
questions like "What does Section 4.2 say?" — a splitter that breaks across
section boundaries makes those questions harder to answer. We use section
headers as preferred split points so a chunk either starts at a section or
at a paragraph break within one, never mid-sentence across two sections.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from config import DOCUMENTS_PATH, CHUNK_SIZE, CHUNK_OVERLAP

# Metadata lookup for each policy file (stem → metadata dict)
POLICY_METADATA: dict[str, dict] = {
    "production_deployment_policy": {
        "policy_type": "production_deployment",
        "last_reviewed_date": "2026-08-01",
        "severity_applies_to": "all",
    },
    "security_review_policy": {
        "policy_type": "security_review",
        "last_reviewed_date": "2026-08-01",
        "severity_applies_to": "high,critical",
    },
    "incident_response_runbook": {
        "policy_type": "incident_response",
        "last_reviewed_date": "2026-08-01",
        "severity_applies_to": "low,medium,high,critical",
    },
}

_SECTION_RE = re.compile(r"^#{1,3} ", re.MULTILINE)

splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    add_start_index=True,
    separators=[
        "\n## ",
        "\n### ",
        "\n\n",
        "\n",
        ". ",
        " ",
        "",
    ],
)


def _extract_section_header(text: str) -> str:
    """Best-effort: return the first markdown header in the chunk text,
    or 'General' if none."""
    match = re.search(r"^#{1,3}\s+(.+)$", text, re.MULTILINE)
    if match:
        return match.group(1).strip()
    # If no header in the chunk, look for the last numbered-section line
    match2 = re.search(r"^##+ Section \d+", text, re.MULTILINE)
    if match2:
        return match2.group(0).strip()
    return "General"


def load_markdown(path: Path) -> Document:
    if not path.exists():
        raise FileNotFoundError(f"Policy file not found: {path}")
    text = path.read_text(encoding="utf-8")
    return Document(
        page_content=text,
        metadata={"source": str(path), "filename": path.name},
    )


def chunk_document(document: Document, policy_stem: str) -> list[Document]:
    base_meta = POLICY_METADATA.get(policy_stem, {
        "policy_type": policy_stem,
        "last_reviewed_date": "unknown",
        "severity_applies_to": "all",
    })

    chunks = splitter.split_documents([document])
    total = len(chunks)
    for i, chunk in enumerate(chunks):
        section = _extract_section_header(chunk.page_content)
        chunk.metadata.update({
            **base_meta,
            "chunk_id": i,
            "total_chunks": total,
            "section": section,
            "source_file": document.metadata.get("filename", ""),
        })
    return chunks


def load_and_chunk(path: Path) -> list[Document]:
    doc = load_markdown(path)
    stem = path.stem
    return chunk_document(doc, stem)


def load_all_policies() -> list[Document]:
    all_chunks = []
    for stem in POLICY_METADATA:
        path = DOCUMENTS_PATH / f"{stem}.md"
        if path.exists():
            all_chunks.extend(load_and_chunk(path))
    return all_chunks
