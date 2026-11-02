"""The API as an attacker sees it: injection through every input, and every endpoint across tenants."""

import uuid

import pytest
from sqlalchemy import func, select

from finrecon_ai.api.security import hash_password
from finrecon_ai.db.enums import IngestionMethod, RoleName, SourceType
from finrecon_ai.db.models import FinancialSource, ImportBatch, Role, Tenant, Transaction, User
from tests.security.test_tool_abuse import SQL_PAYLOADS
from tests.test_api import PASSWORD, auth, client, scripts  # noqa: F401 (fixtures)
from tests.test_workflows import store  # noqa: F401 (fixture)


@pytest.fixture
def attacker(session, client, world):
    """A manager in a different tenant, with a source of their own."""
    tenant = Tenant(name="Attacker", slug=f"attacker-{uuid.uuid4().hex[:6]}")
    session.add(tenant)
    session.flush()
    session.add(User(tenant_id=tenant.id, email="mallory@attacker.dev", full_name="Mallory",
                     password_hash=hash_password(PASSWORD),
                     roles=[session.scalar(select(Role).where(Role.name == RoleName.MANAGER))]))
    session.flush()
    token = client.post("/auth/login", json={"tenant": tenant.slug, "email": "mallory@attacker.dev",
                                             "password": PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
def test_sql_payloads_in_query_parameters(client, payload):
    headers = auth(client, RoleName.MANAGER)
    assert client.get("/audit-events", params={"event_type": payload, "entity_id": payload},
                      headers=headers).json() == []
    assert client.get("/cases", params={"status": payload}, headers=headers).status_code == 422
    search = client.get("/policies/search", params={"q": payload}, headers=headers)
    assert search.status_code in (200, 422)


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
def test_sql_payloads_in_login(client, session, payload):
    users_before = session.scalar(select(func.count()).select_from(User))
    response = client.post("/auth/login", json={"tenant": payload, "email": payload, "password": payload})
    assert response.status_code == 401
    assert session.scalar(select(func.count()).select_from(User)) == users_before


def test_sql_payload_in_note_is_stored_verbatim(client, case_ids):
    payload = "'); DELETE FROM investigation_cases; --"
    response = client.post(f"/cases/{case_ids[0]}/notes", json={"text": payload},
                           headers=auth(client, RoleName.MANAGER))
    assert response.status_code == 201 and response.json()["excerpt"] == payload
    assert client.get("/cases", headers=auth(client, RoleName.MANAGER)).json()["total"] == len(case_ids)


def test_malformed_ids_are_rejected_by_validation(client):
    headers = auth(client, RoleName.MANAGER)
    for path in ("/cases/1 OR 1=1", "/cases/../../etc/passwd", "/imports/' OR '1'='1"):
        assert client.get(path, headers=headers).status_code in (404, 422)


# --- every endpoint across tenants ---


def test_attacker_reads_nothing_of_another_tenant(session, world, client, case_ids, attacker):
    case_id = case_ids[0]
    batch = session.scalar(select(ImportBatch).where(ImportBatch.tenant_id == world[0].id))
    source = session.scalar(select(FinancialSource).where(FinancialSource.tenant_id == world[0].id))
    from finrecon_ai.db.models import ReconciliationRun

    run = session.scalar(select(ReconciliationRun).where(ReconciliationRun.tenant_id == world[0].id))
    for path in (f"/cases/{case_id}", f"/cases/{case_id}/workflow", f"/cases/{case_id}/audit",
                 f"/imports/{batch.id}", f"/reconciliation/runs/{run.id}"):
        assert client.get(path, headers=attacker).status_code == 404, path
    for path in ("/cases", "/sources", "/reconciliation/runs", "/approvals", "/policies"):
        body = client.get(path, headers=attacker).json()
        assert body in ([], {"total": 0, "items": []}), path
    # The attacker's audit log holds only their own login, nothing of the victim's history.
    events = client.get("/audit-events", headers=attacker).json()
    assert [e["event_type"] for e in events] == ["auth.login_succeeded"]
    assert source.id  # exists, but invisible


def test_attacker_cannot_act_on_another_tenant(session, world, client, case_ids, attacker, small_dataset_dir):
    case_id = case_ids[0]
    victim_user = str(world[2][RoleName.ANALYST].user_id)
    source = session.scalar(select(FinancialSource).where(FinancialSource.tenant_id == world[0].id))
    batch = session.scalar(select(ImportBatch).where(ImportBatch.tenant_id == world[0].id))
    attempts = [
        ("post", f"/cases/{case_id}/assign", {"json": {"assignee_id": victim_user}}),
        ("post", f"/cases/{case_id}/transition", {"json": {"to_status": "investigating"}}),
        ("post", f"/cases/{case_id}/notes", {"json": {"text": "pwned"}}),
        ("post", f"/cases/{case_id}/investigate", {}),
        ("post", f"/approvals/{uuid.uuid4()}/decision", {"json": {"decision": "approve"}}),
        ("post", f"/sources/{source.id}/imports", {"files": {"file": ("x.csv", b"a\n")}}),
        ("post", "/reconciliation/runs", {"json": {"import_batch_ids": [str(batch.id)]}}),
    ]
    transactions_before = session.scalar(select(func.count()).select_from(Transaction))
    for method, path, kwargs in attempts:
        response = getattr(client, method)(path, headers=attacker, **kwargs)
        assert response.status_code == 404, f"{path}: {response.status_code} {response.text}"
    assert session.scalar(select(func.count()).select_from(Transaction)) == transactions_before


def test_attacker_cannot_assign_victim_user_to_own_case(session, client, attacker, world):
    """A cross-tenant user ID is treated as unknown, even in the attacker's own tenant."""
    from finrecon_ai.authz import Actor
    from finrecon_ai.cases.service import CaseError, assign_case

    with pytest.raises(CaseError, match="not an active user"):
        attacker_tenant = session.scalar(select(Tenant).where(Tenant.slug.like("attacker-%")))
        actor = Actor(tenant_id=attacker_tenant.id, user_id=uuid.uuid4(), roles=frozenset({RoleName.MANAGER}))
        assign_case(session, actor, _own_case(session, attacker_tenant), world[2][RoleName.ANALYST].user_id)


def _own_case(session, tenant):
    from finrecon_ai.db.enums import ExceptionCategory, Severity
    from finrecon_ai.db.models import InvestigationCase, ReconciliationException, ReconciliationRun

    run = ReconciliationRun(tenant_id=tenant.id, idempotency_key="k", config={}, engine_version="1")
    session.add(run)
    session.flush()
    exc = ReconciliationException(tenant_id=tenant.id, run_id=run.id, category=ExceptionCategory.UNMATCHED,
                                  severity=Severity.LOW)
    session.add(exc)
    session.flush()
    case = InvestigationCase(tenant_id=tenant.id, case_number="CASE-000001", exception_id=exc.id,
                             priority=Severity.LOW, title="t")
    session.add(case)
    session.flush()
    return case.id


def test_source_of_attacker_tenant_with_victim_batch(session, world, client, attacker):
    """Even the attacker's own source cannot be used to reconcile the victim's batches."""
    attacker_tenant = session.scalar(select(Tenant).where(Tenant.slug.like("attacker-%")))
    session.add(FinancialSource(tenant_id=attacker_tenant.id, name="Mine", source_type=SourceType.BANK_STATEMENT,
                                ingestion_method=IngestionMethod.CSV))
    session.flush()
    batch = session.scalar(select(ImportBatch).where(ImportBatch.tenant_id == world[0].id))
    response = client.post("/reconciliation/runs", json={"import_batch_ids": [str(batch.id)]}, headers=attacker)
    assert response.status_code == 404
