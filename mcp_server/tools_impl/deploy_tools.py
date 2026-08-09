"""
deploy_tools.py — deploy_to_production.

This is where Defensive Tool Design, Authorization, and Elicitation all
meet on one handler:

  * Defensive validation independent of the schema: does the repository
    exist, does the named environment actually belong to it (a schema
    can't know that — it only knows environment_name is one of two
    strings), does the pull request belong to this repository, is a
    deployment already Pending/InProgress for this repo+environment.
  * Authorization in the handler, not just the schema/roles tuple: we
    re-fetch the engineer row by session.engineer_id and re-check
    role/active status against the database, rather than trusting that
    session.role hasn't drifted since login.
  * Elicitation: the exact business rule from the Production Deployment
    Policy (see resources/production_deployment_policy.md) — pause for a
    human confirmation when either the production+scan-not-Passed
    condition or the PR-not-Approved condition holds; skip it entirely
    for a clean, reviewed, passing deploy.
"""

from mcp_server import db
from mcp_server.protocol import JSONRPCError, ERR_NOT_FOUND, ERR_CONFLICT, ERR_UNAUTHORIZED
from mcp_server.tools_impl import text_result


def handle_deploy_to_production(conn, session, ctx, arguments: dict) -> dict:
    # --- Authorization (handler-level, not just tools/list filtering or
    # the schema's roles tuple). Re-fetch the engineer row fresh from the
    # database rather than trusting the in-memory session hasn't drifted
    # — "the schema says engineer_id is an integer" is not authorization,
    # and neither is "the session object still says senior" on its own. ---
    session.require_role("senior", "lead")
    engineer = db.get_engineer_by_id(conn, session.engineer_id)
    if engineer is None or not engineer["active"] or engineer["role"] not in ("senior", "lead"):
        raise JSONRPCError(
            ERR_UNAUTHORIZED,
            "Deploying engineer's database record no longer authorizes this action "
            "(inactive or role changed since login).",
        )

    repository_name = arguments["repository_name"]
    environment_name = arguments["environment_name"]
    pull_request_id = arguments["pull_request_id"]

    # --- Defensive validation: independent of what the schema already
    # checked (types/enum membership). These are business rules that only
    # the database can answer. ---
    repository = db.get_repository_by_name(conn, repository_name)
    if repository is None:
        raise JSONRPCError(ERR_NOT_FOUND, f"No repository '{repository_name}' found.")

    environment = db.get_environment(conn, repository["id"], environment_name)
    if environment is None:
        raise JSONRPCError(
            ERR_NOT_FOUND,
            f"Repository '{repository_name}' has no '{environment_name}' environment. "
            f"The schema only validated that '{environment_name}' is a legal environment "
            f"NAME — it does not know whether this repository actually has one.",
        )

    pull_request = db.get_pull_request(conn, pull_request_id)
    if pull_request is None:
        raise JSONRPCError(ERR_NOT_FOUND, f"No pull request #{pull_request_id} found.")
    if pull_request["repository_id"] != repository["id"]:
        raise JSONRPCError(
            ERR_CONFLICT,
            f"Pull request #{pull_request_id} belongs to repository "
            f"'{pull_request['repository_name']}', not '{repository_name}'.",
        )

    in_flight = db.get_in_flight_deployment(conn, repository["id"], environment["id"])
    if in_flight is not None:
        raise JSONRPCError(
            ERR_CONFLICT,
            f"A deployment (#{in_flight['id']}, status {in_flight['status']}) is already "
            f"in progress for '{repository_name}'/'{environment_name}'. Wait for it to "
            f"finish before starting another.",
        )

    scan = db.get_latest_security_scan(conn, pull_request_id)
    scan_status = scan["status"] if scan else "Pending"

    # --- Elicitation: the exact business rule from the Production
    # Deployment Policy resource. ---
    needs_confirmation = (
        (environment_name == "production" and scan_status != "Passed")
        or (pull_request["status"] != "Approved")
    )

    confirmation_note = None
    if needs_confirmation:
        reasons = []
        if environment_name == "production" and scan_status != "Passed":
            reasons.append(f"target is production and the latest security scan is '{scan_status}', not Passed")
        if pull_request["status"] != "Approved":
            reasons.append(f"pull request #{pull_request_id} has not been through code review (status: '{pull_request['status']}')")

        answer = ctx.elicit(
            message=(
                f"Deploying '{repository_name}' PR #{pull_request_id} "
                f"('{pull_request['title']}') to {environment_name} is risky: "
                f"{'; '.join(reasons)}. Confirm you want to proceed anyway?"
            ),
            requested_schema={
                "type": "object",
                "properties": {
                    "confirm": {
                        "type": "boolean",
                        "description": "true to proceed with the deploy despite the risk, false to abort.",
                    }
                },
                "required": ["confirm"],
            },
        )

        accepted = answer.get("action") == "accept" and answer.get("content", {}).get("confirm") is True
        if not accepted:
            return text_result({
                "deployed": False,
                "deployment_created": False,
                "message": "Deploy not started — declined at confirmation step.",
            })
        confirmation_note = f"Human-confirmed override: {'; '.join(reasons)}."

    # --- Everything checked out (or was explicitly confirmed): create the
    # deployment. This demo server simulates it completing immediately. ---
    notes = confirmation_note or "Routine deploy: reviewed, passing scan."
    cur = conn.execute(
        """
        INSERT INTO deployments (repository_id, environment_id, deployed_by,
                                  pull_request_id, status, notes)
        VALUES (?, ?, ?, ?, 'Succeeded', ?)
        """,
        (repository["id"], environment["id"], session.engineer_id, pull_request_id, notes),
    )
    conn.commit()

    return text_result({
        "deployed": True,
        "deployment_created": True,
        "deployment_id": cur.lastrowid,
        "status": "Succeeded",
        "elicitation_required": needs_confirmation,
        "message": f"'{repository_name}' PR #{pull_request_id} deployed to {environment_name}.",
    })
