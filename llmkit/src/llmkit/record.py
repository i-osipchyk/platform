"""CallRecord: the one row every LLM call produces, whatever the provider or sink."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class CallRecord:
    # Attribution: these three answer "which project / where / what part of it spent this".
    project: str
    env: str          # local | demo | live (anything lowercase)
    component: str    # agent / job / step name inside the project, e.g. "news-agent"

    provider: str     # deepseek | openai | anthropic | ...
    model: str
    started_at: str   # ISO 8601 UTC
    latency_ms: float

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0

    cost_usd: float | None = None  # None = model not in the price table
    cost_breakdown: dict[str, float] | None = None

    prompt_id: str | None = None   # versioned prompt file, e.g. "reject_v1"
    trace_id: str | None = None    # groups the calls of one run (see Tracker.trace)
    trace_name: str | None = None
    span_id: str | None = None
    error: str | None = None       # set when the call raised; tokens are then 0
    metadata: dict[str, Any] = field(default_factory=dict)

    # Optional content capture (off by default: prompts can be large and sensitive).
    input: Any = None
    output: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None and v != {}}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CallRecord:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
