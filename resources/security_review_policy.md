# Coderift Technologies — Security Review Policy

**Owner:** Security Engineering  
**Last reviewed:** 2026-07-01  
**Applies to:** All pull requests submitted against any Coderift repository.

---

## Section 1 — Purpose and Scope

1.1 This policy defines when a security review is required, what each scan type and status means operationally, who may override a failed scan, and the time limits within which scan results remain valid for deployment decisions.

1.2 "Security scan" means any automated or manual review that produces a `Passed`, `Failed`, or `Pending` result stored in the `security_scans` table, linked to a specific pull request.

1.3 All PRs must have at least one security scan on record before merging or deploying. A PR with no scan record is treated identically to a PR with a `Failed` scan for all gating decisions.

1.4 This policy applies to scans of any type: `SAST` (Static Application Security Testing), `Dependency` (software composition analysis), `Secrets` (credential and secret detection), and `DAST` (Dynamic Application Security Testing for deployed services).

---

## Section 2 — When a Security Review Is Required

2.1 **All pull requests.** Every PR opened against any repository requires a `SAST` scan before it may be approved, merged, or deployed.

2.2 **Dependency changes.** Any PR that modifies `requirements.txt`, `package.json`, `pyproject.toml`, `go.mod`, or equivalent dependency manifests also requires a `Dependency` scan in addition to `SAST`.

2.3 **Authentication and authorization changes.** PRs touching authentication flows, access control logic, session management, or the MCP server's authorization handlers require an additional manual security review sign-off from a `lead`-role engineer.

2.4 **Secrets management changes.** PRs modifying `.env.example`, environment variable handling, or credential injection logic require a `Secrets` scan. If a `Secrets` scan returns `Failed`, the PR must be closed and a new PR opened — the commit history containing the secret cannot be reused.

2.5 **Production configuration changes.** Any PR that modifies production-environment feature flags, infrastructure configuration, or deployment parameters requires review by the on-call lead before deployment.

2.6 **Hotfix PRs.** A hotfix PR (bypassing normal sprint process) may be deployed to staging for validation without a completed review, but requires both `Passed` scan and `Approved` status before deploying to production, same as any other PR. There is no "hotfix exception" to the production gate.

---

## Section 3 — Scan Statuses and Their Operational Meaning

3.1 **Passed.** The scan completed and found no issues above the configured severity threshold. A PR with `Passed` as its latest scan status is eligible for automatic (no-elicitation) deployment if it also has `Approved` review status.

3.2 **Failed.** The scan completed and found one or more issues at or above the configured severity threshold. Causes: known CVEs in dependencies, hardcoded secrets, injection vulnerabilities, unvalidated input handling, or unsafe deserialization.

3.3 **Pending.** The scan was requested but has not yet completed. A PR in `Pending` scan state must not be deployed to production without a human-in-the-loop confirmation. Staging deployments of `Pending`-scan PRs require a `senior` or `lead` engineer and a recorded justification.

3.4 **No scan record.** A PR with no entry in `security_scans` at all is treated as `Failed` for all gating purposes. Run `run_pre_deploy_checks` to trigger a fresh scan.

3.5 **Stale scan.** A `Passed` scan older than 30 days is considered stale if the PR has received new commits since the scan was run. Re-run `run_pre_deploy_checks` before deploying.

---

## Section 4 — Override Authority and Conditions

4.1 Only a `lead`-role engineer may authorize deployment of a PR with a `Failed` scan to production. A `senior` engineer may not self-authorize a failed-scan production deployment.

4.2 Override requires explicit elicitation confirmation (the MCP server's `elicitation/create` gate) — verbal or chat-based approval is not sufficient. The confirmation must be captured in the system's audit trail.

4.3 Valid reasons for a `Failed` scan override: (a) the finding is a known false positive documented in the security team's false-positive registry, (b) the deployment corrects a worse active vulnerability and delay would increase risk, (c) the affected code path is provably unreachable in the deployed configuration.

4.4 Invalid reasons for override: (a) "we're under time pressure," (b) "the scan always fails on this repo," (c) "it worked fine in staging." These do not constitute documented justifications.

4.5 Every override is reviewed by the Security team (security@coderift.dev) within 24 hours of production deployment. Repeated unjustified overrides are escalated to the engineering lead.

4.6 A `Pending` scan may be overridden by a `senior` or `lead` engineer, subject to the same elicitation-capture requirement, when the scan infrastructure is known to be delayed and the code change is low-risk.

---

## Section 5 — Scan Validity Time Limits

5.1 A `Passed` scan result is valid for deployment for 30 calendar days from its `created_at` timestamp, provided the PR has not received new commits in the interim.

5.2 If new commits are pushed to a PR after a `Passed` scan, the scan result is immediately invalidated and `run_pre_deploy_checks` must be re-run regardless of when the previous scan was completed.

5.3 A `Failed` scan result does not expire — it must be resolved (either fixed and re-scanned to `Passed`, or explicitly overridden per Section 4) before deployment.

5.4 A `Pending` scan in progress for more than 2 hours is considered stalled. The engineer should contact the Security team and may initiate a re-scan via `run_pre_deploy_checks`.

5.5 For emergency hotfixes under active incident conditions (see incident_response_runbook.md Section 4), the scan validity window may be extended to 72 hours with explicit lead approval. This does not waive the scan requirement — a valid scan must still exist, just within the extended window.

---

## Section 6 — Running and Re-Running Scans

6.1 The `run_pre_deploy_checks` MCP tool runs three sequential stages: (1) unit tests, (2) integration tests, (3) a fresh `SAST` security scan. It writes a new `security_scans` row on completion.

6.2 Each stage reports intermediate progress. Engineers must not interrupt `run_pre_deploy_checks` mid-run — an interrupted run may leave the security_scans table in an inconsistent state.

6.3 A new scan triggered by `run_pre_deploy_checks` supersedes the previous latest scan for gating purposes (the system uses the most recent scan by `created_at`).

6.4 Dependency scans (`Dependency` type) are not currently triggered by `run_pre_deploy_checks` and must be requested from the Security team separately when a dependency-manifest-modifying PR is ready for review.

6.5 Engineers may not manually insert or modify `security_scans` rows. All scan records must originate from the scan infrastructure via `run_pre_deploy_checks` or the Security team's toolchain.

---

## Section 7 — Scan Failure Response

7.1 When a scan returns `Failed`, the responsible engineer (typically the PR author) should resolve all identified issues and push a corrected commit before re-running the scan.

7.2 If the failure is believed to be a false positive, file a ticket with the Security team before proceeding. Do not deploy with a known-failed scan while waiting for false-positive classification.

7.3 For `Failed` dependency scans identifying known CVEs: update the affected package to a patched version. If no patched version exists, contact the Security team for a compensating control decision.

7.4 For `Failed` secrets scans: treat the secret as compromised immediately. Rotate it in all environments before deploying. Do not push a "removal" commit and deploy — the secret's exposure window in the commit history is the real risk.

7.5 The Security team must be notified of any `Failed` scan that is overridden and deployed to production within 1 hour of that deployment completing.
