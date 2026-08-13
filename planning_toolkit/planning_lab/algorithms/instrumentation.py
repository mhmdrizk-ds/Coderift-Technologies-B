"""
instrumentation.py — a counting proxy around any BaseChatModel.

Used to measure total LLM calls, approximate tokens, latency, AND context
size (raw input character length, both total-across-the-run and
largest-single-call — tracked as its own field, not inferred from the
token estimate) for BOTH decomposition-first (Teammate 1's, imported and
run completely unmodified) and dynamic decomposition on the exact same
case, so the divergence comparison required by the task is backed by real
numbers rather than a description of expected behavior. Wrapping the llm
parameter is the only way to instrument decomposition.py without editing
it — decomposition.py already accepts any BaseChatModel via its `llm`
parameter, so passing a counting proxy in place of a bare
CoderiftChatModel requires zero changes to the frozen file.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable, RunnableLambda


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class CallStats:
    def __init__(self):
        self.call_count = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_latency_seconds = 0.0
        self.total_context_chars = 0  # sum of every call's raw input character
                                       # length across the run — the "context
                                       # size" figure, tracked independently
                                       # of (not derived from) input_tokens.
                                       # The lab spec asks specifically whether
                                       # a set of tests "resulted in bigger
                                       # context"; that's a statement about how
                                       # much text was pushed into the model,
                                       # which a character count answers
                                       # directly without going through the
                                       # token-estimation proxy at all.
        self.max_single_prompt_chars = 0  # largest single call's input length;
                                           # distinguishes "many small calls"
                                           # from "one large call" for the same
                                           # total_context_chars.
        self.calls: list[dict] = []

    def record(self, kind: str, input_text: str, output_text: str, latency: float):
        in_tok = _approx_tokens(input_text)
        out_tok = _approx_tokens(output_text)
        in_chars = len(input_text)
        self.call_count += 1
        self.input_tokens += in_tok
        self.output_tokens += out_tok
        self.total_latency_seconds += latency
        self.total_context_chars += in_chars
        self.max_single_prompt_chars = max(self.max_single_prompt_chars, in_chars)
        self.calls.append({
            "kind": kind, "input_tokens": in_tok, "output_tokens": out_tok,
            "input_chars": in_chars, "latency_seconds": round(latency, 4),
        })

    def summary(self) -> dict:
        return {
            "llm_call_count": self.call_count,
            "total_tokens": self.input_tokens + self.output_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_latency_seconds": round(self.total_latency_seconds, 4),
            "total_context_chars": self.total_context_chars,
            "max_single_prompt_chars": self.max_single_prompt_chars,
            "context_size_method": "total_context_chars sums every call's raw input "
                                    "character length across the run (not derived from "
                                    "input_tokens); max_single_prompt_chars is the "
                                    "largest single call's input length.",
        }


class CountingChatModel(BaseChatModel):
    """Wraps a real BaseChatModel and records every _generate / structured-
    output call into a shared CallStats. Delegates all actual generation to
    the wrapped model — this class adds measurement only, no behavior
    change, so wrapping decomposition-first's llm parameter with this
    doesn't alter what it does or how it reasons."""

    inner: Any
    stats: Any

    model_config = {"arbitrary_types_allowed": True}

    @property
    def _llm_type(self) -> str:
        return f"counting[{self.inner._llm_type}]"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        input_text = "\n".join(str(m.content) for m in messages)
        start = time.perf_counter()
        result = self.inner._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        latency = time.perf_counter() - start
        output_text = result.generations[0].message.content
        self.stats.record("invoke", input_text, output_text, latency)
        return result

    def with_structured_output(self, schema, *, include_raw: bool = False, **kwargs: Any) -> Runnable:
        inner_runnable = self.inner.with_structured_output(schema, include_raw=include_raw, **kwargs)

        def _invoke(input_messages, **_kwargs):
            input_text = "\n".join(
                str(m[1]) if isinstance(m, tuple) else str(getattr(m, "content", m))
                for m in input_messages
            )
            start = time.perf_counter()
            try:
                result = inner_runnable.invoke(input_messages, **_kwargs)
            finally:
                latency = time.perf_counter() - start
            output_text = result.model_dump_json() if hasattr(result, "model_dump_json") else str(result)
            self.stats.record("structured_output", input_text, output_text, latency)
            return result

        return RunnableLambda(_invoke)


def instrumented(llm: BaseChatModel) -> tuple[CountingChatModel, CallStats]:
    stats = CallStats()
    return CountingChatModel(inner=llm, stats=stats), stats
