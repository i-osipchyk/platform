"""Tracker: turns (provider, model, usage, timing) into a priced CallRecord and fans it out.

Use it directly when a project calls an SDK itself (Anthropic Messages, raw HTTP); LLM.chat()
uses it under the hood for OpenAI-compatible providers.
"""

from __future__ import annotations

import contextvars
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from llmkit import pricing
from llmkit.record import CallRecord
from llmkit.sinks import Sink, default_sinks
from llmkit.usage import Usage


@dataclass(frozen=True, slots=True)
class _Trace:
    id: str
    name: str


_current_trace: contextvars.ContextVar[_Trace | None] = contextvars.ContextVar(
    "llmkit_trace", default=None
)


class Tracker:
    def __init__(
        self,
        project: str | None = None,
        env: str | None = None,
        sinks: list[Sink] | None = None,
        capture_content: bool | None = None,
    ) -> None:
        project = project or os.getenv("LLMKIT_PROJECT")
        if not project:
            raise ValueError("llmkit needs a project: pass project= or set LLMKIT_PROJECT")
        self.project = project
        self.env = env or os.getenv("LLMKIT_ENV") or "local"
        self.sinks = default_sinks() if sinks is None else sinks
        if capture_content is None:
            capture_content = os.getenv("LLMKIT_CAPTURE_CONTENT", "").lower() in ("1", "true")
        self.capture_content = capture_content

    @contextmanager
    def trace(self, name: str) -> Iterator[str]:
        """Group every call inside the block under one trace (e.g. one pipeline run)."""
        token = _current_trace.set(_Trace(secrets.token_hex(16), name))
        try:
            yield _current_trace.get().id  # type: ignore[union-attr]
        finally:
            _current_trace.reset(token)

    def record(
        self,
        *,
        provider: str,
        model: str,
        component: str,
        usage: Usage,
        started_at: datetime,
        latency_ms: float,
        prompt_id: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
        input: Any = None,
        output: Any = None,
    ) -> CallRecord:
        if started_at.tzinfo is None:
            raise ValueError("started_at must be timezone-aware (use datetime.now(UTC))")
        started_at = started_at.astimezone(UTC)
        c = pricing.cost(model, usage, started_at)
        trace = _current_trace.get()
        record = CallRecord(
            project=self.project,
            env=self.env,
            component=component,
            provider=provider,
            model=model,
            started_at=started_at.isoformat(),
            latency_ms=round(latency_ms, 1),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cost_usd=None if c is None else round(c.total, 8),
            cost_breakdown=None
            if c is None
            else {
                "input": c.input,
                "output": c.output,
                "input_cache_read": c.cache_read,
                "input_cache_write": c.cache_write,
                "total": c.total,
            },
            prompt_id=prompt_id,
            trace_id=trace.id if trace else secrets.token_hex(16),
            trace_name=trace.name if trace else None,
            span_id=secrets.token_hex(8),
            error=error,
            metadata=metadata or {},
            input=input if self.capture_content else None,
            output=output if self.capture_content else None,
        )
        for sink in self.sinks:
            sink.emit(record)
        return record

    def flush(self) -> None:
        """Push buffered records. Call at the end of a Lambda handler."""
        for sink in self.sinks:
            sink.flush()
