# Demo Transcript — Coderift Technologies MCP Server Lab

This is real output from an actual run:

```bash
python db/init_db.py
python -m agent.client --all --no-rebuild
```

All 10 scenarios completed with no errors. Every assertion in
`agent/scenarios.py` (tool visibility, elicitation triggers, notification
firing, authorization refusals) passed against this run — nothing below is
hand-edited or hypothetical.

---

```

>>> Running scenario: capability_negotiation_full
>>> Full client (elicitation+sampling declared): confirm server capabilities before relying on them, browse resources/prompts pre-auth.
== initialize / initialized handshake (full client) ==
  client declared -> {'elicitation': {}, 'sampling': {}}
  server declared <- {'tools': {'listChanged': True}, 'resources': {'listChanged': False, 'subscribe': False}, 'prompts': {'listChanged': False}}
  server info     <- {'name': 'coderift-technologies', 'version': '0.1.0'}

######################################################################
# SCENARIO 1: Capability Negotiation (full client) + Resources + Prompts
######################################################################

  Confirmed: server declared tools.listChanged=true, so it's safe to rely on notifications/tools/list_changed later.

== tools/list (anonymous, pre-authentication) ==
  ['authenticate', 'check_deployment_status', 'get_pull_request']

== resources/list ==
  - policy://production-deployment: Production Deployment Policy

== resources/read policy://production-deployment ==
  # Coderift Technologies — Production Deployment Policy ...

== prompts/list ==
  - draft_rollback_plan: Rollback Plan
  - draft_incident_postmortem: Incident Postmortem

== prompts/get draft_rollback_plan (deployment_id=2) ==
  Draft a rollback plan for deployment #2.
  - Repository: payments-service
  - Deployment status: Succeeded

>>> Running scenario: capability_negotiation_read_only
>>> Read-only client (no elicitation/sampling declared): deploy_to_production and draft_incident_summary must stay hidden from tools/list even for a senior engineer; check_deployment_status is the mandated fallback; calling deploy_to_production directly anyway is rejected cleanly with ERR_CAPABILITY_UNSUPPORTED.
== initialize / initialized handshake (read_only client) ==
  client declared -> {}
  server declared <- {'tools': {'listChanged': True}, 'resources': {'listChanged': False, 'subscribe': False}, 'prompts': {'listChanged': False}}
  server info     <- {'name': 'coderift-technologies', 'version': '0.1.0'}

######################################################################
# SCENARIO 2: Capability Negotiation (client WITHOUT elicitation/sampling)
######################################################################

== tools/list (anonymous, pre-authentication, read-only client) ==
  ['authenticate', 'check_deployment_status', 'get_pull_request']

== authenticate(access_code='ENG-SEN-01') — a senior engineer ==

  [notification] notifications/tools/list_changed received — invalidating cached tool list

== tools/list AFTER authenticating as senior, still on the read-only client ==
  ['authenticate', 'check_deployment_status', 'get_pull_request', 'list_active_incidents', 'list_feature_flags', 'run_pre_deploy_checks', 'merge_pull_request', 'rollback_deployment']

  Confirmed: deploy_to_production and draft_incident_summary stayed hidden (no elicitation/sampling declared); merge_pull_request and rollback_deployment still appear (role-gated only, no capability requirement).

== fallback in action: check_deployment_status('payments-service', 'staging') ==
  {
  "repository_name": "payments-service",
  "environment_name": "staging",
  "deployment": {
    "deployment_id": 2,
    "status": "Succeeded",
    "deployed_by": "Marcus Webb",
    "pull_request_title": "Add retry logic to payment webhook",
    "created_at": "2026-08-09 00:58:19",
    "notes": "Routine staging deploy, no issues."
  }
}

== calling deploy_to_production anyway: 'checkout-web' -> 'production', PR #2 (a case that would need elicitation) — not offered, but still routed to the handler ==
  rejected cleanly as expected: [-32005] This action requires human-in-the-loop confirmation (elicitation), but the connected client did not declare elicitation support during initialize.

>>> Running scenario: defensive_and_authorization
>>> Missing required field, disallowed extra field, an unauthenticated write, an unauthorized (junior) write, and an inactive engineer's access code — the server must reject all five.
== initialize / initialized handshake (full client) ==
  client declared -> {'elicitation': {}, 'sampling': {}}
  server declared <- {'tools': {'listChanged': True}, 'resources': {'listChanged': False, 'subscribe': False}, 'prompts': {'listChanged': False}}
  server info     <- {'name': 'coderift-technologies', 'version': '0.1.0'}

######################################################################
# SCENARIO 3: Defensive Tool Design + Authorization
######################################################################

== missing required field ==
  rejected as expected: [-32602] $: missing required field 'pull_request_id'

== unknown/extra field (additionalProperties: false) ==
  rejected as expected: [-32602] $: unexpected field(s) not allowed by schema: ['sneaky']

== unauthenticated write attempt ==
  rejected as expected: [-32001] This tool requires authentication. Call the 'authenticate' tool with a valid access_code first.

== authenticate as junior (access_code='ENG-JUN-01') ==

  [notification] notifications/tools/list_changed received — invalidating cached tool list

== junior engineer attempts deploy_to_production directly (not shown in tools/list, but the handler must still refuse it) ==
  rejected as expected: [-32002] Role 'junior' is not authorized to call this tool. Requires one of: senior, lead.

== inactive engineer's access code is refused outright ==
  rejected as expected: [-32003] Access code 'ENG-SEN-99' belongs to an inactive engineer.

>>> Running scenario: notifications_on_promotion
>>> Authenticate as junior (read-only tools + run_pre_deploy_checks appear), then re-authenticate as senior on the SAME connection (write tools + list_feature_flags appear) — tools/list_changed fires both times, no reconnect.
== initialize / initialized handshake (full client) ==
  client declared -> {'elicitation': {}, 'sampling': {}}
  server declared <- {'tools': {'listChanged': True}, 'resources': {'listChanged': False, 'subscribe': False}, 'prompts': {'listChanged': False}}
  server info     <- {'name': 'coderift-technologies', 'version': '0.1.0'}

######################################################################
# SCENARIO 4: Notifications (role promotion -> tools/list_changed, twice)
######################################################################

== tools/list before authentication ==
  ['authenticate', 'check_deployment_status', 'get_pull_request']

== authenticate(access_code='ENG-JUN-01') — junior ==

  [notification] notifications/tools/list_changed received — invalidating cached tool list
  ['authenticate', 'check_deployment_status', 'get_pull_request', 'list_active_incidents', 'run_pre_deploy_checks', 'draft_incident_summary']
  newly visible after junior login: ['draft_incident_summary', 'list_active_incidents', 'run_pre_deploy_checks']

== re-authenticate on the SAME connection, no reconnect (access_code='ENG-SEN-01') — senior ==

  [notification] notifications/tools/list_changed received — invalidating cached tool list
  ['authenticate', 'check_deployment_status', 'get_pull_request', 'list_active_incidents', 'list_feature_flags', 'run_pre_deploy_checks', 'draft_incident_summary', 'deploy_to_production', 'merge_pull_request', 'rollback_deployment']
  newly visible after senior promotion: ['deploy_to_production', 'list_feature_flags', 'merge_pull_request', 'rollback_deployment']

>>> Running scenario: uncontrolled_deploy
>>> PR 1 is Approved with a Passed scan, deployed to staging — completes immediately, no elicitation.
== initialize / initialized handshake (full client) ==
  client declared -> {'elicitation': {}, 'sampling': {}}
  server declared <- {'tools': {'listChanged': True}, 'resources': {'listChanged': False, 'subscribe': False}, 'prompts': {'listChanged': False}}
  server info     <- {'name': 'coderift-technologies', 'version': '0.1.0'}

######################################################################
# SCENARIO 5: Uncontrolled deploy (Approved, Passed scan, staging) — no elicitation
######################################################################

  [notification] notifications/tools/list_changed received — invalidating cached tool list
  {
  "deployed": true,
  "deployment_created": true,
  "deployment_id": 3,
  "status": "Succeeded",
  "elicitation_required": false,
  "message": "'payments-service' PR #1 deployed to staging."
}

>>> Running scenario: controlled_deploy_scan_not_passed
>>> PR 2 is Approved but its latest scan is Failed; deploying to production triggers elicitation under rule (a). The engineer accepts, so the deploy proceeds with a recorded confirmation note.
== initialize / initialized handshake (full client) ==
  client declared -> {'elicitation': {}, 'sampling': {}}
  server declared <- {'tools': {'listChanged': True}, 'resources': {'listChanged': False, 'subscribe': False}, 'prompts': {'listChanged': False}}
  server info     <- {'name': 'coderift-technologies', 'version': '0.1.0'}

######################################################################
# SCENARIO 6: Controlled deploy — production + scan not Passed (rule a), ACCEPTED
######################################################################

  [notification] notifications/tools/list_changed received — invalidating cached tool list

== deploy_to_production('checkout-web', 'production', pull_request_id=2) — expect elicitation/create mid-call ==

======================================================================
HUMAN CONFIRMATION REQUESTED (elicitation/create)
----------------------------------------------------------------------
Deploying 'checkout-web' PR #2 ('Refactor checkout price calculation') to production is risky: target is production and the latest security scan is 'Failed', not Passed. Confirm you want to proceed anyway?
[scripted demo response] -> {'action': 'accept', 'content': {'confirm': True}}
======================================================================
  {
  "deployed": true,
  "deployment_created": true,
  "deployment_id": 4,
  "status": "Succeeded",
  "elicitation_required": true,
  "message": "'checkout-web' PR #2 deployed to production."
}

>>> Running scenario: controlled_deploy_unreviewed_declined
>>> PR 3 is still Open (not reviewed); deploying it anywhere triggers elicitation under rule (b), regardless of environment. This time the engineer declines, so no deployment is created.
== initialize / initialized handshake (full client) ==
  client declared -> {'elicitation': {}, 'sampling': {}}
  server declared <- {'tools': {'listChanged': True}, 'resources': {'listChanged': False, 'subscribe': False}, 'prompts': {'listChanged': False}}
  server info     <- {'name': 'coderift-technologies', 'version': '0.1.0'}

######################################################################
# SCENARIO 7: Controlled deploy — unreviewed PR (rule b), DECLINED
######################################################################

  [notification] notifications/tools/list_changed received — invalidating cached tool list

== deploy_to_production('billing-worker', 'staging', pull_request_id=3) — PR not Approved, expect elicitation, then decline ==

======================================================================
HUMAN CONFIRMATION REQUESTED (elicitation/create)
----------------------------------------------------------------------
Deploying 'billing-worker' PR #3 ('Hotfix: incorrect invoice rounding') to staging is risky: pull request #3 has not been through code review (status: 'Open'). Confirm you want to proceed anyway?
[scripted demo response] -> {'action': 'decline'}
======================================================================
  {
  "deployed": false,
  "deployment_created": false,
  "message": "Deploy not started \u2014 declined at confirmation step."
}

>>> Running scenario: progress_pre_deploy_checks
>>> run_pre_deploy_checks walks unit tests, integration tests, then a fresh security scan for PR 3, reporting progress after each stage instead of one blocking response.
== initialize / initialized handshake (full client) ==
  client declared -> {'elicitation': {}, 'sampling': {}}
  server declared <- {'tools': {'listChanged': True}, 'resources': {'listChanged': False, 'subscribe': False}, 'prompts': {'listChanged': False}}
  server info     <- {'name': 'coderift-technologies', 'version': '0.1.0'}

######################################################################
# SCENARIO 8: Progress Tracking (run_pre_deploy_checks, sequential stages)
######################################################################

  [notification] notifications/tools/list_changed received — invalidating cached tool list

== run_pre_deploy_checks(pull_request_id=3) ==
  [------------------------------] 0/3 (0%) — Starting unit tests
  [##########--------------------] 1/3 (33%) — Unit tests passed
  [####################----------] 2/3 (66%) — Integration tests passed
  [##############################] 3/3 (100%) — Security scan Passed

== final result ==
  {
  "pull_request_id": 3,
  "stages": [
    {
      "stage": "Unit tests",
      "result": "Passed"
    },
    {
      "stage": "Integration tests",
      "result": "Passed"
    },
    {
      "stage": "Security scan",
      "result": "Passed"
    }
  ],
  "new_security_scan_id": 6,
  "final_scan_status": "Passed"
}

>>> Running scenario: sampling_incident_summary
>>> draft_incident_summary asks the CLIENT's own model to draft a plain-language summary of the seeded critical incident via sampling/createMessage.
== initialize / initialized handshake (full client) ==
  client declared -> {'elicitation': {}, 'sampling': {}}
  server declared <- {'tools': {'listChanged': True}, 'resources': {'listChanged': False, 'subscribe': False}, 'prompts': {'listChanged': False}}
  server info     <- {'name': 'coderift-technologies', 'version': '0.1.0'}

######################################################################
# SCENARIO 9: Sampling (client's model drafts an incident summary)
######################################################################

  [notification] notifications/tools/list_changed received — invalidating cached tool list

======================================================================
SAMPLING REQUEST (sampling/createMessage) — reasoning on the CLIENT
----------------------------------------------------------------------
[offline fallback rule engine]
Invoice queries returning stale cached totals is a critical-severity incident, currently open. It followed a deployment to billing-worker/production that shipped 'Add caching layer for invoice queries' and ended in status 'Failed'. The incident remains open and needs continued attention.

[fallback: no GOOGLE_API_KEY/GEMINI_API_KEY configured — generated by a local rule engine from the structured facts above, not a live model call]
======================================================================

== tool result ==
  {
  "incident_facts": {
    "incident_id": 1,
    "title": "Invoice queries returning stale cached totals",
    "severity": "critical",
    "status": "open",
    "created_at": "2026-08-09 00:58:19",
    "resolved_at": null,
    "deployment_id": 1,
    "deployment_status": "Failed",
    "deployment_notes": "Cache invalidation bug caused stale invoice totals to be served.",
    "repository_name": "billing-worker",
    "environment_name": "production",
    "pull_request_title": "Add caching layer for invoice queries",
    "pull_request_description": "Adds an in-memory cache in front of the invoice lookup query."
  },
  "model_summary": {
    "role": "assistant",
    "content": {
      "type": "text",
      "text": "Invoice queries returning stale cached totals is a critical-severity incident, currently open. It followed a deployment to billing-worker/production that shipped 'Add caching layer for invoice queries' and ended in status 'Failed'. The incident remains open and needs continued attention.\n\n[fallback: no GOOGLE_API_KEY/GEMINI_API_KEY configured \u2014 generated by a local rule engine from the structured facts above, not a live model call]"
    }
  }
}

>>> Running scenario: merge_and_rollback
>>> merge_pull_request succeeds for an Approved+Passed PR and is refused for a still-Open one; rollback_deployment succeeds once and is refused on a second attempt against the same now-RolledBack deployment.
== initialize / initialized handshake (full client) ==
  client declared -> {'elicitation': {}, 'sampling': {}}
  server declared <- {'tools': {'listChanged': True}, 'resources': {'listChanged': False, 'subscribe': False}, 'prompts': {'listChanged': False}}
  server info     <- {'name': 'coderift-technologies', 'version': '0.1.0'}

######################################################################
# SCENARIO 10: merge_pull_request + rollback_deployment (defensive validation)
######################################################################

  [notification] notifications/tools/list_changed received — invalidating cached tool list

== merge_pull_request(pull_request_id=1) — Approved + Passed scan, should succeed ==
  {
  "pull_request_id": 1,
  "status": "Merged",
  "message": "Pull request #1 merged."
}

== merge_pull_request(pull_request_id=3) — still Open, should be refused ==
  rejected as expected: [-32004] Pull request #3 is 'Open', not Approved. Cannot merge an Open, Rejected, or already-Merged pull request.

== rollback_deployment(deployment_id=2) ==
  {
  "deployment_id": 2,
  "status": "RolledBack",
  "repository_name": "payments-service",
  "environment_name": "staging",
  "message": "Deployment #2 rolled back: Elevated error rate observed in payments-service staging after this deploy; rolling back pending investigation."
}

== rollback_deployment(deployment_id=2) again — already RolledBack, should be refused ==
  rejected as expected: [-32004] Deployment #2 is 'RolledBack'; only a Succeeded or InProgress deployment can be rolled back.

```
