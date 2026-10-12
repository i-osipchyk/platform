# llmkit

The one place every `~/dev` project calls an LLM through. Each call becomes a priced `CallRecord`
attributed to **project / env / component**, sent to one or more sinks.

Core is stdlib-only (drops into any Lambda image). `llmkit[openai]` adds the chat client.

## Install

```toml
# pyproject.toml
dependencies = ["llmkit[openai]"]

[tool.uv.sources]
llmkit = { git = "https://github.com/i-osipchyk/platform", subdirectory = "llmkit", tag = "v0.1.1" }
```

While iterating on llmkit itself, point a project at the working copy instead:
`llmkit = { path = "../platform/llmkit", editable = true }`. Don't commit that, because a Docker build
can't see outside the project.

## Use

```python
from llmkit import LLM

llm = LLM()  # project / env from LLMKIT_PROJECT / LLMKIT_ENV (the lambda-app module sets both)

with llm.trace("corn-cycle"):                    # optional: group one run's calls
    r = llm.chat(
        model="deepseek-flash",
        component="news-agent",                  # required: what part of the project spent it
        prompt_id="news_v3",                     # optional: versioned prompt file
        messages=[...], tools=[...], temperature=0,   # passed straight to chat.completions.create
    )
r.message        # SDK message (tool_calls, content)
r.text           # content or ""
r.record         # CallRecord: tokens, cost_usd, trace_id, ...

llm.flush()      # at the end of a Lambda handler (only matters with batching sinks)
```

Calling an SDK yourself (Anthropic, raw HTTP)? Report it:

```python
from datetime import UTC, datetime
from llmkit import Tracker, Usage

tracker = Tracker()
started = datetime.now(UTC)
resp = anthropic_client.messages.create(...)
tracker.record(provider="anthropic", model=resp.model, component="advisor",
               usage=Usage.from_anthropic(resp.usage), started_at=started,
               latency_ms=(datetime.now(UTC) - started).total_seconds() * 1000)
```

## Configuration (environment)

| Var | Default | |
|---|---|---|
| `LLMKIT_PROJECT` | (required) | project slug, the same one used in AWS names and tags |
| `LLMKIT_ENV` | `local` | `local` / `demo` / `live` |
| `LLMKIT_SINKS` | auto | `jsonl,stdout,langfuse` or `none`. Auto picks `stdout` on Lambda, `jsonl` elsewhere, and adds `langfuse` when its keys are set |
| `LLMKIT_JSONL_PATH` | `~/.llmkit/usage.jsonl` | one file shared by every project |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | none / none / EU cloud | |
| `LLMKIT_LANGFUSE_BATCH` | `1` | raise it for backtests that make thousands of calls |
| `LLMKIT_CAPTURE_CONTENT` | off | also send prompts and outputs (Langfuse, JSONL) |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` | none / api.deepseek.com | |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | | |

## CLI

```bash
uvx --from ./llmkit llmkit report                        # local spend by project
uvx --from ./llmkit llmkit report --by project component model --since 2026-10-01
uvx --from ./llmkit llmkit prices
```

## Pricing

`src/llmkit/pricing.py` is the only price table. DeepSeek charges **by time of day**: peak is
01:00–04:00 and 06:00–10:00 UTC on weekdays, and every other hour costs half. Each call is priced
at its start time. A model missing from the table is recorded with `cost_usd: null` and counted
as "unpriced" in reports, rather than being guessed. When prices change, update the table, bump
`PRICING_VERIFIED_ON`, and release a new platform version.

## Develop

```bash
cd llmkit && uv sync && uv run pytest && uv run ruff check . && uv run mypy
```
