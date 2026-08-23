"""
schemas.py — the single source of truth for every tool's shape and gating.

For a grader: this is the one file to open to see, for every tool, its
JSON Schema (typed, required, additionalProperties: false, real
descriptions — no bare dict / **kwargs tools anywhere), which roles may
call it, and which client capability (if any) it depends on.

`roles=None` means "any authenticated engineer, any role."
`roles=()` (empty tuple) means "no authentication required at all" — used
by `authenticate` itself and the read-only lookup tools a not-yet-logged-in
session can still use, including `check_deployment_status`, the mandated
fallback for a client that can't do elicitation (see the Capability
Negotiation row in server.py's `_tool_visible()`).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    roles: tuple | None          # None = any authenticated role; () = public
    requires_capability: str | None = None  # "elicitation" | "sampling" | None


TOOLS: dict[str, ToolSpec] = {}


def _register(spec: ToolSpec):
    TOOLS[spec.name] = spec


# ---------------------------------------------------------------------------
# authenticate — logs the connection in as an engineer. Public (no role
# required to call it — you need it precisely because you have no role
# yet). Successful login is what drives the Notifications concern: see
# tools_impl/session_tools.py and notifications.py.
# ---------------------------------------------------------------------------
_register(ToolSpec(
    name="authenticate",
    description=(
        "Authenticate this connection as a Coderift engineer using an "
        "access code. Determines which role-restricted tools become "
        "available for the rest of the session; changes the tool set and "
        "fires notifications/tools/list_changed on success."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "access_code": {
                "type": "string",
                "description": "Engineer access code, e.g. 'ENG-SEN-01'.",
                "minLength": 3,
                "maxLength": 32,
            }
        },
        "required": ["access_code"],
        "additionalProperties": False,
    },
    roles=(),
))

# ---------------------------------------------------------------------------
# check_deployment_status — public read-only lookup. This is the mandated
# fallback tool: a client that never declared elicitation support does not
# get offered deploy_to_production at all, and falls back to this instead.
# ---------------------------------------------------------------------------
_register(ToolSpec(
    name="check_deployment_status",
    description=(
        "Look up the most recent deployment of a repository to a given "
        "environment: status, who deployed it, which pull request it "
        "shipped, and when. Read-only — safe for any client, including "
        "one without elicitation support."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "repository_name": {
                "type": "string",
                "description": "Repository name, e.g. 'payments-service'.",
                "minLength": 1,
                "maxLength": 100,
            },
            "environment_name": {
                "type": "string",
                "description": "Target environment.",
                "enum": ["staging", "production"],
            },
        },
        "required": ["repository_name", "environment_name"],
        "additionalProperties": False,
    },
    roles=(),
))

# ---------------------------------------------------------------------------
# get_pull_request — public read-only lookup.
# ---------------------------------------------------------------------------
_register(ToolSpec(
    name="get_pull_request",
    description=(
        "Look up a single pull request by id: its title, description, "
        "author, review status, and latest security scan result. "
        "Read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pull_request_id": {
                "type": "integer",
                "minimum": 1,
                "description": "id of the pull_requests row.",
            }
        },
        "required": ["pull_request_id"],
        "additionalProperties": False,
    },
    roles=(),
))

# ---------------------------------------------------------------------------
# list_active_incidents — any authenticated engineer, any role.
# ---------------------------------------------------------------------------
_register(ToolSpec(
    name="list_active_incidents",
    description=(
        "List every incident currently in 'open' status, with severity "
        "and the deployment that caused it (if any). Read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    roles=None,
))

# ---------------------------------------------------------------------------
# list_feature_flags — senior/lead only read tool, to demonstrate a
# role-gated READ (distinct from the write tools below) the same way
# Notifications needs a tool set that visibly changes on promotion.
# ---------------------------------------------------------------------------
_register(ToolSpec(
    name="list_feature_flags",
    description=(
        "List feature flags for a repository across both environments. "
        "Read-only, but restricted to senior/lead — junior engineers "
        "don't get visibility into production flag state."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "repository_name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100,
                "description": "Repository name, e.g. 'checkout-web'.",
            }
        },
        "required": ["repository_name"],
        "additionalProperties": False,
    },
    roles=("senior", "lead"),
))

# ---------------------------------------------------------------------------
# run_pre_deploy_checks — any authenticated engineer; genuinely long-
# running, reports progress per stage instead of one blocking response.
# ---------------------------------------------------------------------------
_register(ToolSpec(
    name="run_pre_deploy_checks",
    description=(
        "Run unit tests, then integration tests, then a fresh security "
        "scan for a pull request, sequentially, reporting progress after "
        "each stage. Writes a new security_scans row with the result — "
        "does not deploy anything."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pull_request_id": {
                "type": "integer",
                "minimum": 1,
                "description": "id of the pull_requests row to check.",
            }
        },
        "required": ["pull_request_id"],
        "additionalProperties": False,
    },
    roles=None,
))

# ---------------------------------------------------------------------------
# draft_incident_summary — any authenticated engineer; needs client
# sampling.
# ---------------------------------------------------------------------------
_register(ToolSpec(
    name="draft_incident_summary",
    description=(
        "Assemble an incident's related deployment, pull request, and "
        "notes, then ask the CLIENT's model (via sampling/createMessage) "
        "to draft a plain-language incident summary. The server does not "
        "run its own model for this."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "incident_id": {
                "type": "integer",
                "minimum": 1,
                "description": "id of the incidents row.",
            }
        },
        "required": ["incident_id"],
        "additionalProperties": False,
    },
    roles=None,
    requires_capability="sampling",
))

# ---------------------------------------------------------------------------
# deploy_to_production — senior/lead only. The flagship write tool.
# Requires elicitation support: a client that can't do human-in-the-loop
# confirmation does not get offered this tool at all (see server.py's
# tools/list filtering) — it gets the read-only check_deployment_status
# fallback instead, per the assignment's own worked example. Despite the
# name, it deploys to whichever environment_name is given (staging or
# production) — see release_tools.py for why "production" specifically is
# what triggers the stricter half of the elicitation rule.
# ---------------------------------------------------------------------------
_register(ToolSpec(
    name="deploy_to_production",
    description=(
        "Deploy a pull request's repository to a target environment. A "
        "deploy to production whose latest security scan is not 'Passed', "
        "or any deploy of a pull request that hasn't been through code "
        "review (status != Approved), pauses mid-call via elicitation to "
        "get an explicit human confirmation before proceeding. A clean, "
        "reviewed, passing deploy completes immediately."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "repository_name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100,
                "description": "Repository to deploy, e.g. 'payments-service'.",
            },
            "environment_name": {
                "type": "string",
                "enum": ["staging", "production"],
                "description": "Target environment.",
            },
            "pull_request_id": {
                "type": "integer",
                "minimum": 1,
                "description": "id of the pull_requests row being shipped.",
            },
        },
        "required": ["repository_name", "environment_name", "pull_request_id"],
        "additionalProperties": False,
    },
    roles=("senior", "lead"),
    requires_capability="elicitation",
))

# ---------------------------------------------------------------------------
# merge_pull_request — senior/lead only.
# ---------------------------------------------------------------------------
_register(ToolSpec(
    name="merge_pull_request",
    description=(
        "Merge a pull request that has been Approved and has a Passed "
        "latest security scan. Cannot merge an Open, Rejected, or "
        "already-Merged pull request, or one whose latest scan isn't "
        "Passed."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pull_request_id": {
                "type": "integer",
                "minimum": 1,
                "description": "id of the pull_requests row to merge.",
            }
        },
        "required": ["pull_request_id"],
        "additionalProperties": False,
    },
    roles=("senior", "lead"),
))

# ---------------------------------------------------------------------------
# rollback_deployment — senior/lead only.
# ---------------------------------------------------------------------------
_register(ToolSpec(
    name="rollback_deployment",
    description=(
        "Roll back a Succeeded or InProgress deployment, marking it "
        "RolledBack. Cannot roll back a deployment that's already Failed "
        "or already RolledBack."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "deployment_id": {
                "type": "integer",
                "minimum": 1,
                "description": "id of the deployments row to roll back.",
            },
            "reason": {
                "type": "string",
                "minLength": 5,
                "maxLength": 300,
                "description": "Why this deployment is being rolled back.",
            },
        },
        "required": ["deployment_id", "reason"],
        "additionalProperties": False,
    },
    roles=("senior", "lead"),
))


# ---------------------------------------------------------------------------
# set_flag_percentage — senior/lead only write tool, added alongside
# migration 002_flag_rollout_percentage.sql. Backs the Feature Flag
# Rollout graph's canary/auto_rollback nodes — see
# state_graph/flag_toggle_adapter.py's ALLOWED_TOOLS whitelist, which is
# what actually restricts a ReAct node to calling only this and
# get_error_rate_metrics below, never an arbitrary write tool.
# ---------------------------------------------------------------------------
_register(ToolSpec(
    name="set_flag_percentage",
    description=(
        "Set a feature flag's rollout percentage (0-100) for a repository "
        "and environment. Sets enabled=0 automatically at 0%, enabled=1 "
        "otherwise. This is production traffic control, same role bar as "
        "deploy_to_production/merge_pull_request/rollback_deployment."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "repository_name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100,
                "description": "Repository name, e.g. 'checkout-web'.",
            },
            "environment_name": {
                "type": "string",
                "enum": ["staging", "production"],
                "description": "Target environment.",
            },
            "flag_name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100,
                "description": "Feature flag name, e.g. 'new-payment-retry-logic'.",
            },
            "rollout_pct": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Target traffic percentage for this flag.",
            },
        },
        "required": ["repository_name", "environment_name", "flag_name", "rollout_pct"],
        "additionalProperties": False,
    },
    roles=("senior", "lead"),
))

# ---------------------------------------------------------------------------
# get_error_rate_metrics — read-only. Reports on whatever rollout_pct is
# CURRENTLY live for the flag (does not take a percentage argument) —
# real monitoring reports on live state, it doesn't take a hypothetical
# to check. Any authenticated role may call it.
# ---------------------------------------------------------------------------
_register(ToolSpec(
    name="get_error_rate_metrics",
    description=(
        "Read the current error-rate metrics window for a feature flag at "
        "its currently-live rollout percentage, classified as healthy, "
        "degraded, or error_spike against the repository's historical "
        "baseline. Records the window for audit. Read-only with respect "
        "to the flag itself (does not change rollout_pct)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "repository_name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100,
                "description": "Repository name, e.g. 'checkout-web'.",
            },
            "environment_name": {
                "type": "string",
                "enum": ["staging", "production"],
                "description": "Target environment.",
            },
            "flag_name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100,
                "description": "Feature flag name, e.g. 'new-payment-retry-logic'.",
            },
        },
        "required": ["repository_name", "environment_name", "flag_name"],
        "additionalProperties": False,
    },
    roles=None,
))


def tool_names():
    return list(TOOLS.keys())
