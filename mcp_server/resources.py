"""
resources.py — resources/list and resources/read.

The Production Deployment Policy is modeled as a resource, not a tool,
because it's static reference text the model should read once and reason
over for the rest of the session (e.g. when deciding how to respond to an
elicitation prompt) — not something it calls a function for on every
deploy.
"""

from pathlib import Path

from mcp_server.protocol import JSONRPCError, ERR_NOT_FOUND

RESOURCES_DIR = Path(__file__).resolve().parent.parent / "resources"

_CATALOG = {
    "policy://production-deployment": (
        "production_deployment_policy.md",
        "Production Deployment Policy",
    ),
}


def list_resources() -> dict:
    resources = []
    for uri, (fname, title) in _CATALOG.items():
        resources.append({
            "uri": uri,
            "name": title,
            "mimeType": "text/markdown",
        })
    return {"resources": resources}


def read_resource(uri: str) -> dict:
    entry = _CATALOG.get(uri)
    if entry is None:
        raise JSONRPCError(ERR_NOT_FOUND, f"No resource with uri '{uri}'.")
    fname, title = entry
    path = RESOURCES_DIR / fname
    if not path.exists():
        raise JSONRPCError(ERR_NOT_FOUND, f"Resource file '{fname}' missing on disk.")
    text = path.read_text(encoding="utf-8")
    return {
        "contents": [
            {"uri": uri, "mimeType": "text/markdown", "text": text}
        ]
    }
