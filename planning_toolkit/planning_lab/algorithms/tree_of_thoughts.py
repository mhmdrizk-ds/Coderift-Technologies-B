from __future__ import annotations

import json

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field

from mcp_server import db
from mcp_server.auth import Session
from mcp_server.tools_impl.query_tools import handle_get_pull_request

from ..models import Thought
from .environment import Environment


class ThoughtCandidates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[str] = Field(min_length=1, max_length=3)


class ThoughtEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0.0, le=1.0)
    rationale: str


def tree_of_thoughts(
    problem: str,
    llm: BaseChatModel,
    depth: int = 2,
    beam_width: int = 2,
) -> list[Thought]:
    frontier = [Thought(state="Start", score=0.5, rationale="root")]
    for _ in range(depth):
        candidates: list[Thought] = []
        for parent in frontier:
            generated = llm.with_structured_output(
                ThoughtCandidates,
                method="json_schema",
            ).invoke([
                ("system", "Generate distinct candidate next steps for Tree-of-Thoughts search."),
                ("human", f"""Problem: {problem}
Partial path: {parent.state}
Propose two distinct promising continuations."""),
            ], temperature=0.5)
            for state in generated.candidates[:2]:
                judged = llm.with_structured_output(
                    ThoughtEvaluation,
                    method="json_schema",
                ).invoke([
                    ("system", "Independently evaluate a partial solution."),
                    ("human", f"""Problem: {problem}
Candidate path: {state}
Score correctness, feasibility, and progress. Do not reward confident wording."""),
                ], temperature=0.1)
                candidates.append(
                    Thought(state=state, score=judged.score, rationale=judged.rationale)
                )
        frontier = sorted(candidates, key=lambda item: item.score, reverse=True)[:beam_width]
        if not frontier:
            break
    return frontier


# ---------------------------------------------------------------------------
# Coderift-specific wiring: rank_release_order for the genuinely ambiguous
# case (an Approved PR whose latest scan is Pending — not Failed, not
# Passed). See planning_toolkit/README.md, "Why Tree of Thoughts,
# specifically, for ambiguous ranking" for the full rationale. This routes
# ONE real sub-task of the Release Readiness & Rollout Planning Agent's DAG
# through search-and-self-evaluate rather than a single greedy pass —
# decomposition-first/dynamic decomposition (decomposition.py,
# dynamic_decomposition.py) generate the DAG and call this function for the
# rank_release_order task specifically when they detect an Approved+Pending
# PR among the candidates (see decide_ranking_strategy() below and
# agent.py's routing table).
# ---------------------------------------------------------------------------

RANK_STRATEGY_SYSTEM = """You are ranking pull requests for a Coderift Technologies
production release. Exactly one candidate PR is Approved (a human already reviewed
and cleared it) but its latest security scan is still Pending — not Failed, not
Passed. That specific PR is genuinely ambiguous: there is no single correct answer
for how to treat it, only defensible strategies with different risk trade-offs.
Every other candidate PR should be handled by the ordinary rule (Approved/Merged +
Passed scan = ready; anything else = blocked, named explicitly)."""


class RankingStrategyCandidates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[str] = Field(
        min_length=2, max_length=3,
        description="Each candidate is a COMPLETE draft release-order write-up "
                     "(not a one-line label) that names every candidate PR by id "
                     "and states how the Pending-scan PR is being handled.",
    )


def gather_release_facts(repository_name: str, candidate_pull_request_ids: list[int]) -> dict:
    conn = db.get_connection()
    session = Session()
    session.login(db.get_engineer_by_id(conn, 4))  # lead — read-only calls, role is incidental
    try:
        prs = [
            json.loads(
                handle_get_pull_request(conn, session, None, {"pull_request_id": pid})
                ["content"][0]["text"]
            )
            for pid in candidate_pull_request_ids
        ]
    finally:
        conn.close()
    return {"repository_name": repository_name, "candidate_pull_requests": prs}


def find_ambiguous_pending_pr(facts: dict) -> dict | None:
    for pr in facts["candidate_pull_requests"]:
        scan = pr.get("latest_security_scan")
        scan_status = scan["status"] if scan else "Pending"
        if pr["status"] == "Approved" and scan_status == "Pending":
            return pr
    return None


def _deterministic_ranking_candidates(facts: dict, ambiguous_pr: dict) -> list[str]:
    """Three real, distinct, hand-written strategies used when no live model
    is configured — every strategy still reasons over the real facts
    gathered above, so the offline path exercises genuine search-and-select
    over real content rather than a stub."""
    pid = ambiguous_pr["pull_request_id"]
    other_lines = []
    for pr in facts["candidate_pull_requests"]:
        if pr["pull_request_id"] == pid:
            continue
        scan = pr.get("latest_security_scan")
        scan_status = scan["status"] if scan else "Pending"
        ready = pr["status"] in ("Approved", "Merged") and scan_status == "Passed"
        other_lines.append(
            f"PR #{pr['pull_request_id']} ({pr['status']}, scan {scan_status}) is "
            f"{'READY' if ready else 'BLOCKED — not release-ready'}."
        )
    others = "\n".join(other_lines) or "No other candidate PRs."

    return [
        f"RELEASE ORDER for {facts['repository_name']}\n"
        f"Strategy: include_with_caveat\n"
        f"PR #{pid} ({ambiguous_pr['title']}) is Approved by a human reviewer; its "
        f"security scan is still Pending, not Failed. Include PR #{pid} in this "
        f"release, but hold the deploy until the scan resolves — treat 'Pending' as "
        f"'not yet blocking' rather than 'blocking', since a human has already signed "
        f"off on the change.\n{others}",

        f"RELEASE ORDER for {facts['repository_name']}\n"
        f"Strategy: exclude_until_resolved\n"
        f"PR #{pid} ({ambiguous_pr['title']}) is Approved but its security scan is "
        f"still Pending. BLOCK PR #{pid} from this release until the scan reports "
        f"Passed — a Pending scan carries the same uncertainty as an unknown result, "
        f"and shipping code whose scan hasn't finished is a real production risk "
        f"regardless of reviewer sign-off.\n{others}",

        f"RELEASE ORDER for {facts['repository_name']}\n"
        f"Strategy: escalate_for_manual_review\n"
        f"PR #{pid} ({ambiguous_pr['title']}) is Approved with a Pending scan. Do not "
        f"decide automatically either way — escalate PR #{pid} to a human release "
        f"manager for an explicit call before this release proceeds, and proceed with "
        f"every other candidate PR on the ordinary ready/blocked rule in the "
        f"meantime.\n{others}",
    ]


def rank_release_order_with_tree_of_thoughts(
    repository_name: str,
    candidate_pull_request_ids: list[int],
    llm: BaseChatModel,
    environment: Environment | None = None,
    refine_rounds: int = 1,
) -> dict:
    """Tree-of-Thoughts search over the ambiguous rank_release_order
    sub-task: generate several candidate strategies for the Approved+Pending
    PR, self-evaluate each (model judgment — ToT's canonical evaluation;
    grounding is reserved for Self-Refine/Reflexion/LATS per the lab's
    'grounded vs ungrounded critique' concern), keep the best, then run up
    to `refine_rounds` more generate/evaluate passes on the surviving
    branch. Returns the winning Thought plus the full search trace and,
    for reporting only (NOT used as the search criterion — that would
    blur the ToT/LATS boundary this module's README draws), the grounded
    Environment score of the winning draft.

    If `environment` is None, uses the real, DB-backed Environment
    (grounded). Callers doing the required ungrounded-vs-grounded LATS
    contrast pass an UngroundedEnvironment here too so both search methods
    are compared under the same two conditions.
    """
    environment = environment or Environment()
    facts = gather_release_facts(repository_name, candidate_pull_request_ids)
    ambiguous_pr = find_ambiguous_pending_pr(facts)
    if ambiguous_pr is None:
        raise ValueError(
            f"No Approved+Pending PR among {candidate_pull_request_ids} for "
            f"'{repository_name}' — Tree of Thoughts is reserved for this specific "
            f"ambiguous case; an unambiguous set should go through Plan-and-Solve."
        )

    trace: list[dict] = []
    used_offline_fallback = False
    try:
        generated = llm.with_structured_output(
            RankingStrategyCandidates, method="json_schema",
        ).invoke([
            ("system", RANK_STRATEGY_SYSTEM),
            ("human", f"""Real facts (from the Coderift database): {json.dumps(facts)}

Propose {3} distinct, complete release-order write-ups, each naming every candidate
PR by id and taking a different defensible stance on PR #{ambiguous_pr['pull_request_id']}
(the Approved+Pending one)."""),
        ], temperature=0.6)
        candidate_texts = generated.candidates
    except Exception:
        used_offline_fallback = True
        candidate_texts = _deterministic_ranking_candidates(facts, ambiguous_pr)

    frontier: list[Thought] = []
    for text in candidate_texts:
        thought = _self_evaluate(text, facts, ambiguous_pr, llm, used_offline_fallback)
        frontier.append(thought)
        trace.append({"round": 0, "state": text, "score": thought.score, "rationale": thought.rationale})

    frontier.sort(key=lambda t: t.score, reverse=True)

    for round_number in range(1, refine_rounds + 1):
        best = frontier[0]
        refined_text = _refine_thought(best, facts, ambiguous_pr, llm, used_offline_fallback)
        refined_thought = _self_evaluate(refined_text, facts, ambiguous_pr, llm, used_offline_fallback)
        trace.append({
            "round": round_number, "state": refined_text,
            "score": refined_thought.score, "rationale": refined_thought.rationale,
            "refined_from_score": best.score,
        })
        if refined_thought.score >= best.score:
            frontier[0] = refined_thought
        frontier.sort(key=lambda t: t.score, reverse=True)

    winner = frontier[0]
    grounded_state = json.dumps({
        "action": "release_plan_covers_all",
        "repository_name": repository_name,
        "candidate_pull_request_ids": candidate_pull_request_ids,
        "draft": winner.state,
    })
    grounded_feedback = environment.evaluate(grounded_state)

    return {
        "ambiguous_pull_request_id": ambiguous_pr["pull_request_id"],
        "winner": winner,
        "winner_model_self_score": winner.score,
        "winner_grounded_score": grounded_feedback.score,
        "winner_grounded_success": grounded_feedback.success,
        "winner_grounded_details": grounded_feedback.details,
        "search_trace": trace,
        "used_offline_fallback": used_offline_fallback,
        "candidates_generated": len(candidate_texts),
    }


def _self_evaluate(
    text: str, facts: dict, ambiguous_pr: dict, llm: BaseChatModel, offline: bool,
) -> Thought:
    if offline:
        # Deterministic, content-based self-evaluation so the offline path is
        # reproducible: reward mentioning every candidate PR by id and
        # explicitly naming a stance on the ambiguous one.
        ids_mentioned = sum(
            1 for pr in facts["candidate_pull_requests"]
            if f"#{pr['pull_request_id']}" in text
        )
        total = len(facts["candidate_pull_requests"])
        coverage = ids_mentioned / total if total else 0.0
        names_a_stance = any(
            marker in text
            for marker in ("include_with_caveat", "exclude_until_resolved", "escalate_for_manual_review")
        )
        score = round(min(1.0, 0.6 * coverage + (0.4 if names_a_stance else 0.0)), 4)
        # Deterministic, content-based tie-breaker: a strategy that defers the
        # decision to a human ("escalate") is less complete as a release-order
        # ANSWER than one that commits, and a strategy that ignores the human
        # reviewer's sign-off ("exclude") discards real signal already gathered
        # — both real, statable judgment criteria, not arbitrary noise.
        if "escalate_for_manual_review" in text:
            score = round(max(0.0, score - 0.2), 4)
            penalty_note = " Penalized for deferring the decision rather than committing to a release order."
        elif "exclude_until_resolved" in text:
            score = round(max(0.0, score - 0.1), 4)
            penalty_note = " Mildly penalized for discarding the human reviewer's Approved sign-off entirely."
        else:
            penalty_note = ""
        rationale = (
            f"[offline self-eval] mentions {ids_mentioned}/{total} candidate PRs by id; "
            f"{'names' if names_a_stance else 'does not name'} an explicit stance on the "
            f"ambiguous PR #{ambiguous_pr['pull_request_id']}.{penalty_note}"
        )
        return Thought(state=text, score=score, rationale=rationale)
    try:
        judged = llm.with_structured_output(ThoughtEvaluation, method="json_schema").invoke([
            ("system", "Independently evaluate a candidate release-order strategy. Score "
                       "correctness, whether every candidate PR is addressed by id, and "
                       "whether the ambiguous PR's treatment is clearly justified. Do not "
                       "reward confident wording alone."),
            ("human", f"Facts: {json.dumps(facts)}\n\nCandidate strategy:\n{text}"),
        ], temperature=0.1)
        return Thought(state=text, score=judged.score, rationale=judged.rationale)
    except Exception:
        return _self_evaluate(text, facts, ambiguous_pr, llm, offline=True)


def _refine_thought(
    thought: Thought, facts: dict, ambiguous_pr: dict, llm: BaseChatModel, offline: bool,
) -> str:
    if offline:
        return thought.state + (
            f"\n\n[refinement] Escalation note added: notify the release manager that "
            f"PR #{ambiguous_pr['pull_request_id']}'s scan status should be re-checked "
            f"before this release ships, regardless of which stance was taken."
        )
    try:
        response = llm.invoke([
            ("system", "Improve a release-order strategy given a prior self-critique score "
                       "and rationale. Address the weakest point the rationale identified."),
            ("human", f"Facts: {json.dumps(facts)}\n\nCurrent draft (self-score "
                      f"{thought.score}, rationale: {thought.rationale}):\n{thought.state}\n\n"
                      f"Return only the improved draft."),
        ], temperature=0.3)
        text = response.content
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("empty response")
        return text.strip()
    except Exception:
        return _refine_thought(thought, facts, ambiguous_pr, llm, offline=True)
