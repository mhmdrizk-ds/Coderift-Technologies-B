# Coderift Technologies — Incident Response Runbook

**Owner:** Platform Engineering  
**Last reviewed:** 2026-07-01  
**Applies to:** All engineers on-call or involved in incident response.

---

## Section 1 — Severity Classifications

1.1 **Critical.** A `critical` incident means customer-facing data loss, payment processing failure, complete service outage, or a security breach with active exposure. Response begins immediately, 24/7.

1.2 **High.** A `high` incident means significant degradation of a customer-facing service (elevated error rate, >500ms P95 latency increase, or partial payment failures). Response begins within 15 minutes during business hours, 30 minutes off-hours.

1.3 **Medium.** A `medium` incident means non-customer-facing degradation, internal tooling failure, or a staging environment issue affecting a team's ability to ship. Response begins within 2 hours.

1.4 **Low.** A `low` incident means minor issues with no immediate customer impact, cosmetic bugs, or performance regressions below the `high` threshold. Response begins within 24 hours.

1.5 Incident severity may be upgraded mid-incident if initial assessment was incorrect. Downgrade requires lead engineer sign-off — never downgrade severity to reduce pressure.

1.6 An incident is "open" from the moment it is created in the `incidents` table until a `lead` engineer explicitly marks it `resolved` with a recorded `resolved_at` timestamp.

---

## Section 2 — Response Time SLAs

2.1 `Critical` incidents: acknowledgment within 5 minutes, incident channel open within 10 minutes, initial remediation action (rollback or hotfix deploy) within 30 minutes.

2.2 `High` incidents: acknowledgment within 15 minutes (business hours) or 30 minutes (off-hours), remediation action within 2 hours.

2.3 `Medium` incidents: acknowledgment within 2 hours, remediation within 8 hours.

2.4 `Low` incidents: acknowledgment within 24 hours, remediation within the next sprint.

2.5 SLA clocks start from the `created_at` timestamp on the `incidents` table row, not from when an engineer first becomes aware. Monitoring and alerting must create the row promptly.

2.6 Failure to meet a `critical` SLA acknowledgment window requires a retroactive incident report explaining the detection gap.

---

## Section 3 — Who to Page

3.1 For any `critical` incident: page Priya Raman (`ENG-LEAD-01`, Platform lead) immediately via PagerDuty. Do not wait to assess scope — page first, scope after.

3.2 For `critical` or `high` incidents affecting `payments-service`: also page Marcus Webb (`ENG-SEN-01`).

3.3 For `critical` or `high` incidents affecting `checkout-web`: also page Ines Duarte (`ENG-SEN-02`).

3.4 For `critical` or `high` incidents affecting `billing-worker`: page both Priya Raman and the billing team lead.

3.5 For any incident involving a security breach or suspected data exposure: page security@coderift.dev in addition to the platform lead, regardless of severity classification.

3.6 If the primary on-call lead does not acknowledge within 5 minutes on a `critical` incident, escalate to the full on-call rotation and post in #incidents on Slack.

3.7 Do not wait for perfect information before paging. Page on suspicion of `critical` — a false alarm is far less costly than a delayed page on a real incident.

---

## Section 4 — Deployment Halt Conditions

4.1 **Halt all deployments** when: any `critical` incident is open and its cause has not been identified, or when a deployment itself triggered the incident and rollback has not yet completed.

4.2 A deployment halt means no new deployments to any environment until the lead engineer explicitly lifts the halt. The halt is enforced operationally — the MCP server does not automatically block deployments during incidents, so the on-call engineer must communicate the halt actively.

4.3 A rollback of the offending deployment may proceed during a halt (it is remediation, not a new deployment). A new forward deployment may not.

4.4 During a deployment halt triggered by a `billing-worker` incident (as in incident #1 in the seed database), the payments-service and checkout-web can still receive deployments unless their own health is also affected.

4.5 **Deploy to staging only** (not production) when: a `high` incident is open and its root cause is known but the fix has not yet been validated. Validate the fix in staging, resolve the incident, then deploy to production.

4.6 Lift the deployment halt by explicit statement from the on-call lead in the incident channel, followed by updating the incident status to `resolved` in the system.

---

## Section 5 — Emergency Hotfix Procedure

5.1 A "hotfix" is a PR created specifically to address an active `critical` or `high` incident, bypassing the normal sprint process.

5.2 Hotfix PRs must still have a security scan (`run_pre_deploy_checks`) before deploying to production. There is no scan waiver for hotfixes — expedite the scan, do not skip it.

5.3 Hotfix PRs must be reviewed by a `lead` engineer, not just a `senior`. For `critical` incidents, the lead may self-assign as reviewer if no other lead is available and the time pressure is documented.

5.4 Hotfix deployment to staging for validation does not require an `Approved` PR status, but the staging deployment must complete successfully before the PR is merged and deployed to production.

5.5 After a hotfix deployment to production, create a follow-up PR within 24 hours to add tests covering the fixed code path. Hotfixes without follow-up test coverage must be tracked as a tech debt item.

5.6 The scan validity window for hotfix PRs deployed during a `critical` incident may be extended to 72 hours with the on-call lead's approval (see security_review_policy.md Section 5.5).

---

## Section 6 — Post-Incident Review Requirements

6.1 Every `critical` incident requires a written post-incident review (PIR) within 48 hours of the incident resolving.

6.2 Every `high` incident requires a PIR within 1 week of resolving.

6.3 `Medium` and `low` incidents require a brief retro note, not a full PIR, within the next sprint.

6.4 The PIR must include: timeline of events (detection → acknowledgment → remediation → resolution), root cause analysis, contributing factors, a list of action items with owners, and what monitoring or tooling change would have reduced time-to-detect.

6.5 PIR action items are tracked as GitHub Issues with the `incident-followup` label and must be completed within two sprints of the incident close date.

6.6 PIRs are blameless. The goal is system improvement, not individual accountability. Do not name engineers as the "cause" of an incident — name the technical or process gap.

6.7 For the `billing-worker` caching incident (incident #1 in the database), a PIR covering the cache invalidation bug and the missing post-deploy validation step is required. Action items should include: adding a cache correctness health check to the post-deploy monitoring suite, and requiring `run_pre_deploy_checks` re-run after any `RolledBack` deployment in the same environment before the next forward deploy.

---

## Section 7 — Incident Records in the System

7.1 Every incident must have a row in the `incidents` table. Incidents tracked only in Slack or PagerDuty without a system record are policy violations.

7.2 A `deployment_id` foreign key should be populated if a specific deployment triggered the incident. If the cause is environmental (not a code deployment), leave `deployment_id` as null.

7.3 Incident severity and status fields must be kept current as the incident evolves. Do not close (`resolved`) an incident before the service is confirmed stable.

7.4 Use the `draft_incident_summary` MCP tool to generate a human-readable summary of the incident's deployment and PR context for the PIR. Verify the generated summary against the raw facts before including it in any external communication.

7.5 Use the `draft_incident_postmortem` prompt template to structure the PIR document. Fill in all sections — do not leave sections blank because the data "wasn't captured."
