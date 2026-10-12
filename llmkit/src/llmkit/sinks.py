"""Where CallRecords go. A sink must never break the caller: failures are logged, not raised.

  JsonlSink     appends to one local file shared by every project (default ~/.llmkit/usage.jsonl),
                so `llmkit report` shows local spend across all of ~/dev
  StdoutSink    one JSON line per call; on Lambda that lands in CloudWatch Logs and is queryable
                with Logs Insights across every function (docs/llm-observability.md)
  LangfuseSink  OTLP/HTTP JSON to Langfuse's OpenTelemetry endpoint (no SDK dependency)

default_sinks() picks from the environment; see its docstring.
"""

from __future__ import annotations

import atexit
import base64
import json
import logging
import os
import sys
import threading
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from llmkit.record import CallRecord

logger = logging.getLogger(__name__)

DEFAULT_JSONL = Path.home() / ".llmkit" / "usage.jsonl"


class Sink(Protocol):
    def emit(self, record: CallRecord) -> None: ...
    def flush(self) -> None: ...


class JsonlSink:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.getenv("LLMKIT_JSONL_PATH") or DEFAULT_JSONL).expanduser()
        self._lock = threading.Lock()

    def emit(self, record: CallRecord) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record.to_dict(), default=str)
            with self._lock, self.path.open("a") as f:
                f.write(line + "\n")
        except OSError as exc:
            logger.warning("llmkit: JsonlSink failed: %s", exc)

    def flush(self) -> None:
        pass


class StdoutSink:
    """Prints {"llmkit": {...}}; the wrapper key makes the lines easy to filter in Logs Insights."""

    def emit(self, record: CallRecord) -> None:
        d = record.to_dict()
        d.pop("input", None)
        d.pop("output", None)
        print(json.dumps({"llmkit": d}, default=str), file=sys.stdout, flush=True)

    def flush(self) -> None:
        pass


def _attr(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        v: dict[str, Any] = {"boolValue": value}
    elif isinstance(value, int):
        v = {"intValue": str(value)}
    elif isinstance(value, float):
        v = {"doubleValue": value}
    elif isinstance(value, list):
        v = {"arrayValue": {"values": [{"stringValue": str(x)} for x in value]}}
    else:
        v = {"stringValue": value if isinstance(value, str) else json.dumps(value, default=str)}
    return {"key": key, "value": v}


def _nanos(iso: str, plus_ms: float = 0.0) -> str:
    ts = datetime.fromisoformat(iso).timestamp() + plus_ms / 1000
    return str(int(ts * 1_000_000_000))


class LangfuseSink:
    """Sends each call as a Langfuse "generation" span over OTLP/HTTP JSON.

    Langfuse attribution: tag = project, environment = env, trace name = trace or component.
    Cost is sent explicitly (cost_details) so Langfuse never re-prices with its own table.
    Spans are buffered up to `batch_size` and flushed at exit; Lambda handlers should call
    Tracker.flush() before returning (or use batch_size=1, the default).
    """

    def __init__(
        self,
        public_key: str,
        secret_key: str,
        host: str = "https://cloud.langfuse.com",
        batch_size: int = 1,
        timeout: float = 5.0,
    ) -> None:
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self._url = host.rstrip("/") + "/api/public/otel/v1/traces"
        self._headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "x-langfuse-ingestion-version": "4",
        }
        self._batch_size = batch_size
        self._timeout = timeout
        self._buffer: list[CallRecord] = []
        self._lock = threading.Lock()
        atexit.register(self.flush)

    def emit(self, record: CallRecord) -> None:
        with self._lock:
            self._buffer.append(record)
            full = len(self._buffer) >= self._batch_size
        if full:
            self.flush()

    def flush(self) -> None:
        with self._lock:
            batch, self._buffer = self._buffer, []
        if not batch:
            return
        body = json.dumps(self._payload(batch)).encode()
        request = urllib.request.Request(self._url, data=body, headers=self._headers)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                response.read()
        except Exception as exc:  # noqa: BLE001 - a sink must never break the caller
            logger.warning("llmkit: Langfuse export of %d span(s) failed: %s", len(batch), exc)

    def _payload(self, batch: list[CallRecord]) -> dict[str, Any]:
        by_project: dict[str, list[dict[str, Any]]] = {}
        for r in batch:
            by_project.setdefault(r.project, []).append(self._span(r))
        return {
            "resourceSpans": [
                {
                    "resource": {"attributes": [_attr("service.name", project)]},
                    "scopeSpans": [{"scope": {"name": "llmkit"}, "spans": spans}],
                }
                for project, spans in by_project.items()
            ]
        }

    @staticmethod
    def _span(r: CallRecord) -> dict[str, Any]:
        usage = {
            "input": r.input_tokens,
            "output": r.output_tokens,
            "input_cache_read": r.cache_read_tokens,
            "input_cache_write": r.cache_write_tokens,
        }
        attrs = [
            _attr("langfuse.observation.type", "generation"),
            _attr("langfuse.observation.model.name", r.model),
            _attr("langfuse.observation.usage_details", usage),
            _attr("langfuse.environment", r.env),
            _attr("langfuse.trace.name", r.trace_name or r.component),
            _attr("langfuse.trace.tags", [r.project]),
            _attr("langfuse.trace.metadata.project", r.project),
            _attr("langfuse.observation.metadata.component", r.component),
            _attr("langfuse.observation.metadata.provider", r.provider),
        ]
        if r.cost_breakdown is not None:
            attrs.append(_attr("langfuse.observation.cost_details", r.cost_breakdown))
        if r.prompt_id:
            attrs.append(_attr("langfuse.observation.metadata.prompt_id", r.prompt_id))
        for k, v in r.metadata.items():
            attrs.append(_attr(f"langfuse.observation.metadata.{k}", v))
        if r.input is not None:
            attrs.append(_attr("langfuse.observation.input", r.input))
        if r.output is not None:
            attrs.append(_attr("langfuse.observation.output", r.output))
        span: dict[str, Any] = {
            "traceId": r.trace_id,
            "spanId": r.span_id,
            "name": r.component,
            "kind": 3,  # CLIENT
            "startTimeUnixNano": _nanos(r.started_at),
            "endTimeUnixNano": _nanos(r.started_at, r.latency_ms),
            "attributes": attrs,
        }
        if r.error:
            attrs.append(_attr("langfuse.observation.level", "ERROR"))
            attrs.append(_attr("langfuse.observation.status_message", r.error))
            span["status"] = {"code": 2, "message": r.error}
        return span


def default_sinks() -> list[Sink]:
    """Sinks chosen from the environment.

    LLMKIT_SINKS=jsonl,stdout,langfuse  explicit list (comma separated); "none" disables all.
    Otherwise: stdout on Lambda (AWS_LAMBDA_FUNCTION_NAME set), jsonl elsewhere, plus langfuse
    whenever LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set (LANGFUSE_HOST optional).
    """
    explicit = os.getenv("LLMKIT_SINKS")
    langfuse_ready = bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))
    if explicit is not None:
        names = {n.strip() for n in explicit.split(",") if n.strip() and n.strip() != "none"}
    else:
        names = {"stdout" if os.getenv("AWS_LAMBDA_FUNCTION_NAME") else "jsonl"}
        if langfuse_ready:
            names.add("langfuse")
    sinks: list[Sink] = []
    if "jsonl" in names:
        sinks.append(JsonlSink())
    if "stdout" in names:
        sinks.append(StdoutSink())
    if "langfuse" in names:
        if langfuse_ready:
            sinks.append(
                LangfuseSink(
                    os.environ["LANGFUSE_PUBLIC_KEY"],
                    os.environ["LANGFUSE_SECRET_KEY"],
                    os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
                    batch_size=int(os.getenv("LLMKIT_LANGFUSE_BATCH", "1")),
                )
            )
        else:
            logger.warning("llmkit: langfuse sink requested but LANGFUSE_*_KEY is not set")
    return sinks
