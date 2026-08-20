from __future__ import annotations


class Interrupt(Exception):
    def __init__(self, reason: str, payload: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.payload = payload or {}


class NodeFailure(Exception):
    def __init__(self, error_code: str, message: str, payload: dict | None = None):
        super().__init__(f"[{error_code}] {message}")
        self.error_code = error_code
        self.message = message
        self.payload = payload or {}