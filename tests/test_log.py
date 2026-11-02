import json
import logging
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from finrecon_ai import log as log_module
from finrecon_ai.audit import record_event
from finrecon_ai.db.enums import ActorType
from finrecon_ai.db.models import AuditEvent, Tenant
from finrecon_ai.log import MASK, configure_logging, get_logger, log_context, redact

log = get_logger("finrecon_ai.tests")


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "logs" / "finrecon.log"
    configure_logging("DEBUG", "json", str(path))
    yield path
    configure_logging("WARNING", "console", None)


def lines(path):
    for handler in logging.getLogger("finrecon").handlers:
        handler.flush()
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_file_gets_json_lines_with_context(log_file):
    with log_context(correlation_id="abc123", tenant_id=uuid.UUID(int=1)):
        with log_context(case_id="c-1"):
            log.info("thing.happened", amount=Decimal("1.50"), count=3)
        log.info("after.inner")

    first, second = lines(log_file)
    assert first["event"] == "thing.happened" and first["level"] == "INFO"
    assert first["logger"] == "finrecon.tests"
    assert first["ts"].endswith("Z")
    assert first["correlation_id"] == "abc123" and first["case_id"] == "c-1"
    assert first["amount"] == "1.50" and first["count"] == 3
    assert "case_id" not in second, "inner context must not leak out of its block"


def test_log_directory_is_created(tmp_path):
    path = tmp_path / "nested" / "logs" / "app.log"
    configure_logging("INFO", "console", str(path))
    log.info("x")
    assert path.exists()
    configure_logging("WARNING", "console", None)


def test_configure_is_idempotent(log_file):
    configure_logging("INFO", "json", str(log_file))
    configure_logging("INFO", "json", str(log_file))
    assert len(logging.getLogger("finrecon").handlers) == 2  # one stream, one file
    log.info("once")
    assert [entry["event"] for entry in lines(log_file)] == ["once"]


def test_file_rotates(log_file):
    handler = next(h for h in logging.getLogger("finrecon").handlers
                   if isinstance(h, logging.handlers.RotatingFileHandler))
    assert handler.maxBytes == log_module.MAX_FILE_BYTES and handler.backupCount == log_module.BACKUP_FILES


def test_level_filters(log_file):
    configure_logging("WARNING", "json", str(log_file))
    log.info("hidden")
    log.warning("shown")
    assert [entry["event"] for entry in lines(log_file)] == ["shown"]


@pytest.mark.parametrize("value, expected", [
    ({"api_key": "abc"}, {"api_key": MASK}),
    ({"Authorization": "Bearer xyz"}, {"Authorization": MASK}),
    ({"nested": {"db_password": "pw"}}, {"nested": {"db_password": MASK}}),
    ("key sk-ant-api03-SECRET_value here", f"key {MASK} here"),
    ("postgresql://finrecon:hunter2@localhost/db", f"postgresql://finrecon:{MASK}@localhost/db"),
    ([{"token": "t"}, "ok"], [{"token": MASK}, "ok"]),
    ({"access_token": "t", "input_tokens": 10, "x-api-key": "k"}, {"access_token": MASK, "input_tokens": 10, "x-api-key": MASK}),
])
def test_redaction(value, expected):
    assert redact(value) == expected


def test_secrets_never_reach_the_file(log_file):
    with log_context(api_key="sk-ant-api03-LEAK"):
        log.info("connect", url="postgresql://u:hunter2@db/x", note="key sk-ant-api03-LEAK2")
        try:
            raise ValueError("bad key sk-ant-api03-LEAK3")
        except ValueError:
            log.exception("failed")
    text = log_file.read_text()
    assert "LEAK" not in text and "hunter2" not in text
    assert lines(log_file)[-1]["error"]["type"] == "ValueError"


def test_audit_event_carries_correlation_id(session):
    tenant = Tenant(name="T", slug=f"t-{uuid.uuid4().hex[:6]}")
    session.add(tenant)
    session.flush()
    with log_context(correlation_id="corr-42"):
        record_event(session, tenant_id=tenant.id, event_type="test", entity_type="x", entity_id=1,
                     actor_type=ActorType.SYSTEM)
    assert session.scalar(select(AuditEvent.request_id).where(AuditEvent.tenant_id == tenant.id)) == "corr-42"


def test_agent_run_logs_calls_without_case_data(log_file, session, world, case_ids):
    from tests.fakes import REPORT, FakeClient, final, reply, tool_use
    from finrecon_ai.agents.prompt_registry import load_prompt
    from finrecon_ai.agents.runtime import run_agent
    from finrecon_ai.agents.tools import ToolContext
    from finrecon_ai.db.enums import AgentName
    from finrecon_ai.db.models import InvestigationCase, ReconciliationException

    case = session.get(InvestigationCase, case_ids[0])
    ctx = ToolContext(session=session, tenant_id=world[0].id, case=case,
                      exception=session.get(ReconciliationException, case.exception_id))
    client = FakeClient(reply("tool_use", tool_use("get_case_transactions")), final(REPORT))
    run_agent(session, spec=load_prompt(AgentName.INVESTIGATION, "v1"), ctx=ctx,
              user_content="Investigate SECRET-CASE-TEXT", client=client)

    entries = [e for e in lines(log_file) if e["logger"] == "finrecon.agents.runtime"]
    events = [e["event"] for e in entries]
    assert events == ["agent.started", "claude.call", "agent.tool_call", "claude.call", "agent.completed"]
    assert all(e["case_id"] == str(case.id) and "execution_id" in e for e in entries)
    call = entries[1]
    assert call["input_tokens"] == 100 and call["stop_reason"] == "tool_use" and "duration_ms" in call
    text = log_file.read_text()
    assert "SECRET-CASE-TEXT" not in text, "prompt/user content must not be logged"
    assert "raw_payload" not in text and REPORT["summary"] not in text
