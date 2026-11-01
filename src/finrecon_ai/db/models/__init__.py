"""Import every model so Base.metadata is complete for Alembic."""

from finrecon_ai.db.models.approval import ApprovalDecision, ApprovalRequest
from finrecon_ai.db.models.governance import AgentExecutionRecord, AuditEvent, PolicyDocument
from finrecon_ai.db.models.identity import Role, Tenant, User, user_roles
from finrecon_ai.db.models.ingestion import FinancialSource, ImportBatch, Transaction
from finrecon_ai.db.models.investigation import (
    InvestigationCase,
    InvestigationEvidence,
    InvestigationFinding,
    ResolutionRecommendation,
)
from finrecon_ai.db.models.reconciliation import (
    ReconciliationException,
    ReconciliationRun,
    TransactionMatch,
    exception_transactions,
    transaction_match_items,
)

__all__ = [
    "AgentExecutionRecord",
    "ApprovalDecision",
    "ApprovalRequest",
    "AuditEvent",
    "FinancialSource",
    "ImportBatch",
    "InvestigationCase",
    "InvestigationEvidence",
    "InvestigationFinding",
    "PolicyDocument",
    "ReconciliationException",
    "ReconciliationRun",
    "ResolutionRecommendation",
    "Role",
    "Tenant",
    "Transaction",
    "TransactionMatch",
    "User",
    "exception_transactions",
    "transaction_match_items",
    "user_roles",
]
