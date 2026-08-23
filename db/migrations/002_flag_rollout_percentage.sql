-- 002_flag_rollout_percentage.sql
--
-- Owner: Person C (flag-rollout branch).
--
-- The original schema.sql's `feature_flags` table only has a boolean
-- `enabled` column — there was no way to represent "this flag is live for
-- 25% of production traffic," which the Feature Flag Rollout graph needs
-- as its core unit of state. This migration ADDS to that table rather
-- than replacing it, the same additive convention 001 already established
-- for the state-graph/admin tables (never touch the 8 original tables'
-- existing columns, only add new ones/new tables alongside them).
--
-- `rollout_pct` defaults to 100 for existing rows so the pre-existing
-- boolean semantics keep working unchanged for any code that only reads
-- `enabled`: an already-`enabled=1` flag is fully rolled out (100%), and
-- an `enabled=0` flag is unaffected (still off, rollout_pct irrelevant).
ALTER TABLE feature_flags ADD COLUMN rollout_pct INTEGER NOT NULL DEFAULT 100
    CHECK (rollout_pct >= 0 AND rollout_pct <= 100);

-- A rollout-window metrics record: what a real external monitoring signal
-- for a specific (flag, step) would report. Written by the metrics tool
-- below; read by the flag_rollout graph's awaiting_metrics node via
-- get_error_rate_metrics. Kept separate from feature_flags itself because
-- a flag has exactly one current rollout_pct but many historical metric
-- windows as it steps through a rollout sequence.
CREATE TABLE IF NOT EXISTS flag_rollout_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_flag_id INTEGER NOT NULL,
    rollout_pct     INTEGER NOT NULL,       -- the % this window was measured at
    error_rate      REAL NOT NULL,          -- observed error rate for this window
    baseline_error_rate REAL NOT NULL,      -- this repo's historical baseline, for comparison
    result          TEXT NOT NULL CHECK (result IN ('healthy', 'degraded', 'error_spike')),
    recorded_at     TEXT NOT NULL DEFAULT (datetime('now')),

    FOREIGN KEY (feature_flag_id) REFERENCES feature_flags(id)
);
CREATE INDEX IF NOT EXISTS idx_flag_rollout_metrics_flag
    ON flag_rollout_metrics (feature_flag_id, recorded_at DESC);
