"""
graph_rag.py — bonus Graph RAG architecture.

Coderift's policy corpus has real entity relationships worth modeling as a
graph, not a flat pile of unrelated passages: a failed security scan
triggers a security review, which can block a deployment; an active
critical incident triggers a deployment halt; a rollback triggers a
post-incident review. These aren't just topically related chunks — they're
a causal chain a multi-hop question needs to traverse.

Graph structure:
  - CONCEPT nodes: deployment_approval, security_scan, incident_response,
    rollback_criteria, deployment_halt, postmortem, override_authority
  - SECTION nodes: one per policy chunk, linked to the concept(s) it governs
  - Concept -> concept edges encode real triggers from the policy text:
    failed_scan -> security_review -> deployment_block
    active_critical_incident -> deployment_halt
    rollback -> postmortem_required

answer_graph(query) returns the same result shape as naive/hybrid/agentic
so it slots into the same eval harness and Self-RAG verification.
"""

import re
import sys
import time
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parent / "vector_store"))

from vector_db import get_collection
from embeddings import get_embedding_model
from rag.llm import generate_answer

# ---------------------------------------------------------------------------
# Concept graph: nodes are policy concepts; edges encode real triggers
# pulled directly from the policy text (see resources/*.md section refs
# in each edge's `source` attribute for traceability).
# ---------------------------------------------------------------------------

CONCEPT_EDGES = [
    # (from_concept, to_concept, relationship, source_section)
    ("failed_scan", "security_review", "requires", "security_review_policy.md Section 4"),
    ("security_review", "deployment_block", "can_cause", "production_deployment_policy.md Section 2.2"),
    ("failed_scan", "deployment_block", "can_cause", "production_deployment_policy.md Section 2.2"),
    ("unreviewed_pr", "deployment_block", "can_cause", "production_deployment_policy.md Section 2.3"),
    ("failed_scan", "elicitation_override", "can_trigger", "production_deployment_policy.md Section 5.1"),
    ("unreviewed_pr", "elicitation_override", "can_trigger", "production_deployment_policy.md Section 5.1"),
    ("elicitation_override", "lead_authorization", "requires", "security_review_policy.md Section 4.1"),
    ("elicitation_override", "audit_log", "requires", "production_deployment_policy.md Section 3.6"),
    ("active_critical_incident", "deployment_halt", "can_cause", "incident_response_runbook.md Section 4.1"),
    ("deployment_halt", "rollback", "allows_during_halt", "incident_response_runbook.md Section 4.3"),
    ("critical_incident", "paging", "requires", "incident_response_runbook.md Section 3.1"),
    ("critical_incident", "postmortem", "requires", "incident_response_runbook.md Section 6.1"),
    ("rollback", "postmortem_input", "feeds", "incident_response_runbook.md Section 6.7"),
    ("rollback", "rerun_pre_deploy_checks", "requires", "production_deployment_policy.md Section 6.6"),
    ("consecutive_failed_deployments", "deployment_instability_flag", "can_cause", "incident_response_runbook.md Section 6.7"),
    ("deployment_instability_flag", "deployment_halt", "should_trigger", "incident_response_runbook.md Section 4.1"),
    ("hotfix", "security_scan", "still_requires", "security_review_policy.md Section 2.6"),
    ("hotfix", "lead_review", "requires", "incident_response_runbook.md Section 5.3"),
    ("emergency_hotfix", "scan_validity_extension", "may_receive", "security_review_policy.md Section 5.5"),
]

# Keyword -> concept mapping, used to find entry points into the graph from
# a free-text query.
CONCEPT_KEYWORDS = {
    "failed_scan": ["failed scan", "scan failed", "failed security scan"],
    "security_review": ["security review", "security scan"],
    "deployment_block": ["cannot deploy", "block", "blocked deployment"],
    "unreviewed_pr": ["unreviewed", "not approved", "not reviewed", "open pr", "open pull request"],
    "elicitation_override": ["override", "elicitation", "confirm"],
    "lead_authorization": ["lead", "authorize", "authorization"],
    "audit_log": ["audit", "recorded"],
    "active_critical_incident": ["active critical incident", "critical incident", "active incident"],
    "deployment_halt": ["halt", "halt all deployments", "stop deployments"],
    "rollback": ["rollback", "roll back", "rolled back"],
    "critical_incident": ["critical incident", "critical severity"],
    "paging": ["page", "paging", "on-call", "pagerduty"],
    "postmortem": ["postmortem", "post-incident review", "pir"],
    "consecutive_failed_deployments": ["consecutive failed", "3 consecutive", "repeated failures"],
    "deployment_instability_flag": ["unstable", "instability", "deployment-unstable"],
    "hotfix": ["hotfix", "emergency fix"],
    "emergency_hotfix": ["emergency hotfix", "emergency fix during incident"],
}


def build_concept_graph() -> nx.DiGraph:
    g = nx.DiGraph()
    for src, dst, rel, source_section in CONCEPT_EDGES:
        g.add_edge(src, dst, relationship=rel, source=source_section)
    return g


_GRAPH: nx.DiGraph | None = None


def get_graph() -> nx.DiGraph:
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_concept_graph()
    return _GRAPH


def _find_entry_concepts(query: str) -> list[str]:
    q = query.lower()
    hits = []
    for concept, keywords in CONCEPT_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            hits.append(concept)
    return hits


def _multi_hop_traverse(entry_concepts: list[str], max_hops: int = 2) -> set[str]:
    """From each entry concept, walk forward (successors) and backward
    (predecessors) up to max_hops — a multi-part question about "failed
    scan" should also surface what a failed scan requires (security_review)
    AND what can trigger a failed scan check in the first place."""
    g = get_graph()
    visited: set[str] = set()
    frontier = set(c for c in entry_concepts if c in g)
    visited |= frontier

    for _ in range(max_hops):
        next_frontier = set()
        for node in frontier:
            next_frontier |= set(g.successors(node))
            next_frontier |= set(g.predecessors(node))
        next_frontier -= visited
        visited |= next_frontier
        frontier = next_frontier

    return visited


def _concepts_to_query_terms(concepts: set[str]) -> str:
    """Turn graph concept node names into a search string for the vector
    store — e.g. 'deployment_halt' -> 'deployment halt'."""
    return " ".join(c.replace("_", " ") for c in concepts)


def _get_source_sections(concepts: set[str]) -> list[str]:
    g = get_graph()
    sections = set()
    for u, v, data in g.edges(data=True):
        if u in concepts or v in concepts:
            sections.add(data["source"])
    return sorted(sections)


def answer_graph(query: str, k: int = 6) -> dict:
    start = time.perf_counter()

    entry_concepts = _find_entry_concepts(query)
    if not entry_concepts:
        # No graph entry point found — fall back to a plain retrieval over
        # all policies using the raw query.
        traversed_concepts: set[str] = set()
        expanded_query = query
    else:
        traversed_concepts = _multi_hop_traverse(entry_concepts, max_hops=2)
        expanded_query = query + " " + _concepts_to_query_terms(traversed_concepts)

    col = get_collection()
    embedding_model = get_embedding_model()
    qvec = embedding_model.embed_query(expanded_query)
    results = col.query(query_embeddings=[qvec], n_results=k, include=["documents", "metadatas"])

    docs = results["documents"][0]
    metas = results["metadatas"][0]

    result = generate_answer(query, docs)
    latency = time.perf_counter() - start

    return {
        "strategy": "graph",
        "query": query,
        "policy_name": "+".join(sorted({m.get("policy_type", "") for m in metas})),
        "answer": result["answer"],
        "source_chunks": [{"content": d, "metadata": m} for d, m in zip(docs, metas)],
        "entry_concepts": entry_concepts,
        "traversed_concepts": sorted(traversed_concepts),
        "source_sections": _get_source_sections(traversed_concepts) if traversed_concepts else [],
        "used_live_model": result["used_live_model"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "latency_seconds": latency,
    }


if __name__ == "__main__":
    out = answer_graph(
        "For a repository with 3 consecutive failed deployments and an active "
        "critical incident, what is the complete required response?"
    )
    print("Entry concepts:", out["entry_concepts"])
    print("Traversed concepts:", out["traversed_concepts"])
    print("Source sections:", out["source_sections"])
    print()
    print(out["answer"])
