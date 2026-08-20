from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path(__file__).parent.parent / "db" / "coderift.db"


class ToolRegistry:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def register(self, agent_id: str, tool_name: str, updated_by: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO agent_tool_registrations "
                "(agent_id, tool_name, enabled, updated_by, updated_at) "
                "VALUES (?, ?, 1, ?, datetime('now')) "
                "ON CONFLICT(agent_id, tool_name) DO UPDATE SET "
                "enabled = 1, updated_by = excluded.updated_by, "
                "updated_at = datetime('now')",
                (agent_id, tool_name, updated_by),
            )
            conn.commit()
        finally:
            conn.close()

    def deregister(self, agent_id: str, tool_name: str, updated_by: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO agent_tool_registrations "
                "(agent_id, tool_name, enabled, updated_by, updated_at) "
                "VALUES (?, ?, 0, ?, datetime('now')) "
                "ON CONFLICT(agent_id, tool_name) DO UPDATE SET "
                "enabled = 0, updated_by = excluded.updated_by, "
                "updated_at = datetime('now')",
                (agent_id, tool_name, updated_by),
            )
            conn.commit()
        finally:
            conn.close()

    def is_enabled(self, agent_id: str, tool_name: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT enabled FROM agent_tool_registrations "
                "WHERE agent_id = ? AND tool_name = ?",
                (agent_id, tool_name),
            ).fetchone()
            return True if row is None else bool(row["enabled"])
        finally:
            conn.close()

    def list_for_agent(self, agent_id: str) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT tool_name, enabled, updated_by, updated_at "
                "FROM agent_tool_registrations WHERE agent_id = ? "
                "ORDER BY tool_name",
                (agent_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def list_agents(self) -> list[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT agent_id FROM agent_tool_registrations "
                "ORDER BY agent_id"
            ).fetchall()
            return [row["agent_id"] for row in rows]
        finally:
            conn.close()

    def seed_default_tools(self, agent_id: str, tool_names: list[str],
                              seeded_by: str = "system") -> None:
        for tool_name in tool_names:
            self.register(agent_id, tool_name, updated_by=seeded_by)