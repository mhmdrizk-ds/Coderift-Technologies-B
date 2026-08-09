# Coderift Technologies — Production Deployment Policy

**Owner:** Platform Engineering
**Applies to:** all repositories, all environments

## 1. Required security scan status

No pull request may be deployed to **production** unless its most recent
security scan status is **Passed**. A scan status of `Failed` or `Pending`
blocks an automatic production deploy — the deploying engineer must
explicitly confirm they understand the risk before the deploy proceeds
(see §3, Elicitation).

Staging deploys are not blocked by scan status, since staging does not
serve customer traffic. This is a deliberate, narrower rule than the PR
review requirement below.

## 2. Required PR review status

No pull request may be deployed to **any environment**, staging or
production, unless it is in `Approved` status. An `Open` (unreviewed) or
`Rejected` pull request cannot be auto-deployed anywhere — deploying an
unreviewed change even to staging still requires explicit human
confirmation, because staging is often used to validate exactly the kind
of change that hasn't been reviewed yet.

## 3. Human-in-the-loop confirmation (elicitation)

`deploy_to_production` pauses for human confirmation when either:

- the target environment is `production` **and** the latest security scan
  is not `Passed`, or
- the pull request has not been through code review (`status != Approved`)

A deploy that is Approved, has a Passed scan, and targets staging
completes immediately with no confirmation step. This keeps confirmation
fatigue low — it only fires for genuinely risky combinations.

## 4. Rollback procedure

Any `senior` or `lead` engineer may roll back a `Succeeded` or
`InProgress` deployment using `rollback_deployment`, with a documented
reason. A deployment already `Failed` or `RolledBack` cannot be rolled
back again. Rolling back does not automatically reopen the pull request
or revert the merge — that is a separate, manual step for the owning
team.

## 5. Escalation contacts

- **Platform on-call:** #platform-oncall (Slack), escalates to the
  Platform Engineering lead within 15 minutes for `critical` severity
  incidents.
- **Security review questions:** #appsec, business hours only; for an
  urgent Failed scan blocking a release, page `#platform-oncall` instead.
- **Payments-specific incidents:** additionally notify the Payments team
  lead directly, given regulatory reporting requirements on payment
  failures.
