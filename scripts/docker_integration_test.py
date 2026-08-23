"""
scripts/docker_integration_test.py — final Docker integration test
(Final Project — Person C)

This does NOT start containers itself. Run `docker-compose up --build`
first (in another terminal, or backgrounded), wait for all services to
report healthy, then run this script against the already-running stack:

    docker-compose up --build -d
    python scripts/docker_integration_test.py
    docker-compose down

Everything below talks to the containers only over their exposed HTTP
ports (mcp_server:8000, admin_platform:8001, user_platform:8010) or via
`docker-compose exec` for the one check that specifically needs to run
*inside* a container's process/environment (the env-var-passthrough
regression check). Nothing here imports state_graph/, mcp_server/, or
planning_toolkit/ modules directly — that would test this host's Python
environment, not the container's, which defeats the point of an
integration test.

Each check prints PASS/FAIL with what it actually did. The script exits
non-zero if any check fails.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from typing import Any

import urllib.error
import urllib.request

MCP_BASE = "http://localhost:8000"
ADMIN_BASE = "http://localhost:8001"
USER_BASE = "http://localhost:8010"

TIMEOUT = 15  # seconds per HTTP call — containers on a loaded CI box can be slow to respond


class CheckFailure(Exception):
    pass


results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str) -> None:
    results.append((name, passed, detail))
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {name}")
    print(f"       {detail}")


def http_json(method: str, url: str, body: dict | list | None = None, timeout: int = TIMEOUT) -> tuple[int, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw.decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise CheckFailure(f"could not reach {url}: {exc}") from exc


def run_check(name: str, fn) -> None:
    try:
        detail = fn()
        record(name, True, detail)
    except CheckFailure as exc:
        record(name, False, str(exc))
    except Exception as exc:  # noqa: BLE001 — we want every failure, not just ours
        record(name, False, f"unexpected {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# 1. MCP server reachable over real HTTP transport: /health, then a real
#    initialize + tools/list JSON-RPC round trip.
# ---------------------------------------------------------------------------

def check_mcp_health_and_protocol() -> str:
    status, body = http_json("GET", f"{MCP_BASE}/health")
    if status != 200:
        raise CheckFailure(f"/health returned HTTP {status}: {body}")
    if not isinstance(body, dict) or body.get("status") != "ok":
        raise CheckFailure(f"/health body unexpected: {body}")

    status, body = http_json(
        "POST",
        f"{MCP_BASE}/mcp",
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "docker-integration-test"},
                },
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ],
    )
    if status != 200:
        raise CheckFailure(f"/mcp batch returned HTTP {status}: {body}")
    if not isinstance(body, list) or len(body) != 2:
        raise CheckFailure(f"expected a 2-element JSON-RPC batch response, got: {body}")

    init_result = body[0].get("result", {})
    if init_result.get("serverInfo", {}).get("name") != "coderift-technologies":
        raise CheckFailure(f"unexpected initialize result: {init_result}")

    tools_result = body[1].get("result", {})
    tool_names = {t["name"] for t in tools_result.get("tools", [])}
    # roles=() tools (mcp_server/schemas.py) — visible with no authentication at all.
    unauthenticated_expected = {"authenticate", "check_deployment_status", "get_pull_request"}
    missing = unauthenticated_expected - tool_names
    if missing:
        raise CheckFailure(
            f"unauthenticated tools/list missing expected tools {missing}; got {tool_names}"
        )
    return (
        f"/health OK; initialize + tools/list round-tripped over real HTTP; "
        f"unauthenticated tool set = {sorted(tool_names)}"
    )


# ---------------------------------------------------------------------------
# 2. User platform reachable, /agents lists all five live agents.
# ---------------------------------------------------------------------------

def check_user_platform_agents() -> str:
    status, body = http_json("GET", f"{USER_BASE}/agents")
    if status != 200:
        raise CheckFailure(f"/agents returned HTTP {status}: {body}")
    agents = body.get("agents", []) if isinstance(body, dict) else []
    agent_ids = {a["agent_id"] for a in agents}
    expected = {"incident_response", "security_remediation", "flag_rollout", "memory_rag", "planning"}
    missing = expected - agent_ids
    if missing:
        raise CheckFailure(f"/agents is missing {missing}; got {agent_ids}")
    unavailable = [a["agent_id"] for a in agents if not a.get("available")]
    if unavailable:
        raise CheckFailure(f"these agents are registered but marked unavailable: {unavailable}")
    return f"/agents lists all five agents, all marked available: {sorted(agent_ids)}"


# ---------------------------------------------------------------------------
# 3. Drive flag_rollout through a real run over the platform's HTTP API:
#    start -> awaiting_metrics cycle -> HITL gate at blast-radius
#    threshold -> resume with approval -> completed. Entirely over HTTP.
#
#    KNOWN, DOCUMENTED LIMITATION (see README's mcp_server audit /
#    flag_rollout section): user_platform/backend.py builds this graph
#    via make_flag_rollout_graph(checkpointer=..., hitl_store=...,
#    ticket_store=...) with no explicit `mcp=`, so
#    FlagToggleAdapter() defaults to an in-memory SimulatedFlagToggleClient
#    rather than a client that actually calls mcp_server's
#    set_flag_percentage / get_error_rate_metrics tools. That means this
#    check proves the *graph logic* (HITL gating, checkpointing, resume)
#    is correctly wired end-to-end through the live HTTP platform, but it
#    does NOT prove flag state changes reach the real feature_flags table
#    or the real MCP server — because currently, nothing does. That gap
#    is flagged as a FAIL of its own sub-check below rather than silently
#    passed over.
# ---------------------------------------------------------------------------

def check_flag_rollout_over_http() -> str:
    run_id = f"docker-it-{uuid.uuid4()}"
    steps: list[str] = []

    status, body = http_json(
        "POST",
        f"{USER_BASE}/graph/start",
        {
            "agent_id": "flag_rollout",
            "run_id": run_id,
            "initial_state": {
                "repo": "billing-worker",
                "environment": "production",
                "flag_name": "docker-integration-test-flag",
                "rollout_sequence": [10, 30, 60, 100],  # crosses the 50% blast-radius threshold at step 3
            },
        },
    )
    if status != 200:
        raise CheckFailure(f"/graph/start returned HTTP {status}: {body}")
    if body.get("status") != "waiting" or body.get("node") != "awaiting_metrics":
        raise CheckFailure(f"expected a 'waiting'/'awaiting_metrics' start result, got: {body}")
    steps.append(f"start -> {body['status']}/{body['node']}")

    # Drive one awaiting_metrics cycle: 10% -> 30%.
    status, body = http_json(
        "POST",
        f"{USER_BASE}/graph/resume",
        {"agent_id": "flag_rollout", "run_id": run_id, "external_event": {"metrics_result": "healthy"}},
    )
    if status != 200:
        raise CheckFailure(f"first /graph/resume (10%->30%) returned HTTP {status}: {body}")
    steps.append(f"resume 1 (10%->30%) -> {body.get('status')}/{body.get('node')}")

    # Next step (30% -> 60%) crosses the 50% blast-radius threshold, so
    # this resume should land us on the HITL gate, not straight through.
    status, body = http_json(
        "POST",
        f"{USER_BASE}/graph/resume",
        {"agent_id": "flag_rollout", "run_id": run_id, "external_event": {"metrics_result": "healthy"}},
    )
    if status != 200:
        raise CheckFailure(f"second /graph/resume (30%->60%, crosses threshold) returned HTTP {status}: {body}")
    if body.get("status") != "paused_hitl":
        raise CheckFailure(
            f"expected the 30%->60% step (crosses 50% blast-radius threshold) to pause for HITL, "
            f"got: {body}"
        )
    steps.append(f"resume 2 (30%->60%, crosses threshold) -> paused_hitl at {body.get('node')}")

    # Confirm the pause is genuinely visible through the platform's own
    # HITL-pending listing, not just returned once and forgotten.
    status, body = http_json("GET", f"{USER_BASE}/hitl/pending?graph_name=flag_rollout")
    if status != 200:
        raise CheckFailure(f"/hitl/pending returned HTTP {status}: {body}")
    pending_for_run = [t for t in body.get("pending", []) if t["run_id"] == run_id]
    if not pending_for_run:
        raise CheckFailure(f"run {run_id} paused for HITL but is not visible in /hitl/pending")
    steps.append(f"confirmed visible in /hitl/pending: task id {pending_for_run[0]['id']}")

    # Resume with an approval decision — this canaries at 60% and waits
    # for a fresh metrics signal there (it does NOT skip straight to 100%
    # — see tests/test_flag_rollout.py's own assertion of this).
    status, body = http_json(
        "POST",
        f"{USER_BASE}/graph/resume",
        {
            "agent_id": "flag_rollout",
            "run_id": run_id,
            "hitl_decision": {"approved": True, "decided_by": "docker-integration-test", "note": "approved"},
        },
    )
    if status != 200:
        raise CheckFailure(f"/graph/resume with hitl_decision returned HTTP {status}: {body}")
    if body.get("status") != "waiting" or body.get("node") != "awaiting_metrics":
        raise CheckFailure(f"expected the run to resume to 'waiting'/'awaiting_metrics' (canaried at 60%) after approval, got: {body}")
    steps.append(f"resume with approval -> {body.get('status')}/{body.get('node')} (canaried at 60%)")

    # 60% -> 100% is ALSO at/above the blast-radius threshold, so every
    # step at or above threshold gates HITL again, not just the first
    # crossing (this is deliberate per the graph's own test suite) — so
    # this resume lands back on the HITL gate for the 100% step, not
    # 'completed' yet.
    status, body = http_json(
        "POST",
        f"{USER_BASE}/graph/resume",
        {"agent_id": "flag_rollout", "run_id": run_id, "external_event": {"metrics_result": "healthy"}},
    )
    if status != 200:
        raise CheckFailure(f"/graph/resume (60%->100%, also crosses threshold) returned HTTP {status}: {body}")
    if body.get("status") != "paused_hitl":
        raise CheckFailure(
            f"expected the 60%->100% step to ALSO gate HITL (every at/above-threshold "
            f"step needs its own sign-off), got: {body}"
        )
    steps.append(f"resume (60%->100%, also crosses threshold) -> paused_hitl at {body.get('node')}")

    # Approve the final step.
    status, body = http_json(
        "POST",
        f"{USER_BASE}/graph/resume",
        {
            "agent_id": "flag_rollout",
            "run_id": run_id,
            "hitl_decision": {"approved": True, "decided_by": "docker-integration-test", "note": "approved final step"},
        },
    )
    if status != 200:
        raise CheckFailure(f"final approval /graph/resume returned HTTP {status}: {body}")
    if body.get("status") != "waiting":
        raise CheckFailure(f"expected 'waiting' after final approval (still awaiting the 100% metrics signal), got: {body}")
    steps.append(f"final approval -> {body.get('status')}/{body.get('node')}")

    # Final metrics signal at 100% completes the run.
    status, body = http_json(
        "POST",
        f"{USER_BASE}/graph/resume",
        {"agent_id": "flag_rollout", "run_id": run_id, "external_event": {"metrics_result": "healthy"}},
    )
    if status != 200:
        raise CheckFailure(f"final /graph/resume (100% metrics signal) returned HTTP {status}: {body}")
    if body.get("status") != "completed":
        raise CheckFailure(f"expected the run to complete after the final metrics signal, got: {body}")
    if body.get("state", {}).get("current_rollout_pct") != 100:
        raise CheckFailure(f"run completed but current_rollout_pct != 100: {body.get('state')}")
    steps.append(f"final resume -> completed at {body['state']['current_rollout_pct']}%")

    return (
        "Full flag_rollout run driven entirely over HTTP against the running "
        "platform container: " + "; ".join(steps) + ". NOTE: this proves the "
        "graph's HITL gating, checkpointing, and resume logic are correctly "
        "wired through the live HTTP platform — it does NOT prove flag "
        "changes reach the real MCP server or feature_flags table (see "
        "check_flag_rollout_actually_calls_real_mcp_server below, which "
        "checks that specifically and is expected to currently FAIL)."
    )


def check_flag_rollout_actually_calls_real_mcp_server() -> str:
    """Separate, deliberately strict check: did the run above actually
    change the real `feature_flags` row for docker-integration-test-flag,
    the way a genuine MCP-backed rollout would? This is checked via the
    admin platform's DB-backed view rather than importing sqlite3 against
    the container's volume directly, since the whole point is staying on
    the HTTP side of the boundary.

    As of this audit, user_platform/backend.py builds flag_rollout's
    graph with no explicit `mcp=`, so FlagToggleAdapter() defaults to an
    in-memory SimulatedFlagToggleClient — meaning this check is expected
    to FAIL until someone wires a real MCP-backed client through
    user_platform (mirroring how make_incident_response_graph's default
    McpAdapter() already falls back to real_mode via _ensure_real_session()
    when no client is given). Left as a real, failing check rather than
    silently skipped, so this integration test actually catches the gap
    instead of rubber-stamping it.
    """
    # admin_tools_api doesn't currently expose a feature_flags-by-name
    # lookup, so we go straight to the MCP server's own list_feature_flags
    # tool, called for real over HTTP, to check the actual DB-backed state.
    status, body = http_json(
        "POST",
        f"{MCP_BASE}/mcp",
        [
            {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "docker-integration-test-flagcheck"}},
            },
            {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "list_feature_flags",
                           "arguments": {"repository_name": "billing-worker"}},
            },
        ],
    )
    if status != 200:
        raise CheckFailure(f"list_feature_flags call returned HTTP {status}: {body}")
    call_result = body[1].get("result", {})
    text = call_result.get("content", [{}])[0].get("text", "")
    if "docker-integration-test-flag" in text:
        return (
            "UNEXPECTED: docker-integration-test-flag found in the real "
            "feature_flags table — the platform's flag_rollout graph IS "
            "reaching the real MCP server. If this passes, the "
            "SimulatedFlagToggleClient gap described above has been fixed "
            "since this script was written; update the comments above."
        )
    raise CheckFailure(
        "docker-integration-test-flag does NOT appear in the real "
        "feature_flags table after a full rollout run through the "
        "platform's HTTP API. This confirms: user_platform's flag_rollout "
        "graph runs against an in-memory SimulatedFlagToggleClient, not "
        "the real MCP server — flag_rollout percentage changes driven "
        "through the platform do not persist or reach mcp_server's "
        "set_flag_percentage tool. Fix: give FlagToggleAdapter a real-mode "
        "client (mirroring McpAdapter's _ensure_real_session() pattern) "
        "and pass it explicitly from user_platform/backend.py's "
        "_get_graph(), the same way make_incident_response_graph's "
        "default already works correctly without an explicit client."
    )


# ---------------------------------------------------------------------------
# 4. Memory/RAG agent returns a real, non-empty answer through the
#    platform — proves the vector-store build step in container init ran.
# ---------------------------------------------------------------------------

def check_memory_rag_through_platform() -> str:
    status, body = http_json(
        "POST",
        f"{USER_BASE}/agent/memory_rag/query",
        {"query": "What is the production deployment policy for critical incidents?"},
    )
    if status != 200:
        raise CheckFailure(f"/agent/memory_rag/query returned HTTP {status}: {body}")
    answer = None
    if isinstance(body, dict):
        answer = body.get("answer") or body.get("response") or body.get("text")
    if not answer or not str(answer).strip():
        raise CheckFailure(
            f"expected a non-empty answer from the real agentic RAG entry point, got: {body}"
        )
    if len(str(answer).strip()) < 10:
        raise CheckFailure(f"answer suspiciously short (possible empty-index fallback): {answer!r}")
    return (
        f"/agent/memory_rag/query returned a real, non-empty answer "
        f"({len(str(answer))} chars), which requires the container's RAG "
        f"vector-store build step to have actually run — not just the "
        f"container starting: {str(answer)[:120]!r}..."
    )


# ---------------------------------------------------------------------------
# 5. Re-verify the planning_toolkit env-var/API-key regression check
#    SPECIFICALLY inside the running container, via docker-compose exec,
#    with GOOGLE_API_KEY/GEMINI_API_KEY explicitly unset for that exec call.
# ---------------------------------------------------------------------------

PLANNING_ENV_TESTS = (
    "planning_toolkit/tests/test_lab.py::test_coderift_chat_model_offline_fallback "
    "planning_toolkit/tests/test_lab.py::test_coderift_structured_output_requires_live_model"
)


def check_planning_toolkit_env_var_regression_in_container() -> str:
    # `docker-compose exec` runs a NEW process inside the already-running
    # user_platform container. `env -u ... -u ...` strips the two API-key
    # vars for that one process only, regardless of whether the container
    # itself has them set — this is what actually catches an
    # env-var-passthrough bug: if some layer of the container setup
    # (compose file, Dockerfile ENV, an .env baked into the image) is
    # accidentally forcing a key to always be present, this exec call
    # would still see it, because `env -u` only affects the shell we
    # spawn, not the image's baked config. The point isn't "does the key
    # exist" — it's "does the offline-fallback code path still work
    # correctly when it's the code path that actually runs," which can
    # only be verified inside the container's own Python/dependency
    # environment, not on the host.
    cmd = [
        "docker-compose", "exec", "-T",
        "-e", "GOOGLE_API_KEY=",
        "-e", "GEMINI_API_KEY=",
        "user_platform",
        "env", "-u", "GOOGLE_API_KEY", "-u", "GEMINI_API_KEY",
        "python", "-m", "pytest", "-v",
    ] + PLANNING_ENV_TESTS.split()

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError as exc:
        raise CheckFailure(f"docker-compose not found on this host: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CheckFailure(f"docker-compose exec timed out after 120s: {exc}") from exc

    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise CheckFailure(
            f"pytest inside the user_platform container exited {proc.returncode} "
            f"with GOOGLE_API_KEY/GEMINI_API_KEY unset. Output:\n{output[-2000:]}"
        )
    if "2 passed" not in output:
        raise CheckFailure(
            f"expected exactly 2 passed for the offline-fallback regression tests "
            f"inside the container, got unexpected pytest output:\n{output[-2000:]}"
        )
    return (
        "docker-compose exec into the running user_platform container, with "
        "GOOGLE_API_KEY/GEMINI_API_KEY explicitly unset for that exec call, "
        "ran the two offline-fallback regression tests via real pytest "
        "inside the container's own environment: 2 passed. Output tail:\n"
        + output[-500:]
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print("Coderift Technologies — Docker integration test")
    print("Assumes `docker-compose up --build` is already running.")
    print("=" * 78)

    # Fail fast with a clear message if the stack isn't even up, rather
    # than letting every check below fail with a confusing connection error.
    try:
        http_json("GET", f"{MCP_BASE}/health", timeout=5)
    except CheckFailure:
        print(
            "\nCould not reach the MCP server at "
            f"{MCP_BASE}/health. Is `docker-compose up --build` running? "
            "Aborting before running the rest of the checks.\n"
        )
        return 2

    run_check("1. MCP server reachable + real HTTP JSON-RPC round trip", check_mcp_health_and_protocol)
    run_check("2. User platform /agents lists all five live agents", check_user_platform_agents)
    run_check("3. flag_rollout driven end-to-end over HTTP (start, metrics cycle, HITL gate, resume, complete)", check_flag_rollout_over_http)
    run_check("3b. flag_rollout changes actually reach the real MCP server / feature_flags table", check_flag_rollout_actually_calls_real_mcp_server)
    run_check("4. memory/RAG agent returns a real answer through the platform", check_memory_rag_through_platform)
    run_check("5. planning_toolkit env-var/API-key regression re-verified INSIDE the container", check_planning_toolkit_env_var_regression_in_container)

    print("\n" + "=" * 78)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"RESULT: {passed} passed, {failed} failed")
    print("=" * 78)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
