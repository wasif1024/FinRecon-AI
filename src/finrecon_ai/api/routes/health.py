from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from finrecon_ai.api.deps import Runtime, runtime
from finrecon_ai.log import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
def health(rt: Runtime = Depends(runtime)) -> JSONResponse:
    """Unauthenticated; reports component status only, never details that would help an attacker."""
    checks = {"database": _check(lambda: _db(rt)), "vector_store": _check(lambda: rt.policy_store().client.get_collections())}
    healthy = checks["database"] == "ok"  # search can degrade without taking the API down
    return JSONResponse({"status": "ok" if healthy else "degraded", **checks}, status_code=200 if healthy else 503)


def _db(rt: Runtime) -> None:
    with rt.unit_of_work() as session:
        session.execute(text("SELECT 1"))


def _check(probe) -> str:
    try:
        probe()
        return "ok"
    except Exception as exc:  # noqa: BLE001 - health must not raise
        log.warning("health.check_failed", error_type=type(exc).__name__)
        return "unavailable"
