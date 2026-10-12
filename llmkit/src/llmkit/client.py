"""LLM: a tracked chat client for OpenAI-compatible providers (DeepSeek, OpenAI, ...).

    llm = LLM()                                   # project/env from LLMKIT_PROJECT / LLMKIT_ENV
    result = llm.chat(
        model="deepseek-flash",
        component="news-agent",
        messages=[{"role": "user", "content": "..."}],
        tools=[...], temperature=0,               # anything else goes to chat.completions.create
    )
    result.message   # the SDK's message object (tool_calls etc.)
    result.text      # message.content or ""
    result.record    # the CallRecord (tokens, cost_usd, ...)

For providers with their own SDK (Anthropic), call it yourself and report with
`llm.tracker.record(..., usage=Usage.from_anthropic(response.usage))`.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from llmkit.record import CallRecord
from llmkit.sinks import Sink
from llmkit.tracker import Tracker
from llmkit.usage import Usage


@dataclass(frozen=True, slots=True)
class Provider:
    name: str
    base_url: str | None
    api_key_env: str
    base_url_env: str


PROVIDERS: dict[str, Provider] = {
    "deepseek": Provider("deepseek", "https://api.deepseek.com", "DEEPSEEK_API_KEY",
                         "DEEPSEEK_BASE_URL"),
    "openai": Provider("openai", None, "OPENAI_API_KEY", "OPENAI_BASE_URL"),
}


def provider_for(model: str) -> str:
    lower = model.lower()
    if lower.startswith("deepseek"):
        return "deepseek"
    if lower.startswith(("gpt", "o1", "o3", "o4", "chatgpt")):
        return "openai"
    raise ValueError(f"can't infer the provider of {model!r}; pass provider=")


@dataclass(frozen=True, slots=True)
class ChatResult:
    message: Any
    record: CallRecord
    raw: Any

    @property
    def text(self) -> str:
        return getattr(self.message, "content", None) or ""

    @property
    def cost_usd(self) -> float | None:
        return self.record.cost_usd


class LLM:
    def __init__(
        self,
        project: str | None = None,
        env: str | None = None,
        sinks: list[Sink] | None = None,
        clients: dict[str, Any] | None = None,
        capture_content: bool | None = None,
    ) -> None:
        self.tracker = Tracker(project, env, sinks, capture_content)
        self._clients: dict[str, Any] = dict(clients or {})

    @property
    def project(self) -> str:
        return self.tracker.project

    def trace(self, name: str):  # type: ignore[no-untyped-def]
        return self.tracker.trace(name)

    def flush(self) -> None:
        self.tracker.flush()

    def client(self, provider: str) -> Any:
        if provider not in self._clients:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise ImportError("LLM.chat needs `llmkit[openai]`") from exc
            spec = PROVIDERS[provider]
            api_key = os.getenv(spec.api_key_env)
            if not api_key:
                raise ValueError(f"{spec.api_key_env} is not set")
            base_url = os.getenv(spec.base_url_env) or spec.base_url
            self._clients[provider] = OpenAI(api_key=api_key, base_url=base_url)
        return self._clients[provider]

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        component: str,
        provider: str | None = None,
        prompt_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        **create_kwargs: Any,
    ) -> ChatResult:
        provider = provider or provider_for(model)
        client = self.client(provider)
        started = datetime.now(UTC)
        t0 = time.perf_counter()
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, **create_kwargs
            )
        except Exception as exc:
            self.tracker.record(
                provider=provider, model=model, component=component, usage=Usage(),
                started_at=started, latency_ms=(time.perf_counter() - t0) * 1000,
                prompt_id=prompt_id, error=repr(exc)[:500], metadata=metadata, input=messages,
            )
            raise
        latency_ms = (time.perf_counter() - t0) * 1000
        message = response.choices[0].message
        record = self.tracker.record(
            provider=provider,
            model=getattr(response, "model", None) or model,
            component=component,
            usage=Usage.from_openai(getattr(response, "usage", None)),
            started_at=started,
            latency_ms=latency_ms,
            prompt_id=prompt_id,
            metadata=metadata,
            input=messages,
            output=getattr(message, "content", None),
        )
        return ChatResult(message=message, record=record, raw=response)
