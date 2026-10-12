# LLM observability and cost per project

Goal: for any period, know **how much each project spent on LLMs**, and inside a project which
component, model and prompt drove it. One rule makes that possible:

> **Every LLM call goes through llmkit, with a `component`.**

llmkit turns each call into a `CallRecord` with project, env, component, model, tokens (split
into uncached / cache read / cache write / output), cost, latency, trace and prompt id, and sends
it to sinks. See `../llmkit/README.md` for the API.

## Where the numbers end up

| Sink | When it's on | What it gives you |
|---|---|---|
| **JSONL** `~/.llmkit/usage.jsonl` | local runs (default) | one file for all projects. Run `llmkit report` for a table by project / component / model |
| **stdout → CloudWatch Logs** | on Lambda (default) | durable copy next to the function logs. Logs Insights queries below |
| **Langfuse** | whenever `LANGFUSE_*_KEY` is set | one dashboard for local and AWS runs: cost by project (tag), env, model, plus traces of agent loops |

### Recommended setup
1. Create a free [Langfuse Cloud](https://cloud.langfuse.com) account (EU region) with **one Langfuse
   project for all of ~/dev**. llmkit tags each trace with the project slug, so a single dashboard
   can split by project. Separate Langfuse projects would hide the total.
2. Put the keys in your shell profile (local runs) and in SSM `/platform/shared/langfuse-*` (Lambda,
   see `deployment.md`). `config.load_env()` loads them on Lambda.
3. In Langfuse, build a dashboard: *Cost* grouped by *Tags* (= project), filtered by *Environment*.
4. Backtests making thousands of calls: set `LLMKIT_LANGFUSE_BATCH=50` so they don't send one
   request per call. Check the free tier's monthly event limit against hermes's volume.

llmkit sends its **own** cost (`cost_details`). Langfuse never re-prices, so Langfuse,
JSONL and CloudWatch always agree.

Langfuse's legacy ingestion API is retired on 2026-11-16. llmkit uses the OpenTelemetry endpoint
(`/api/public/otel/v1/traces`) over plain HTTP, so it isn't affected and has no SDK to keep updated.

## Questions → answers

**What did each project cost this month (local runs)?**
```bash
uvx --from ~/dev/platform/llmkit llmkit report --since 2026-10-01
```

**…and on AWS?** CloudWatch → Logs Insights, select the log groups `/aws/lambda/*`:
```
fields @timestamp
| filter ispresent(llmkit.cost_usd)
| stats sum(llmkit.cost_usd) as usd, count(*) as calls by llmkit.project, llmkit.env
| sort usd desc
```
By component and model inside one project:
```
filter llmkit.project = "ags"
| stats sum(llmkit.cost_usd) as usd, sum(llmkit.input_tokens) as uncached_in,
        sum(llmkit.cache_read_tokens) as cached_in, sum(llmkit.output_tokens) as out
  by llmkit.component, llmkit.model
| sort usd desc
```
Calls whose model has no price (they'd be missing from the totals):
```
filter ispresent(llmkit.model) and not ispresent(llmkit.cost_usd) and not ispresent(llmkit.error)
| stats count(*) by llmkit.project, llmkit.model
```

**Total cost of a project** = LLM (above) + AWS (Cost Explorer → group by tag `project`). The
bootstrap budgets alert on the AWS part. Langfuse can alert on the LLM part.

**Are llmkit's numbers right?** Use one DeepSeek API key per project, then compare the DeepSeek
console's usage for that key with the project's llmkit total. Expect small differences:
- llmkit doesn't model Chinese public holidays, which are off-peak at DeepSeek, so it overstates
  cost on those days
- calls made outside llmkit won't show up

## Pricing upkeep

- `llmkit/src/llmkit/pricing.py` is the only price table. Unknown models are recorded as **unpriced**
  (never guessed), and `llmkit report` has an `unpriced` column.
- DeepSeek prices **by time of day**: weekday 01:00–04:00 and 06:00–10:00 UTC are peak, and every
  other hour costs half. llmkit prices each call at its start time. Batch jobs scheduled
  off-peak cost half, so choose schedules with this in mind.
- When a provider changes prices or you adopt a new model: update the table, bump
  `PRICING_VERIFIED_ON`, release a platform tag, and bump projects. Already-recorded calls keep the
  cost they were recorded with.

## Rules for project code

- One module per project (`llm.py`) owns the `LLM()` instance. Nothing else imports `openai` or
  `anthropic` directly.
- `component` names a stable part of the system (`news-agent`, `screener`, `advisor`), not
  a per-call value. Put per-call details (market id, commodity) in `metadata=`.
- Wrap one logical run in `llm.trace("<run name>")` so agent loops show up as one trace.
- Lambda handlers call `llm.flush()` in a `finally` block.
- Content capture (`LLMKIT_CAPTURE_CONTENT=1`) is off by default. Turn it on locally when
  debugging prompts. Projects that already audit full prompts (bondhype's `llm_calls/`, ags's
  run logs) keep doing so; llmkit replaces only their usage and cost tracking.
