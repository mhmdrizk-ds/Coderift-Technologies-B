from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()

from typing import Any, Type, TypeVar

from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, ValidationError

GOOGLE_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GOOGLE_MODEL = os.environ.get("GOOGLE_MODEL", "gemini-2.5-flash")

ModelT = TypeVar("ModelT", bound=BaseModel)


class NoLiveModelConfigured(RuntimeError):
    """Raised by the structured-output path when no live model is
    configured AND the caller didn't supply a fallback. Plain
    `.invoke()` never raises this — it degrades to a labeled offline
    placeholder instead, matching rag/llm.py's existing contract for
    the rest of this repo."""


def _call_google(prompt: str, system_prompt: str | None, max_tokens: int) -> str | None:
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if system_prompt:
        body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
    url = f"{GOOGLE_API_BASE}/{GOOGLE_MODEL}:generateContent?key={api_key}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
        return text or None
    except Exception as exc:
        print(f"Gemini API error: {type(exc).__name__}: {exc}")
        return None


def _split_system_human(messages: list[BaseMessage]) -> tuple[str, str | None]:
    system_prompt = None
    human_parts = []
    for m in messages:
        if isinstance(m, SystemMessage):
            system_prompt = m.content if system_prompt is None else f"{system_prompt}\n{m.content}"
        else:
            human_parts.append(m.content if isinstance(m.content, str) else str(m.content))
    return "\n\n".join(human_parts), system_prompt


class _StructuredOutputRunnable(Runnable):
    def __init__(self, model: "CoderiftChatModel", schema: Type[ModelT]):
        self._model = model
        self._schema = schema

    def invoke(self, messages: list[tuple[str, str]] | list[BaseMessage], **kwargs) -> ModelT:
        lc_messages = self._model._coerce_messages(messages)
        prompt, system_prompt = _split_system_human(lc_messages)
        schema_hint = (
            f"\n\nRespond with ONLY a single JSON object matching this JSON "
            f"schema (no prose, no markdown fences):\n{self._schema.model_json_schema()}"
        )
        text = _call_google(prompt + schema_hint, system_prompt, max_tokens=4096)
        if text is None:
            raise NoLiveModelConfigured(
                "with_structured_output has no GOOGLE_API_KEY/GEMINI_API_KEY to call — "
                "catch NoLiveModelConfigured at the call site and substitute a "
                "deterministic fallback Plan, the same way decompose_release_readiness_plan() does."
            )
        cleaned = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        return self._schema.model_validate(json.loads(cleaned))


class CoderiftChatModel(SimpleChatModel):
    """Drop-in replacement for `ChatMistralAI` — a real `BaseChatModel`."""

    @property
    def _llm_type(self) -> str:
        return "coderift-gemini-or-offline"

    def _call(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> str:
        prompt, system_prompt = _split_system_human(messages)
        text = _call_google(prompt, system_prompt, max_tokens=4096)
        if text is None:
            text = (
                "[offline fallback: no GOOGLE_API_KEY/GEMINI_API_KEY configured — "
                "echoing the real facts supplied in the prompt below rather than "
                "inventing prose]\n\n" + prompt
            )
        return text

    def _coerce_messages(self, messages) -> list[BaseMessage]:
        if isinstance(messages, str):
            return [HumanMessage(content=messages)]

        if messages and isinstance(messages[0], BaseMessage):
            return messages

        coerced: list[BaseMessage] = []

        for role, text in messages:
            if role == "system":
                coerced.append(SystemMessage(content=text))
            else:
                coerced.append(HumanMessage(content=text))

        return coerced

    def invoke(self, messages, **kwargs) -> AIMessage:  
        return super().invoke(self._coerce_messages(messages), **kwargs)

    def with_structured_output(
        self, schema: Type[ModelT], method: str = "json_schema", **kwargs: Any,
    ) -> _StructuredOutputRunnable:
        return _StructuredOutputRunnable(self, schema)
