"""test_tool_registry_enforcement.py — proves the admin panel's tool
add/remove actually reaches the live MCP server, per the assignment's
explicit guardrail: "Tool add/remove from the admin panel must actually
reach the live MCP server. A UI toggle that doesn't change what tools the
agent can call earns no credit for this concern."

Before this fix, ToolRegistry existed and had a real schema
(agent_tool_registrations) but server.py's tools/list and tools/call
never consulted it — a toggle in the (future) admin UI would have updated
a database row nobody read. These tests exercise the real dispatch
functions in mcp_server/server.py directly, not a mock.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mcp_server import server
from mcp_server.auth import Session
from mcp_server.protocol import JSONRPCError, ERR_TOOL_DISABLED
from mcp_server.tool_registry import ToolRegistry

SCHEMA_SQL = Path(__file__).parent.parent / "db" / "schema.sql"
SEED_SQL = Path(__file__).parent.parent / "db" / "seed.sql"
MIGRATION_SQL = (Path(__file__).parent.parent / "db" / "migrations" /
                  "001_state_graph_and_admin_tables.sql")


@pytest.fixture()
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    c.executescript(SEED_SQL.read_text(encoding="utf-8"))
    c.executescript(MIGRATION_SQL.read_text(encoding="utf-8"))
    c.commit()
    yield c
    c.close()


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"  # same file the conn fixture writes to
    reg = ToolRegistry(db_path)
    monkeypatch.setattr(server, "TOOL_REGISTRY", reg)
    return reg


def agent_session(agent_id: str) -> Session:
    session = Session()
    server.handle_initialize(session, {
        "capabilities": {},
        "clientInfo": {"name": agent_id, "version": "0.1.0"},
    })
    return session


def test_agent_session_gets_agent_id_from_client_info():
    session = agent_session("security_remediation_agent")
    assert session.agent_id == "security_remediation_agent"


def test_human_session_with_no_client_info_is_unaffected_by_registry(conn, registry):
    """A plain human client (no clientInfo.name) must never be gated by
    per-agent tool registrations — those exist to restrict agents, not to
    accidentally lock out a human engineer using their own client."""
    registry.seed_default_tools("security_remediation_agent", ["get_pull_request"])
    registry.deregister("security_remediation_agent", "get_pull_request", updated_by="admin_alice")

    human_session = Session()
    server.handle_initialize(human_session, {"capabilities": {}})  # no clientInfo
    assert human_session.agent_id is None

    tools = server.handle_tools_list(human_session)["tools"]
    assert any(t["name"] == "get_pull_request" for t in tools)

    result = server.handle_tools_call(conn, human_session, {
        "name": "get_pull_request", "arguments": {"pull_request_id": 1},
    })
    assert result is not None  # not blocked


def test_disabled_tool_disappears_from_tools_list_for_that_agent(conn, registry):
    registry.seed_default_tools("security_remediation_agent", ["get_pull_request", "check_deployment_status"])
    registry.deregister("security_remediation_agent", "get_pull_request", updated_by="admin_alice")

    session = agent_session("security_remediation_agent")
    tool_names = {t["name"] for t in server.handle_tools_list(session)["tools"]}
    assert "get_pull_request" not in tool_names
    assert "check_deployment_status" in tool_names  # still enabled, both are public tools


def test_disabled_tool_is_rejected_at_tools_call_not_just_hidden(conn, registry):
    """The stricter, more important check: even if a client calls a
    disabled tool by name directly (ignoring what tools/list returned),
    the call must not execute."""
    registry.seed_default_tools("security_remediation_agent", ["get_pull_request"])
    registry.deregister("security_remediation_agent", "get_pull_request", updated_by="admin_alice")

    session = agent_session("security_remediation_agent")
    with pytest.raises(JSONRPCError) as exc_info:
        server.handle_tools_call(conn, session, {
            "name": "get_pull_request", "arguments": {"pull_request_id": 1},
        })
    assert exc_info.value.code == ERR_TOOL_DISABLED


def test_re_enabling_a_tool_makes_it_callable_again_without_redeploy(conn, registry):
    """Proves this is genuinely runtime, not requiring a server restart —
    the same in-process TOOL_REGISTRY / same live conn are reused."""
    registry.seed_default_tools("security_remediation_agent", ["get_pull_request"])
    registry.deregister("security_remediation_agent", "get_pull_request", updated_by="admin_alice")
    session = agent_session("security_remediation_agent")

    with pytest.raises(JSONRPCError):
        server.handle_tools_call(conn, session, {
            "name": "get_pull_request", "arguments": {"pull_request_id": 1},
        })

    registry.register("security_remediation_agent", "get_pull_request", updated_by="admin_alice")
    result = server.handle_tools_call(conn, session, {
        "name": "get_pull_request", "arguments": {"pull_request_id": 1},
    })
    assert result is not None


def test_agent_with_no_registrations_at_all_defaults_to_enabled(conn, registry):
    """An admin who has never touched this agent's tool list yet should
    not have accidentally locked it out of everything — ToolRegistry.
    is_enabled() already returns True for an unknown (agent, tool) pair;
    this proves that default survives all the way through dispatch."""
    session = agent_session("brand_new_agent_nobody_configured_yet")
    result = server.handle_tools_call(conn, session, {
        "name": "get_pull_request", "arguments": {"pull_request_id": 1},
    })
    assert result is not None
