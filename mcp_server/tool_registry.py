"""
mcp_server/tool_registry.py — Runtime Tool Registry for Admin Platform

Owner: Person A

ToolRegistry answers "may agent X call tool Y right now" against the live
agent_tool_registrations table, with NO in-memory caching — every call
opens a connection, reads the current row, and closes it again (same
pattern state_graph/store.py uses). That's deliberate, not an
optimization we forgot: the admin API (admin_platform/admin_tools_api.py)
and a running MCP server are normally two separate processes sharing one
sqlite file. A cache in either process would go stale the moment the
other one wrote a row, which is exactly the guardrail this file exists to
satisfy — "Tool add/remove from the admin panel must actually reach the
live MCP server," not just update a row nobody re-reads.
"""

import sqlite3
from pathlib import Path
from typing import List, Optional

# db/data/ subdirectory, not db/ directly -- must match db/init_db.py,
# state_graph/store.py, and admin_platform/admin_tools_api.py exactly.
# (Previously pointed at db/coderift.db, a path nothing else in this repo
# ever wrote to -- sqlite3.connect() silently created an empty file there,
# and is_enabled()'s OperationalError fallback made every check fail open,
# so an admin's tool toggle in admin_platform never reached the live
# server. Fixed as part of the Final Project's admin-platform correction.)
DB_PATH = Path(__file__).resolve().parent.parent / "db" / "data" / "coderift.db"


class ToolRegistry:
    """Per-instance registry. Pass a db_path to point it at a different
    database (tests use a tmp_path db); defaults to the real coderift.db
    otherwise, same as every other store in this project."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path is not None else DB_PATH

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def is_enabled(self, agent_id: str, tool_name: str) -> bool:
        """Default ALLOW when this (agent, tool) pair has no registration
        row at all — an admin who never touched this agent's tool list
        should not have accidentally locked it out of everything."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT enabled FROM agent_tool_registrations "
                "WHERE agent_id = ? AND tool_name = ?",
                (agent_id, tool_name),
            ).fetchone()
        except sqlite3.OperationalError:
            # Table doesn't exist yet (migration not applied) — allow all.
            return True
        finally:
            conn.close()
        if row is None:
            return True
        return bool(row["enabled"])

    def register(self, agent_id: str, tool_name: str,
                  updated_by: Optional[str] = None) -> None:
        """Enable (or re-enable) tool_name for agent_id."""
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO agent_tool_registrations
                    (agent_id, tool_name, enabled, updated_by)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(agent_id, tool_name) DO UPDATE
                    SET enabled = 1, updated_by = excluded.updated_by,
                        updated_at = datetime('now')
                """,
                (agent_id, tool_name, updated_by),
            )
            conn.commit()
        finally:
            conn.close()

    def deregister(self, agent_id: str, tool_name: str,
                     updated_by: Optional[str] = None) -> None:
        """Disable tool_name for agent_id. The row stays (enabled=0)
        rather than being deleted, so there's an audit trail of who
        disabled what and when."""
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO agent_tool_registrations
                    (agent_id, tool_name, enabled, updated_by)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(agent_id, tool_name) DO UPDATE
                    SET enabled = 0, updated_by = excluded.updated_by,
                        updated_at = datetime('now')
                """,
                (agent_id, tool_name, updated_by),
            )
            conn.commit()
        finally:
            conn.close()

    def seed_default_tools(self, agent_id: str, tool_names: List[str],
                             updated_by: Optional[str] = None) -> None:
        """Bulk-enable a starting tool set for a newly-configured agent."""
        for tool_name in tool_names:
            self.register(agent_id, tool_name, updated_by=updated_by)

    def get_tools(self, agent_id: str) -> List[str]:
        """Currently-enabled tool names for this agent."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT tool_name FROM agent_tool_registrations "
                "WHERE agent_id = ? AND enabled = 1 ORDER BY tool_name",
                (agent_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()
        return [r["tool_name"] for r in rows]

    def refresh(self) -> None:
        """No-op, kept for backward compatibility with any caller that
        still expects a refresh step: this registry never caches, so
        every call above already reads the live table."""
        return None


def is_tool_enabled(agent_id: str, tool_name: str) -> bool:
    """Module-level convenience for a one-off check against the real
    coderift.db without holding a ToolRegistry instance."""
    return ToolRegistry().is_enabled(agent_id, tool_name)