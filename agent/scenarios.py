from agent.mcp_client import ServerError


def _header(title):
    print("\n" + "#" * 70)
    print(f"# {title}")
    print("#" * 70)


# ---------------------------------------------------------------------------
# 1. Capability negotiation (WITH elicitation+sampling) + Resources + Prompts
# ---------------------------------------------------------------------------
def scenario_capability_negotiation_full(session, data):
    _header("SCENARIO 1: Capability Negotiation (full client) + Resources + Prompts")

    assert session.server_supports("tools.listChanged"), (
        "Server did not declare tools.listChanged — the agent should not "
        "rely on notifications/tools/list_changed if this is false."
    )
    print(
        "\n  Confirmed: server declared tools.listChanged=true, so it's "
        "safe to rely on notifications/tools/list_changed later."
    )

    print("\n== tools/list (anonymous, pre-authentication) ==")
    tools = session.tools_list()
    names = [t["name"] for t in tools]
    print(" ", names)
    assert "deploy_to_production" not in names, "write tool should be hidden pre-auth"
    assert "check_deployment_status" in names, "public read tool should always be visible"

    print("\n== resources/list ==")
    res = session.list_resources()
    for r in res["resources"]:
        print(f"  - {r['uri']}: {r['name']}")

    print("\n== resources/read policy://production-deployment ==")
    content = session.read_resource("policy://production-deployment")["contents"][0]["text"]
    print(" ", content.splitlines()[0], "...")

    print("\n== prompts/list ==")
    prompts = session.list_prompts()
    for p in prompts["prompts"]:
        print(f"  - {p['name']}: {p['title']}")

    print(f"\n== prompts/get draft_rollback_plan (deployment_id={data['deployment_id']}) ==")
    prompt = session.get_prompt("draft_rollback_plan", {"deployment_id": data["deployment_id"]})
    text = prompt["messages"][0]["content"]["text"]
    print(" ", text.splitlines()[0])
    for line in text.splitlines():
        if line.startswith("- Repository") or line.startswith("- Deployment status"):
            print(" ", line)


# ---------------------------------------------------------------------------
# 2. Capability negotiation (WITHOUT elicitation/sampling) — the mandated
#    fallback path.
# ---------------------------------------------------------------------------
def scenario_capability_negotiation_read_only(session, data):
    _header("SCENARIO 2: Capability Negotiation (client WITHOUT elicitation/sampling)")

    print("\n== tools/list (anonymous, pre-authentication, read-only client) ==")
    before = [t["name"] for t in session.tools_list()]
    print(" ", before)

    print(f"\n== authenticate(access_code={data['senior_access_code']!r}) — a senior engineer ==")
    session.authenticate(data["senior_access_code"])

    print("\n== tools/list AFTER authenticating as senior, still on the read-only client ==")
    after = [t["name"] for t in session.tools_list(force=True)]
    print(" ", after)

    assert "deploy_to_production" not in after, (
        "deploy_to_production must stay hidden from a client that never "
        "declared elicitation support, even for a senior engineer"
    )
    assert "draft_incident_summary" not in after, (
        "draft_incident_summary must stay hidden — it requires sampling"
    )
    assert "check_deployment_status" in after, "the mandated read-only fallback must still be offered"
    assert "merge_pull_request" in after, (
        "merge_pull_request has no capability requirement, only a role "
        "requirement, so it SHOULD still appear for a senior engineer"
    )
    print(
        "\n  Confirmed: deploy_to_production and draft_incident_summary stayed hidden "
        "(no elicitation/sampling declared); merge_pull_request and rollback_deployment "
        "still appear (role-gated only, no capability requirement)."
    )

    print(f"\n== fallback in action: check_deployment_status({data['fallback_repository_name']!r}, "
          f"{data['fallback_environment_name']!r}) ==")
    result = session.call_tool(
        "check_deployment_status",
        {"repository_name": data["fallback_repository_name"], "environment_name": data["fallback_environment_name"]},
    )
    print(" ", session.result_text(result))

    print(
        f"\n== calling deploy_to_production anyway: {data['repository_name']!r} -> "
        f"{data['environment_name']!r}, PR #{data['deploy_pull_request_id']} (a case that would "
        "need elicitation) — not offered, but still routed to the handler =="
    )
    try:
        session.call_tool(
            "deploy_to_production",
            {
                "repository_name": data["repository_name"],
                "environment_name": data["environment_name"],
                "pull_request_id": data["deploy_pull_request_id"],
            },
        )
        raise AssertionError("expected ERR_CAPABILITY_UNSUPPORTED, got a result")
    except ServerError as exc:
        print(f"  rejected cleanly as expected: {exc}")
        assert exc.code == -32005, f"expected ERR_CAPABILITY_UNSUPPORTED (-32005), got {exc.code}"


# ---------------------------------------------------------------------------
# 3. Defensive tool design + Authorization
# ---------------------------------------------------------------------------
def scenario_defensive_and_authorization(session, data):
    _header("SCENARIO 3: Defensive Tool Design + Authorization")

    print("\n== missing required field ==")
    try:
        session.call_tool(data["bad_call_missing_field"]["tool"], data["bad_call_missing_field"]["arguments"])
        raise AssertionError("expected a validation error, got a result")
    except ServerError as exc:
        print(f"  rejected as expected: {exc}")

    print("\n== unknown/extra field (additionalProperties: false) ==")
    try:
        session.call_tool(data["bad_call_extra_field"]["tool"], data["bad_call_extra_field"]["arguments"])
        raise AssertionError("expected a validation error, got a result")
    except ServerError as exc:
        print(f"  rejected as expected: {exc}")

    print("\n== unauthenticated write attempt ==")
    try:
        session.call_tool(data["unauthenticated_write"]["tool"], data["unauthenticated_write"]["arguments"])
        raise AssertionError("expected an authentication error, got a result")
    except ServerError as exc:
        print(f"  rejected as expected: {exc}")

    print(f"\n== authenticate as junior (access_code={data['junior_access_code']!r}) ==")
    session.authenticate(data["junior_access_code"])

    print("\n== junior engineer attempts deploy_to_production directly (not shown in tools/list, "
          "but the handler must still refuse it) ==")
    try:
        session.call_tool(
            "deploy_to_production",
            {
                "repository_name": data["repository_name"],
                "environment_name": data["environment_name"],
                "pull_request_id": data["deploy_pull_request_id"],
            },
        )
        raise AssertionError("expected an authorization error, got a result")
    except ServerError as exc:
        print(f"  rejected as expected: {exc}")

    print("\n== inactive engineer's access code is refused outright ==")
    try:
        session.authenticate(data["inactive_access_code"])
        raise AssertionError("expected an error, got a successful login")
    except ServerError as exc:
        print(f"  rejected as expected: {exc}")


# ---------------------------------------------------------------------------
# 4. Notifications — role promotion mid-session, no reconnect
# ---------------------------------------------------------------------------
def scenario_notifications_on_promotion(session, data):
    _header("SCENARIO 4: Notifications (role promotion -> tools/list_changed, twice)")

    print("\n== tools/list before authentication ==")
    anonymous = [t["name"] for t in session.tools_list()]
    print(" ", anonymous)

    print(f"\n== authenticate(access_code={data['junior_access_code']!r}) — junior ==")
    session.authenticate(data["junior_access_code"])
    after_junior = [t["name"] for t in session.tools_list(force=True)]
    print(" ", after_junior)
    newly_visible_junior = sorted(set(after_junior) - set(anonymous))
    print(f"  newly visible after junior login: {newly_visible_junior}")
    assert "run_pre_deploy_checks" in newly_visible_junior
    assert "deploy_to_production" not in after_junior, "still senior/lead-only"

    print(f"\n== re-authenticate on the SAME connection, no reconnect "
          f"(access_code={data['senior_access_code']!r}) — senior ==")
    session.authenticate(data["senior_access_code"])
    after_senior = [t["name"] for t in session.tools_list(force=True)]
    print(" ", after_senior)
    newly_visible_senior = sorted(set(after_senior) - set(after_junior))
    print(f"  newly visible after senior promotion: {newly_visible_senior}")
    assert "deploy_to_production" in newly_visible_senior
    assert "merge_pull_request" in newly_visible_senior
    assert "list_feature_flags" in newly_visible_senior


# ---------------------------------------------------------------------------
# 5. Uncontrolled deploy: reviewed, passing scan, staging -> no elicitation
# ---------------------------------------------------------------------------
def scenario_uncontrolled_deploy(session, data):
    _header("SCENARIO 5: Uncontrolled deploy (Approved, Passed scan, staging) — no elicitation")
    session.authenticate(data["senior_access_code"])
    result = session.call_tool(
        "deploy_to_production",
        {
            "repository_name": data["repository_name"],
            "environment_name": data["environment_name"],
            "pull_request_id": data["pull_request_id"],
        },
    )
    print(" ", session.result_text(result))


# ---------------------------------------------------------------------------
# 6. Controlled deploy: production + scan not Passed -> elicitation, accepted
# ---------------------------------------------------------------------------
def scenario_controlled_deploy_scan_not_passed(session, data):
    _header("SCENARIO 6: Controlled deploy — production + scan not Passed (rule a), ACCEPTED")
    session.authenticate(data["senior_access_code"])
    print(
        f"\n== deploy_to_production({data['repository_name']!r}, {data['environment_name']!r}, "
        f"pull_request_id={data['pull_request_id']}) — expect elicitation/create mid-call =="
    )
    result = session.call_tool(
        "deploy_to_production",
        {
            "repository_name": data["repository_name"],
            "environment_name": data["environment_name"],
            "pull_request_id": data["pull_request_id"],
        },
    )
    print(" ", session.result_text(result))


# ---------------------------------------------------------------------------
# 7. Controlled deploy: PR not reviewed -> elicitation, DECLINED this time
# ---------------------------------------------------------------------------
def scenario_controlled_deploy_unreviewed_declined(session, data):
    _header("SCENARIO 7: Controlled deploy — unreviewed PR (rule b), DECLINED")
    session.authenticate(data["lead_access_code"])
    print(
        f"\n== deploy_to_production({data['repository_name']!r}, {data['environment_name']!r}, "
        f"pull_request_id={data['pull_request_id']}) — PR not Approved, expect elicitation, "
        "then decline =="
    )
    result = session.call_tool(
        "deploy_to_production",
        {
            "repository_name": data["repository_name"],
            "environment_name": data["environment_name"],
            "pull_request_id": data["pull_request_id"],
        },
    )
    print(" ", session.result_text(result))


# ---------------------------------------------------------------------------
# 8. Progress tracking — run_pre_deploy_checks
# ---------------------------------------------------------------------------
def scenario_progress_pre_deploy_checks(session, data):
    _header("SCENARIO 8: Progress Tracking (run_pre_deploy_checks, sequential stages)")
    session.authenticate(data["junior_access_code"])
    token = f"predeploy-pr-{data['pull_request_id']}"
    print(f"\n== run_pre_deploy_checks(pull_request_id={data['pull_request_id']}) ==")
    result = session.call_tool(
        "run_pre_deploy_checks",
        {"pull_request_id": data["pull_request_id"]},
        progress_token=token,
    )
    print("\n== final result ==")
    print(" ", session.result_text(result))


# ---------------------------------------------------------------------------
# 9. Sampling — draft_incident_summary
# ---------------------------------------------------------------------------
def scenario_sampling_incident_summary(session, data):
    _header("SCENARIO 9: Sampling (client's model drafts an incident summary)")
    session.authenticate(data["lead_access_code"])
    result = session.call_tool("draft_incident_summary", {"incident_id": data["incident_id"]})
    print("\n== tool result ==")
    print(" ", session.result_text(result))


# ---------------------------------------------------------------------------
# 10. merge_pull_request + rollback_deployment — write-tool sanity, a second
#     flavor of Defensive Tool Design distinct from deploy_to_production.
# ---------------------------------------------------------------------------
def scenario_merge_and_rollback(session, data):
    _header("SCENARIO 10: merge_pull_request + rollback_deployment (defensive validation)")
    session.authenticate(data["senior_access_code"])

    print(f"\n== merge_pull_request(pull_request_id={data['mergeable_pull_request_id']}) — "
          "Approved + Passed scan, should succeed ==")
    result = session.call_tool("merge_pull_request", {"pull_request_id": data["mergeable_pull_request_id"]})
    print(" ", session.result_text(result))

    print(f"\n== merge_pull_request(pull_request_id={data['unreviewed_pull_request_id']}) — "
          "still Open, should be refused ==")
    try:
        session.call_tool("merge_pull_request", {"pull_request_id": data["unreviewed_pull_request_id"]})
        raise AssertionError("expected a conflict error, got a result")
    except ServerError as exc:
        print(f"  rejected as expected: {exc}")

    print(f"\n== rollback_deployment(deployment_id={data['rollback_deployment_id']}) ==")
    result = session.call_tool(
        "rollback_deployment",
        {"deployment_id": data["rollback_deployment_id"], "reason": data["rollback_reason"]},
    )
    print(" ", session.result_text(result))

    print(f"\n== rollback_deployment(deployment_id={data['rollback_deployment_id']}) again — "
          "already RolledBack, should be refused ==")
    try:
        session.call_tool(
            "rollback_deployment",
            {"deployment_id": data["rollback_deployment_id"], "reason": "duplicate attempt"},
        )
        raise AssertionError("expected a conflict error, got a result")
    except ServerError as exc:
        print(f"  rejected as expected: {exc}")


SCENARIOS = {
    "capability_negotiation_full": scenario_capability_negotiation_full,
    "capability_negotiation_read_only": scenario_capability_negotiation_read_only,
    "defensive_and_authorization": scenario_defensive_and_authorization,
    "notifications_on_promotion": scenario_notifications_on_promotion,
    "uncontrolled_deploy": scenario_uncontrolled_deploy,
    "controlled_deploy_scan_not_passed": scenario_controlled_deploy_scan_not_passed,
    "controlled_deploy_unreviewed_declined": scenario_controlled_deploy_unreviewed_declined,
    "progress_pre_deploy_checks": scenario_progress_pre_deploy_checks,
    "sampling_incident_summary": scenario_sampling_incident_summary,
    "merge_and_rollback": scenario_merge_and_rollback,
}

# Read-only-client scenarios use capability_profile="read_only"; every other
# scenario uses the default "full" client. client.py consults this to build
# the right session for each scenario.
READ_ONLY_SCENARIOS = {"capability_negotiation_read_only"}

# Fixed run order for `--all` — capability negotiation and read-only paths
# first, then defensive/notifications groundwork, then the three deploy
# outcomes (uncontrolled / elicit+accept / elicit+decline), then the
# remaining concerns.
SCENARIO_ORDER = [
    "capability_negotiation_full",
    "capability_negotiation_read_only",
    "defensive_and_authorization",
    "notifications_on_promotion",
    "uncontrolled_deploy",
    "controlled_deploy_scan_not_passed",
    "controlled_deploy_unreviewed_declined",
    "progress_pre_deploy_checks",
    "sampling_incident_summary",
    "merge_and_rollback",
]
