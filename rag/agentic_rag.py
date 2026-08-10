"""
agentic_rag.py — retrieve -> observe -> decide whether to retrieve again.

Wins on multi-part questions that need more than one policy: e.g. "for a
critical incident with 3 consecutive failed deployments, what is the full
response chain?" needs the incident_response_runbook AND the
production_deployment_policy combined — naive/hybrid only ever hit one
policy per call.

Loop: plan -> retrieve -> grade relevance -> plan again (capped at MAX_ROUNDS).
Planning uses a live LLM call when available; offline fallback is a keyword
classifier against each policy's vocabulary.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vector_store"))

from retrieve import retrieve_policy_chunks
from rag.llm import _call_google, generate_answer
from rag.self_rag import check_relevance

MAX_ROUNDS = 3

# Coderift policy vocabulary — maps keywords to the policy they primarily live in
KNOWN_POLICIES = (
    "production_deployment",
    "security_review",
    "incident_response",
)

POLICY_KEYWORDS = {
    "production_deployment": [
        "deploy", "deployment", "pull request", "staging", "production",
        "merge", "rollback", "feature flag", "environment", "gate",
    ],
    "security_review": [
        "security scan", "sast", "dependency", "secrets", "cve", "scan",
        "vulnerability", "override", "failed scan", "pending scan", "validity",
    ],
    "incident_response": [
        "incident", "critical", "severity", "page", "on-call", "halt",
        "postmortem", "pir", "runbook", "sla", "response time", "escalate",
    ],
}


def _offline_plan(query: str, covered: set[str]) -> dict:
    q = query.lower()
    for policy_name, keywords in POLICY_KEYWORDS.items():
        if policy_name in covered:
            continue
        if any(k in q for k in keywords):
            return {"action": "retrieve", "policy_name": policy_name, "query": query}
    if not covered:
        return {"action": "retrieve", "policy_name": "production_deployment", "query": query}
    return {"action": "answer"}


def _plan_next_step(query: str, covered: set[str], rounds_so_far: list[dict]) -> dict:
    history = "\n".join(
        f"- retrieved from {r['policy_name']}: relevant={r['relevant']}" for r in rounds_so_far
    )
    prompt = (
        "You are planning retrieval for a policy question at Coderift Technologies. "
        f"Available policies: {', '.join(KNOWN_POLICIES)}.\n"
        f"Question: {query}\n"
        f"Retrieval so far:\n{history or '(none yet)'}\n\n"
        "Respond with ONLY a JSON object, no other text, one of:\n"
        '{"action": "retrieve", "policy_name": "<one of the available policies>", "query": "<sub-query>"}\n'
        'or {"action": "answer"}'
    )
    text = _call_google(prompt, None, 100)
    if text is not None:
        try:
            cleaned = text.strip().strip("`").replace("json\n", "")
            decision = json.loads(cleaned)
            if decision.get("action") in ("retrieve", "answer"):
                return decision
        except (json.JSONDecodeError, AttributeError):
            pass
    return _offline_plan(query, covered)


def answer_agentic(query: str, k: int = 5) -> dict:
    start = time.perf_counter()
    covered: set[str] = set()
    rounds: list[dict] = []
    collected_chunks: list[str] = []
    collected_meta: list[dict] = []
    total_input = 0
    total_output = 0
    used_live_any = False

    for _ in range(MAX_ROUNDS):
        decision = _plan_next_step(query, covered, rounds)
        if decision["action"] != "retrieve":
            break

        policy_name = decision["policy_name"]
        sub_query = decision.get("query", query)

        docs = retrieve_policy_chunks(query=sub_query, policy_name=policy_name, k=k)
        chunk_texts = [d.page_content for d in docs]

        relevance = check_relevance(sub_query, chunk_texts)
        used_live_any = used_live_any or relevance.get("used_live_model", False)

        rounds.append({
            "policy_name": policy_name,
            "sub_query": sub_query,
            "num_chunks": len(chunk_texts),
            "relevant": relevance["relevant"],
            "relevance_reason": relevance["reason"],
        })
        covered.add(policy_name)

        if relevance["relevant"]:
            collected_chunks.extend(chunk_texts)
            collected_meta.extend([d.metadata for d in docs])

    result = generate_answer(query, collected_chunks)
    latency = time.perf_counter() - start
    total_input += result["input_tokens"]
    total_output += result["output_tokens"]
    used_live_any = used_live_any or result["used_live_model"]

    return {
        "strategy": "agentic",
        "query": query,
        "policy_name": "+".join(sorted(covered)) if covered else None,
        "answer": result["answer"],
        "source_chunks": [{"content": c, "metadata": m}
                          for c, m in zip(collected_chunks, collected_meta)],
        "rounds": rounds,
        "used_live_model": used_live_any,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "latency_seconds": latency,
    }
