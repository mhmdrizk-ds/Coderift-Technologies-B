from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# db/data/ subdirectory, not db/ directly -- see db/init_db.py's comment:
# Docker named volumes can't reliably mount onto a single file path.
DEFAULT_DB_PATH = Path(__file__).parent.parent / "db" / "data" / "coderift.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

@dataclass
class Checkpoint:
    id: int
    run_id: str
    graph_name: str
    node_name: str
    state: dict
    status: str
    created_at: str


class CheckpointStore:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def save(self, run_id: str, graph_name: str, node_name: str,
              state: dict, status: str) -> Checkpoint:
        conn = _connect(self.db_path)
        try:
            cur = conn.execute(
                "INSERT INTO checkpoints (run_id, graph_name, node_name, "
                "state_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, graph_name, node_name, json.dumps(state), status, _now()),
            )
            conn.commit()
            return Checkpoint(cur.lastrowid, run_id, graph_name, node_name,
                               state, status, _now())
        finally:
            conn.close()

    def load_latest(self, run_id: str) -> Optional[Checkpoint]:
        conn = _connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM checkpoints WHERE run_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            return Checkpoint(
                id=row["id"], run_id=row["run_id"], graph_name=row["graph_name"],
                node_name=row["node_name"], state=json.loads(row["state_json"]),
                status=row["status"], created_at=row["created_at"],
            )
        finally:
            conn.close()

    def history(self, run_id: str) -> list[Checkpoint]:
        """Full transition history for a run — useful for a platform screen
        that lets an admin inspect exactly how a run got where it is."""
        conn = _connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM checkpoints WHERE run_id = ? ORDER BY id ASC",
                (run_id,),
            ).fetchall()
            return [
                Checkpoint(row["id"], row["run_id"], row["graph_name"],
                           row["node_name"], json.loads(row["state_json"]),
                           row["status"], row["created_at"])
                for row in rows
            ]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# HITL tasks
# ---------------------------------------------------------------------------

@dataclass
class HitlTask:
    id: int
    run_id: str
    graph_name: str
    node_name: str
    reason: str
    payload: dict
    status: str
    decision: Optional[dict] = None
    decided_by: Optional[str] = None


class HitlStore:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create(self, run_id: str, graph_name: str, node_name: str,
                reason: str, payload: dict) -> HitlTask:
        conn = _connect(self.db_path)
        try:
            cur = conn.execute(
                "INSERT INTO hitl_tasks (run_id, graph_name, node_name, "
                "reason, payload_json, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                (run_id, graph_name, node_name, reason, json.dumps(payload), _now()),
            )
            conn.commit()
            return HitlTask(cur.lastrowid, run_id, graph_name, node_name,
                             reason, payload, "pending")
        finally:
            conn.close()

    def list_pending(self, graph_name: Optional[str] = None) -> list[HitlTask]:
        conn = _connect(self.db_path)
        try:
            if graph_name:
                rows = conn.execute(
                    "SELECT * FROM hitl_tasks WHERE status = 'pending' "
                    "AND graph_name = ? ORDER BY created_at ASC",
                    (graph_name,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM hitl_tasks WHERE status = 'pending' "
                    "ORDER BY created_at ASC",
                ).fetchall()
            return [self._row_to_task(r) for r in rows]
        finally:
            conn.close()

    def decide(self, hitl_task_id: int, approved: bool,
                decided_by: str, reason: str = "") -> HitlTask:
        decision = {"approved": approved, "reason": reason}
        conn = _connect(self.db_path)
        try:
            conn.execute(
                "UPDATE hitl_tasks SET status = ?, decision_json = ?, "
                "decided_by = ?, decided_at = ? WHERE id = ?",
                ("approved" if approved else "rejected", json.dumps(decision),
                 decided_by, _now(), hitl_task_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM hitl_tasks WHERE id = ?", (hitl_task_id,)
            ).fetchone()
            return self._row_to_task(row)
        finally:
            conn.close()

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> HitlTask:
        return HitlTask(
            id=row["id"], run_id=row["run_id"], graph_name=row["graph_name"],
            node_name=row["node_name"], reason=row["reason"],
            payload=json.loads(row["payload_json"] or "{}"),
            status=row["status"],
            decision=json.loads(row["decision_json"]) if row["decision_json"] else None,
            decided_by=row["decided_by"],
        )


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

@dataclass
class Ticket:
    id: int
    run_id: str
    graph_name: str
    node_name: str
    error_code: str
    message: str
    payload: dict
    state_snapshot: dict
    status: str


class TicketStore:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create(self, run_id: str, graph_name: str, node_name: str,
                error_code: str, message: str, payload: dict,
                state_snapshot: dict) -> Ticket:
        conn = _connect(self.db_path)
        try:
            cur = conn.execute(
                "INSERT INTO tickets (run_id, graph_name, node_name, "
                "error_code, message, payload_json, state_snapshot_json, "
                "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)",
                (run_id, graph_name, node_name, error_code, message,
                 json.dumps(payload or {}), json.dumps(state_snapshot), _now()),
            )
            conn.commit()
            return Ticket(cur.lastrowid, run_id, graph_name, node_name,
                           error_code, message, payload or {}, state_snapshot, "open")
        finally:
            conn.close()

    def list_open(self, graph_name: Optional[str] = None) -> list[Ticket]:
        conn = _connect(self.db_path)
        try:
            if graph_name:
                rows = conn.execute(
                    "SELECT * FROM tickets WHERE status != 'resolved' "
                    "AND graph_name = ? ORDER BY created_at ASC",
                    (graph_name,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tickets WHERE status != 'resolved' "
                    "ORDER BY created_at ASC",
                ).fetchall()
            return [self._row_to_ticket(r) for r in rows]
        finally:
            conn.close()

    def set_status(self, ticket_id: int, status: str,
                     resolution_notes: str = "") -> Ticket:
        assert status in ("open", "investigating", "resolved")
        conn = _connect(self.db_path)
        try:
            resolved_at = _now() if status == "resolved" else None
            conn.execute(
                "UPDATE tickets SET status = ?, resolution_notes = ?, "
                "resolved_at = ? WHERE id = ?",
                (status, resolution_notes, resolved_at, ticket_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
            return self._row_to_ticket(row)
        finally:
            conn.close()

    @staticmethod
    def _row_to_ticket(row: sqlite3.Row) -> Ticket:
        return Ticket(
            id=row["id"], run_id=row["run_id"], graph_name=row["graph_name"],
            node_name=row["node_name"], error_code=row["error_code"],
            message=row["message"], payload=json.loads(row["payload_json"] or "{}"),
            state_snapshot=json.loads(row["state_snapshot_json"]),
            status=row["status"],
        )