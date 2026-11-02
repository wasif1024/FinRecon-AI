"""How service errors reach clients: one table for the REST handlers and the RPC services."""

from finrecon_ai.approvals.executor import ExecutionError
from finrecon_ai.approvals.service import ApprovalError
from finrecon_ai.authz import PermissionDenied
from finrecon_ai.cases.service import CaseError, InvalidTransition
from finrecon_ai.errors import NotFound
from finrecon_ai.ingestion.service import IngestionError
from finrecon_ai.policies.parsing import DocumentRejected
from finrecon_ai.reconciliation.service import ReconciliationError
from finrecon_ai.workflows.case_investigation import WorkflowError

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


def status_for(exc: Exception) -> int | None:
    return next((status for error_type, status in ERROR_STATUS if isinstance(exc, error_type)), None)
