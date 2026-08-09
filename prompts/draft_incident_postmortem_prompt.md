Draft an incident postmortem for incident #{{incident_id}}.

## Context

- Title: {{title}}
- Severity: {{severity}}
- Status: {{status}}
- Related deployment: {{deployment_repository}} / {{deployment_environment}}
  (status: {{deployment_status}})
- Pull request shipped by that deployment: {{pull_request_title}}
- Incident opened: {{created_at}}
- Incident resolved: {{resolved_at}}

## Instructions

Using the context above, draft a postmortem with these sections:

1. **Summary** — one or two sentences a non-engineer could understand.
2. **Timeline** — deploy, incident detection, resolution (fill in
   plausible relative timing if exact timestamps for detection aren't
   given).
3. **Root cause** — your best inference from the pull request title and
   deployment context; flag clearly if this needs the deploying
   engineer to confirm.
4. **Impact** — who/what was affected, informed by the incident's
   severity.
5. **Follow-up actions** — concrete, assignable next steps to prevent a
   recurrence.

Do not fabricate specific metrics (error rates, customer counts) that
aren't in the context above — flag them as "TBD, needs on-call input"
instead of inventing numbers.
