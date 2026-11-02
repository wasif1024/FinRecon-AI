"""OpenTelemetry: request, workflow, agent and query spans, metrics and log correlation (in-memory exporters)."""

import io
import json
import logging

import pytest
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from finrecon.v1 import cases_pb2 as cs
from finrecon_ai import telemetry
from finrecon_ai.config import Settings
from finrecon_ai.db.enums import ExceptionCategory, RoleName
from finrecon_ai.log import JsonFormatter, get_logger
from tests.fakes import AgentScripts
from tests.test_api import approver_headers, auth, client, scripts, store  # noqa: F401 (fixtures)
from tests.test_approvals import proposal_steps
from tests.test_rpc import call, inline_background  # noqa: F401 (fixture)
from tests.test_workflows import case_of, investigation_steps

SPANS = InMemorySpanExporter()
METRICS = InMemoryMetricReader()


@pytest.fixture(scope="module", autouse=True)
def providers():
    """The global providers can be set once per process; these tests are the only ones that set them."""
    trace.set_tracer_provider(provider := TracerProvider())
    provider.add_span_processor(SimpleSpanProcessor(SPANS))
    metrics.set_meter_provider(MeterProvider(metric_readers=[METRICS]))
    yield


@pytest.fixture
def spans():
    SPANS.clear()
    return SPANS


@pytest.fixture
def traced_client(client, engine):
    """As telemetry.instrument_app does it (configure_telemetry is not called: it would export)."""
    FastAPIInstrumentor.instrument_app(client.app, excluded_urls="health", exclude_spans=["receive", "send"])
    yield client
    FastAPIInstrumentor.uninstrument_app(client.app)


def counter(name: str) -> dict[tuple, int]:
    data = METRICS.get_metrics_data()
    points = [p for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics if m.name == name
              for p in m.data.data_points]
    return {tuple(sorted(p.attributes.items())): p.value for p in points}


def test_off_without_an_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    settings = Settings(database_url="postgresql+psycopg://unused/x", otel_exporter_otlp_endpoint=None)
    assert telemetry.configure_telemetry(settings, "test") is False


def test_rpc_requests_are_named_after_the_method(traced_client, case_ids, spans):
    headers = auth(traced_client, RoleName.MANAGER)
    call(traced_client, "CaseService", "GetCase", cs.GetCaseRequest(case_id=str(case_ids[0])), cs.GetCaseResponse,
         headers)
    server = next(s for s in spans.get_finished_spans() if s.kind == trace.SpanKind.SERVER
                  and s.attributes.get("rpc.method") == "GetCase")
    assert server.name == "finrecon.v1.CaseService/GetCase"
    assert server.attributes["rpc.system"] == "connect_rpc"
    assert not any(s.name.endswith((" http receive", " http send")) for s in spans.get_finished_spans())


def test_queries_are_spans_without_their_parameters(engine, spans):
    from sqlalchemy import create_engine, text

    PsycopgInstrumentor().instrument(enable_commenter=False)
    fresh = create_engine(engine.url)  # a new connection: instrumentation applies to connections opened after it
    try:
        with fresh.connect() as connection, telemetry.tracer.start_as_current_span("request") as parent:
            connection.execute(text("SELECT :secret AS x"), {"secret": "hunter2-value"})
    finally:
        fresh.dispose()
        PsycopgInstrumentor().uninstrument()
    query = next(s for s in spans.get_finished_spans() if "AS x" in str(s.attributes.get("db.statement")))
    assert query.parent.span_id == parent.get_span_context().span_id
    assert "hunter2-value" not in str(query.attributes), "statements, never their parameters"


def test_rpc_errors_carry_the_connect_code(traced_client, spans):
    response = traced_client.post("/finrecon.v1.CaseService/GetCase", json={"caseId": "not-a-uuid"},
                                  headers=auth(traced_client, RoleName.MANAGER))
    assert response.status_code == 400
    server = next(s for s in spans.get_finished_spans() if s.attributes.get("rpc.method") == "GetCase")
    assert server.attributes["rpc.connect_rpc.error_code"] == "invalid_argument"


def test_workflow_and_agent_runs_are_traced_and_counted(session, world, case_ids, traced_client, scripts, spans):
    case = case_of(session, world[0].id, ExceptionCategory.FEE_DISCREPANCY)
    scripts.llm = AgentScripts(investigation=investigation_steps(),
                               resolution=proposal_steps("confirm_fee_discrepancy"))
    before = counter("finrecon.workflow.runs").get((("kind", "investigate"), ("outcome", "paused_for_approval")), 0)
    call(traced_client, "CaseService", "StartInvestigation", cs.StartInvestigationRequest(case_id=str(case.id)),
         cs.StartInvestigationResponse, auth(traced_client, RoleName.MANAGER))

    finished = {s.name: s for s in spans.get_finished_spans()}
    workflow = finished["workflow.investigate"]
    assert workflow.attributes["finrecon.workflow.outcome"] == "paused_for_approval"
    agent = finished["agent.investigation"]
    assert agent.parent and agent.context.trace_id == workflow.context.trace_id
    assert agent.attributes["finrecon.agent.status"] == "completed"
    assert agent.attributes["gen_ai.usage.input_tokens"] > 0
    assert workflow.context.trace_id == next(s for s in spans.get_finished_spans()
                                             if s.attributes.get("rpc.method") == "StartInvestigation"
                                             ).context.trace_id, "the background run continues the request's trace"

    runs = counter("finrecon.workflow.runs")
    assert runs[(("kind", "investigate"), ("outcome", "paused_for_approval"))] == before + 1
    assert any(dict(k)["agent"] == "investigation" and dict(k)["status"] == "completed"
               for k in counter("finrecon.agent.runs"))
    assert any(dict(k)["direction"] == "input" for k in counter("finrecon.agent.tokens"))


def test_log_lines_carry_the_trace(spans):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("finrecon.test_telemetry")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        with telemetry.tracer.start_as_current_span("work") as span:
            get_logger("finrecon_ai.test_telemetry").info("inside")
        get_logger("finrecon_ai.test_telemetry").info("outside")
    finally:
        logger.removeHandler(handler)
    inside, outside = (json.loads(line) for line in stream.getvalue().splitlines())
    assert inside["trace_id"] == format(span.get_span_context().trace_id, "032x")
    assert "trace_id" not in outside


def test_lone_calls_are_not_exported_but_calls_in_a_request_are():
    from opentelemetry.sdk.trace import TracerProvider

    exported = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(telemetry.SkipLoneCalls(SimpleSpanProcessor(exported)))
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("SELECT", kind=trace.SpanKind.CLIENT):
        pass
    with tracer.start_as_current_span("POST /cases", kind=trace.SpanKind.SERVER):
        with tracer.start_as_current_span("SELECT 1", kind=trace.SpanKind.CLIENT):
            pass
    assert [s.name for s in exported.get_finished_spans()] == ["SELECT 1", "POST /cases"]
