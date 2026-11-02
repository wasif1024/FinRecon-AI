"""SRS §12 performance: standard dashboard queries within 2 seconds, on the 1,000-payment dataset.

Every request each dashboard page makes, timed page by page through the API (REST and Connect) as the
application's database role, with one case investigated (scripted agents) so its page has findings, evidence and an
audit trail. Import and reconciliation have their own targets (test_ingestion.py, test_reconciliation.py).
"""

import time

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from qdrant_client import QdrantClient
from sqlalchemy import select

from finrecon.v1 import approvals_pb2 as ap
from finrecon.v1 import cases_pb2 as cs
from finrecon.v1 import enums_pb2
from finrecon.v1 import identity_pb2 as ids
from finrecon_ai.api.security import hash_password
from finrecon_ai.authz import Actor, load_actor
from finrecon_ai.cases.service import create_cases_for_run
from finrecon_ai.cli import DEMO_SOURCES, seed_demo
from finrecon_ai.db.enums import ExceptionCategory, RoleName
from finrecon_ai.db.models import FinancialSource, User
from finrecon_ai.ingestion.service import ingest_file
from finrecon_ai.policies.embedding import HashingEmbedder
from finrecon_ai.policies.service import upload_policy
from finrecon_ai.policies.store import PolicyStore
from finrecon_ai.reconciliation.service import run_reconciliation
from finrecon_ai.reports.service import ReportKind
from finrecon_ai.synthetic.generator import generate, write_dataset
from tests.fakes import AgentScripts
from tests.test_api import PASSWORD, auth, make_client
from tests.test_approvals import proposal_steps
from tests.test_rpc import call, inline_background  # noqa: F401 (fixture)
from tests.test_workflows import SAMPLES, case_of, investigation_steps

TARGET_SECONDS = 2.0  # SRS §12: standard dashboard queries within 2 seconds under normal development load


@pytest.fixture
def full_client(session, tmp_path, app_role):
    """The SRS dataset size: 1,000 payments (about 3,000 records across the three sources)."""
    write_dataset(generate(seed=42, payments=1000), tmp_path / "data")
    tenant = seed_demo(session)
    batches = []
    for name, *_, file_name in DEMO_SOURCES.values():
        source = session.scalar(select(FinancialSource).where(FinancialSource.tenant_id == tenant.id,
                                                              FinancialSource.name == name))
        batches.append(ingest_file(session, tenant_id=tenant.id, source_id=source.id, file_name=file_name,
                                   content=(tmp_path / "data" / file_name).read_bytes()).batch.id)
    run = run_reconciliation(session, tenant_id=tenant.id, import_batch_ids=batches).run
    create_cases_for_run(session, Actor.system(tenant.id), run.id)
    for user in session.scalars(select(User).where(User.tenant_id == tenant.id)):
        user.password_hash = hash_password(PASSWORD)
    admin = session.scalar(select(User).where(User.email == "admin@demo.finrecon.dev"))
    store = PolicyStore(QdrantClient(":memory:"), HashingEmbedder())
    upload_policy(session, load_actor(session, tenant.id, admin.id), store, file_name="fees.md",
                  content=(SAMPLES / "payment_processing_fee_policy.md").read_bytes(), storage_dir=tmp_path / "docs")
    session.flush()
    scripts = type("Scripts", (), {"llm": AgentScripts(investigation=investigation_steps(),
                                                       resolution=proposal_steps("confirm_fee_discrepancy"))})()
    client = make_client(session, store, scripts, InMemorySaver(), app_role)
    fee = case_of(session, tenant.id, ExceptionCategory.FEE_DISCREPANCY)
    manager = auth(client, RoleName.MANAGER)
    call(client, "CaseService", "StartInvestigation", cs.StartInvestigationRequest(case_id=str(fee.id)),
         cs.StartInvestigationResponse, manager)
    return client, str(fee.id)


def test_dashboard_pages_answer_within_two_seconds(full_client):
    client, case_id = full_client
    manager = auth(client, RoleName.MANAGER)

    def rpc(service, method, request, response):
        return lambda: call(client, service, method, request, response, manager)

    def rest(path, **params):
        def get():
            response = client.get(path, params=params, headers=manager)
            assert response.status_code == 200, (path, response.status_code)
        return get

    me = rpc("IdentityService", "GetMe", ids.GetMeRequest(), ids.GetMeResponse)
    users = rpc("IdentityService", "ListUsers", ids.ListUsersRequest(), ids.ListUsersResponse)
    pages = {
        "overview": [me, rest("/reconciliation/runs"), *(
            rpc("CaseService", "ListCases", cs.ListCasesRequest(status=s, limit=1), cs.ListCasesResponse)
            for s in (enums_pb2.CASE_STATUS_NEW, enums_pb2.CASE_STATUS_INVESTIGATING,
                      enums_pb2.CASE_STATUS_AWAITING_EVIDENCE, enums_pb2.CASE_STATUS_AWAITING_APPROVAL,
                      enums_pb2.CASE_STATUS_RESOLVED, enums_pb2.CASE_STATUS_CLOSED))],
        "cases": [me, users, rpc("CaseService", "ListCases", cs.ListCasesRequest(limit=25), cs.ListCasesResponse)],
        "case detail": [me, users,
                        rpc("CaseService", "GetCase", cs.GetCaseRequest(case_id=case_id), cs.GetCaseResponse),
                        rpc("CaseService", "GetWorkflow", cs.GetWorkflowRequest(case_id=case_id),
                            cs.GetWorkflowResponse),
                        rpc("CaseService", "ListCaseAudit", cs.ListCaseAuditRequest(case_id=case_id),
                            cs.ListCaseAuditResponse)],
        "approvals": [me, users, rpc("ApprovalService", "ListPendingApprovals", ap.ListPendingApprovalsRequest(),
                                     ap.ListPendingApprovalsResponse)],
        "data": [me, rest("/sources"), rest("/imports"), rest("/reconciliation/runs")],
        "policies": [me, rest("/policies"), rest("/policies/search", q="fee discrepancy threshold", limit=6)],
        **{f"report {kind.value}": [me, rest(f"/reports/{kind.value}")] for kind in ReportKind},
        "agent performance": [me, rest("/metrics/agents", days=30)],
        "audit trail": [me, users, rest("/audit-events", limit=100), rest("/audit-events/verify")],
    }

    timings = {}
    for page, requests in pages.items():
        started = time.perf_counter()
        for request in requests:
            request()
        timings[page] = time.perf_counter() - started
    slow = {page: round(seconds, 2) for page, seconds in timings.items() if seconds >= TARGET_SECONDS}
    assert not slow, f"pages over {TARGET_SECONDS}s: {slow} (all: { {p: round(s, 2) for p, s in timings.items()} })"
