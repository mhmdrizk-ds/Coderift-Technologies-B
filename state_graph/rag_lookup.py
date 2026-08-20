from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

RUNBOOK_PATH = Path(__file__).parent.parent / "resources" / "incident_response_runbook.md"


def lookup_runbook_guidance(query: str, k: int = 3) -> dict:
    result = _try_agentic_retriever(query, k)
    if result is not None:
        return result
    return _keyword_fallback(query, k)


def _try_agentic_retriever(query: str, k: int) -> Optional[dict]:
    try:
        from rag.agentic_rag import agentic_retrieve  # existing repo module
    except Exception:
        return None
    try:
        hits = agentic_retrieve(query, k=k) 
        citations = [getattr(h, "section", None) or getattr(h, "id", "runbook")
                      for h in hits]
        guidance = "\n".join(getattr(h, "text", str(h)) for h in hits)
        return {"guidance": guidance, "citations": citations, "source": "agentic"}
    except Exception:
        return None


def _keyword_fallback(query: str, k: int) -> dict:
    if not RUNBOOK_PATH.exists():
        return {
            "guidance": "(runbook not found on disk — no guidance retrieved)",
            "citations": [],
            "source": "keyword_fallback",
        }

    text = RUNBOOK_PATH.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^(#{1,3} .+)$", text)
   
    chunks = []
    for i in range(1, len(sections), 2):
        header = sections[i].strip("# ").strip()
        body = sections[i + 1].strip() if i + 1 < len(sections) else ""
        chunks.append((header, body))

    query_terms = {t.lower() for t in re.findall(r"[a-zA-Z]{3,}", query)}
    scored = []
    for header, body in chunks:
        haystack = (header + " " + body).lower()
        score = sum(haystack.count(term) for term in query_terms)
        if score > 0:
            scored.append((score, header, body))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:k]

    if not top:
        return {"guidance": "(no matching runbook section found)",
                 "citations": [], "source": "keyword_fallback"}

    guidance = "\n\n".join(f"[{header}] {body[:400]}" for _, header, body in top)
    citations = [header for _, header, _ in top]
    return {"guidance": guidance, "citations": citations, "source": "keyword_fallback"}