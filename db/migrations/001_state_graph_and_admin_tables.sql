CREATE TABLE IF NOT EXISTS checkpoints (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    graph_name  TEXT NOT NULL,          -- e.g. 'incident_response'
    node_name   TEXT NOT NULL,          -- the node this checkpoint resumes INTO
    state_json  TEXT NOT NULL,          -- full serialized graph state at this point
    status      TEXT NOT NULL CHECK (status IN
                    ('running', 'paused_hitl', 'waiting', 'ticketed', 'completed')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_run_id
    ON checkpoints (run_id, id DESC);

CREATE TABLE IF NOT EXISTS hitl_tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    graph_name    TEXT NOT NULL,
    node_name     TEXT NOT NULL,
    reason        TEXT NOT NULL,        -- human-readable: WHY this needed a human
    payload_json  TEXT,                 -- whatever the admin needs to see to decide
    status        TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected')),
    decision_json TEXT,                 -- {"approved": bool, "reason": "...", ...}
    decided_by    TEXT,                 -- admin identifier
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_hitl_tasks_status
    ON hitl_tasks (status, created_at);

CREATE TABLE IF NOT EXISTS tickets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL,
    graph_name          TEXT NOT NULL,
    node_name           TEXT NOT NULL,
    error_code          TEXT NOT NULL,     -- e.g. 'DEPLOY_FIX_TOOL_ERROR'
    message             TEXT NOT NULL,
    payload_json        TEXT,
    state_snapshot_json TEXT NOT NULL,     -- state AT THE MOMENT OF FAILURE, for resume
    status              TEXT NOT NULL DEFAULT 'open'
                          CHECK (status IN ('open', 'investigating', 'resolved')),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at         TEXT,
    resolution_notes    TEXT
);
CREATE INDEX IF NOT EXISTS idx_tickets_status
    ON tickets (status, created_at);

CREATE TABLE IF NOT EXISTS agent_tool_registrations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id     TEXT NOT NULL,          -- e.g. 'incident_response_agent'
    tool_name    TEXT NOT NULL,          -- must match a real MCP tool name
    enabled      INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    updated_by   TEXT,                   -- admin identifier
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (agent_id, tool_name)
);