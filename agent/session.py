import sys
from pathlib import Path

from agent import capabilities
from agent.mcp_client import MCPClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class CoderiftAgentSession:
    def __init__(
        self,
        elicitation_handler=None,
        sampling_handler=None,
        progress_handler=None,
        verbose=True,
        capability_profile="full",
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
    def call_tool(self, name, arguments, progress_token=None):
        return self.client.call_tool(name, arguments, progress_token=progress_token)

    @staticmethod
    def result_text(result):
        try:
            return result["content"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return str(result)

    def authenticate(self, access_code):
        return self.call_tool("authenticate", {"access_code": access_code})

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

    def close(self):
        self.client.close()
