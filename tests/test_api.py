"""REST API: authentication, authorization, tenant isolation and the main flows, through HTTP."""

import uuid
from contextlib import contextmanager, nullcontext
from datetime import timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from finrecon_ai.api.app import create_app
from finrecon_ai.api.deps import Runtime
from finrecon_ai.api.security import ALGORITHM, LoginThrottle, create_token, hash_password
from finrecon_ai.cli import DEMO_SOURCES, DEMO_USERS
from finrecon_ai.config import Settings
from finrecon_ai.db.enums import CaseStatus, ExceptionCategory, RoleName
from finrecon_ai.db.models import AuditEvent, FinancialSource, Role, Tenant, User
from finrecon_ai.workflows.case_investigation import WorkflowDeps
from tests.fakes import AgentScripts
from tests.test_approvals import proposal_steps
from tests.test_workflows import case_of, investigation_steps

SECRET = "test-secret-" + "x" * 40
PASSWORD = "correct horse battery"


@pytest.fixture
def scripts():
    """The fake agents a test's workflow runs will use; tests replace .llm as needed."""
    return type("Scripts", (), {"llm": AgentScripts()})()


@pytest.fixture
def client(session, world, store, scripts):
    @contextmanager
    def unit_of_work():
        yield session

    saver = InMemorySaver()
    rt = Runtime(
        jwt_secret=SECRET, jwt_ttl_minutes=60, unit_of_work=unit_of_work,
        workflow_deps=lambda: WorkflowDeps(unit_of_work=unit_of_work, llm=scripts.llm, policy_store=store),
        checkpointer=lambda: nullcontext(saver), policy_store=lambda: store,
        run_lock=lambda case_id: nullcontext(True), throttle=LoginThrottle(max_failures=3),
    )
    for user in session.scalars(select(User).where(User.tenant_id == world[0].id)):
        user.password_hash = hash_password(PASSWORD)
    session.flush()
    app = create_app(Settings(database_url="postgresql+psycopg://unused/x", jwt_secret=SECRET, log_file=""), rt)
    return TestClient(app)


from tests.test_workflows import store  # noqa: E402,F401 (fixture)


def login(client, role: RoleName | str, password: str = PASSWORD, tenant: str = "demo"):
    email = DEMO_USERS[role][0] if isinstance(role, RoleName) else role
    return client.post("/auth/login", json={"tenant": tenant, "email": email, "password": password})


def auth(client, role: RoleName | str) -> dict[str, str]:
    response = login(client, role)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def approver_headers(session, world, client):
    user = User(tenant_id=world[0].id, email="approver2@demo.dev", full_name="Approver",
                password_hash=hash_password(PASSWORD),
                roles=[session.scalar(select(Role).where(Role.name == RoleName.MANAGER))])
    session.add(user)
    session.flush()
    return auth(client, "approver2@demo.dev")


# --- authentication ---


def test_login_and_me(client):
    headers = auth(client, RoleName.ANALYST)
    me = client.get("/auth/me", headers=headers).json()
    assert me["email"] == DEMO_USERS[RoleName.ANALYST][0]
    assert me["roles"] == ["analyst"] and "case:work" in me["permissions"]


@pytest.mark.parametrize("email, password, tenant", [
    ("analyst@demo.finrecon.dev", "wrong password!!", "demo"),
    ("nobody@demo.finrecon.dev", PASSWORD, "demo"),
    ("analyst@demo.finrecon.dev", PASSWORD, "no-such-tenant"),
])
def test_bad_logins_look_identical(client, email, password, tenant):
    response = login(client, email, password, tenant)
    assert response.status_code == 401 and response.json() == {"detail": "invalid credentials"}


def test_inactive_user_cannot_log_in_or_use_old_token(session, world, client):
    headers = auth(client, RoleName.ANALYST)
    user = session.scalar(select(User).where(User.email == DEMO_USERS[RoleName.ANALYST][0]))
    user.is_active = False
    session.flush()
    assert login(client, RoleName.ANALYST).status_code == 401
    assert client.get("/auth/me", headers=headers).status_code == 401, "deactivation applies immediately"


def test_role_change_applies_to_existing_token(session, client):
    headers = auth(client, RoleName.ANALYST)
    user = session.scalar(select(User).where(User.email == DEMO_USERS[RoleName.ANALYST][0]))
    user.roles = [session.scalar(select(Role).where(Role.name == RoleName.AUDITOR))]
    session.flush()
    assert client.get("/auth/me", headers=headers).json()["roles"] == ["auditor"]


def test_repeated_failures_lock_the_account(client):
    for _ in range(3):
        assert login(client, RoleName.ANALYST, "wrong password!!").status_code == 401
    assert login(client, RoleName.ANALYST).status_code == 429, "even the right password is refused while locked"


def test_failed_login_is_audited(session, world, client):
    login(client, RoleName.ANALYST, "wrong password!!")
    events = session.scalars(select(AuditEvent.event_type).where(AuditEvent.tenant_id == world[0].id)).all()
    assert "auth.login_failed" in events


@pytest.mark.parametrize("header", [None, "Bearer garbage", "Basic abc"])
def test_requests_without_valid_token_are_rejected(client, header):
    headers = {"Authorization": header} if header else {}
    response = client.get("/cases", headers=headers)
    assert response.status_code == 401 and response.headers["WWW-Authenticate"] == "Bearer"


def test_expired_tampered_and_foreign_tokens(session, world, client):
    user = session.scalar(select(User).where(User.email == DEMO_USERS[RoleName.ANALYST][0]))
    expired, _ = create_token(user.id, world[0].id, SECRET, ttl_minutes=-1)
    forged, _ = create_token(user.id, world[0].id, "another-secret-" + "y" * 40, 60)
    other_tenant, _ = create_token(user.id, uuid.uuid4(), SECRET, 60)
    unsigned = jwt.encode({"sub": str(user.id), "tid": str(world[0].id)}, key=None, algorithm="none")
    for token in (expired, forged, other_tenant, unsigned):
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_token_uses_expected_algorithm(client):
    token = login(client, RoleName.ANALYST).json()["access_token"]
    assert jwt.get_unverified_header(token)["alg"] == ALGORITHM


def test_app_refuses_to_start_without_secret():
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_app(Settings(database_url="postgresql+psycopg://unused/x"), Runtime(
            "short", 60, None, None, None, None, None, LoginThrottle()))


# --- authorization and tenant isolation ---


def test_roles_are_enforced(client, world, case_ids):
    case_id = str(case_ids[0])
    analyst_id = str(world[2][RoleName.ANALYST].user_id)
    auditor = auth(client, RoleName.AUDITOR)
    assert client.get(f"/cases/{case_id}", headers=auditor).status_code == 200
    assert client.post(f"/cases/{case_id}/assign", json={"assignee_id": analyst_id},
                       headers=auditor).status_code == 403
    assert client.post(f"/cases/{case_id}/assign", json={"assignee_id": analyst_id},
                       headers=auth(client, RoleName.MANAGER)).status_code == 200
    assert client.get("/audit-events", headers=auth(client, RoleName.ANALYST)).status_code == 403
    assert client.get("/approvals", headers=auth(client, RoleName.ANALYST)).status_code == 403


def test_other_tenant_sees_nothing(session, client, case_ids):
    other = Tenant(name="Other", slug="other")
    session.add(other)
    session.flush()
    session.add(User(tenant_id=other.id, email="mgr@other.dev", full_name="M", password_hash=hash_password(PASSWORD),
                     roles=[session.scalar(select(Role).where(Role.name == RoleName.MANAGER))]))
    session.flush()
    login_response = client.post("/auth/login", json={"tenant": "other", "email": "mgr@other.dev",
                                                      "password": PASSWORD})
    headers = {"Authorization": f"Bearer {login_response.json()['access_token']}"}

    assert client.get("/cases", headers=headers).json()["total"] == 0
    response = client.get(f"/cases/{case_ids[0]}", headers=headers)
    assert response.status_code == 404, "another tenant's case looks exactly like a missing one"
    assert client.get("/sources", headers=headers).json() == []
    # Only its own chain: the single event is this tenant's login, none of the demo tenant's history.
    assert client.get("/audit-events/verify", headers=headers).json()["events_checked"] == 1


# --- data and case flows ---


def test_upload_and_reconcile(session, world, client, small_dataset_dir):
    headers = auth(client, RoleName.ANALYST)
    sources = {s["name"]: s["id"] for s in client.get("/sources", headers=headers).json()}
    name, *_, file_name = DEMO_SOURCES["bank"]
    content = (small_dataset_dir / file_name).read_bytes()

    first = client.post(f"/sources/{sources[name]}/imports", files={"file": (file_name, content)}, headers=headers)
    assert first.status_code == 200, "world already imported this exact file"
    detail = client.get(f"/imports/{first.json()['id']}", headers=headers).json()
    assert detail["rejected_records"] == len(detail["validation_errors"])

    run = client.post("/reconciliation/runs", json={"import_batch_ids": [first.json()["id"]]}, headers=headers)
    assert run.status_code == 201 and run.json()["cases_opened"] >= 0
    assert client.get(f"/reconciliation/runs/{run.json()['id']}", headers=headers).status_code == 200


def test_upload_needs_import_permission(client, session):
    source = session.scalar(select(FinancialSource))
    response = client.post(f"/sources/{source.id}/imports", files={"file": ("x.csv", b"a,b\n")},
                           headers=auth(client, RoleName.AUDITOR))
    assert response.status_code == 403


def test_case_list_detail_and_note(client, case_ids):
    headers = auth(client, RoleName.MANAGER)
    listing = client.get("/cases", params={"limit": 5}, headers=headers).json()
    assert listing["total"] == len(case_ids) and len(listing["items"]) == 5

    case_id = listing["items"][0]["id"]
    detail = client.get(f"/cases/{case_id}", headers=headers).json()
    assert detail["transactions"] and detail["exception"]["category"]
    note = client.post(f"/cases/{case_id}/notes", json={"text": "Called PayGate."}, headers=headers)
    assert note.status_code == 201 and note.json()["evidence_type"] == "analyst_note"


def test_service_errors_map_to_http(client, case_ids):
    headers = auth(client, RoleName.MANAGER)
    case_id = str(case_ids[0])
    bad = client.post(f"/cases/{case_id}/transition", json={"to_status": "resolved"}, headers=headers)
    assert bad.status_code == 409
    assert client.get(f"/cases/{uuid.uuid4()}", headers=headers).status_code == 404
    unknown_field = client.post(f"/cases/{case_id}/notes", json={"text": "x", "admin": True}, headers=headers)
    assert unknown_field.status_code == 422


def test_request_id_round_trip(client):
    response = client.get("/health", headers={"X-Request-ID": "trace-12345678"})
    assert response.headers["X-Request-ID"] == "trace-12345678"
    generated = client.get("/health", headers={"X-Request-ID": "bad id with spaces"})
    assert generated.headers["X-Request-ID"] != "bad id with spaces"


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["database"] == "ok"


# --- investigation and approval through HTTP ---


def test_investigate_then_approve_over_http(session, world, case_ids, client, scripts, approver_headers):
    case = case_of(session, world[0].id, ExceptionCategory.FEE_DISCREPANCY)
    scripts.llm = AgentScripts(investigation=investigation_steps(),
                               resolution=proposal_steps("confirm_fee_discrepancy"))
    manager = auth(client, RoleName.MANAGER)

    started = client.post(f"/cases/{case.id}/investigate", headers=manager)
    assert started.status_code == 202  # TestClient runs the background task before returning
    workflow = client.get(f"/cases/{case.id}/workflow", headers=manager).json()
    assert workflow["paused_for_approval"] and workflow["approval_request_id"]
    assert client.post(f"/cases/{case.id}/investigate", headers=manager).status_code == 409

    pending = client.get("/approvals", headers=approver_headers).json()
    assert [p["case_number"] for p in pending] == [case.case_number]
    request_id = workflow["approval_request_id"]
    own = client.post(f"/approvals/{request_id}/decision", json={"decision": "approve"}, headers=manager)
    assert own.status_code == 403, "the manager who started it may not approve it"

    decided = client.post(f"/approvals/{request_id}/decision", json={"decision": "approve"}, headers=approver_headers)
    assert decided.status_code == 202 and decided.json()["decision"] == "approve"
    detail = client.get(f"/cases/{case.id}", headers=manager).json()
    assert detail["status"] == CaseStatus.RESOLVED.value
    assert detail["recommendations"][-1]["status"] == "executed" and len(detail["decisions"]) == 1
    assert client.get("/audit-events/verify", headers=manager).json()["intact"]


def test_policy_upload_and_search(client):
    admin = auth(client, RoleName.ADMIN)
    content = b"# Chargeback Policy\n## 1. Window\nChargebacks must be disputed within 30 days."
    uploaded = client.post("/policies", files={"file": ("chargebacks.md", content)}, headers=admin)
    assert uploaded.status_code == 201 and uploaded.json()["status"] == "indexed"
    hits = client.get("/policies/search", params={"q": "chargeback dispute window"},
                      headers=auth(client, RoleName.ANALYST)).json()
    assert hits[0]["title"] == "Chargeback Policy"
    assert client.post("/policies", files={"file": ("x.md", b"# T\ntext")},
                       headers=auth(client, RoleName.ANALYST)).status_code == 403


def test_errors_never_leak_internals(client, monkeypatch):
    from finrecon_ai.api.routes import cases

    def boom(*args, **kwargs):
        raise RuntimeError("postgresql://finrecon:hunter2@db secret stack detail")

    monkeypatch.setattr(cases, "get_case", boom)
    response = client.get(f"/cases/{uuid.uuid4()}", headers=auth(client, RoleName.MANAGER))
    assert response.status_code == 500
    assert response.json() == {"detail": "internal error", "request_id": response.headers["X-Request-ID"]}
