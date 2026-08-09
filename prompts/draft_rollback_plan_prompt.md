Draft a rollback plan for deployment #{{deployment_id}}.

## Context

- Repository: {{repository_name}}
- Environment: {{environment_name}}
- Deployment status: {{deployment_status}}
- Pull request shipped: {{pull_request_title}}
- Deployed by: {{deployed_by_name}}
- Deployed at: {{created_at}}

## Instructions

Using the context above, write a short rollback plan that covers:

1. **Pre-checks** — what to confirm before rolling back (is another
   deployment currently in progress for this environment, are there
   dependent services that also need to roll back together).
2. **Rollback steps** — the actual `rollback_deployment` call and any
   manual follow-up (e.g. re-opening the pull request, notifying the
   owning team).
3. **Verification** — how to confirm the rollback actually restored the
   previous good state.
4. **Communication** — who to notify (see the Production Deployment
   Policy's escalation contacts) and what to tell them.

Keep it concrete and specific to this deployment, not a generic template.
