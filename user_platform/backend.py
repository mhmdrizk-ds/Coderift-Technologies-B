"""
user_platform/backend.py — the User Platform (project brief 2.3).

Owner: Person C (flag-rollout branch).

A real FastAPI app, not a mock: every agent listed here is invoked through
its actual backend entry point — graph.start()/resume() for the three
state-graph agents (via a persistent CheckpointStore/HitlStore/
TicketStore on db/coderift.db, the same stores the graphs themselves
use), or the real function call for the memory/RAG and planning agents.
Nothing here fabricates a canned response.

Why FastAPI + server-rendered HTML/JS instead of a separate frontend
framework: mcp_server/server_http.py is already the only frontend-facing
surface in this repo, and it's FastAPI + plain JSON — there is no
existing React/Vue/etc. admin surface to match (checked; none exists at
the time of this branch). Introducing a second framework for the user
platform when the sibling admin-platform surface (Person A's tool
management UI) doesn't exist yet either would mean picking a stack with
nothing to actually match, so this stays inside the one stack already
established: FastAPI backend, a single static HTML/JS page
(user_platform/static/index.html) that talks to it over fetch(), no build
step, no framework to keep in sync with anything else in the repo.

Run it:
    python -m user_platform.backend            # http://localhost:8010
    python -m user_platform.backend --port 9010
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from state_graph.store import CheckpointStore, HitlStore, TicketStore
from state_graph.incident_response import make_incident_response_graph
from state_graph.security_remediation import make_security_remediation_graph
from state_graph.flag_rollout import make_flag_rollout_graph

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Coderift Technologies — User Platform")

# Shared stores, same sqlite file the graphs themselves persist to (see
# state_graph/store.py's DEFAULT_DB_PATH) — a run started here survives a
# platform restart exactly the same way a run started by a test survives
# a "crash," because it's the identical checkpoint mechanism, not a
# platform-specific shadow copy of run state.
_checkpointer = CheckpointStore()
_hitl_store = HitlStore()
_ticket_store = TicketStore()

# Registry of the five live agents. `kind` distinguishes how invoke()
# below dispatches: "state_graph" agents go through graph.start()/
# resume(); "function" agents call a real existing entry point directly.
# security_remediation is fully wired now that state_graph/
# security_remediation.py has been merged (owner: Person B) — it was
# briefly listed as unavailable while this branch was cut before that
# merge landed; that placeholder has been removed now that the real
# graph_factory is in place.
AGENTS: dict[str, dict] = {
    "incident_response": {
        "label": "Incident Response & Postmortem",
        "kind": "state_graph",
        "graph_factory": make_incident_response_graph,
        "available": True,
    },
    "security_remediation": {
        "label": "Security Vulnerability Remediation",
        "kind": "state_graph",
        "graph_factory": make_security_remediation_graph,
        "available": True,
    },
    "flag_rollout": {
        "label": "Feature Flag Rollout & Rollback Governance",
        "kind": "state_graph",
        "graph_factory": make_flag_rollout_graph,
        "available": True,
    },
    "memory_rag": {
        "label": "Memory & RAG Agent",
        "kind": "function",
        "available": True,
    },
    "planning": {
        "label": "Release Readiness & Incident Remediation Planning",
        "kind": "function",
        "available": True,
    },
}

_graph_instances: dict[str, object] = {}


def _get_graph(agent_id: str):
    if agent_id not in _graph_instances:
        factory = AGENTS[agent_id]["graph_factory"]
        _graph_instances[agent_id] = factory(
            checkpointer=_checkpointer, hitl_store=_hitl_store, ticket_store=_ticket_store,
        )
    return _graph_instances[agent_id]


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class StartRunRequest(BaseModel):
    agent_id: str
    run_id: str
    initial_state: dict


class ResumeRunRequest(BaseModel):
    agent_id: str
    run_id: str
    hitl_decision: Optional[dict] = None
    external_event: Optional[dict] = None


class MemoryQueryRequest(BaseModel):
    query: str


class PlanningRequest(BaseModel):
    repository_name: str
    candidate_pull_request_ids: list[int]
    method: Optional[str] = None


# ---------------------------------------------------------------------------
# Agent listing / switching
# ---------------------------------------------------------------------------

@app.get("/agents")
async def list_agents():
    return {
        "agents": [
            {
                "agent_id": agent_id,
                "label": spec["label"],
                "kind": spec["kind"],
                "available": spec["available"],
                "unavailable_reason": spec.get("unavailable_reason"),
            }
            for agent_id, spec in AGENTS.items()
        ]
    }


# ---------------------------------------------------------------------------
# State-graph agents: real graph.start() / graph.resume()
# ---------------------------------------------------------------------------

@app.post("/graph/start")
async def start_graph_run(req: StartRunRequest):
    spec = AGENTS.get(req.agent_id)
    if spec is None or spec["kind"] != "state_graph":
        raise HTTPException(404, f"'{req.agent_id}' is not a state-graph agent.")
    if not spec["available"]:
        raise HTTPException(503, spec.get("unavailable_reason", "Agent not available."))

    graph = _get_graph(req.agent_id)
    result = graph.start(req.run_id, req.initial_state)
    return result


@app.post("/graph/resume")
async def resume_graph_run(req: ResumeRunRequest):
    spec = AGENTS.get(req.agent_id)
    if spec is None or spec["kind"] != "state_graph":
        raise HTTPException(404, f"'{req.agent_id}' is not a state-graph agent.")
    if not spec["available"]:
        raise HTTPException(503, spec.get("unavailable_reason", "Agent not available."))

    graph = _get_graph(req.agent_id)
    try:
        result = graph.resume(
            req.run_id, hitl_decision=req.hitl_decision, external_event=req.external_event,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return result


@app.get("/graph/{agent_id}/history/{run_id}")
async def graph_history(agent_id: str, run_id: str):
    """Lets the chat UI show what actually happened for a run — real
    checkpoint history, the same data test_crash_and_resume_no_reexecution
    asserts against, not a UI-only log."""
    history = _checkpointer.history(run_id)
    return {
        "run_id": run_id,
        "checkpoints": [
            {"node_name": c.node_name, "status": c.status, "created_at": c.created_at}
            for c in history
        ],
    }


@app.get("/hitl/pending")
async def hitl_pending(graph_name: Optional[str] = None):
    tasks = _hitl_store.list_pending(graph_name)
    return {"pending": [
        {"id": t.id, "run_id": t.run_id, "graph_name": t.graph_name,
         "node_name": t.node_name, "reason": t.reason, "payload": t.payload}
        for t in tasks
    ]}


@app.get("/tickets/open")
async def tickets_open(graph_name: Optional[str] = None):
    tickets = _ticket_store.list_open(graph_name)
    return {"open": [
        {"id": t.id, "run_id": t.run_id, "graph_name": t.graph_name,
         "node_name": t.node_name, "error_code": t.error_code, "message": t.message}
        for t in tickets
    ]}


# ---------------------------------------------------------------------------
# Function agents: memory/RAG and planning — real entry points, not mocks
# ---------------------------------------------------------------------------

@app.post("/agent/memory_rag/query")
async def memory_rag_query(req: MemoryQueryRequest):
    """Calls the real agentic RAG entry point (rag/agentic_rag.py:
    answer_agentic) — the same multi-round retrieve/grade/re-plan loop
    the RAG lab's own tests exercise, not a canned string."""
    from rag.agentic_rag import answer_agentic
    return answer_agentic(req.query)


@app.post("/agent/planning/run")
async def planning_run(req: PlanningRequest):
    """Calls the real planning agent entry point
    (planning_toolkit/planning_lab/agent.py: run_release_readiness_plan)
    — routes to decomposition-first or dynamic decomposition for real,
    against the live database, exactly as planning_toolkit/demo_task1.py
    already does for the CLI demo."""
    from planning_toolkit.model_provider import CoderiftChatModel
    from planning_toolkit.planning_lab.agent import run_release_readiness_plan

    llm = CoderiftChatModel()
    try:
        return run_release_readiness_plan(
            repository_name=req.repository_name,
            candidate_pull_request_ids=req.candidate_pull_request_ids,
            llm=llm,
            method=req.method,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------------------
# Static chat UI
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description="Coderift Technologies — User Platform")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    print(f"Starting Coderift Technologies User Platform on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
