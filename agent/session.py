import sys
from pathlib import Path

from agent import capabilities
from agent.mcp_client import MCPClient
from memory.api import MemorySystem
from rag.naive_rag import answer_naive
from rag.hybrid_rag import answer_hybrid
from rag.agentic_rag import answer_agentic
from rag.self_rag import verify_rag_result, verify_memory_recall

PROJECT_ROOT = Path(__file__).resolve().parent.parent

RAG_STRATEGIES = {
    "naive": answer_naive,
    "hybrid": answer_hybrid,
    "agentic": lambda query, policy_name=None, k=5: answer_agentic(query, k=k),
}


class CoderiftAgentSession:
    def __init__(
        self,
        elicitation_handler=None,
        sampling_handler=None,
        progress_handler=None,
        verbose=True,
        capability_profile="full",
        memory_buffer_capacity=50,
    ):
        """
        capability_profile: "full" declares elicitation+sampling support
        (see capabilities.FULL_CAPABILITIES) and passes real handlers for
        both. "read_only" declares neither (capabilities.READ_ONLY_
        CAPABILITIES) — used to demo the "client without elicitation
        support" path, where deploy_to_production and draft_incident_summary
        never appear in tools/list at all.
        """
        self.verbose = verbose
        self._tools_cache = None
        self._tools_dirty = True
        self.server_capabilities = {}
        self.capability_profile = capability_profile

        if capability_profile == "full":
            client_capabilities = capabilities.FULL_CAPABILITIES
            client_info = capabilities.FULL_CLIENT_INFO
        elif capability_profile == "read_only":
            client_capabilities = capabilities.READ_ONLY_CAPABILITIES
            client_info = capabilities.READ_ONLY_CLIENT_INFO
            # A client that doesn't declare these capabilities also
            # shouldn't wire up handlers for them — there'd be nothing
            # correct to do with an elicitation/create it never promised
            # to answer.
            elicitation_handler = None
            sampling_handler = None
        else:
            raise ValueError(f"Unknown capability_profile: {capability_profile!r}")

        self._client_capabilities = client_capabilities
        self._client_info = client_info

        self.client = MCPClient(
            server_cmd=[sys.executable, "-m", "mcp_server.server"],
            cwd=str(PROJECT_ROOT),
            elicitation_handler=elicitation_handler,
            sampling_handler=sampling_handler,
            progress_handler=progress_handler,
            notification_handler=self._on_notification,
        )

        # --- Memory & RAG integration --------------------------------------
        # One MemorySystem per session — short-term buffer + scratchpad +
        # episodic/semantic stores, all private to this engineer's session
        # until consolidation runs. See memory/api.py for the full surface;
        # this session only ever imports MemorySystem, never the internal
        # router/consolidation/store modules directly.
        self.memory = MemorySystem(buffer_capacity=memory_buffer_capacity)

    # ---- Capability Negotiation -----------------------------------------
    def initialize(self):
        result = self.client.request(
            "initialize",
            {
                "protocolVersion": capabilities.PROTOCOL_VERSION,
                "capabilities": self._client_capabilities,
                "clientInfo": self._client_info,
            },
        )
        self.server_capabilities = result.get("capabilities", {})
        self.client.notify("notifications/initialized")

        if self.verbose:
            print(f"== initialize / initialized handshake ({self.capability_profile} client) ==")
            print(f"  client declared -> {self._client_capabilities}")
            print(f"  server declared <- {self.server_capabilities}")
            print(f"  server info     <- {result.get('serverInfo')}")
        return result

    def server_supports(self, dotted_path: str) -> bool:
        """Dotted-path lookup into the server's declared capabilities,
        e.g. server_supports('tools.listChanged'). Called before the
        agent assumes a server-side promise is real, rather than just
        hoping."""
        node = self.server_capabilities
        for part in dotted_path.split("."):
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        return bool(node) if not isinstance(node, dict) else True

    # ---- Notifications ---------------------------------------------------
    def _on_notification(self, method, params):
        if method == "notifications/tools/list_changed":
            if self.verbose:
                print(
                    "\n  [notification] notifications/tools/list_changed received "
                    "— invalidating cached tool list"
                )
            self._tools_dirty = True
        elif self.verbose:
            print(f"\n  [notification] unhandled: {method} {params}")

    def tools_list(self, force=False):
        if self._tools_dirty or force or self._tools_cache is None:
            result = self.client.request("tools/list")
            self._tools_cache = result.get("tools", [])
            self._tools_dirty = False
        return self._tools_cache

    # ---- thin wrappers used by scenarios ----------------------------------
    def call_tool(self, name, arguments, progress_token=None, remember=True):
        result = self.client.call_tool(name, arguments, progress_token=progress_token)
        if remember:
            # Every tool exchange is a message the short-term buffer should
            # see — this is what "call memory.remember_turn() after every
            # message exchange" means in an agent that talks to the MCP
            # server instead of (or in addition to) a human. The router
            # decides forget-vs-promote for each one once it ages out of
            # the buffer; see memory/router.py's Coderift keyword list.
            self.memory.remember_turn("user", f"call {name}({arguments})")
            self.memory.remember_turn("tool", f"{name} -> {self.result_text(result)}")
        return result

    @staticmethod
    def result_text(result):
        try:
            return result["content"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return str(result)

    def authenticate(self, access_code):
        result = self.call_tool("authenticate", {"access_code": access_code})
        return result

    def list_resources(self):
        return self.client.request("resources/list")

    def read_resource(self, uri):
        return self.client.request("resources/read", {"uri": uri})

    def list_prompts(self):
        return self.client.request("prompts/list")

    def get_prompt(self, name, arguments=None):
        payload = {"name": name}
        if arguments:
            payload["arguments"] = arguments
        return self.client.request("prompts/get", payload)

    # ---- Memory recall, Self-RAG-verified ---------------------------------
    def recall(self, topic: str) -> dict:
        """Recall a semantic fact and verify it before trusting it — the
        same Self-RAG check applied to RAG answers below is applied here,
        per the assignment's requirement that a recalled memory is not
        automatically trustworthy just because it came from "memory"
        instead of "retrieval". Returns {"recalled": dict|None,
        "verification": dict}."""
        recalled = self.memory.recall(topic)
        verification = verify_memory_recall(topic, recalled)
        if self.verbose:
            if recalled is None:
                print(f"  [memory] recall({topic!r}) -> nothing known")
            else:
                print(f"  [memory] recall({topic!r}) -> v{recalled['version']}, "
                      f"self-rag passed={verification['passed']}")
        return {"recalled": recalled, "verification": verification}

    # ---- RAG policy questions, Self-RAG-verified with one retry ----------
    def ask_policy_question(self, query: str, strategy: str = "agentic",
                             policy_name: str | None = None) -> dict:
        """Answer a policy question via the given RAG strategy, verify with
        Self-RAG, and — if verification fails — retry once with a
        rewritten query before honestly reporting failure. Never returns a
        confident answer without a retrieved chunk behind it."""
        if strategy not in RAG_STRATEGIES:
            raise ValueError(f"Unknown RAG strategy: {strategy!r}")
        fn = RAG_STRATEGIES[strategy]

        result = fn(query, policy_name=policy_name)
        verified = verify_rag_result(result)

        if not verified["self_rag"]["passed"]:
            rewritten = f"Coderift Technologies policy: {query}"
            if self.verbose:
                print(f"  [self-rag] first attempt failed verification "
                      f"({verified['self_rag']['relevance']['reason'][:60]}...) — retrying with rewritten query")
            retry_result = fn(rewritten, policy_name=policy_name)
            retry_verified = verify_rag_result(retry_result)
            if retry_verified["self_rag"]["passed"]:
                return retry_verified
            # Both attempts failed verification — report that honestly
            # rather than returning an unsupported answer.
            retry_verified["self_rag"]["final_status"] = "failed_after_retry"
            return retry_verified

        verified["self_rag"]["final_status"] = "passed_first_attempt"
        return verified

    # ---- Session lifecycle -------------------------------------------------
    def run_consolidation(self) -> dict:
        """Periodic consolidation pass — episodic memory becomes semantic
        facts. Called explicitly, never at write time (see
        memory/consolidation.py's docstring)."""
        summary = self.memory.run_consolidation_now()
        if self.verbose:
            print(f"  [memory] consolidation ran: {summary['episodes_processed']} episodes "
                  f"processed, topics touched: {summary['topics_touched']}")
        return summary

    def close(self):
        # End-of-session cleanup: run consolidation once so anything this
        # session's router promoted to episodic memory has a chance to
        # become a semantic fact before the session object goes away.
        self.run_consolidation()
        self.client.close()
