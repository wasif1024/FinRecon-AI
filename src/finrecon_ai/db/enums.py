from enum import StrEnum


class RoleName(StrEnum):
    ANALYST = "analyst"
    MANAGER = "manager"
    ADMIN = "admin"
    AUDITOR = "auditor"


# --- Ingestion (FR-01, FR-02) ---


class SourceType(StrEnum):
    BANK_STATEMENT = "bank_statement"
    PAYMENT_GATEWAY = "payment_gateway"
    MERCHANT_SETTLEMENT = "merchant_settlement"


class IngestionMethod(StrEnum):
    CSV = "csv"
    JSON = "json"
    SIMULATED_API = "simulated_api"


class ImportBatchStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class Direction(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class TransactionStatus(StrEnum):
    PENDING = "pending"
    SETTLED = "settled"
    FAILED = "failed"
    REVERSED = "reversed"
    REFUNDED = "refunded"


# --- Reconciliation (FR-03, FR-04, FR-08) ---


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MatchClassification(StrEnum):
    MATCHED = "matched"
    PARTIALLY_MATCHED = "partially_matched"
    DUPLICATE = "duplicate"
    AMOUNT_MISMATCH = "amount_mismatch"
    DATE_MISMATCH = "date_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"


class MatchStrategy(StrEnum):
    EXACT_REFERENCE = "exact_reference"
    AMOUNT_CURRENCY = "amount_currency"
    DATE_WINDOW = "date_window"
    COUNTERPARTY = "counterparty"
    TOLERANCE = "tolerance"


class ExceptionCategory(StrEnum):
    UNMATCHED = "unmatched"
    PARTIALLY_MATCHED = "partially_matched"
    CONFIRMED_DUPLICATE = "confirmed_duplicate"
    POTENTIAL_DUPLICATE = "potential_duplicate"
    AMOUNT_MISMATCH = "amount_mismatch"
    DATE_MISMATCH = "date_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    FEE_DISCREPANCY = "fee_discrepancy"
    MISSING_SETTLEMENT = "missing_settlement"
    ANOMALY = "anomaly"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ExceptionStatus(StrEnum):
    OPEN = "open"
    CASE_CREATED = "case_created"
    DISMISSED = "dismissed"


# --- Investigation (FR-05, FR-06, FR-10) ---


class CaseStatus(StrEnum):
    NEW = "new"
    INVESTIGATING = "investigating"
    AWAITING_EVIDENCE = "awaiting_evidence"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESOLVED = "resolved"
    CLOSED = "closed"


class EvidenceType(StrEnum):
    TRANSACTION = "transaction"
    POLICY_PASSAGE = "policy_passage"
    SUPPORTING_DOCUMENT = "supporting_document"
    HISTORICAL_CASE = "historical_case"
    ANALYST_NOTE = "analyst_note"


class FindingType(StrEnum):
    """FR-06: verified facts must be kept apart from AI hypotheses."""

    VERIFIED_FACT = "verified_fact"
    HYPOTHESIS = "hypothesis"
    OUTSTANDING_REQUIREMENT = "outstanding_requirement"


class ResolutionType(StrEnum):
    CONFIRM_FEE_DISCREPANCY = "confirm_fee_discrepancy"
    REQUEST_DOCUMENTATION = "request_documentation"
    INVESTIGATE_DUPLICATE = "investigate_duplicate"
    ESCALATE = "escalate"
    MANUAL_MATCH = "manual_match"
    NO_ACTION_REQUIRED = "no_action_required"


class RecommendationStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXECUTED = "executed"


# --- Approval (FR-09) ---


class ApprovalRequestStatus(StrEnum):
    PENDING = "pending"
    DECIDED = "decided"
    CANCELLED = "cancelled"


class ApprovalDecisionType(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_EVIDENCE = "request_evidence"
    RETURN_FOR_INVESTIGATION = "return_for_investigation"


# --- Governance (FR-07, FR-12, §7) ---


class ActorType(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class PolicyDocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"
    ARCHIVED = "archived"


class AgentName(StrEnum):
    INGESTION = "ingestion"
    RECONCILIATION = "reconciliation"
    INVESTIGATION = "investigation"
    RISK_ANALYSIS = "risk_analysis"
    RESOLUTION = "resolution"


class AgentExecutionStatus(StrEnum):
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
