"""Aggregate a usage JSONL (default ~/.llmkit/usage.jsonl) into spend tables."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from llmkit.record import CallRecord


def read_records(path: Path) -> Iterator[CallRecord]:
    if not path.exists():
        return
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield CallRecord.from_dict(json.loads(line))


@dataclass
class Row:
    calls: int = 0
    errors: int = 0
    unpriced: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    models: set[str] = field(default_factory=set)


def aggregate(
    records: Iterable[CallRecord],
    by: tuple[str, ...] = ("project",),
    since: date | None = None,
    until: date | None = None,
    project: str | None = None,
) -> dict[tuple[str, ...], Row]:
    rows: dict[tuple[str, ...], Row] = defaultdict(Row)
    for r in records:
        day = date.fromisoformat(r.started_at[:10])
        if (since and day < since) or (until and day > until):
            continue
        if project and r.project != project:
            continue
        row = rows[tuple(str(getattr(r, k)) for k in by)]
        row.calls += 1
        row.errors += r.error is not None
        row.unpriced += r.cost_usd is None and r.error is None
        row.input_tokens += r.input_tokens
        row.output_tokens += r.output_tokens
        row.cache_read_tokens += r.cache_read_tokens
        row.cost_usd += r.cost_usd or 0.0
        row.models.add(r.model)
    return dict(rows)


def render(rows: dict[tuple[str, ...], Row], by: tuple[str, ...]) -> str:
    header = [*by, "calls", "errors", "unpriced", "in", "cached", "out", "cost_usd"]
    body = [
        [*key, str(r.calls), str(r.errors), str(r.unpriced), f"{r.input_tokens:,}",
         f"{r.cache_read_tokens:,}", f"{r.output_tokens:,}", f"{r.cost_usd:.4f}"]
        for key, r in sorted(rows.items(), key=lambda kv: -kv[1].cost_usd)
    ]
    total = sum(r.cost_usd for r in rows.values())
    body.append(["TOTAL", *[""] * (len(header) - 2), f"{total:.4f}"])
    widths = [max(len(row[i]) for row in [header, *body]) for i in range(len(header))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True))
                     for row in [header, *body])
