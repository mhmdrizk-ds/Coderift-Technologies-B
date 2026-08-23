"""
flag_tools.py — set_flag_percentage, get_error_rate_metrics.

Owner: Person C (flag-rollout branch). Added alongside migration
002_flag_rollout_percentage.sql, which gave feature_flags a real
`rollout_pct` column and added `flag_rollout_metrics` — the original
schema only had a boolean `enabled`, with no way to represent a canary
rollout at less than 100%.

These two tools are the ONLY flag-toggle surface the state_graph.flag_
rollout graph's constrained-ReAct nodes (canary, auto_rollback) are
permitted to call — see state_graph/flag_toggle_adapter.py's
ALLOWED_TOOLS whitelist. An unconstrained ReAct loop here could toggle
production traffic percentages in ways the graph never modeled (e.g.
skipping straight to 100% because the model "thought it looked healthy
enough"), so the whitelist is enforced in the adapter layer, and these
two handlers are deliberately narrow: one write (set a specific,
graph-computed percentage), one read (a simulated but real, DB-derived
metrics window) — no generic "call any tool" surface exists here.

Same handler signature and defensive-validation shape as the rest of
tools_impl/: schema validation already ran in server.py before either
handler is reached; these re-check business rules the schema can't
express (does this flag actually exist for this repo+environment).
"""

import random

from mcp_server import db
from mcp_server.protocol import JSONRPCError, ERR_NOT_FOUND, ERR_UNAUTHORIZED
from mcp_server.tools_impl import text_result


def handle_set_flag_percentage(conn, session, ctx, arguments: dict) -> dict:
    # --- Authorization: same role bar as the other write tools
    # (deploy_to_production, merge_pull_request, rollback_deployment) —
    # a flag-percentage change is production traffic control, not a
    # read. ---
    session.require_role("senior", "lead")

    repository_name = arguments["repository_name"]
    environment_name = arguments["environment_name"]
    flag_name = arguments["flag_name"]
    rollout_pct = arguments["rollout_pct"]

    repository = db.get_repository_by_name(conn, repository_name)
    if repository is None:
        raise JSONRPCError(ERR_NOT_FOUND, f"No repository '{repository_name}' found.")

    environment = db.get_environment(conn, repository["id"], environment_name)
    if environment is None:
        raise JSONRPCError(
            ERR_NOT_FOUND,
            f"Repository '{repository_name}' has no '{environment_name}' environment.",
        )

    flag = db.get_feature_flag(conn, repository["id"], environment["id"], flag_name)
    if flag is None:
        raise JSONRPCError(
            ERR_NOT_FOUND,
            f"No feature flag '{flag_name}' for '{repository_name}'/'{environment_name}'. "
            f"The schema only validated that rollout_pct is 0-100 — it does not know "
            f"whether this repository/environment actually has a flag by this name.",
        )

    conn.execute(
        "UPDATE feature_flags SET rollout_pct = ?, enabled = ? WHERE id = ?",
        (rollout_pct, 1 if rollout_pct > 0 else 0, flag["id"]),
    )
    conn.commit()

    return text_result({
        "repository_name": repository_name,
        "environment_name": environment_name,
        "flag_name": flag_name,
        "rollout_pct": rollout_pct,
        "previous_rollout_pct": flag["rollout_pct"],
        "message": f"'{flag_name}' set to {rollout_pct}% for '{repository_name}'/'{environment_name}'.",
    })


def handle_get_error_rate_metrics(conn, session, ctx, arguments: dict) -> dict:
    """Read-only. Simulates an external monitoring window reporting back
    on the flag's CURRENT rollout_pct (not a percentage the caller
    supplies) — a real monitoring integration reports on whatever
    percentage is actually live right now, so this tool takes no
    rollout_pct argument, only which flag to check. This is what backs
    the flag_rollout graph's `awaiting_metrics` WAIT_KEY node: a caller
    polls this after setting a percentage via set_flag_percentage, and it
    genuinely varies run to run (see the random jitter below) rather than
    being a deterministic echo, so the graph's WAIT_KEY/resume path is
    exercised for real rather than short-circuited by a canned reply.
    """
    repository_name = arguments["repository_name"]
    environment_name = arguments["environment_name"]
    flag_name = arguments["flag_name"]

    repository = db.get_repository_by_name(conn, repository_name)
    if repository is None:
        raise JSONRPCError(ERR_NOT_FOUND, f"No repository '{repository_name}' found.")

    environment = db.get_environment(conn, repository["id"], environment_name)
    if environment is None:
        raise JSONRPCError(
            ERR_NOT_FOUND,
            f"Repository '{repository_name}' has no '{environment_name}' environment.",
        )

    flag = db.get_feature_flag(conn, repository["id"], environment["id"], flag_name)
    if flag is None:
        raise JSONRPCError(
            ERR_NOT_FOUND,
            f"No feature flag '{flag_name}' for '{repository_name}'/'{environment_name}'.",
        )

    baseline = db.get_historical_baseline_error_rate(conn, repository["id"])

    # Simulated observed error rate: baseline plus jitter that scales with
    # how much traffic this rollout_pct actually exposes — a 5% canary
    # cannot plausibly show the same absolute error-rate swing as a 100%
    # rollout, so the jitter is scaled by rollout_pct/100 rather than
    # being a flat random draw independent of exposure.
    exposure = flag["rollout_pct"] / 100.0
    jitter = random.uniform(-0.3, 1.2) * baseline * exposure
    observed = max(0.0, round(baseline + jitter, 4))

    if observed >= baseline * 3:
        result = "error_spike"
    elif observed >= baseline * 1.5:
        result = "degraded"
    else:
        result = "healthy"

    conn.execute(
        "INSERT INTO flag_rollout_metrics "
        "(feature_flag_id, rollout_pct, error_rate, baseline_error_rate, result) "
        "VALUES (?, ?, ?, ?, ?)",
        (flag["id"], flag["rollout_pct"], observed, baseline, result),
    )
    conn.commit()

    return text_result({
        "repository_name": repository_name,
        "environment_name": environment_name,
        "flag_name": flag_name,
        "rollout_pct": flag["rollout_pct"],
        "error_rate": observed,
        "baseline_error_rate": baseline,
        "result": result,
    })
