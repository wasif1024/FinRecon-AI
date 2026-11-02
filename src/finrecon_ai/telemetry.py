"""OpenTelemetry (SRS §9): traces and metrics for the API, the workflows and the database, exported over OTLP.

Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set (e.g. http://localhost:4318; `scripts/start.sh --otel` runs a local
Grafana stack). The other standard variables apply as usual: OTEL_EXPORTER_OTLP_HEADERS, OTEL_RESOURCE_ATTRIBUTES,
OTEL_TRACES_SAMPLER ...

This is the service view: every REST, Connect and gRPC request, database query and outbound HTTP call (Claude,
LangSmith, the Agent Server), with durations and errors, plus counters for workflows and agent runs. LangSmith
stays the agent view (prompts, tool calls, evaluations). Neither records request bodies, SQL parameters or prompt
text here: spans carry ids, routes, statuses and counts. Log lines carry the trace and span id, so a log line leads
to its trace.
"""

import os
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.sdk.trace import SpanProcessor

from finrecon_ai.log import get_logger

log = get_logger(__name__)

INSTRUMENTATION = "finrecon"
_configured = False

tracer = trace.get_tracer(INSTRUMENTATION)
meter = metrics.get_meter(INSTRUMENTATION)

# Instruments are no-ops until a MeterProvider is configured, so recording them is always safe.
workflow_runs = meter.create_counter(
    "finrecon.workflow.runs", unit="{run}",
    description="Case workflow runs by how they ended (paused_for_approval, finished, failed)")
agent_runs = meter.create_counter("finrecon.agent.runs", unit="{run}", description="Agent runs by agent and status")
agent_tokens = meter.create_counter("finrecon.agent.tokens", unit="{token}",
                                    description="Claude tokens used by agent runs, by agent, model and direction")
agent_duration = meter.create_histogram("finrecon.agent.duration", unit="s", description="Agent run duration")


def configure_telemetry(settings, service_name: str) -> bool:
    """Set up exporting once per process; returns True when telemetry is on. Call before the first database
    connection opens so its queries are traced."""
    global _configured
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or settings.otel_exporter_otlp_endpoint
    if _configured or not endpoint:
        return _configured
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = endpoint  # the exporters read it from the environment

    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME") or service_name,
                                "service.namespace": "finrecon", "service.version": _version(),
                                "deployment.environment.name": settings.environment})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SkipLoneCalls(BatchSpanProcessor(OTLPSpanExporter())))
    trace.set_tracer_provider(provider)
    metrics.set_meter_provider(MeterProvider(resource=resource,
                                             metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())]))
    # At the driver, so LangGraph's checkpoint queries are traced as well as SQLAlchemy's (whose instrumentation
    # does not support SQLAlchemy 2.1). Spans hold the statement with placeholders, never the parameters.
    PsycopgInstrumentor().instrument(enable_commenter=False)
    HTTPXClientInstrumentor().instrument()
    _configured = True
    log.info("telemetry.enabled", endpoint=endpoint, service=resource.attributes["service.name"])
    return True


class SkipLoneCalls(SpanProcessor):
    """Exports every span except a client span with no parent: a query or outbound call made outside any request,
    job or command (connection setup, pool checks, the health check's probes), which would otherwise be a trace
    of its own and fill the trace list. Wraps the exporting processor."""

    def __init__(self, processor):
        self._processor = processor

    def on_start(self, span, parent_context=None):
        self._processor.on_start(span, parent_context)

    def _on_ending(self, span):
        self._processor._on_ending(span)

    def on_end(self, span):
        if span.parent is None and span.kind is trace.SpanKind.CLIENT:
            return
        self._processor.on_end(span)

    def shutdown(self):
        self._processor.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        return self._processor.force_flush(timeout_millis)


def instrument_app(app) -> None:
    """Server spans and HTTP metrics for every request, REST and RPC (the Connect apps are mounted on it)."""
    if not _configured:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    # No spans for each ASGI message ("http receive", "http send"): one server span per request.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health", exclude_spans=["receive", "send"])


def name_rpc_span(service: str, method: str) -> None:
    """Name the request's server span after the RPC, as the gRPC conventions do."""
    span = trace.get_current_span()
    if span.is_recording():
        span.update_name(f"{service}/{method}")
        span.set_attributes({"rpc.system": "connect_rpc", "rpc.service": service, "rpc.method": method})


def record_rpc_error(code: str) -> None:
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("rpc.connect_rpc.error_code", code)


def record_agent_run(agent: str, status: str, duration_s: float | None, usage_by_model: dict[str, Any]) -> None:
    attributes = {"agent": agent, "status": status}
    agent_runs.add(1, attributes)
    if duration_s is not None:
        agent_duration.record(duration_s, attributes)
    for model, usage in (usage_by_model or {}).items():
        for direction in ("input", "output"):
            tokens = int((usage or {}).get(f"{direction}_tokens") or 0)
            if tokens:
                agent_tokens.add(tokens, {"agent": agent, "model": model, "direction": direction})


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("finrecon-ai")
    except PackageNotFoundError:
        return "unknown"
