"""
db/apply_migration.py

Owner: Person A.

Applies db/migrations/*.sql, in filename order, to db/coderift.db.
Run this ONCE after `python db/init_db.py` (which builds the base schema
from schema.sql + seed.sql). This script only ADDS the state-graph and
admin-tooling tables from 001_state_graph_and_admin_tables.sql; it never
touches the 8 original tables.

Usage:
    python db/apply_migration.py
    python db/apply_migration.py --db path/to/other.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
# Same db/data/ subdirectory as init_db.py -- see its comment for why.
DEFAULT_DB_PATH = Path(__file__).parent / "data" / "coderift.db"


def apply_migrations(db_path: Path) -> None:
    if not db_path.exists():
        print(
            f"[apply_migration] {db_path} does not exist yet. "
            f"Run `python db/init_db.py` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        print(f"[apply_migration] no .sql files found in {MIGRATIONS_DIR}")
        return

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        for migration_file in migration_files:
            sql = migration_file.read_text(encoding="utf-8")
            print(f"[apply_migration] applying {migration_file.name} ...")
            conn.executescript(sql)
            conn.commit()
        print("[apply_migration] done.")
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH))
    args = parser.parse_args()
    apply_migrations(Path(args.db))