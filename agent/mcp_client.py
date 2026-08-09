import itertools
import json
import subprocess


class ServerError(Exception):
    """Raised when the server replies with a JSON-RPC error object."""

    def __init__(self, code, message, data=None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


class MCPClient:
    def __init__(
        self,
        server_cmd,
        cwd=None,
        elicitation_handler=None,
        sampling_handler=None,
        progress_handler=None,
        notification_handler=None,
    ):
        """
        server_cmd: list[str], e.g. [sys.executable, "-m", "mcp_server.server"]
        cwd: working directory to launch the server in (it resolves
             db/, resources/, prompts/ relative to its own package
             location's parent, so this must be the project root).

        elicitation_handler(message: str, requested_schema: dict) -> dict
            must return {"action": "accept"|"decline"|"cancel", "content": {...}?}
            per the elicitation/create response shape in the spec.
            May be None — a client without elicitation support simply
            never passes one, and never declares the capability either
            (see capabilities.py).
        sampling_handler(messages: list, system_prompt: str|None, max_tokens: int) -> dict
            must return {"role": "assistant", "content": {"type": "text", "text": ...}}
        progress_handler(progress, total, message) -> None
            called for every notifications/progress notification.
        notification_handler(method: str, params: dict) -> None
            called for any other notification (right now, that's just
            notifications/tools/list_changed).
        """
        self._proc = subprocess.Popen(
            server_cmd,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._ids = itertools.count(1)
        self.elicitation_handler = elicitation_handler
        self.sampling_handler = sampling_handler
        self.progress_handler = progress_handler
        self.notification_handler = notification_handler

    # ---- raw framing --------------------------------------------------
    def _write(self, obj):
        self._proc.stdin.write(json.dumps(obj) + "\n")
        self._proc.stdin.flush()

    def _read(self):
        line = self._proc.stdout.readline()
        if not line:
            err = self._proc.stderr.read()
            raise ConnectionError(
                f"Server closed its stdout unexpectedly.\n--- server stderr ---\n{err}"
            )
        return json.loads(line)

    def close(self):
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        self._proc.terminate()

    # ---- notifications the CLIENT sends --------------------------------
    def notify(self, method, params=None):
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ---- requests the CLIENT sends, blocking for the matching reply ----
    def request(self, method, params=None):
        req_id = next(self._ids)
        self._write({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        return self._await(req_id)

    def call_tool(self, name, arguments, progress_token=None):
        """tools/call, with full support for the server calling back into
        us mid-flight (elicitation/create, sampling/createMessage) and for
        streaming notifications/progress while we wait for the final
        result."""
        req_id = next(self._ids)
        params = {"name": name, "arguments": arguments}
        if progress_token:
            params["_meta"] = {"progressToken": progress_token}
        self._write({"jsonrpc": "2.0", "id": req_id, "method": "tools/call", "params": params})
        return self._await(req_id)

    # ---- the actual multiplexing loop ----------------------------------
    def _await(self, waiting_id):
        while True:
            msg = self._read()

            # a) notification FROM the server (no id)
            if "method" in msg and "id" not in msg:
                self._handle_notification(msg)
                continue

            # b) request FROM the server (elicitation/create, sampling/createMessage)
            if "method" in msg and "id" in msg:
                self._handle_server_request(msg)
                continue

            # c) response to one of OUR requests
            if msg.get("id") == waiting_id:
                if "error" in msg:
                    e = msg["error"]
                    raise ServerError(e.get("code"), e.get("message"), e.get("data"))
                return msg.get("result")
            # A response to some other in-flight id: this agent only ever
            # has one request outstanding at a time, so this shouldn't
            # happen; dropping it is safer than misapplying it.

    def _handle_notification(self, msg):
        method = msg["method"]
        params = msg.get("params", {}) or {}
        if method == "notifications/progress":
            if self.progress_handler:
                self.progress_handler(params.get("progress"), params.get("total"), params.get("message"))
        elif self.notification_handler:
            self.notification_handler(method, params)

    def _handle_server_request(self, msg):
        method = msg["method"]
        params = msg.get("params", {}) or {}
        req_id = msg["id"]
        try:
            if method == "elicitation/create":
                if not self.elicitation_handler:
                    raise RuntimeError(
                        "Server sent elicitation/create but this client never declared "
                        "elicitation support — it should not have offered a tool that "
                        "needs it. Failing loudly rather than guessing an answer."
                    )
                result = self.elicitation_handler(params.get("message"), params.get("requestedSchema"))
            elif method == "sampling/createMessage":
                if not self.sampling_handler:
                    raise RuntimeError(
                        "Server sent sampling/createMessage but this client never "
                        "declared sampling support."
                    )
                result = self.sampling_handler(
                    params.get("messages", []), params.get("systemPrompt"), params.get("maxTokens", 600)
                )
            else:
                raise RuntimeError(f"Unhandled server->client request: {method}")
            self._write({"jsonrpc": "2.0", "id": req_id, "result": result})
        except Exception as exc:  # noqa: BLE001 - must not let this kill the loop
            self._write({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(exc)}})
