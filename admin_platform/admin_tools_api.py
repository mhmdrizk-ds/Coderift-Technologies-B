"""
admin_platform/admin_tools_api.py — Admin Platform Backend API

FastAPI server providing REST endpoints consumed by admin_platform/admin_tools.html.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

DB_PATH = Path(__file__).resolve().parent.parent / "db" / "coderift.db"
RAG_RESOURCES_DIR = Path(__file__).resolve().parent.parent / "resources"


def _get_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class AgentToolCreate(BaseModel):
    agent_id: str
    tool_name: str
    enabled: bool = True


class AgentToolToggle(BaseModel):
    enabled: bool


class HITLResolve(BaseModel):
    approved: bool
    decided_by: str
    note: str = ""


class TicketResolve(BaseModel):
    note: str = ""


class RAGDocUpload(BaseModel):
    name: str
    content: str


app = FastAPI(title="Coderift Admin API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------
# 1. Agent Tool Management (agent_tool_registrations table)
# ------------------------------------------------------------------

@app.get("/api/agent-tools")
def list_agent_tools() -> List[Dict[str, Any]]:
    conn = _get_db_conn()
    rows = conn.execute("SELECT * FROM agent_tool_registrations ORDER BY agent_id, tool_name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/agent-tools")
def create_agent_tool(payload: AgentToolCreate) -> Dict[str, Any]:
    conn = _get_db_conn()
    try:
        conn.execute(
            """
            INSERT INTO agent_tool_registrations (agent_id, tool_name, enabled)
            VALUES (?, ?, ?)
            ON CONFLICT(agent_id, tool_name) DO UPDATE SET enabled = excluded.enabled
            """,
            (payload.agent_id, payload.tool_name, payload.enabled),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))
    conn.close()
    return {"status": "ok", "agent_id": payload.agent_id, "tool_name": payload.tool_name}


@app.post("/api/agent-tools/{tool_id}/toggle")
def toggle_agent_tool(tool_id: int, payload: AgentToolToggle) -> Dict[str, Any]:
    conn = _get_db_conn()
    conn.execute("UPDATE agent_tool_registrations SET enabled = ? WHERE id = ?", (payload.enabled, tool_id))
    conn.commit()
    conn.close()
    return {"status": "ok", "id": tool_id, "enabled": payload.enabled}


@app.delete("/api/agent-tools/{tool_id}")
def delete_agent_tool(tool_id: int) -> Dict[str, Any]:
    conn = _get_db_conn()
    conn.execute("DELETE FROM agent_tool_registrations WHERE id = ?", (tool_id,))
    conn.commit()
    conn.close()
    return {"status": "ok", "deleted_id": tool_id}


# ------------------------------------------------------------------
# 2. HITL Tasks
# ------------------------------------------------------------------

@app.get("/api/hitl-tasks")
def list_hitl_tasks() -> List[Dict[str, Any]]:
    conn = _get_db_conn()
    rows = conn.execute("SELECT * FROM hitl_tasks ORDER BY created_at DESC").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        for key in ["payload_json", "decision_json"]:
            if d.get(key):
                try:
                    d[key.replace("_json", "")] = json.loads(d[key])
                except Exception:
                    pass
        result.append(d)
    return result


@app.post("/api/hitl-tasks/{task_id}/resolve")
def resolve_hitl_task(task_id: int, payload: HITLResolve) -> Dict[str, Any]:
    conn = _get_db_conn()
    conn.execute(
        """
        UPDATE hitl_tasks
        SET status = ?, decision_json = ?, decided_by = ?, decided_at = datetime('now')
        WHERE id = ?
        """,
        (
            "approved" if payload.approved else "rejected",
            json.dumps({"approved": payload.approved, "reason": payload.note}),
            payload.decided_by,
            task_id,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT run_id FROM hitl_tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="HITL task not found")

    run_id = row["run_id"]

    try:
        from state_graph.incident_response import make_incident_response_graph
        graph = make_incident_response_graph()
        decision = {
            "approved": payload.approved,
            "decided_by": payload.decided_by,
            "reason": payload.note
        }
        result = graph.resume(run_id, hitl_decision=decision)
        return {"status": "ok", "task_id": task_id, "decision": payload.approved, "graph_result": result}
    except Exception as e:
        return {"status": "resolved_but_resume_failed", "error": str(e), "task_id": task_id}


# ------------------------------------------------------------------
# 3. Tickets
# ------------------------------------------------------------------

@app.get("/api/tickets")
def list_tickets() -> List[Dict[str, Any]]:
    conn = _get_db_conn()
    rows = conn.execute("SELECT * FROM tickets ORDER BY created_at DESC").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        for key in ["payload_json", "state_snapshot_json"]:
            if d.get(key):
                try:
                    d[key.replace("_json", "")] = json.loads(d[key])
                except Exception:
                    pass
        result.append(d)
    return result


@app.post("/api/tickets/{ticket_id}/resolve")
def resolve_ticket(ticket_id: int, payload: TicketResolve) -> Dict[str, Any]:
    conn = _get_db_conn()
    conn.execute(
        """
        UPDATE tickets
        SET status = 'resolved', resolution_notes = ?, resolved_at = datetime('now')
        WHERE id = ?
        """,
        (payload.note, ticket_id),
    )
    conn.commit()
    row = conn.execute("SELECT run_id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    run_id = row["run_id"]

    try:
        from state_graph.incident_response import make_incident_response_graph
        graph = make_incident_response_graph()
        result = graph.resume(run_id)
        return {"status": "ok", "ticket_id": ticket_id, "graph_result": result}
    except Exception as e:
        return {"status": "resolved_but_resume_failed", "error": str(e), "ticket_id": ticket_id}


# ------------------------------------------------------------------
# 4. Checkpoints
# ------------------------------------------------------------------

@app.get("/api/checkpoints")
def list_checkpoints(graph_name: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_db_conn()
    if graph_name:
        rows = conn.execute(
            "SELECT * FROM checkpoints WHERE graph_name = ? ORDER BY id DESC",
            (graph_name,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM checkpoints ORDER BY id DESC").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("state_json"):
            try:
                d["state"] = json.loads(d["state_json"])
            except Exception:
                pass
        result.append(d)
    return result


# ------------------------------------------------------------------
# 5. External Events
# ------------------------------------------------------------------

@app.post("/api/runs/{run_id}/event")
def send_external_event(run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from state_graph.incident_response import make_incident_response_graph
        graph = make_incident_response_graph()
        result = graph.resume(run_id, external_event=payload)
        return {"status": "ok", "run_id": run_id, "graph_result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# 6. RAG Documents
# ------------------------------------------------------------------

@app.get("/api/rag-docs")
def list_rag_docs() -> List[Dict[str, str]]:
    docs = []
    if RAG_RESOURCES_DIR.exists():
        for f in sorted(RAG_RESOURCES_DIR.glob("*.md")):
            docs.append({"name": f.name, "path": str(f)})
    return docs


@app.post("/api/rag-docs")
def upload_rag_doc(payload: RAGDocUpload) -> Dict[str, Any]:
    target = RAG_RESOURCES_DIR / payload.name
    target.write_text(payload.content, encoding="utf-8")
    return {"status": "ok", "name": payload.name, "path": str(target)}


@app.delete("/api/rag-docs/{doc_name}")
def delete_rag_doc(doc_name: str) -> Dict[str, Any]:
    target = RAG_RESOURCES_DIR / doc_name
    if target.exists():
        target.unlink()
        return {"status": "ok", "deleted": doc_name}
    raise HTTPException(status_code=404, detail=f"Document '{doc_name}' not found")


# ------------------------------------------------------------------
# Serve the admin UI
# ------------------------------------------------------------------

@app.get("/")
def serve_admin_ui():
    ui_path = Path(__file__).resolve().parent / "admin_tools.html"
    return FileResponse(ui_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)