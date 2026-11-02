"""FastAPI application with the Connect/gRPC services mounted on it. Run with: uv run finrecon serve

Every endpoint and RPC goes through the same permission-checked services as the CLI; the API adds authentication,
error mapping and request IDs, never its own business rules.
"""

import re
import threading
from contextlib import asynccontextmanager
from functools import cache

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from finrecon_ai.api.deps import Runtime
from finrecon_ai.api.errors import ERROR_STATUS
from finrecon_ai.api.routes import (
    approvals,
    audit,
    auth,
    cases,
    health,
    metrics,
    policies,
    reconciliation,
    reports,
    sources,
    users,
)
from finrecon_ai.api.rpc import rpc_applications
from finrecon_ai.api.security import MIN_SECRET_LENGTH, LoginThrottle
from finrecon_ai.config import Settings, get_settings
from finrecon_ai.log import configure_logging, get_logger, log_context, new_correlation_id

log = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

def create_app(settings: Settings | None = None, rt: Runtime | None = None) -> FastAPI:
    settings = settings or get_settings()
    recover = rt is None and settings.recover_on_startup  # tests pass their own runtime
    if rt is None:
        configure_logging(settings.log_level, settings.log_format, settings.log_file or None)
        from finrecon_ai.tracing import configure_tracing

        configure_tracing(settings)
        from finrecon_ai.telemetry import configure_telemetry

        configure_telemetry(settings, "finrecon-api")  # before the engine exists, so queries are traced
        rt = default_runtime(settings)
    if len(rt.jwt_secret or "") < MIN_SECRET_LENGTH:
        raise RuntimeError(f"JWT_SECRET must be set to at least {MIN_SECRET_LENGTH} characters")

    app = FastAPI(title="FinRecon AI", version="0.1.0", lifespan=_lifespan if recover else None,
                  description="Agentic financial reconciliation with human-approved resolutions.")
    app.state.runtime = rt
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                       allow_methods=["*"], allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
                       expose_headers=[REQUEST_ID_HEADER])

    app.add_middleware(RequestContext)

    for error_type, status_code in ERROR_STATUS:
        app.add_exception_handler(error_type, _handler(status_code))

    for module in (health, auth, users, sources, reconciliation, cases, approvals, policies, reports, metrics, audit):
        app.include_router(module.router)
    for rpc_app in rpc_applications(rt):
        app.mount(rpc_app.path, rpc_app)
    from finrecon_ai.telemetry import instrument_app

    instrument_app(app)
    return app


class RequestContext:
    """Request ID, request log line and the last-resort 500, for REST and RPC alike. Plain ASGI rather than
    @app.middleware("http"), which would drop the HTTP trailers gRPC sends its status in."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER, "")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else new_correlation_id()
        status: int | None = None

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", [])
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        with log_context(correlation_id=request_id):
            try:
                await self.app(scope, receive, send_with_id)
            except Exception:
                log.exception("api.unhandled_error", method=scope["method"], path=scope["path"])
                if status is not None:  # the response has started; nothing more can be sent
                    raise
                await JSONResponse({"detail": "internal error", "request_id": request_id}, status_code=500,
                                   headers={REQUEST_ID_HEADER: request_id})(scope, receive, send)
                status = 500
            log.info("api.request", method=scope["method"], path=scope["path"], status=status)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Finish runs a previous API process left mid-way (deploy, crash), in the background."""
    from finrecon_ai.workflows.case_investigation import recover_interrupted

    rt: Runtime = app.state.runtime
    threading.Thread(target=recover_interrupted, args=(rt.workflow_deps(), rt.checkpointer, rt.run_lock),
                     name="workflow-recovery", daemon=True).start()
    yield


def _handler(status_code: int):
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=status_code)

    return handle


def _warn_if_owner_login() -> None:
    from finrecon_ai.db.roles import report
    from finrecon_ai.db.session import get_engine

    with get_engine().connect() as connection:
        login = report(connection)
    if not login.safe_for_application:
        log.warning("db.application_login_not_restricted", user=login.user, owned_tables=login.owned_tables,
                    superuser=login.superuser, bypass_rls=login.bypass_rls,
                    hint="connect as a role that owns nothing: finrecon db grant-app-role (README)")


def default_runtime(settings: Settings) -> Runtime:
    from finrecon_ai.agents.llm import default_client
    from finrecon_ai.agents.workspace import default_workspaces
    from finrecon_ai.policies.store import default_store
    from finrecon_ai.workflows.case_investigation import (
        WorkflowDeps,
        case_run_lock,
        ensure_checkpoint_tables,
        postgres_checkpointer,
        session_unit_of_work,
    )

    ensure_checkpoint_tables()  # at startup, before any request opens a transaction
    _warn_if_owner_login()
    unit_of_work = session_unit_of_work()
    store = cache(default_store)
    llm = cache(default_client)
    workspaces = cache(default_workspaces)
    return Runtime(
        jwt_secret=settings.jwt_secret or "", jwt_ttl_minutes=settings.jwt_ttl_minutes, unit_of_work=unit_of_work,
        workflow_deps=lambda: WorkflowDeps(unit_of_work=unit_of_work, llm=llm(), policy_store=store(),
                                           workspaces=workspaces()),
        checkpointer=postgres_checkpointer, policy_store=store, run_lock=case_run_lock, throttle=LoginThrottle(),
    )
