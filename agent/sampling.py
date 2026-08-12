"""
sampling.py — the CLIENT's model, invoked when the server sends a
sampling/createMessage request (used by draft_incident_summary). This is
the "reasoning happens on the client, not the server" half of the
Sampling concern — mcp_server/tools_impl/incident_tools.py is the other
half (the call-site).

Tries a live Google Gemini call if GOOGLE_API_KEY/GEMINI_API_KEY is set in
the environment; otherwise falls back to a deterministic rule-based
summary built from the facts the server embedded in the message text, so
the tool call still resolves to a real, checkable answer offline and the
demo is repeatable without any API key.
"""

import ast
import json
import os
import re
import urllib.error
import urllib.request

GOOGLE_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GOOGLE_MODEL = os.environ.get("GOOGLE_MODEL", "gemini-2.5-flash")

MISTRAL_API_BASE = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")

def _flatten_messages(messages):
    """MCP sampling `messages` -> Gemini `contents` (role "assistant" ->
    "model", per the Gemini API's role naming)."""
    contents = []
    for m in messages:
        content = m.get("content", {})
        text = content.get("text", "") if isinstance(content, dict) else str(content)
        role = "model" if m.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": text}]})
    return contents


def _call_google(messages, system_prompt, max_tokens):
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    body = {
        "contents": _flatten_messages(messages),
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if system_prompt:
        body["systemInstruction"] = {"parts": [{"text": system_prompt}]}

    url = f"{GOOGLE_API_BASE}/{GOOGLE_MODEL}:generateContent?key={api_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        candidates = data.get("candidates", [])
        if not candidates:
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
        return text or None
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError, TimeoutError):
        return None


def _call_mistral(messages, system_prompt, max_tokens):
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        return None

    mistral_messages = []
    if system_prompt:
        mistral_messages.append({"role": "system", "content": system_prompt})
    for m in messages:
        content = m.get("content", {})
        text = content.get("text", "") if isinstance(content, dict) else str(content)
        role = m.get("role", "user")
        mistral_messages.append({"role": role, "content": text})

    body = {
        "model": MISTRAL_MODEL,
        "messages": mistral_messages,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        MISTRAL_API_BASE,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip() or None
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError, TimeoutError):
        return None


def _extract_facts_dict(joined_text):
    """The server embeds a Python-repr'd facts dict in the message text
    (see incident_tools.py). Pull it back out so the offline fallback can
    reason over real structured fields instead of raw string matching."""
    match = re.search(r"Incident facts:\n(\{.*\})", joined_text, re.DOTALL)
    if not match:
        return {}
    try:
        return ast.literal_eval(match.group(1))
    except (ValueError, SyntaxError):
        return {}


def _fallback_incident_summary(messages):
    """Deterministic stand-in when no GOOGLE_API_KEY/GEMINI_API_KEY is
    configured. Builds a real, checkable summary from the incident facts
    the server assembled, rather than inventing anything."""
    joined = "\n".join((m.get("content", {}) or {}).get("text", "") for m in messages)
    facts = _extract_facts_dict(joined)

    title = facts.get("title", "an incident")
    severity = facts.get("severity", "unknown")
    status = facts.get("status", "unknown")
    repo = facts.get("repository_name") or "an unspecified repository"
    env = facts.get("environment_name") or "an unspecified environment"
    deploy_status = facts.get("deployment_status")
    pr_title = facts.get("pull_request_title")

    lines = [
        f"{title} is a {severity}-severity incident, currently {status}.",
    ]
    if deploy_status and pr_title:
        lines.append(
            f"It followed a deployment to {repo}/{env} that shipped "
            f"'{pr_title}' and ended in status '{deploy_status}'."
        )
    else:
        lines.append(f"It is associated with {repo}/{env}; no linked deployment record was found.")

    if status == "resolved":
        lines.append(f"The incident has since been resolved as of {facts.get('resolved_at', 'an unrecorded time')}.")
    else:
        lines.append("The incident remains open and needs continued attention.")

    lines.append(
        "[fallback: no GOOGLE_API_KEY/GEMINI_API_KEY configured — generated by a "
        "local rule engine from the structured facts above, not a live model call]"
    )
    return " ".join(lines[:-1]) + "\n\n" + lines[-1]


def sampling_handler(messages, system_prompt, max_tokens):
    """Registered on MCPClient as the sampling/createMessage responder."""
    print("\n" + "=" * 70)
    print("SAMPLING REQUEST (sampling/createMessage) — reasoning on the CLIENT")
    print("-" * 70)

    text = _call_google(messages, system_prompt, max_tokens)
    if text is None:
        text = _call_mistral(messages, system_prompt, max_tokens)
    used_live_model = text is not None
    if text is None:
        text = _fallback_incident_summary(messages)

    print(f"[{'live Google Gemini API call' if used_live_model else 'offline fallback rule engine'}]")
    print(text)
    print("=" * 70)

    return {"role": "assistant", "content": {"type": "text", "text": text}}
