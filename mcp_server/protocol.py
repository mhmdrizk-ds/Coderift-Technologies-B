"""
protocol.py — JSON-RPC 2.0 message framing over stdio.

The MCP spec's stdio transport is plain: one JSON-RPC message per line on
stdin/stdout, UTF-8, no embedded newlines. This module is the only place
that touches sys.stdin / sys.stdout, so every other module talks in plain
Python dicts. server_http.py talks JSON-RPC over HTTP instead, but reuses
the same JSONRPCError / make_response / make_error_response helpers so the
message *shapes* are identical across both transports.
"""

import sys
import json
import itertools
import threading

_id_lock = threading.Lock()
_id_counter = itertools.count(1)


class JSONRPCError(Exception):
    """Raised by tool handlers / dispatch code to produce a proper JSON-RPC error."""

    def __init__(self, code: int, message: str, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_error_obj(self):
        err = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err


# Standard JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Application-level error codes (MCP servers are free to define their own
# range below -32000; grouped so a grader can see the pattern at a glance).
ERR_UNAUTHENTICATED = -32001
ERR_UNAUTHORIZED = -32002
ERR_NOT_FOUND = -32003
ERR_CONFLICT = -32004
ERR_CAPABILITY_UNSUPPORTED = -32005


def next_id():
    with _id_lock:
        return next(_id_counter)


def send_message(msg: dict):
    """Write one JSON-RPC message as a single line to stdout."""
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def read_message():
    """Read and parse one JSON-RPC message from stdin. Returns None on EOF."""
    line = sys.stdin.readline()
    if line == "":
        return None
    line = line.strip()
    if not line:
        return read_message()
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise JSONRPCError(PARSE_ERROR, f"Invalid JSON: {exc}") from exc


def is_request(msg: dict) -> bool:
    return "method" in msg and "id" in msg


def is_notification(msg: dict) -> bool:
    return "method" in msg and "id" not in msg


def is_response(msg: dict) -> bool:
    return "method" not in msg and ("result" in msg or "error" in msg)


def make_response(id_, result=None):
    return {"jsonrpc": "2.0", "id": id_, "result": result if result is not None else {}}


def make_error_response(id_, error: JSONRPCError):
    return {"jsonrpc": "2.0", "id": id_, "error": error.to_error_obj()}


def make_request(method: str, params: dict = None) -> dict:
    """Build a server -> client request (used for elicitation/create and
    sampling/createMessage, the two places this server initiates a call
    instead of just answering one)."""
    return {"jsonrpc": "2.0", "id": next_id(), "method": method, "params": params or {}}


def make_notification(method: str, params: dict = None) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params or {}}
