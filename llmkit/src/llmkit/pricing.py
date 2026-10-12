"""The one price table for every project. USD per million tokens.

Matching is LONGEST PREFIX, so "claude-opus-4-8" never falls into the "claude-opus-4" row and
dated snapshots still resolve. There is deliberately no catch-all: an unknown model prices as
None ("unpriced") instead of silently borrowing another model's rates.

DeepSeek bills by time of day: peak is 01:00-04:00 and 06:00-10:00 UTC, Monday-Friday, and
everything else costs half. Calls are priced at their request start time. Chinese public
holidays (off-peak at DeepSeek) are not modelled, so those days slightly overstate cost.

Re-verify before trusting a figure, then bump PRICING_VERIFIED_ON:
  DeepSeek   https://api-docs.deepseek.com/quick_start/pricing
  Anthropic  https://docs.anthropic.com/en/docs/about-claude/pricing
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from llmkit.usage import Usage

logger = logging.getLogger(__name__)

PRICING_VERIFIED_ON = "2026-10-12"


@dataclass(frozen=True, slots=True)
class Rates:
    input: float
    output: float
    cache_read: float
    cache_write: float | None = None  # None: billed like uncached input

    def scaled(self, factor: float) -> Rates:
        return Rates(
            self.input * factor,
            self.output * factor,
            self.cache_read * factor,
            None if self.cache_write is None else self.cache_write * factor,
        )


@dataclass(frozen=True, slots=True)
class Price:
    rates: Rates
    # Returns the multiplier for a request started at `at` (UTC); None means flat pricing.
    time_factor: Callable[[datetime], float] | None = None

    def rates_at(self, at: datetime) -> Rates:
        return self.rates if self.time_factor is None else self.rates.scaled(self.time_factor(at))


def deepseek_time_factor(at: datetime) -> float:
    """1.0 in DeepSeek peak hours, 0.5 otherwise. `at` must be timezone-aware UTC."""
    if at.weekday() >= 5:
        return 0.5
    peak = 1 <= at.hour < 4 or 6 <= at.hour < 10
    return 1.0 if peak else 0.5


_DS = deepseek_time_factor

# Rates are the PEAK (full) price; time_factor scales them down off-peak.
_TABLE: dict[str, Price] = {
    # --- DeepSeek (verified 2026-10-12) ------------------------------------------------------
    "deepseek-flash": Price(Rates(input=0.30, output=1.20, cache_read=0.006), _DS),
    "deepseek-v4-flash": Price(Rates(input=0.30, output=1.20, cache_read=0.006), _DS),
    "deepseek-v4-pro": Price(Rates(input=1.32, output=3.96, cache_read=0.044), _DS),
    # deepseek-chat / deepseek-reasoner are no longer on the pricing page: left unpriced.
    # --- Anthropic (from hermes, verified 2026-09-30) ----------------------------------------
    "claude-fable-5-1": Price(Rates(10.00, 50.00, 0.25, 12.50)),
    "claude-fable-5": Price(Rates(10.00, 50.00, 1.00, 12.50)),
    "claude-opus-5": Price(Rates(5.00, 25.00, 0.50, 6.25)),
    "claude-sonnet-5": Price(Rates(2.00, 10.00, 0.20, 2.50)),
    "claude-opus-4-8": Price(Rates(5.00, 25.00, 0.50, 6.25)),
    "claude-opus-4-7": Price(Rates(5.00, 25.00, 0.50, 6.25)),
    "claude-opus-4-6": Price(Rates(5.00, 25.00, 0.50, 6.25)),
    "claude-sonnet-4-6": Price(Rates(3.00, 15.00, 0.30, 3.75)),
    "claude-haiku-4-5": Price(Rates(1.00, 5.00, 0.10, 1.25)),
    "claude-opus-4": Price(Rates(15.00, 75.00, 1.50, 18.75)),
    "claude-sonnet-4": Price(Rates(3.00, 15.00, 0.30, 3.75)),
    "claude-haiku-4": Price(Rates(0.80, 4.00, 0.08, 1.00)),
}

_warned: set[str] = set()


def register(prefix: str, price: Price) -> None:
    """Add or override a row at runtime (e.g. a model released after this package)."""
    _TABLE[prefix.lower()] = price


def price_for(model: str) -> Price | None:
    lower = model.lower()
    best = max((p for p in _TABLE if lower.startswith(p)), key=len, default=None)
    return None if best is None else _TABLE[best]


@dataclass(frozen=True, slots=True)
class Cost:
    input: float
    output: float
    cache_read: float
    cache_write: float

    @property
    def total(self) -> float:
        return self.input + self.output + self.cache_read + self.cache_write


def cost(model: str, usage: Usage, at: datetime) -> Cost | None:
    """USD cost of one call, or None when the model is not in the table."""
    price = price_for(model)
    if price is None:
        if model not in _warned:
            _warned.add(model)
            logger.warning("llmkit: no price for model %r; its calls are recorded unpriced", model)
        return None
    r = price.rates_at(at)
    per = 1_000_000
    write_rate = r.input if r.cache_write is None else r.cache_write
    return Cost(
        input=usage.input_tokens * r.input / per,
        output=usage.output_tokens * r.output / per,
        cache_read=usage.cache_read_tokens * r.cache_read / per,
        cache_write=usage.cache_write_tokens * write_rate / per,
    )
