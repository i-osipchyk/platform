import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from llmkit import LLM, CallRecord, JsonlSink, LangfuseSink, Tracker, Usage, cost
from llmkit.pricing import deepseek_is_peak, price_for
from llmkit.report import aggregate, read_records, render
from llmkit.sinks import default_sinks

PEAK = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)      # Monday 08:00 UTC
OFF_PEAK = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)  # Monday 12:00 UTC
WEEKEND = datetime(2026, 10, 11, 8, 0, tzinfo=UTC)    # Sunday


class ListSink:
    def __init__(self):
        self.records: list[CallRecord] = []

    def emit(self, record):
        self.records.append(record)

    def flush(self):
        pass


# --- usage --------------------------------------------------------------------------------


def test_deepseek_usage_splits_cache_hits_from_misses():
    u = Usage.from_openai({"prompt_tokens": 1000, "completion_tokens": 50,
                           "prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 200})
    assert (u.input_tokens, u.cache_read_tokens, u.output_tokens) == (200, 800, 50)
    assert u.total_input_tokens == 1000


def test_openai_usage_reads_cached_and_reasoning_details():
    u = Usage.from_openai(SimpleNamespace(
        prompt_tokens=1000, completion_tokens=300,
        prompt_tokens_details=SimpleNamespace(cached_tokens=600),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=200),
    ))
    assert (u.input_tokens, u.cache_read_tokens, u.output_tokens, u.reasoning_tokens) == (
        400, 600, 300, 200)


def test_anthropic_usage_keeps_buckets_disjoint():
    u = Usage.from_anthropic({"input_tokens": 10, "output_tokens": 5,
                              "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20})
    assert (u.input_tokens, u.cache_read_tokens, u.cache_write_tokens) == (10, 100, 20)


def test_missing_usage_is_zero():
    assert Usage.from_openai(None) == Usage()


# --- pricing ------------------------------------------------------------------------------


@pytest.mark.parametrize(("at", "peak"), [
    (datetime(2026, 10, 12, 0, 59, tzinfo=UTC), False),
    (datetime(2026, 10, 12, 1, 0, tzinfo=UTC), True),
    (datetime(2026, 10, 12, 3, 59, tzinfo=UTC), True),
    (datetime(2026, 10, 12, 4, 0, tzinfo=UTC), False),
    (datetime(2026, 10, 12, 6, 0, tzinfo=UTC), True),
    (datetime(2026, 10, 12, 9, 59, tzinfo=UTC), True),
    (datetime(2026, 10, 12, 10, 0, tzinfo=UTC), False),
    (datetime(2026, 10, 16, 8, 0, tzinfo=UTC), True),   # Friday
    (WEEKEND, False),
])
def test_deepseek_peak_windows(at, peak):
    assert deepseek_is_peak(at) is peak


# Documented rates, USD / 1M tokens: (cache miss, cache hit, output) peak / off-peak.
DEEPSEEK_DOCS = {
    "deepseek-flash": ((0.30, 0.006, 1.20), (0.15, 0.003, 0.60)),
    "deepseek-v4-flash": ((0.30, 0.006, 1.20), (0.15, 0.003, 0.60)),
    "deepseek-v4-flash-vision-exp": ((0.30, 0.006, 1.20), (0.15, 0.003, 0.60)),
    "deepseek-v4-pro": ((1.32, 0.044, 3.96), (0.66, 0.022, 1.98)),
}


@pytest.mark.parametrize("model", DEEPSEEK_DOCS)
@pytest.mark.parametrize(("at", "column"), [(PEAK, 0), (OFF_PEAK, 1), (WEEKEND, 1)])
def test_deepseek_rates_match_docs(model, at, column):
    miss, hit, out = DEEPSEEK_DOCS[model][column]
    one_m = 1_000_000
    assert cost(model, Usage(input_tokens=one_m), at).total == pytest.approx(miss)
    assert cost(model, Usage(cache_read_tokens=one_m), at).total == pytest.approx(hit)
    assert cost(model, Usage(output_tokens=one_m), at).total == pytest.approx(out)


def test_longest_prefix_wins_and_unknown_is_unpriced():
    assert price_for("claude-opus-4-8-20260101").rates.input == 5.00
    assert price_for("claude-opus-4-1").rates.input == 15.00
    assert cost("deepseek-chat", Usage(input_tokens=1), PEAK) is None


def test_anthropic_cache_write_rate():
    c = cost("claude-sonnet-4-6", Usage(cache_write_tokens=1_000_000), PEAK)
    assert c.cache_write == pytest.approx(3.75)


# --- tracker ------------------------------------------------------------------------------


def test_tracker_requires_a_project(monkeypatch):
    monkeypatch.delenv("LLMKIT_PROJECT", raising=False)
    with pytest.raises(ValueError):
        Tracker(sinks=[])


def test_tracker_attributes_prices_and_groups_by_trace():
    sink = ListSink()
    t = Tracker("ags", "demo", sinks=[sink])
    with t.trace("corn-cycle") as trace_id:
        for component in ("news", "technical"):
            t.record(provider="deepseek", model="deepseek-flash", component=component,
                     usage=Usage(input_tokens=1000, output_tokens=100),
                     started_at=OFF_PEAK, latency_ms=12.3)
    t.record(provider="deepseek", model="deepseek-flash", component="solo", usage=Usage(),
             started_at=OFF_PEAK, latency_ms=1)
    a, b, solo = sink.records
    assert (a.project, a.env, a.component) == ("ags", "demo", "news")
    assert a.trace_id == b.trace_id == trace_id and a.trace_name == "corn-cycle"
    assert solo.trace_id != trace_id and solo.trace_name is None
    assert a.cost_usd == pytest.approx((1000 * 0.15 + 100 * 0.6) / 1e6)


def test_content_is_dropped_unless_captured():
    sink = ListSink()
    Tracker("p", sinks=[sink]).record(provider="x", model="m", component="c", usage=Usage(),
                                      started_at=PEAK, latency_ms=1, input="secret")
    assert sink.records[0].input is None


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        Tracker("p", sinks=[]).record(provider="x", model="m", component="c", usage=Usage(),
                                      started_at=datetime(2026, 1, 1), latency_ms=1)


# --- LLM.chat -----------------------------------------------------------------------------


class FakeCompletions:
    def __init__(self, fail=False):
        self.fail = fail
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.fail:
            raise RuntimeError("boom")
        return SimpleNamespace(
            model=kwargs["model"],
            choices=[SimpleNamespace(message=SimpleNamespace(content="hi", tool_calls=None))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2,
                                  prompt_cache_hit_tokens=4, prompt_cache_miss_tokens=6),
        )


def fake_client(fail=False):
    completions = FakeCompletions(fail)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def test_chat_records_and_passes_kwargs_through():
    sink = ListSink()
    client, completions = fake_client()
    llm = LLM("bondhype", "live", sinks=[sink], clients={"deepseek": client})
    result = llm.chat(model="deepseek-flash", component="screener", prompt_id="reject_v1",
                      messages=[{"role": "user", "content": "x"}], temperature=0)
    assert result.text == "hi"
    assert completions.kwargs["temperature"] == 0
    r = sink.records[0]
    assert (r.provider, r.input_tokens, r.cache_read_tokens, r.prompt_id) == (
        "deepseek", 6, 4, "reject_v1")
    assert result.cost_usd == r.cost_usd


def test_chat_records_failures_then_reraises():
    sink = ListSink()
    client, _ = fake_client(fail=True)
    llm = LLM("p", sinks=[sink], clients={"deepseek": client})
    with pytest.raises(RuntimeError):
        llm.chat(model="deepseek-flash", component="c", messages=[])
    assert "boom" in sink.records[0].error


def test_unknown_provider_needs_explicit_name():
    with pytest.raises(ValueError):
        LLM("p", sinks=[]).chat(model="mystery-1", component="c", messages=[])


# --- sinks & report -----------------------------------------------------------------------


def test_jsonl_roundtrip_and_report(tmp_path):
    path = tmp_path / "usage.jsonl"
    t = Tracker("ags", sinks=[JsonlSink(path)])
    t.record(provider="deepseek", model="deepseek-flash", component="news",
             usage=Usage(input_tokens=1_000_000), started_at=PEAK, latency_ms=1)
    Tracker("gtb", sinks=[JsonlSink(path)]).record(
        provider="deepseek", model="deepseek-chat", component="x", usage=Usage(),
        started_at=PEAK, latency_ms=1)
    rows = aggregate(read_records(path), ("project",))
    assert rows[("ags",)].cost_usd == pytest.approx(0.30)
    assert rows[("gtb",)].unpriced == 1
    assert "TOTAL" in render(rows, ("project",))


def test_langfuse_payload_shape():
    sink = LangfuseSink("pk", "sk", batch_size=100)  # never reaches the batch size: no send
    t = Tracker("ags", "demo", sinks=[sink])
    rec = t.record(provider="deepseek", model="deepseek-flash", component="news",
                   usage=Usage(input_tokens=5), started_at=PEAK, latency_ms=100)
    payload = sink._payload([rec])
    span = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    attrs = {a["key"]: a["value"] for a in span["attributes"]}
    assert len(span["traceId"]) == 32 and len(span["spanId"]) == 16
    assert attrs["langfuse.observation.type"] == {"stringValue": "generation"}
    assert json.loads(attrs["langfuse.observation.usage_details"]["stringValue"])["input"] == 5
    assert attrs["langfuse.environment"] == {"stringValue": "demo"}
    assert int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"]) == 100_000_000
    sink._buffer.clear()  # or the atexit flush would really send it


def test_default_sinks(monkeypatch, tmp_path):
    for k in ("LLMKIT_SINKS", "AWS_LAMBDA_FUNCTION_NAME", "LANGFUSE_PUBLIC_KEY",
              "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert [type(s).__name__ for s in default_sinks()] == ["JsonlSink"]
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "f")
    assert [type(s).__name__ for s in default_sinks()] == ["StdoutSink"]
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    assert {type(s).__name__ for s in default_sinks()} == {"StdoutSink", "LangfuseSink"}
    monkeypatch.setenv("LLMKIT_SINKS", "jsonl")
    assert [type(s).__name__ for s in default_sinks()] == ["JsonlSink"]
    monkeypatch.setenv("LLMKIT_SINKS", "none")
    assert default_sinks() == []
