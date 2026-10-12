"""llmkit CLI.

    llmkit report                          # spend by project, all time, from ~/.llmkit/usage.jsonl
    llmkit report --by project component   # finer grouping (any CallRecord field)
    llmkit report --since 2026-10-01 --project ags
    llmkit prices                          # the price table in force
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from llmkit import pricing
from llmkit.report import aggregate, read_records, render
from llmkit.sinks import DEFAULT_JSONL


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="llmkit")
    sub = parser.add_subparsers(dest="cmd", required=True)
    rep = sub.add_parser("report", help="aggregate local usage")
    rep.add_argument("--file", type=Path, default=DEFAULT_JSONL)
    rep.add_argument("--by", nargs="+", default=["project"])
    rep.add_argument("--since", type=date.fromisoformat)
    rep.add_argument("--until", type=date.fromisoformat)
    rep.add_argument("--project")
    sub.add_parser("prices", help="print the price table")
    args = parser.parse_args(argv)

    if args.cmd == "report":
        by = tuple(args.by)
        rows = aggregate(read_records(args.file), by, args.since, args.until, args.project)
        print(render(rows, by) if rows else f"no records in {args.file}")
    else:
        print(f"verified on {pricing.PRICING_VERIFIED_ON} (USD / MTok)")
        for prefix, p in sorted(pricing._TABLE.items()):
            columns = [("", p.rates)] if p.off_peak is None else [
                ("peak", p.rates), ("off-peak", p.off_peak)]
            for label, r in columns:
                print(f"  {prefix:22} {label:8} in {r.input:>6} out {r.output:>6} "
                      f"cache_read {r.cache_read:>6}")


if __name__ == "__main__":
    main()
