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

-- 1. بيانات جدول checkpoints
INSERT INTO checkpoints (run_id, graph_name, node_name, state_json, status)
VALUES 
    ('run-uuid-101', 'incident_response', 'triage', '{"incident_id": 1, "severity": "medium", "repo": "billing-worker"}', 'completed'),
    ('run-uuid-102', 'incident_response', 'deploy_fix', '{"incident_id": 2, "severity": "critical", "pr_id": 43}', 'paused_hitl'),
    ('run-uuid-103', 'security_remediation', 'diagnosis', '{"incident_id": 3, "repo": "auth-service"}', 'running');

-- 2. بيانات جدول hitl_tasks
INSERT INTO hitl_tasks (run_id, graph_name, node_name, reason, payload_json, status, decision_json, decided_by, decided_at)
VALUES 
    ('run-uuid-102', 'incident_response', 'deploy_fix', 'Critical incident requires lead sign-off before deploy', '{"pr_id": 43, "environment": "production"}', 'pending', NULL, NULL, NULL),
    ('run-uuid-104', 'incident_response', 'rollback', 'Approval needed for manual rollback', '{"deployment_id": 999}', 'approved', '{"approved": true, "reason": "Looks good"}', 'ENG-LEAD-01', datetime('now')),
    ('run-uuid-105', 'security_remediation', 'merge_pull_request', 'Security scan failed, override required', '{"pr_id": 50}', 'rejected', '{"approved": false, "reason": "Fix the vulnerabilities first"}', 'SEC-ADMIN', datetime('now'));

-- 3. بيانات جدول tickets
INSERT INTO tickets (run_id, graph_name, node_name, error_code, message, payload_json, state_snapshot_json, status, resolved_at, resolution_notes)
VALUES 
    ('run-uuid-106', 'incident_response', 'deploy_fix', 'DEPLOY_FIX_TOOL_ERROR', 'Connection timeout while deploying to production', '{"repo": "billing-worker", "pr_id": 45}', '{"incident_id": 4, "step": "deploy"}', 'open', NULL, NULL),
    ('run-uuid-107', 'incident_response', 'draft_summary', 'DRAFT_SUMMARY_TOOL_ERROR', 'Incident ID not found in tracker', '{"incident_id": 99}', '{"incident_id": 99, "severity": "low"}', 'investigating', NULL, NULL),
    ('run-uuid-108', 'security_remediation', 'run_pre_deploy_checks', 'PRE_DEPLOY_CHECKS_TOOL_ERROR', 'API rate limit exceeded', '{"pull_request_id": 12}', '{"pr_id": 12}', 'resolved', datetime('now'), 'Rate limit increased, ready to resume');

-- 4. بيانات جدول agent_tool_registrations
INSERT INTO agent_tool_registrations (agent_id, tool_name, enabled, updated_by)
VALUES 
    ('incident_response_agent', 'draft_incident_summary', 1, 'admin_user'),
    ('incident_response_agent', 'deploy_to_production', 1, 'admin_user'),
    ('incident_response_agent', 'check_deployment_status', 1, 'admin_user'),
    ('incident_response_agent', 'rollback_deployment', 0, 'admin_user'), -- Disabled tool
    ('security_agent', 'run_pre_deploy_checks', 1, 'admin_user');