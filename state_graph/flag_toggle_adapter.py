from __future__ import annotations

from typing import Any

from state_graph.contracts import NodeFailure

# The ONLY tool calls the flag-rollout graph's canary/auto_rollback nodes
# are permitted to make. An unconstrained ReAct loop here could toggle
# production traffic percentages in ways the graph never modeled — e.g.
# deciding on its own that "healthy enough" means skipping straight to
# 100%, bypassing the blast-radius HITL gate the graph exists to enforce.
# Every public method on FlagToggleAdapter below maps 1:1 onto one of
# these names; there is no generic "call(tool_name, args)" escape hatch
# that could reach outside this set.
ALLOWED_TOOLS = frozenset({
    "set_flag_percentage",
    "get_flag_status",
    "get_error_rate_metrics",
})


class FlagToggleAdapter:
    """Constrained ReAct adapter for state_graph.flag_rollout.

    Mirrors state_graph.mcp_adapter.McpAdapter's shape exactly (wrap the
    real/simulated client call, catch failures, raise NodeFailure with a
    specific error_code) but adds one thing McpAdapter doesn't need: an
    explicit whitelist check before any call reaches the client at all.
    McpAdapter's incident-response tools are each individually safe to
    expose as their own method; the flag-rollout graph's tools are
    additionally dangerous as a CLASS (toggling live production traffic
    percentages), so the whitelist is enforced structurally here rather
    than trusted to "the graph just happens to only call the right
    methods."
    """

    def __init__(self, client: Any = None):
        self._client = client or SimulatedFlagToggleClient()

    def _call(self, tool_name: str, args: dict) -> Any:
        if tool_name not in ALLOWED_TOOLS:
            # Not reachable through the public methods below under normal
            # use — this is the structural backstop if a future node is
            # ever added to this file without going through the whitelist.
            raise NodeFailure(
                "FLAG_TOGGLE_TOOL_NOT_WHITELISTED",
                f"'{tool_name}' is not in ALLOWED_TOOLS for the flag-rollout "
                f"graph's constrained ReAct nodes: {sorted(ALLOWED_TOOLS)}.",
                payload={"tool_name": tool_name, "allowed_tools": sorted(ALLOWED_TOOLS)},
            )
        try:
            return self._client.call(tool_name, args)
        except NodeFailure:
            raise
        except Exception as exc:
            raise NodeFailure(
                "FLAG_TOGGLE_TOOL_ERROR",
                f"{tool_name} failed for args={args}: {exc}",
                payload={"tool_name": tool_name, "args": args},
            ) from exc

    def set_flag_percentage(self, repo: str, environment: str, flag_name: str,
                              rollout_pct: int) -> dict:
        return self._call("set_flag_percentage", {
            "repository_name": repo,
            "environment_name": environment,
            "flag_name": flag_name,
            "rollout_pct": rollout_pct,
        })

    def get_flag_status(self, repo: str, environment: str, flag_name: str) -> dict:
        return self._call("get_flag_status", {
            "repository_name": repo,
            "environment_name": environment,
            "flag_name": flag_name,
        })

    def get_error_rate_metrics(self, repo: str, environment: str, flag_name: str) -> dict:
        return self._call("get_error_rate_metrics", {
            "repository_name": repo,
            "environment_name": environment,
            "flag_name": flag_name,
        })


class SimulatedFlagToggleClient:
    """In-memory simulated client so tests never need a live MCP server
    or sqlite connection — the real bridge to mcp_server's
    set_flag_percentage/get_error_rate_metrics tools (via
    server_http.py's /mcp endpoint) is a separate, thin swap-in client
    the user platform wires up for the live demo, matching
    state_graph.mcp_adapter.SimulatedMcpClient's existing convention of
    "tests get a simulated client; the live wiring is a drop-in
    replacement with the same .call(tool_name, args) shape."

    Tracks flag state and a scripted metrics_result queue so tests can
    deterministically drive the awaiting_metrics loop without relying on
    get_error_rate_metrics' real random jitter.
    """

    def __init__(self):
        self._flags: dict[tuple[str, str, str], int] = {}
        self._metrics_queue: list[str] = []
        self.fail_next_set: bool = False
        self.fail_next_metrics: bool = False

    def queue_metrics_result(self, result: str) -> None:
        self._metrics_queue.append(result)

    def call(self, tool_name: str, args: dict) -> Any:
        key = (args["repository_name"], args["environment_name"], args["flag_name"])

        if tool_name == "set_flag_percentage":
            if self.fail_next_set:
                self.fail_next_set = False
                raise RuntimeError("simulated flag-toggle tool timeout")
            previous = self._flags.get(key, 0)
            self._flags[key] = args["rollout_pct"]
            return {
                "flag_name": args["flag_name"],
                "rollout_pct": args["rollout_pct"],
                "previous_rollout_pct": previous,
            }

        if tool_name == "get_flag_status":
            return {"flag_name": args["flag_name"], "rollout_pct": self._flags.get(key, 0)}

        if tool_name == "get_error_rate_metrics":
            if self.fail_next_metrics:
                self.fail_next_metrics = False
                raise RuntimeError("simulated metrics tool timeout")
            result = self._metrics_queue.pop(0) if self._metrics_queue else "healthy"
            return {
                "flag_name": args["flag_name"],
                "rollout_pct": self._flags.get(key, 0),
                "result": result,
            }

        raise ValueError(f"SimulatedFlagToggleClient: unknown tool '{tool_name}'")
