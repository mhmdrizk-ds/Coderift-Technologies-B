"""
mcp_server/tool_registry.py — Runtime Tool Registry for Admin Platform

Owner: Person A

Provides:
  - ToolRegistry class (for backward compatibility with server.py)
  - is_tool_enabled() function (for dispatch checks)
  - refresh_registry() to reload from DB without restart
"""

import sqlite3
from pathlib import Path
from typing import Dict, List, Set

DB_PATH = Path(__file__).resolve().parent.parent / "db" / "coderift.db"

# In-memory cache
_registry: Dict[str, Set[str]] = {}


def _get_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def refresh_registry() -> None:
    """Reload agent_tool_registrations table into memory."""
    global _registry
    _registry = {}
    try:
        conn = _get_db_conn()
        rows = conn.execute(
            "SELECT agent_id, tool_name FROM agent_tool_registrations WHERE enabled = 1"
        ).fetchall()
        conn.close()
        for row in rows:
            agent = row["agent_id"]
            tool = row["tool_name"]
            _registry.setdefault(agent, set()).add(tool)
    except sqlite3.OperationalError:
        # Table does not exist yet — registry stays empty (allow all)
        pass


def is_tool_enabled(agent_id: str, tool_name: str) -> bool:
    """Check if agent is allowed to call tool. Default ALLOW ALL if no entries."""
    if agent_id not in _registry:
        return True
    return tool_name in _registry[agent_id]


# Backward-compatible class for server.py
class ToolRegistry:
    """Backward-compatible ToolRegistry class.

    server.py may instantiate this as: registry = ToolRegistry()
    We keep it lightweight — all real logic is in module-level functions.
    """

    def __init__(self):
        refresh_registry()

    def is_enabled(self, agent_id: str, tool_name: str) -> bool:
        return is_tool_enabled(agent_id, tool_name)

    def refresh(self) -> None:
        refresh_registry()

    def get_tools(self, agent_id: str) -> List[str]:
        refresh_registry()
        return sorted(_registry.get(agent_id, set()))


# Initialize on import
refresh_registry()