"""
context.py — ToolContext: the object every tool handler gets so it can
reach back out to the client mid-call.

These three methods are the exact points where a Coderift tool crosses
into the Elicitation, Sampling, and Progress-tracking protocol concerns.

Because this is a simple single-connection, synchronous stdio server, a
server -> client request (elicitation/create, sampling/createMessage)
blocks the tool handler by reading stdin directly until a matching
response line arrives. That's a deliberate simplification for a
teaching-scale server, not how you'd want to do it under real concurrent
load (that would need per-request queues), but it's spec-correct for a
single stdio session, one call in flight at a time.
"""

from mcp_server import protocol


class ClientDisconnected(Exception):
    pass


class ToolContext:
    def __init__(self, session, progress_token=None):
        self.session = session
        self.progress_token = progress_token

    # ---- Elicitation (elicitation/create) -------------------------------
    def elicit(self, message: str, requested_schema: dict) -> dict:
        """Pause the tool call and ask the human, through the client, a
        yes/no or short structured question. Blocks for the response.

        requested_schema follows the same JSON-Schema-object shape as a
        tool input schema — e.g. {"type": "object", "properties":
        {"confirm": {"type": "boolean"}}, "required": ["confirm"]}.
        """
        if not self.session.supports("elicitation"):
            # Capability negotiation: never assume the client can do this.
            # server.py already hides deploy_to_production from a client
            # that never declared elicitation support, so reaching this
            # line means the client called it anyway — a clean error, not
            # a crash or a silent bypass.
            raise protocol.JSONRPCError(
                protocol.ERR_CAPABILITY_UNSUPPORTED,
                "This action requires human-in-the-loop confirmation "
                "(elicitation), but the connected client did not declare "
                "elicitation support during initialize.",
            )
        req = protocol.make_request(
            "elicitation/create",
            {"message": message, "requestedSchema": requested_schema},
        )
        protocol.send_message(req)
        result = self._await_response(req["id"])
        # Per spec, elicitation/create replies with an `action` of
        # "accept" | "decline" | "cancel" plus `content` when accepted.
        return result

    # ---- Sampling (sampling/createMessage) -------------------------------
    def sample(self, messages: list, system_prompt: str = None, max_tokens: int = 600) -> dict:
        """Ask the CLIENT's model (never a model the server owns) to reason
        over something — used by draft_incident_summary. Blocks for the
        response."""
        if not self.session.supports("sampling"):
            raise protocol.JSONRPCError(
                protocol.ERR_CAPABILITY_UNSUPPORTED,
                "This tool needs the client's model to reason over incident "
                "context (sampling), but the connected client did not "
                "declare sampling support during initialize.",
            )
        params = {"messages": messages, "maxTokens": max_tokens}
        if system_prompt:
            params["systemPrompt"] = system_prompt
        req = protocol.make_request("sampling/createMessage", params)
        protocol.send_message(req)
        return self._await_response(req["id"])

    # ---- Progress tracking (notifications/progress) ----------------------
    def report_progress(self, progress: int, total: int, message: str = None):
        """Fire-and-forget notification — no response expected, so this
        does not block. Silently a no-op if the client didn't hand us a
        progressToken on this call (meaning it doesn't want updates)."""
        if self.progress_token is None:
            return
        params = {"progressToken": self.progress_token, "progress": progress, "total": total}
        if message:
            params["message"] = message
        protocol.send_message(protocol.make_notification("notifications/progress", params))

    # ---- internal ----------------------------------------------------------
    def _await_response(self, request_id):
        while True:
            msg = protocol.read_message()
            if msg is None:
                raise ClientDisconnected("Client closed the connection mid-request.")
            if msg.get("id") == request_id and ("result" in msg or "error" in msg):
                if "error" in msg:
                    err = msg["error"]
                    raise protocol.JSONRPCError(
                        err.get("code", protocol.INTERNAL_ERROR),
                        err.get("message", "Client returned an error"),
                        err.get("data"),
                    )
                return msg["result"]
            # Any other line received while we're blocked waiting on this
            # specific reply is out of scope for this single-in-flight demo
            # server, so it's dropped rather than silently misapplied.
