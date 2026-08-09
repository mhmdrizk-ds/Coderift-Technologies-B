"""
db.py — thin SQLite access layer on top of db/schema.sql.

Deliberately dumb: no ORM, no query builder. Every tool handler in
tools_impl/ writes its own explicit SQL so a grader can see exactly what
each tool reads or writes. This module only owns the connection and a
handful of shared lookups (engineers, repositories, environments, latest
scan) used by more than one tool.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "db" / "coderift.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_engineer_by_access_code(conn, access_code: str):
    return conn.execute(
        "SELECT id, name, role, email, access_code, active FROM engineers WHERE access_code = ?",
        (access_code,),
    ).fetchone()


def get_engineer_by_id(conn, engineer_id: int):
    return conn.execute(
        "SELECT id, name, role, email, access_code, active FROM engineers WHERE id = ?",
        (engineer_id,),
    ).fetchone()


def get_repository_by_name(conn, name: str):
    return conn.execute(
        "SELECT id, name, description, owner_team FROM repositories WHERE name = ?",
        (name,),
    ).fetchone()


def get_environment(conn, repository_id: int, name: str):
    """Environment row for a given repository + environment name, or None
    if that environment doesn't exist for that repository. Used by
    deploy_to_production's defensive validation: the schema can't know
    whether 'production' actually exists for a given repo."""
    return conn.execute(
        "SELECT id, name, repository_id FROM environments WHERE repository_id = ? AND name = ?",
        (repository_id, name),
    ).fetchone()


def get_pull_request(conn, pull_request_id: int):
    return conn.execute(
        """
        SELECT pr.id, pr.repository_id, pr.title, pr.description, pr.author_id,
               pr.status, pr.reviewer_id, pr.created_at,
               r.name AS repository_name,
               a.name AS author_name
        FROM pull_requests pr
        JOIN repositories r ON r.id = pr.repository_id
        JOIN engineers a ON a.id = pr.author_id
        WHERE pr.id = ?
        """,
        (pull_request_id,),
    ).fetchone()


def get_latest_security_scan(conn, pull_request_id: int):
    return conn.execute(
        """
        SELECT id, pull_request_id, status, scan_type, created_at
        FROM security_scans
        WHERE pull_request_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (pull_request_id,),
    ).fetchone()


def get_in_flight_deployment(conn, repository_id: int, environment_id: int):
    """A deployment for this repo+environment that's still Pending or
    InProgress, if any. Used by deploy_to_production's defensive
    validation: 'verify the deployment isn't already in progress' — a
    business rule no JSON Schema can express."""
    return conn.execute(
        """
        SELECT id, status, created_at
        FROM deployments
        WHERE repository_id = ? AND environment_id = ? AND status IN ('Pending', 'InProgress')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (repository_id, environment_id),
    ).fetchone()
