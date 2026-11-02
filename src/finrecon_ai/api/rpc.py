"""Connect RPC services (proto/finrecon/v1), mounted on the API app: the dashboard's case work, typed end to end.

One port serves REST, Connect (HTTP/1.1 or HTTP/2, JSON or binary protobuf) and gRPC (HTTP/2; `finrecon serve`
runs Hypercorn, which speaks cleartext HTTP/2). Each RPC calls the same function as its REST endpoint, so
permissions, tenancy, validation and audit are the REST API's; this module only translates messages and errors.

Domain enums travel as protobuf enums named after the database values (CASE_STATUS_AWAITING_APPROVAL is
"awaiting_approval"). The token is the one Login (or POST /auth/login) returns, sent as "Authorization: Bearer".
"""

import contextvars
import re
import threading
import uuid
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext
from fastapi import BackgroundTasks, HTTPException
from google.protobuf import json_format
from google.protobuf.descriptor import Descriptor, EnumDescriptor, FieldDescriptor
from google.protobuf.message import Message
from pydantic import BaseModel, ValidationError
from pydantic_core import to_jsonable_python
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from finrecon.v1 import approvals_pb2 as approvals_pb
from finrecon.v1 import cases_pb2 as cases_pb
from finrecon.v1 import identity_pb2 as identity_pb
from finrecon.v1.approvals_connect import ApprovalServiceASGIApplication
from finrecon.v1.cases_connect import CaseServiceASGIApplication
from finrecon.v1.identity_connect import IdentityServiceASGIApplication
from finrecon_ai import telemetry
from finrecon_ai.api.deps import Runtime, authenticate
from finrecon_ai.api.errors import status_for
from finrecon_ai.api.routes import approvals, auth, cases, users
from finrecon_ai.api.schemas import (
    AssignIn,
    AuditEventOut,
    CaseSummaryOut,
    DecisionIn,
    DecisionOut,
    EvidenceOut,
    LoginIn,
    NoteIn,
    TransitionIn,
)
from finrecon_ai.authz import Actor
from finrecon_ai.db.enums import CaseStatus, Severity
from finrecon_ai.log import get_logger

log = get_logger(__name__)
M = TypeVar("M", bound=Message)

HTTP_TO_CODE = {400: Code.INVALID_ARGUMENT, 401: Code.UNAUTHENTICATED, 403: Code.PERMISSION_DENIED,
                404: Code.NOT_FOUND, 409: Code.FAILED_PRECONDITION, 422: Code.INVALID_ARGUMENT,
                429: Code.RESOURCE_EXHAUSTED}
# Carried as their JSON form (RFC 3339 for Timestamp, objects for Struct).
_JSON_TYPES = {"google.protobuf.Struct", "google.protobuf.Timestamp", "google.protobuf.Value",
               "google.protobuf.ListValue"}


# --- messages <-> the REST schemas ---


def enum_prefix(enum: EnumDescriptor) -> str:
    """CaseStatus -> "CASE_STATUS_"."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", enum.name).upper() + "_"


def to_message(data: BaseModel | Mapping[str, Any], cls: type[M]) -> M:
    """A response message from a REST response model (or a dict of them). Fields the message does not declare
    are left out; an enum value the proto lacks raises (tests/test_rpc.py keeps the enums in step)."""
    return json_format.ParseDict(_wire(to_jsonable_python(data), cls.DESCRIPTOR), cls())


def _wire(data: Mapping[str, Any], descriptor: Descriptor) -> dict[str, Any]:
    wire = {}
    for field in descriptor.fields:
        value = data.get(field.name)
        if value is None:
            continue
        wire[field.name] = ([_wire_value(field, v) for v in value] if field.is_repeated
                            else _wire_value(field, value))
    return wire


def _wire_value(field: FieldDescriptor, value: Any) -> Any:
    if field.enum_type is not None:
        return enum_prefix(field.enum_type) + str(value).upper()
    if field.message_type is not None and field.message_type.full_name not in _JSON_TYPES:
        return _wire(value, field.message_type)
    return value


def enum_value(message: Message, field: str) -> str | None:
    """A request's enum field as the database value; None when UNSPECIFIED."""
    descriptor = message.DESCRIPTOR.fields_by_name[field].enum_type
    number = getattr(message, field)
    return descriptor.values_by_number[number].name.removeprefix(enum_prefix(descriptor)).lower() if number else None


def _id(value: str, name: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ConnectError(Code.INVALID_ARGUMENT, f"{name} must be a UUID") from None


def _optional(message: Message, field: str) -> Any:
    return getattr(message, field) if message.HasField(field) else None


# --- running a call ---


def connect_error(exc: Exception) -> ConnectError:
    """What the caller sees. Only errors meant for users carry their message; anything else is logged and
    reported as INTERNAL, because connect-python would otherwise send str(exc) to the client."""
    if isinstance(exc, ConnectError):
        return exc
    if isinstance(exc, HTTPException):
        return ConnectError(HTTP_TO_CODE.get(exc.status_code, Code.UNKNOWN), str(exc.detail))
    if isinstance(exc, ValidationError):
        return ConnectError(Code.INVALID_ARGUMENT, "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'request'}: {e['msg']}" for e in exc.errors()))
    if (status := status_for(exc)) is not None:
        return ConnectError(HTTP_TO_CODE[status], str(exc))
    log.error("rpc.unhandled_error", error_type=type(exc).__name__, exc_info=exc)
    return ConnectError(Code.INTERNAL, "internal error")


def start_background(background: BackgroundTasks) -> None:
    """Run what the REST handler queued (a workflow start or resume) once the call's transaction has committed,
    with the call's log context. Tests replace this to run the tasks inline."""
    for task in background.tasks:
        context = contextvars.copy_context()
        threading.Thread(target=context.run, args=(task.func, *task.args), kwargs=task.kwargs,
                         name="rpc-background", daemon=True).start()


class _Service:
    def __init__(self, rt: Runtime):
        self.rt = rt

    async def _call(self, ctx: RequestContext, work: Callable[..., Message], *, signed_in: bool = True) -> Message:
        """`work(session, actor, background)` in one transaction, off the event loop (the services are sync)."""

        def run() -> Message:
            background = BackgroundTasks()
            with self.rt.unit_of_work() as session:
                actor = authenticate(session, self.rt, _bearer(ctx)) if signed_in else None
                result = work(session, actor, background)
            start_background(background)
            return result

        try:
            return await run_in_threadpool(run)
        except Exception as exc:
            raise connect_error(exc) from None


def _bearer(ctx: RequestContext) -> str | None:
    scheme, _, token = ctx.request_headers().get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else None


class IdentityService(_Service):
    async def login(self, request: identity_pb.LoginRequest, ctx: RequestContext) -> identity_pb.LoginResponse:
        def work(session: Session, _actor: None, _background: BackgroundTasks):
            body = LoginIn(tenant=request.tenant, email=request.email, password=request.password)
            return to_message(auth.login(body, session=session, rt=self.rt), identity_pb.LoginResponse)

        return await self._call(ctx, work, signed_in=False)

    async def get_me(self, request: identity_pb.GetMeRequest, ctx: RequestContext) -> identity_pb.GetMeResponse:
        return await self._call(ctx, lambda session, actor, _: to_message(
            auth.me(actor=actor, session=session), identity_pb.GetMeResponse))

    async def list_users(self, request: identity_pb.ListUsersRequest,
                         ctx: RequestContext) -> identity_pb.ListUsersResponse:
        return await self._call(ctx, lambda session, actor, _: to_message(
            {"users": users.list_users(actor=actor, session=session)}, identity_pb.ListUsersResponse))


class CaseService(_Service):
    async def list_cases(self, request: cases_pb.ListCasesRequest, ctx: RequestContext) -> cases_pb.ListCasesResponse:
        limit = request.limit or 50
        if not 1 <= limit <= 200 or request.offset < 0:
            raise ConnectError(Code.INVALID_ARGUMENT, "limit must be 1..200 and offset at least 0")
        assigned_to = _id(request.assigned_to, "assigned_to") if request.HasField("assigned_to") else None

        status, priority = enum_value(request, "status"), enum_value(request, "priority")

        def work(session: Session, actor: Actor, _: BackgroundTasks):
            result = cases.list_cases(status_filter=status and CaseStatus(status),
                                      priority=priority and Severity(priority), assigned_to=assigned_to,
                                      limit=limit, offset=request.offset, actor=actor, session=session)
            return to_message(result, cases_pb.ListCasesResponse)

        return await self._call(ctx, work)

    async def get_case(self, request: cases_pb.GetCaseRequest, ctx: RequestContext) -> cases_pb.GetCaseResponse:
        case_id = _id(request.case_id, "case_id")
        return await self._call(ctx, lambda session, actor, _: to_message(
            {"detail": cases.case_detail(case_id, actor=actor, session=session)}, cases_pb.GetCaseResponse))

    async def assign_case(self, request: cases_pb.AssignCaseRequest,
                          ctx: RequestContext) -> cases_pb.AssignCaseResponse:
        case_id, assignee = _id(request.case_id, "case_id"), _id(request.assignee_id, "assignee_id")

        def work(session: Session, actor: Actor, _: BackgroundTasks):
            case = cases.assign(case_id, AssignIn(assignee_id=assignee), actor=actor, session=session)
            return to_message({"summary": CaseSummaryOut.model_validate(case)}, cases_pb.AssignCaseResponse)

        return await self._call(ctx, work)

    async def transition_case(self, request: cases_pb.TransitionCaseRequest,
                              ctx: RequestContext) -> cases_pb.TransitionCaseResponse:
        case_id = _id(request.case_id, "case_id")

        def work(session: Session, actor: Actor, _: BackgroundTasks):
            body = TransitionIn(to_status=enum_value(request, "to_status"), comment=_optional(request, "comment"),
                                closure_reason=enum_value(request, "closure_reason"))
            case = cases.transition(case_id, body, actor=actor, session=session)
            return to_message({"summary": CaseSummaryOut.model_validate(case)}, cases_pb.TransitionCaseResponse)

        return await self._call(ctx, work)

    async def add_note(self, request: cases_pb.AddNoteRequest, ctx: RequestContext) -> cases_pb.AddNoteResponse:
        case_id = _id(request.case_id, "case_id")

        def work(session: Session, actor: Actor, _: BackgroundTasks):
            evidence = cases.note(case_id, NoteIn(text=request.text), actor=actor, session=session)
            return to_message({"evidence": EvidenceOut.model_validate(evidence)}, cases_pb.AddNoteResponse)

        return await self._call(ctx, work)

    async def start_investigation(self, request: cases_pb.StartInvestigationRequest,
                                  ctx: RequestContext) -> cases_pb.StartInvestigationResponse:
        case_id = _id(request.case_id, "case_id")
        return await self._call(ctx, lambda session, actor, background: to_message(
            {"workflow": cases.investigate(case_id, background, actor=actor, session=session, rt=self.rt)},
            cases_pb.StartInvestigationResponse))

    async def get_workflow(self, request: cases_pb.GetWorkflowRequest,
                           ctx: RequestContext) -> cases_pb.GetWorkflowResponse:
        case_id = _id(request.case_id, "case_id")
        return await self._call(ctx, lambda session, actor, _: to_message(
            {"workflow": cases.workflow(case_id, actor=actor, session=session, rt=self.rt)},
            cases_pb.GetWorkflowResponse))

    async def list_case_audit(self, request: cases_pb.ListCaseAuditRequest,
                              ctx: RequestContext) -> cases_pb.ListCaseAuditResponse:
        case_id = _id(request.case_id, "case_id")
        return await self._call(ctx, lambda session, actor, _: to_message(
            {"events": [AuditEventOut.model_validate(e) for e in cases.case_audit(case_id, actor=actor,
                                                                                 session=session)]},
            cases_pb.ListCaseAuditResponse))


class ApprovalService(_Service):
    async def list_pending_approvals(self, request: approvals_pb.ListPendingApprovalsRequest,
                                     ctx: RequestContext) -> approvals_pb.ListPendingApprovalsResponse:
        return await self._call(ctx, lambda session, actor, _: to_message(
            {"approvals": approvals.pending(actor=actor, session=session)},
            approvals_pb.ListPendingApprovalsResponse))

    async def decide_approval(self, request: approvals_pb.DecideApprovalRequest,
                              ctx: RequestContext) -> approvals_pb.DecideApprovalResponse:
        request_id = _id(request.request_id, "request_id")
        considered = ([_id(i, "evidence_ids") for i in request.evidence_considered.evidence_ids]
                      if request.HasField("evidence_considered") else None)

        def work(session: Session, actor: Actor, background: BackgroundTasks):
            body = DecisionIn(decision=enum_value(request, "decision"), comment=_optional(request, "comment"),
                              evidence_considered_ids=considered)
            row = approvals.decide_request(request_id, body, background, actor=actor, session=session, rt=self.rt)
            return to_message({"decision": DecisionOut.model_validate(row)}, approvals_pb.DecideApprovalResponse)

        return await self._call(ctx, work)


class TelemetryInterceptor:
    """Names the request's OpenTelemetry span after the RPC and records the Connect error code, for every call,
    including requests refused before a service method runs."""

    async def intercept_unary(self, call_next, request, ctx: RequestContext):
        method = ctx.method()
        telemetry.name_rpc_span(method.service_name, method.name)
        try:
            return await call_next(request, ctx)
        except ConnectError as error:
            telemetry.record_rpc_error(error.code.value)
            raise


def rpc_applications(rt: Runtime) -> list:
    """The ASGI apps to mount, each at its own path (/finrecon.v1.CaseService/...)."""
    interceptors = [TelemetryInterceptor()]
    return [IdentityServiceASGIApplication(IdentityService(rt), interceptors=interceptors),
            CaseServiceASGIApplication(CaseService(rt), interceptors=interceptors),
            ApprovalServiceASGIApplication(ApprovalService(rt), interceptors=interceptors)]
