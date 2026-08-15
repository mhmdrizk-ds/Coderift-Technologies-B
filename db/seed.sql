-- engineers: one at every role level, plus one inactive senior for the
-- "inactive badge" edge case (mirrors an ex-employee whose access should
-- be refused even though the row still exists).
INSERT INTO engineers (name, role, email, access_code, active)
VALUES
('Riley Chen', 'junior', 'riley.chen@coderift.dev', 'ENG-JUN-01', 1),
('Marcus Webb', 'senior', 'marcus.webb@coderift.dev', 'ENG-SEN-01', 1),
('Ines Duarte', 'senior', 'ines.duarte@coderift.dev', 'ENG-SEN-02', 1),
('Priya Raman', 'lead', 'priya.raman@coderift.dev', 'ENG-LEAD-01', 1),
('Tom Okafor', 'senior', 'tom.okafor@coderift.dev', 'ENG-SEN-99', 0);

INSERT INTO repositories (name, description, owner_team)
VALUES
('payments-service', 'Handles outbound payment processing and webhooks.', 'Payments'),
('checkout-web', 'Customer-facing checkout flow and price calculation.', 'Frontend'),
('billing-worker', 'Background worker that generates and caches invoices.', 'Billing');

INSERT INTO environments (name, repository_id)
VALUES
('staging', 1),     -- 1: payments-service / staging
('production', 1),  -- 2: payments-service / production
('staging', 2),     -- 3: checkout-web / staging
('production', 2),  -- 4: checkout-web / production
('staging', 3),     -- 5: billing-worker / staging
('production', 3);  -- 6: billing-worker / production

-- author_id / reviewer_id reference engineers.id above.
INSERT INTO pull_requests (repository_id, title, description, author_id, status, reviewer_id)
VALUES
-- 1: clean, approved, will get a Passed scan -> used for the uncontrolled
--    (no-elicitation) staging deploy demo.
(1, 'Add retry logic to payment webhook',
    'Retries transient 5xx responses from the payment gateway up to 3 times.',
    1, 'Approved', 2),
-- 2: approved but its scan below is Failed -> production deploy of this PR
--    must trigger elicitation under rule (a).
(2, 'Refactor checkout price calculation',
    'Extracts tax and discount math into a shared pricing module.',
    2, 'Approved', 3),
-- 3: not yet reviewed (Open) -> deploying this PR anywhere must trigger
--    elicitation under rule (b), regardless of environment.
(3, 'Hotfix: incorrect invoice rounding',
    'Fixes a floating-point rounding error that undercharges invoices by $0.01.',
    4, 'Open', NULL),
-- 4: rejected, failed dependency scan -> negative/edge case, never deployed.
(1, 'Bump dependency versions',
    'Routine dependency bump; rejected after a Failed dependency scan.',
    1, 'Rejected', 2),
-- 5: merged and already deployed once (see deployments #1 below, which
--    Failed and produced the seeded critical incident).
(3, 'Add caching layer for invoice queries',
    'Adds an in-memory cache in front of the invoice lookup query.',
    3, 'Merged', 4),
-- 6: reviewed and approved by a human, but its scan (below) is still
--    Pending, not Failed and not Passed -> the genuinely ambiguous case
--    the Decomposition & Planning lab's Tree of Thoughts routing needs
--    (see planning_toolkit/README.md, "Why Tree of Thoughts"). Additive
--    only: does not renumber or touch PRs 1-5.
(2, 'Add saved-address autofill to checkout',
    'Lets returning customers autofill a saved shipping address at checkout.',
    1, 'Approved', 3);

INSERT INTO security_scans (pull_request_id, status, scan_type)
VALUES
(1, 'Passed', 'SAST'),   -- PR 1: Passed
(2, 'Failed', 'SAST'),   -- PR 2: Failed  (required edge case: a Failed scan)
(3, 'Pending', 'SAST'),  -- PR 3: Pending (required edge case: a Pending scan)
(4, 'Failed', 'Dependency'),
(5, 'Passed', 'SAST'),
(6, 'Pending', 'SAST');  -- PR 6: Approved status but Pending scan (ambiguous ToT case)

-- deployment #1: a Failed production deploy that triggered the seeded
-- critical incident (required edge case). deployment #2: a routine
-- Succeeded staging deploy, used as prior history / context only.
INSERT INTO deployments (repository_id, environment_id, deployed_by, pull_request_id, status, notes)
VALUES
(3, 6, 4, 5, 'Failed', 'Cache invalidation bug caused stale invoice totals to be served.'),
(1, 1, 2, 1, 'Succeeded', 'Routine staging deploy, no issues.');

-- incident #1: active critical incident on billing-worker (required edge
-- case: at least one repository with an active critical incident).
-- incident #2: an older, already-resolved incident for contrast.
INSERT INTO incidents (deployment_id, title, severity, status, resolved_at)
VALUES
(1, 'Invoice queries returning stale cached totals', 'critical', 'open', NULL),
(NULL, 'Elevated checkout-web latency after a config change', 'low', 'resolved', '2026-07-15 09:40:00');

INSERT INTO feature_flags (repository_id, environment_id, name, enabled)
VALUES
(1, 1, 'new-payment-retry-logic', 1),
(1, 2, 'new-payment-retry-logic', 0),
(2, 4, 'new-checkout-price-calc', 0),
(3, 6, 'invoice-cache-layer', 1);
