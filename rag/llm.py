"""
llm.py — one shared place every rag/ module calls out to a model.

Tries a live Google Gemini call if GOOGLE_API_KEY/GEMINI_API_KEY is set;
falls back to a deterministic offline extractive routine so the pipeline
still produces answers grounded in retrieved text with no key configured.
This is the ONE place that talks to a model — naive/hybrid/agentic RAG and
the Self-RAG checks all go through the same call path and token accounting.

Token counts: ~4 chars per token (relative comparison, not billing-accurate).
"""

import json
import os
import re
import urllib.error
import urllib.request

GOOGLE_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GOOGLE_MODEL = os.environ.get("GOOGLE_MODEL", "gemini-2.5-flash")

_CODERIFT_SYSTEM = (
    "You are a policy assistant for Coderift Technologies. Answer the "
    "question using ONLY the policy excerpts given below. If the excerpts "
    "don't contain the answer, say you don't have enough information — "
    "never invent a rule that isn't in the text."
)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _call_google(prompt: str, system_prompt: str | None, max_tokens: int) -> str | None:
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if system_prompt:
        body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
    url = f"{GOOGLE_API_BASE}/{GOOGLE_MODEL}:generateContent?key={api_key}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
        return text or None
    except Exception:
        return None


def _split_sentences(text: str) -> list[str]:
    """Line-first sentence split that doesn't break numbered policy rules."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    sentences = []
    for line in lines:
        current = ""
        i = 0
        while i < len(line):
            ch = line[i]
            current += ch
            if ch in ".!?":
                prev_digit = i > 0 and line[i - 1].isdigit()
                next_boundary = (
                    i + 1 < len(line) and line[i + 1] == " "
                    and i + 2 < len(line) and line[i + 2].isupper()
                )
                if next_boundary and not (ch == "." and prev_digit):
                    sentences.append(current.strip())
                    current = ""
                    i += 1
            i += 1
        if current.strip():
            sentences.append(current.strip())
    return sentences


def _keyword_overlap_score(query: str, text: str) -> float:
    stop = {"what", "does", "the", "policy", "say", "about", "with", "have",
            "that", "this", "when", "section", "are"}
    # Decimal section numbers ('4.2') survive as one token, matching
    # hybrid_rag.py's BM25 tokenizer — otherwise '4.2' splits into '4' and
    # '2', losing the exact-identifier signal entirely.
    raw_tokens = re.findall(r"\d+\.\d+|[a-z0-9]+", query.lower())
    q_words = {w for w in raw_tokens if (len(w) > 3 or re.match(r"^\d+(\.\d+)?$", w)) and w not in stop}
    if not q_words:
        return 0.0
    text_lower = text.lower()
    # An exact numeric/section identifier (e.g. "4.2") matching in the
    # candidate sentence is a much stronger signal than an ordinary word
    # matching — that's the whole point of an "exact-ID" question, so it's
    # weighted heavily here rather than counted the same as any other hit.
    total_weight = 0.0
    hit_weight = 0.0
    for w in q_words:
        is_section_number = "." in w  # e.g. "4.2" — a real decimal section id
        weight = 5.0 if is_section_number else 1.0
        total_weight += weight
        if is_section_number:
            # Exact decimal match only — "4.2" must not match inside "4.25"
            # or be satisfied by a bare "4" elsewhere.
            pattern = rf"(?<!\d){re.escape(w)}(?!\d)"
        elif w.isdigit():
            # A bare number like "3" (meaning "three deployments") must not
            # match inside an unrelated decimal id like "3.1" — but should
            # still match ordinary trailing punctuation like "3." at a
            # sentence end or "3," in prose.
            pattern = rf"(?<!\d){re.escape(w)}(?!\.\d)(?!\d)"
        else:
            # Ordinary word: a plain word boundary — must NOT be blocked by
            # normal sentence-ending punctuation like "production."
            pattern = rf"\b{re.escape(w)}\b"
        if re.search(pattern, text_lower):
            hit_weight += weight
    return hit_weight / total_weight if total_weight else 0.0


def generate_answer(query: str, context_chunks: list[str], max_tokens: int = 400) -> dict:
    """Generate an answer grounded ONLY in context_chunks.
    Returns {answer, used_live_model, input_tokens, output_tokens}."""
    context = "\n\n---\n\n".join(context_chunks)
    prompt = f"Policy excerpts:\n{context}\n\nQuestion: {query}\n\nAnswer:"
    text = _call_google(prompt, _CODERIFT_SYSTEM, max_tokens)
    used_live = text is not None
    if text is None:
        if not context_chunks:
            text = "No relevant policy content was retrieved for this question."
        else:
            scored = []
            for chunk in context_chunks:
                for sent in _split_sentences(chunk):
                    scored.append((_keyword_overlap_score(query, sent), sent))
            scored.sort(key=lambda x: x[0], reverse=True)
            # Scale how many sentences we extract with how much context was
            # retrieved: a single-policy naive/hybrid call (~5 chunks) keeps
            # the original tight 3-sentence answer, but a multi-policy
            # agentic call (10+ chunks, genuinely more ground to cover)
            # should not be capped at the same 3 sentences an unrelated
            # single-policy call gets — that would erase agentic's real
            # advantage on multi-part questions regardless of what it
            # actually retrieved.
            top_n = 3 if len(context_chunks) <= 6 else 6
            top = [s for score, s in scored[:top_n] if score > 0]
            if not top:
                top = [_split_sentences(context_chunks[0])[0]] if context_chunks else []
            text = (
                " ".join(top) + "\n\n[offline fallback: extractive answer from "
                "retrieved chunks, no GOOGLE_API_KEY/GEMINI_API_KEY configured]"
            )
    return {
        "answer": text,
        "used_live_model": used_live,
        "input_tokens": estimate_tokens(_CODERIFT_SYSTEM + prompt),
        "output_tokens": estimate_tokens(text),
    }


def judge_yes_no(instruction: str, max_tokens: int = 120) -> dict:
    """Ask the model a yes/no judgment question (used by self_rag.py)."""
    text = _call_google(instruction, None, max_tokens)
    if text is None:
        return {"used_live_model": False, "raw": None}
    verdict = text.strip().lower().startswith("yes")
    return {"used_live_model": True, "raw": text, "verdict": verdict}
