"""
self_rag.py — the trust layer every RAG answer AND every memory recall passes
through before it reaches a user.

Two checks modeled on the Self-RAG paper:
  1. check_relevance(query, passages)  — is what we retrieved about the question?
  2. check_support(answer, passages)   — does the generated answer trace back to
     the passages, or did the model invent something?

Both checks accept a live-model path and an offline heuristic path.
The same two functions are used for RAG chunks AND for MemorySystem.recall().
"""

import re
from rag.llm import _call_google, _keyword_overlap_score, _split_sentences

RELEVANCE_THRESHOLD = 0.2
SUPPORT_THRESHOLD = 0.15


def check_relevance(query: str, passages: list[str]) -> dict:
    if not passages:
        return {"relevant": False, "reason": "nothing was retrieved", "used_live_model": False}
    combined = "\n".join(passages)
    prompt = (
        "Question: " + query + "\n\nRetrieved passage(s):\n" + combined + "\n\n"
        "Is this passage actually relevant to answering the question? "
        "Answer with just 'yes' or 'no' followed by one short reason."
    )
    text = _call_google(prompt, None, 60)
    if text is not None:
        verdict = text.strip().lower().startswith("yes")
        return {"relevant": verdict, "reason": text.strip(), "used_live_model": True}
    score = _keyword_overlap_score(query, combined)
    relevant = score >= RELEVANCE_THRESHOLD
    reason = (
        f"offline heuristic: {score:.2f} of the question's key terms appear in the "
        f"retrieved text (threshold {RELEVANCE_THRESHOLD})"
    )
    return {"relevant": relevant, "reason": reason, "used_live_model": False, "score": score}


def check_support(answer: str, passages: list[str]) -> dict:
    if not passages:
        return {"supported": False, "reason": "no source passages to check against",
                "used_live_model": False}
    combined = "\n".join(passages)
    prompt = (
        "Source passage(s):\n" + combined + "\n\nGenerated answer:\n" + answer + "\n\n"
        "Is every factual claim in the generated answer actually stated or directly "
        "implied by the source passages? Answer 'yes' or 'no' followed by one short reason. "
        "Say 'no' if the answer adds any rule, number, or fact not present in the source."
    )
    text = _call_google(prompt, None, 80)
    if text is not None:
        verdict = text.strip().lower().startswith("yes")
        return {"supported": verdict, "reason": text.strip(), "used_live_model": True}
    sentences = [s for s in _split_sentences(answer) if s.strip()]
    unsupported = []
    for sent in sentences:
        if "[offline fallback" in sent:
            continue
        answer_numbers = set(re.findall(r"\d+", sent))
        source_numbers = set(re.findall(r"\d+", combined))
        fabricated_number = bool(answer_numbers - source_numbers)
        score = _keyword_overlap_score(sent, combined)
        if fabricated_number or score < SUPPORT_THRESHOLD:
            unsupported.append(sent)
    supported = len(unsupported) == 0
    reason = (
        "offline heuristic: every sentence's key terms trace back to the source text"
        if supported
        else f"offline heuristic: unsupported sentence(s) found: {unsupported}"
    )
    return {"supported": supported, "reason": reason, "used_live_model": False,
            "unsupported_sentences": unsupported}


def verify_rag_result(result: dict) -> dict:
    """Run both checks against a naive/hybrid/agentic RAG result dict."""
    passages = [c["content"] for c in result["source_chunks"]]
    relevance = check_relevance(result["query"], passages)
    support = check_support(result["answer"], passages)
    return {
        **result,
        "self_rag": {
            "relevance": relevance,
            "support": support,
            "passed": relevance["relevant"] and support["supported"],
        },
    }


def verify_memory_recall(query: str, recalled: dict | None) -> dict:
    """Apply relevance/support checks to a memory recall result."""
    if recalled is None:
        return {"relevant": False, "supported": False, "passed": False,
                "reason": "no memory recalled — nothing to verify, agent must say so"}
    statements = recalled.get("statements", [])
    relevance = check_relevance(query, statements)
    from rag.llm import generate_answer
    generated = generate_answer(query, statements)
    support = check_support(generated["answer"], statements)
    return {
        "recalled_topic": recalled.get("topic"),
        "recalled_version": recalled.get("version"),
        "answer": generated["answer"],
        "relevance": relevance,
        "support": support,
        "passed": relevance["relevant"] and support["supported"],
    }


# ---------------------------------------------------------------------------
# Required failure demos: shows at least one real case where an unsupported
# answer gets caught and flagged.
# ---------------------------------------------------------------------------

def demo_relevance_failure() -> dict:
    """Ask a deployment question but hand the checker incident-only chunks —
    simulates a retriever returning its nearest neighbor from the wrong policy."""
    query = "What is the required security scan status before deploying to production?"
    wrong_passages = [
        "Page Priya Raman immediately for any critical incident. "
        "All incidents must have a row in the incidents table."
    ]
    return check_relevance(query, wrong_passages)


def demo_support_failure() -> dict:
    """A generated answer that invents a specific number no source passage contains."""
    passages = [
        "Only engineers with role senior or lead may deploy to production. "
        "Authorization is verified server-side against the authenticated engineer's role."
    ]
    fabricated_answer = (
        "Engineers must wait a mandatory 72-hour cooling-off period and get "
        "sign-off from at least 3 senior engineers before deploying to production."
    )
    return check_support(fabricated_answer, passages)


if __name__ == "__main__":
    print("Relevance-failure demo:", demo_relevance_failure())
    print("Support-failure demo:", demo_support_failure())
