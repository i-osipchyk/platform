"""The one price table for every project. USD per million tokens.

Matching is LONGEST PREFIX, so "claude-opus-4-8" never falls into the "claude-opus-4" row and
dated snapshots still resolve. There is deliberately no catch-all: an unknown model prices as
None ("unpriced") instead of silently borrowing another model's rates.

DeepSeek bills by time of day: peak is 01:00-04:00 and 06:00-10:00 UTC, Monday-Friday, and
everything else is off-peak (half price; both columns are stored as documented). Calls are
priced at their request start time. Chinese public
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


@dataclass(frozen=True, slots=True)
class Price:
    rates: Rates                       # flat price, or the PEAK price when off_peak is set
    off_peak: Rates | None = None      # price outside peak hours, exactly as documented
    is_peak: Callable[[datetime], bool] | None = None  # request start (UTC) -> peak?

    def rates_at(self, at: datetime) -> Rates:
        if self.off_peak is None or self.is_peak is None or self.is_peak(at):
            return self.rates
        return self.off_peak


def deepseek_is_peak(at: datetime) -> bool:
    """DeepSeek peak: 01:00-04:00 and 06:00-10:00 UTC, Monday-Friday. `at` is aware UTC."""
    return at.weekday() < 5 and (1 <= at.hour < 4 or 6 <= at.hour < 10)


def _deepseek(peak: Rates, off_peak: Rates) -> Price:
    return Price(peak, off_peak, deepseek_is_peak)


# DeepSeek, from https://api-docs.deepseek.com/quick_start/pricing (verified 2026-10-12).
# Columns as documented, USD / 1M tokens: input cache miss, output, input cache hit.
_DEEPSEEK_FLASH = _deepseek(
    peak=Rates(input=0.30, output=1.20, cache_read=0.006),
    off_peak=Rates(input=0.15, output=0.60, cache_read=0.003),
)
_DEEPSEEK_V4_PRO = _deepseek(
    peak=Rates(input=1.32, output=3.96, cache_read=0.044),
    off_peak=Rates(input=0.66, output=1.98, cache_read=0.022),
)

_TABLE: dict[str, Price] = {
    # --- DeepSeek ----------------------------------------------------------------------------
    "deepseek-flash": _DEEPSEEK_FLASH,
    # Legacy names the API still accepts for flash (vision-exp is covered by the prefix).
    "deepseek-v4-flash": _DEEPSEEK_FLASH,
    "deepseek-v4-pro": _DEEPSEEK_V4_PRO,
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
