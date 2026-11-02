"""FastAPI application. Run with: uv run finrecon serve  (or uvicorn finrecon_ai.api.app:create_app --factory)

Every endpoint goes through the same permission-checked services as the CLI; the API adds authentication,
HTTP error mapping and request IDs, never its own business rules.
"""

import re
from functools import cache

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from finrecon_ai.api.deps import Runtime
from finrecon_ai.api.routes import (
    approvals,
    audit,
    auth,
    cases,
    health,
    policies,
    reconciliation,
    reports,
    sources,
    users,
)
from finrecon_ai.api.security import MIN_SECRET_LENGTH, LoginThrottle
from finrecon_ai.approvals.executor import ExecutionError
from finrecon_ai.approvals.service import ApprovalError
from finrecon_ai.authz import PermissionDenied
from finrecon_ai.cases.service import CaseError, InvalidTransition
from finrecon_ai.config import Settings, get_settings
from finrecon_ai.errors import NotFound
from finrecon_ai.ingestion.service import IngestionError
from finrecon_ai.log import configure_logging, get_logger, log_context, new_correlation_id
from finrecon_ai.policies.parsing import DocumentRejected
from finrecon_ai.reconciliation.service import ReconciliationError
from finrecon_ai.workflows.case_investigation import WorkflowError

log = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# Most specific first. Service messages are safe to show: they never contain other tenants' data.
ERROR_STATUS: list[tuple[type[Exception], int]] = [
    (PermissionDenied, 403),
    (NotFound, 404),
    (InvalidTransition, 409),
    (WorkflowError, 409),
    (ExecutionError, 409),
    (ApprovalError, 409),
    (CaseError, 422),
    (IngestionError, 422),
    (ReconciliationError, 422),
    (DocumentRejected, 422),
]


def create_app(settings: Settings | None = None, rt: Runtime | None = None) -> FastAPI:
    settings = settings or get_settings()
    if rt is None:
        configure_logging(settings.log_level, settings.log_format, settings.log_file or None)
        rt = default_runtime(settings)
    if len(rt.jwt_secret or "") < MIN_SECRET_LENGTH:
        raise RuntimeError(f"JWT_SECRET must be set to at least {MIN_SECRET_LENGTH} characters")

    app = FastAPI(title="FinRecon AI", version="0.1.0",
                  description="Agentic financial reconciliation with human-approved resolutions.")
    app.state.runtime = rt
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                       allow_methods=["*"], allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
                       expose_headers=[REQUEST_ID_HEADER])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else new_correlation_id()
        with log_context(correlation_id=request_id):
            try:
                response = await call_next(request)
            except Exception:
                log.exception("api.unhandled_error", method=request.method, path=request.url.path)
                response = JSONResponse({"detail": "internal error", "request_id": request_id}, status_code=500)
            log.info("api.request", method=request.method, path=request.url.path, status=response.status_code)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    for error_type, status_code in ERROR_STATUS:
        app.add_exception_handler(error_type, _handler(status_code))

    for module in (health, auth, users, sources, reconciliation, cases, approvals, policies, reports, audit):
        app.include_router(module.router)
    return app


def _handler(status_code: int):
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=status_code)

    return handle


def default_runtime(settings: Settings) -> Runtime:
    from finrecon_ai.agents.llm import default_client
    from finrecon_ai.policies.store import default_store
    from finrecon_ai.workflows.case_investigation import (
        WorkflowDeps,
        case_run_lock,
        ensure_checkpoint_tables,
        postgres_checkpointer,
        session_unit_of_work,
    )

    ensure_checkpoint_tables()  # at startup, before any request opens a transaction
    unit_of_work = session_unit_of_work()
    store = cache(default_store)
    llm = cache(default_client)
    return Runtime(
        jwt_secret=settings.jwt_secret or "", jwt_ttl_minutes=settings.jwt_ttl_minutes, unit_of_work=unit_of_work,
        workflow_deps=lambda: WorkflowDeps(unit_of_work=unit_of_work, llm=llm(), policy_store=store()),
        checkpointer=postgres_checkpointer, policy_store=store, run_lock=case_run_lock, throttle=LoginThrottle(),
    )
