# db/ — Coderift Technologies Database

## Why SQLite

This is a teaching-scale project meant to be cloned and run end-to-end by a
grader in minutes: one file, zero setup, no server process to stand up
alongside the MCP server itself. Coderift's actual production DB would be
Postgres (concurrent writers, real migrations), but nothing in this
project's schema or the 9 protocol concerns depends on Postgres-only
features (no `LISTEN/NOTIFY`, no advisory locks, no JSON columns) — the
`mcp_server/db.py` connection layer is the only place that would need to
change to point this at a real Postgres instance later. SQLite's
`PRAGMA foreign_keys = ON` is enough to enforce the same referential
integrity a grader would expect from the ERD.

## Files

- `schema.sql` — table definitions (8 tables, matching `ERD.mmd` exactly).
- `seed.sql` — realistic seed data, including the required edge cases:
  a PR with a **Failed** security scan, a PR with a **Pending** scan, a
  **Failed** deployment that produced an **open, critical** incident, and
  an engineer at every role level (plus one **inactive** engineer, for the
  "revoked access" auth edge case).
- `ERD.mmd` — Mermaid source for the entity-relationship diagram.
- `init_db.py` — builds `coderift.db` from `schema.sql` + `seed.sql`.

## Build the database

```bash
python db/init_db.py
```

This creates `db/coderift.db`. Re-running it drops and rebuilds from
scratch, so the demo scenarios always start from the same fixed state —
no reliance on lucky random data.
