"""Connect/gRPC services: the same flows and rules as the REST API, over protobuf (no network, no Claude)."""

import asyncio
import struct
import uuid

import pytest
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from google.protobuf import json_format
from google.protobuf.message import Message

from finrecon.v1 import approvals_pb2 as ap
from finrecon.v1 import cases_pb2 as cs
from finrecon.v1 import enums_pb2
from finrecon.v1 import identity_pb2 as ids
from finrecon_ai.api import rpc
from finrecon_ai.cli import DEMO_USERS
from finrecon_ai.db import enums
from finrecon_ai.db.enums import CaseStatus, ExceptionCategory, RoleName
from tests.fakes import AgentScripts
from tests.test_api import PASSWORD, approver_headers, auth, client, scripts, store  # noqa: F401 (fixtures)
from tests.test_approvals import proposal_steps
from tests.test_workflows import case_of, investigation_steps


def call(client, service: str, method: str, request: Message, response_type: type, headers=None):
    """A Connect unary call in binary protobuf, as the dashboard makes it. Raises ConnectError like a client."""
    response = client.post(f"/finrecon.v1.{service}/{method}", content=request.SerializeToString(),
                           headers={"Content-Type": "application/proto", **(headers or {})})
    if response.status_code != 200:
        body = response.json()
        raise ConnectError(Code(body["code"]), body.get("message", ""))
    assert response.headers["content-type"] == "application/proto"
    return response_type.FromString(response.content)


def code_of(fn) -> Code:
    with pytest.raises(ConnectError) as raised:
        fn()
    return raised.value.code


@pytest.fixture(autouse=True)
def inline_background(monkeypatch):
    """Workflow starts and resumes run before the call returns, as REST's TestClient runs background tasks."""
    monkeypatch.setattr(rpc, "start_background", lambda background: [t.func(*t.args, **t.kwargs)
                                                                      for t in background.tasks])


def test_enums_match_the_database():
    for name, descriptor in enums_pb2.DESCRIPTOR.enum_types_by_name.items():
        prefix = rpc.enum_prefix(descriptor)
        proto = {v.name.removeprefix(prefix).lower() for v in descriptor.values if v.number}
        assert proto == {m.value for m in getattr(enums, name)}, name


def test_login_me_and_users(client):
    login = call(client, "IdentityService", "Login", ids.LoginRequest(
        tenant="demo", email=DEMO_USERS[RoleName.ANALYST][0], password=PASSWORD), ids.LoginResponse)
    assert login.access_token and login.expires_at.seconds
    headers = {"Authorization": f"Bearer {login.access_token}"}
    me = call(client, "IdentityService", "GetMe", ids.GetMeRequest(), ids.GetMeResponse, headers)
    assert me.email == DEMO_USERS[RoleName.ANALYST][0] and list(me.roles) == ["analyst"]
    users = call(client, "IdentityService", "ListUsers", ids.ListUsersRequest(), ids.ListUsersResponse, headers)
    assert DEMO_USERS[RoleName.MANAGER][0] in {u.email for u in users.users}

    wrong = ids.LoginRequest(tenant="demo", email=DEMO_USERS[RoleName.ANALYST][0], password="wrong password!")
    assert code_of(lambda: call(client, "IdentityService", "Login", wrong, ids.LoginResponse)) == \
        Code.UNAUTHENTICATED
    for bad in ({}, {"Authorization": "Bearer not-a-token"}, {"Authorization": f"Basic {login.access_token}"}):
        assert code_of(lambda: call(client, "IdentityService", "GetMe", ids.GetMeRequest(), ids.GetMeResponse,
                                    bad)) == Code.UNAUTHENTICATED


def test_lockout_is_resource_exhausted(client):
    wrong = ids.LoginRequest(tenant="demo", email=DEMO_USERS[RoleName.ANALYST][0], password="wrong password!")
    for _ in range(3):
        code_of(lambda: call(client, "IdentityService", "Login", wrong, ids.LoginResponse))
    assert code_of(lambda: call(client, "IdentityService", "Login", wrong, ids.LoginResponse)) == \
        Code.RESOURCE_EXHAUSTED


def test_case_list_and_detail_match_rest(client, case_ids):
    headers = auth(client, RoleName.MANAGER)
    listing = call(client, "CaseService", "ListCases", cs.ListCasesRequest(limit=5), cs.ListCasesResponse, headers)
    assert listing.total == len(case_ids) and len(listing.items) == 5
    new = call(client, "CaseService", "ListCases", cs.ListCasesRequest(status=enums_pb2.CASE_STATUS_NEW),
               cs.ListCasesResponse, headers)
    assert new.total == len(case_ids)
    assert call(client, "CaseService", "ListCases", cs.ListCasesRequest(status=enums_pb2.CASE_STATUS_CLOSED),
                cs.ListCasesResponse, headers).total == 0

    case_id = listing.items[0].id
    detail = call(client, "CaseService", "GetCase", cs.GetCaseRequest(case_id=case_id), cs.GetCaseResponse,
                  headers).detail
    rest = client.get(f"/cases/{case_id}", headers=headers).json()
    assert (detail.case_number, detail.status, detail.created_at.ToDatetime().date().isoformat()) == \
        (rest["case_number"], enums_pb2.CASE_STATUS_NEW, rest["created_at"][:10])
    assert [t.amount for t in detail.transactions] == [t["amount"] for t in rest["transactions"]]
    assert [t.id for t in detail.transactions] == [t["id"] for t in rest["transactions"]]
    assert json_format.MessageToDict(detail.exception.details) == rest["exception"]["details"]
    assert detail.HasField("discrepancy_amount") == (rest["discrepancy_amount"] is not None)
    assert not detail.HasField("resolved_at") and not detail.HasField("assigned_to")
    closing = next(t for t in detail.actions.transitions if t.to_status == enums_pb2.CASE_STATUS_CLOSED)
    assert list(closing.closure_reasons) == [enums_pb2.CLOSURE_REASON_FALSE_POSITIVE,
                                             enums_pb2.CLOSURE_REASON_NOT_ACTIONABLE]


def test_working_a_case(client, world, case_ids):
    manager, auditor = auth(client, RoleName.MANAGER), auth(client, RoleName.AUDITOR)
    case_id, analyst = str(case_ids[0]), str(world[2][RoleName.ANALYST].user_id)
    assign = cs.AssignCaseRequest(case_id=case_id, assignee_id=analyst)
    assert code_of(lambda: call(client, "CaseService", "AssignCase", assign, cs.AssignCaseResponse, auditor)) == \
        Code.PERMISSION_DENIED
    assert call(client, "CaseService", "AssignCase", assign, cs.AssignCaseResponse, manager).summary.assigned_to \
        == analyst

    note = call(client, "CaseService", "AddNote", cs.AddNoteRequest(case_id=case_id, text="Called PayGate."),
                cs.AddNoteResponse, manager).evidence
    assert note.evidence_type == enums_pb2.EVIDENCE_TYPE_ANALYST_NOTE and note.excerpt == "Called PayGate."
    moved = call(client, "CaseService", "TransitionCase", cs.TransitionCaseRequest(
        case_id=case_id, to_status=enums_pb2.CASE_STATUS_INVESTIGATING), cs.TransitionCaseResponse, manager)
    assert moved.summary.status == enums_pb2.CASE_STATUS_INVESTIGATING
    events = call(client, "CaseService", "ListCaseAudit", cs.ListCaseAuditRequest(case_id=case_id),
                  cs.ListCaseAuditResponse, manager).events
    assert {"case.assigned", "case.note_added", "case.status_changed"} <= {e.event_type for e in events}


def test_errors_map_to_connect_codes(client, case_ids):
    headers = auth(client, RoleName.MANAGER)
    case_id = str(case_ids[0])

    def code(method, request, response_type):
        return code_of(lambda: call(client, "CaseService", method, request, response_type, headers))

    assert code("TransitionCase", cs.TransitionCaseRequest(case_id=case_id, to_status=enums_pb2.CASE_STATUS_RESOLVED),
                cs.TransitionCaseResponse) == Code.FAILED_PRECONDITION
    assert code("TransitionCase", cs.TransitionCaseRequest(case_id=case_id), cs.TransitionCaseResponse) == \
        Code.INVALID_ARGUMENT, "to_status is required"
    assert code("GetCase", cs.GetCaseRequest(case_id=str(uuid.uuid4())), cs.GetCaseResponse) == Code.NOT_FOUND
    assert code("GetCase", cs.GetCaseRequest(case_id="CASE-1"), cs.GetCaseResponse) == Code.INVALID_ARGUMENT
    assert code("AddNote", cs.AddNoteRequest(case_id=case_id, text=""), cs.AddNoteResponse) == Code.INVALID_ARGUMENT
    assert code("ListCases", cs.ListCasesRequest(limit=500), cs.ListCasesResponse) == Code.INVALID_ARGUMENT


def test_other_tenant_sees_nothing(session, client, case_ids):
    from tests.test_api import test_other_tenant_sees_nothing as rest_version  # sets up the other tenant

    rest_version(session, client, case_ids)
    login = call(client, "IdentityService", "Login", ids.LoginRequest(
        tenant="other", email="mgr@other.dev", password=PASSWORD), ids.LoginResponse)
    headers = {"Authorization": f"Bearer {login.access_token}"}
    assert call(client, "CaseService", "ListCases", cs.ListCasesRequest(), cs.ListCasesResponse, headers).total == 0
    assert code_of(lambda: call(client, "CaseService", "GetCase", cs.GetCaseRequest(case_id=str(case_ids[0])),
                                cs.GetCaseResponse, headers)) == Code.NOT_FOUND


def test_investigate_then_approve_over_rpc(session, world, case_ids, client, scripts, approver_headers):
    case = case_of(session, world[0].id, ExceptionCategory.FEE_DISCREPANCY)
    scripts.llm = AgentScripts(investigation=investigation_steps(),
                               resolution=proposal_steps("confirm_fee_discrepancy"))
    manager = auth(client, RoleName.MANAGER)
    start = cs.StartInvestigationRequest(case_id=str(case.id))

    started = call(client, "CaseService", "StartInvestigation", start, cs.StartInvestigationResponse, manager)
    assert started.workflow.started and started.workflow.running
    workflow = call(client, "CaseService", "GetWorkflow", cs.GetWorkflowRequest(case_id=str(case.id)),
                    cs.GetWorkflowResponse, manager).workflow
    assert workflow.paused_for_approval and workflow.approval_request_id and workflow.round == 1
    assert code_of(lambda: call(client, "CaseService", "StartInvestigation", start, cs.StartInvestigationResponse,
                                manager)) == Code.FAILED_PRECONDITION

    pending = call(client, "ApprovalService", "ListPendingApprovals", ap.ListPendingApprovalsRequest(),
                   ap.ListPendingApprovalsResponse, approver_headers).approvals
    assert [p.case_number for p in pending] == [case.case_number]
    assert pending[0].recommendation.resolution_type == enums_pb2.RESOLUTION_TYPE_CONFIRM_FEE_DISCREPANCY
    decide = ap.DecideApprovalRequest(request_id=workflow.approval_request_id,
                                      decision=enums_pb2.APPROVAL_DECISION_TYPE_APPROVE)
    assert code_of(lambda: call(client, "ApprovalService", "DecideApproval", decide, ap.DecideApprovalResponse,
                                manager)) == Code.PERMISSION_DENIED, "the manager who started it may not approve it"

    decided = call(client, "ApprovalService", "DecideApproval", decide, ap.DecideApprovalResponse, approver_headers)
    assert decided.decision.decision == enums_pb2.APPROVAL_DECISION_TYPE_APPROVE
    detail = call(client, "CaseService", "GetCase", cs.GetCaseRequest(case_id=str(case.id)), cs.GetCaseResponse,
                  manager).detail
    assert detail.status == enums_pb2.CASE_STATUS_RESOLVED
    assert detail.recommendations[-1].status == enums_pb2.RECOMMENDATION_STATUS_EXECUTED
    assert detail.recommendations[-1].HasField("executed_at") and len(detail.decisions) == 1
    assert client.get(f"/cases/{case.id}", headers=manager).json()["status"] == CaseStatus.RESOLVED.value


def test_decision_records_the_evidence_considered(session, world, case_ids, client, scripts, approver_headers):
    case = case_of(session, world[0].id, ExceptionCategory.FEE_DISCREPANCY)
    scripts.llm = AgentScripts(investigation=investigation_steps(),
                               resolution=proposal_steps("confirm_fee_discrepancy"))
    manager = auth(client, RoleName.MANAGER)
    call(client, "CaseService", "StartInvestigation", cs.StartInvestigationRequest(case_id=str(case.id)),
         cs.StartInvestigationResponse, manager)
    detail = call(client, "CaseService", "GetCase", cs.GetCaseRequest(case_id=str(case.id)), cs.GetCaseResponse,
                  manager).detail
    chosen = [detail.evidence[0].id]
    decide = ap.DecideApprovalRequest(request_id=detail.actions.pending_approval_request_id,
                                      decision=enums_pb2.APPROVAL_DECISION_TYPE_REJECT, comment="Not convinced.",
                                      evidence_considered=ap.EvidenceSelection(evidence_ids=chosen))
    decided = call(client, "ApprovalService", "DecideApproval", decide, ap.DecideApprovalResponse, approver_headers)
    assert decided.decision.comments == "Not convinced."
    from finrecon_ai.db.models import ApprovalDecision

    assert [str(i) for i in session.get(ApprovalDecision, uuid.UUID(decided.decision.id)).evidence_considered_ids] \
        == chosen


def test_json_protocol_and_request_id(client):
    response = client.post("/finrecon.v1.IdentityService/Login", json={
        "tenant": "demo", "email": DEMO_USERS[RoleName.MANAGER][0], "password": PASSWORD},
        headers={"X-Request-ID": "rpc-trace-1234"})
    assert response.status_code == 200 and response.json()["accessToken"]
    assert response.headers["X-Request-ID"] == "rpc-trace-1234"
    unknown = client.post("/finrecon.v1.CaseService/DropTables", json={})
    assert unknown.status_code == 404


def test_errors_never_leak_internals(client, monkeypatch):
    from finrecon_ai.api.routes import cases

    def boom(*args, **kwargs):
        raise RuntimeError("postgresql://finrecon:hunter2@db secret stack detail")

    monkeypatch.setattr(cases, "get_case", boom)
    response = client.post("/finrecon.v1.CaseService/GetCase", json={"caseId": str(uuid.uuid4())},
                           headers=auth(client, RoleName.MANAGER))
    assert response.status_code == 500
    assert response.json() == {"code": "internal", "message": "internal error"}


def test_grpc_status_reaches_the_client_in_trailers(client):
    """gRPC carries its status in HTTP trailers; the request middleware must pass them through."""
    app, request = client.app, ids.LoginRequest(tenant="demo", email="nobody@demo.dev", password="x" * 12)
    body = request.SerializeToString()
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": struct.pack(">BI", 0, len(body)) + body, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "2", "method": "POST", "scheme": "http",
             "path": "/finrecon.v1.IdentityService/Login", "raw_path": b"/finrecon.v1.IdentityService/Login",
             "root_path": "", "query_string": b"", "server": ("test", 80), "client": ("127.0.0.1", 1),
             "headers": [(b"content-type", b"application/grpc"), (b"te", b"trailers")],
             "extensions": {"http.response.trailers": {}}}
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    trailers = dict(next(m for m in sent if m["type"] == "http.response.trailers")["headers"])
    assert start["status"] == 200 and start["trailers"]
    assert any(k.lower() == b"x-request-id" for k, _ in start["headers"])
    assert trailers[b"grpc-status"] == b"16" and trailers[b"grpc-message"] == b"invalid%20credentials"  # UNAUTHENTICATED, percent-encoded
