# Coderift Technologies — Production Deployment Policy

**Owner:** Platform Engineering  
**Last reviewed:** 2026-07-01  
**Applies to:** All repositories deploying to any environment, with heightened requirements for `production`.

---

## Section 1 — Scope and Definitions

1.1 This policy governs every deployment action initiated through the Coderift MCP server's `deploy_to_production` tool, as well as any manual deployment executed by an engineer with direct infrastructure access.

1.2 "Deployment" means any act that changes the running code, configuration, or migration state of a Coderift service in any environment (staging or production).

1.3 "Pull request" (PR) is the unit of change. A PR must exist and be linked to every deployment — no ad-hoc pushes to production without a PR on record.

1.4 "Environment" refers to either `staging` (pre-production, shared test environment) or `production` (live customer-facing environment).

1.5 A deployment is considered "controlled" if it passes all gating conditions in Section 2 without requiring a human override. It is "uncontrolled" if any gate fails and the deploying engineer proceeds via an explicit elicitation-based confirmation.

1.6 Engineers are classified by role: `junior`, `senior`, or `lead`. Only `senior` and `lead` engineers may deploy to any environment or merge pull requests.

---

## Section 2 — Pre-Deployment Gates (All Environments)

2.1 **Security scan required.** Every PR must have a security scan record before deployment. A PR with no scan record at all is treated the same as a `Failed` scan — it cannot be auto-deployed.

2.2 **Passing scan for auto-deploy.** An automatic (no-elicitation) deployment requires the PR's latest security scan to be in `Passed` status. A `Pending` or `Failed` scan blocks automatic deployment.

2.3 **Code review required.** The PR must be in `Approved` status — reviewed by an engineer other than its author — before deployment to any environment, including staging.

2.4 **Author cannot be their own reviewer.** A PR where `author_id == reviewer_id` is invalid and must be treated as un-reviewed regardless of status field value.

2.5 **No deployment of Open or Rejected PRs.** PRs in `Open` status (not yet reviewed) or `Rejected` status cannot be deployed. Attempting to deploy either triggers mandatory elicitation.

2.6 **No deployment during in-progress deploys.** A repository+environment combination that already has a deployment in `Pending` or `InProgress` status must reject new deployment requests until the in-flight deployment resolves.

2.7 **Repository-environment ownership check.** The target environment must actually belong to the target repository. Mismatched repo+environment combinations are rejected server-side, not left to schema validation.

---

## Section 3 — Production-Specific Requirements

3.1 **Heightened scan requirement.** A deployment to `production` with a scan in `Pending` or `Failed` status requires an explicit human-in-the-loop confirmation before proceeding (see Section 5 on override procedure).

3.2 **Heightened review requirement.** A deployment to `production` of a PR that is not in `Approved` status requires an explicit human-in-the-loop confirmation before proceeding, regardless of scan status.

3.3 **Both conditions may apply simultaneously.** If a PR targeting production is both unreviewed and has a failing scan, both rule (a) and rule (b) of Section 5 apply — a single elicitation confirms both risks.

3.4 **Feature flag state review.** Before a production deployment, the deploying engineer should verify that any feature flags associated with the repository are in the intended state for the new release. Use `list_feature_flags` to review.

3.5 **Active incident check.** If the repository has an active `critical` or `high` severity incident at the time of deployment, the deploying engineer must assess whether the deployment addresses or could worsen the incident before proceeding.

3.6 **Deployment notes required for overrides.** Any deployment that proceeds via the human-override elicitation path must have a notes field describing why the override was approved. The system records this for audit.

---

## Section 4 — Authorization Requirements

4.1 Only engineers with role `senior` or `lead` may deploy to production. Junior engineers cannot deploy to any environment.

4.2 Authorization is verified server-side against the authenticated engineer's role on record — the engineer's declared identity in a client message is never trusted without server-side verification.

4.3 Only engineers with role `senior` or `lead` may merge pull requests. A merged PR without an authorized reviewer's approval is a policy violation.

4.4 Only engineers with role `senior` or `lead` may roll back a deployment. Rollback is a write action with production consequences and requires the same authorization as a forward deployment.

4.5 An inactive engineer (active = false) may not perform any write action even if their access code is technically known. The server checks the `active` flag on every authenticated call.

---

## Section 5 — Override Procedure (Human-in-the-Loop Confirmation)

5.1 Override is triggered when: (a) deploying to production with a scan that is not `Passed`, or (b) deploying a PR that is not in `Approved` status to any environment.

5.2 The MCP server pauses the deployment mid-call using `elicitation/create` and presents the specific reason the gate failed (failed scan status, missing review, or both) before asking for confirmation.

5.3 The deploying engineer must explicitly select `confirm: true` (accept) to proceed, or may decline, in which case no deployment record is created.

5.4 A declined elicitation is a clean, auditable "no deploy" — the tool returns a result explaining the decline, and no `deployments` row is written.

5.5 An accepted elicitation creates a deployment record with the `notes` field capturing the override reason. The override is logged and reviewable after the fact.

5.6 Override does not change the security scan status or PR review status. Those records remain as-is. The deploying engineer accepts responsibility for the known gap.

---

## Section 6 — Rollback Procedure

6.1 Identify the deployment id via `check_deployment_status` or by looking at the `deployments` table for the relevant repository and environment.

6.2 Call `rollback_deployment` with a `reason` field that clearly states what failure mode was observed and why rollback is the chosen response.

6.3 A deployment in `Failed` or `RolledBack` status cannot be rolled back a second time. A `Failed` deployment is already in a terminal state — investigate root cause, don't retry the rollback action.

6.4 Rollback marks the deployment as `RolledBack`. It does not automatically redeploy the previous version — a separate deployment of the prior PR is required to restore the previous code.

6.5 If the deployment caused or worsened a customer-visible incident, open or update an incident record immediately after rollback and notify the on-call lead.

6.6 After any production rollback, run `run_pre_deploy_checks` on the PR before re-attempting deployment, even if prior checks had passed. System state may have changed.

---

## Section 7 — Escalation Contacts

7.1 Platform on-call lead: Priya Raman (`ENG-LEAD-01`) — responsible for production stability decisions.

7.2 Payments team lead: Marcus Webb (`ENG-SEN-01`) — escalation for deployments to `payments-service`.

7.3 Frontend team: Ines Duarte (`ENG-SEN-02`) — escalation for deployments to `checkout-web`.

7.4 Security team: security@coderift.dev — for scan failures that an engineer wants to override in production.

7.5 For `critical` severity incidents with no available lead: page the full on-call rotation via PagerDuty and post in #incidents on Slack.

---

## Section 8 — Post-Deployment Verification

8.1 After every production deployment, the deploying engineer must monitor the relevant service's error rate and latency dashboards for at least 10 minutes before leaving the deployment unattended.

8.2 If any anomaly is detected within 30 minutes of a production deployment, initiate rollback immediately rather than waiting to diagnose the root cause first.

8.3 Succeeded status in the deployments table reflects that the deploy action itself completed — it does not mean the service is healthy. Always verify with external monitoring.

8.4 For deployments that enable a previously-disabled feature flag, confirm the flag is toggled correctly in the target environment before declaring the deployment complete.
