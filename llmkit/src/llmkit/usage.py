"""Provider usage objects normalised into one shape.

Every provider reports tokens differently. The one rule here: the four billed buckets are
disjoint, so cost = sum(bucket * rate) with no double counting.

  input_tokens        uncached input, billed at the full input rate
  cache_read_tokens   input served from the provider's prompt cache
  cache_write_tokens  input written to the prompt cache (Anthropic only)
  output_tokens       completion tokens, reasoning included
  reasoning_tokens    the reasoning share of output_tokens (informational, not billed twice)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _get(obj: Any, name: str) -> Any:
    """Read a field from an SDK object or a plain dict (raw HTTP responses)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    @classmethod
    def from_openai(cls, usage: Any) -> Usage:
        """OpenAI-compatible `usage` (SDK object or dict). Handles DeepSeek's cache fields.

        DeepSeek reports prompt_cache_hit_tokens / prompt_cache_miss_tokens alongside
        prompt_tokens (= hit + miss). OpenAI reports prompt_tokens_details.cached_tokens.
        """
        if usage is None:
            return cls()
        prompt = _get(usage, "prompt_tokens") or 0
        completion = _get(usage, "completion_tokens") or 0
        hit = _get(usage, "prompt_cache_hit_tokens")
        if hit is not None:
            miss = _get(usage, "prompt_cache_miss_tokens")
            cached = hit
            uncached = miss if miss is not None else prompt - hit
        else:
            cached = _get(_get(usage, "prompt_tokens_details"), "cached_tokens") or 0
            uncached = prompt - cached
        reasoning = _get(_get(usage, "completion_tokens_details"), "reasoning_tokens") or 0
        return cls(
            input_tokens=uncached,
            output_tokens=completion,
            cache_read_tokens=cached,
            reasoning_tokens=reasoning,
        )

    @classmethod
    def from_anthropic(cls, usage: Any) -> Usage:
        """Anthropic Messages `usage`: input_tokens already excludes cached tokens."""
        if usage is None:
            return cls()
        return cls(
            input_tokens=_get(usage, "input_tokens") or 0,
            output_tokens=_get(usage, "output_tokens") or 0,
            cache_read_tokens=_get(usage, "cache_read_input_tokens") or 0,
            cache_write_tokens=_get(usage, "cache_creation_input_tokens") or 0,
        )
