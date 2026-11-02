"""Read-only tools the agents may call (SRS §7, §11: restricted agent tool permissions).

Every tool is bound to one tenant and one case through ToolContext: the model cannot name another case or
tenant. Tools only read. Every identifier a tool shows (UUID, source transaction ID, case number, policy chunk
ID) is remembered, so evidence the agent cites can be resolved and checked against what it actually retrieved.
"""

import statistics
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from finrecon_ai.agents.schemas import strict_json_schema
from finrecon_ai.db.enums import ExceptionCategory, SourceType
from finrecon_ai.db.models import (
    FinancialSource,
    InvestigationCase,
    ReconciliationException,
    ResolutionRecommendation,
    Transaction,
    TransactionMatch,
    exception_transactions,
    transaction_match_items,
)
from finrecon_ai.policies.store import PolicyStore

MAX_RESULTS = 20
MAX_POLICY_PASSAGES = 8
AMBIGUOUS = "<ambiguous>"  # an alias shown for two different records cannot be cited by that alias


class ToolError(Exception):
    """Reported back to the model as an is_error tool result; the run continues."""


@dataclass
class ToolContext:
    session: Session
    tenant_id: uuid.UUID
    case: InvestigationCase
    exception: ReconciliationException
    policy_store: PolicyStore | None = None
    # kind -> identifier as shown to the model -> canonical ID
    identifiers: dict[str, dict[str, str]] = field(default_factory=lambda: defaultdict(dict))

    def remember(self, kind: str, canonical: Any, *aliases: Any) -> None:
        for alias in (canonical, *aliases):
            if alias is None:
                continue
            known = self.identifiers[kind].get(str(alias))
            self.identifiers[kind][str(alias)] = str(canonical) if known in (None, str(canonical)) else AMBIGUOUS

    def resolve(self, kind: str, identifier: str) -> str | None:
        """Canonical ID for something a tool returned during this run; None if never seen or ambiguous."""
        value = self.identifiers.get(kind, {}).get(identifier.strip())
        return None if value in (None, AMBIGUOUS) else value

    @property
    def seen(self) -> dict[str, set[str]]:
        return {kind: {v for v in ids.values() if v != AMBIGUOUS} for kind, ids in self.identifiers.items()}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], dict[str, Any]]

    def definition(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "strict": True,
                "input_schema": strict_json_schema(self.input_model)}


class _NoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- case tools ---


def _get_case(ctx: ToolContext, _: _NoInput) -> dict[str, Any]:
    case, exc = ctx.case, ctx.exception
    return {
        "case_id": str(case.id), "case_number": case.case_number, "title": case.title, "status": case.status,
        "priority": case.priority, "category": exc.category, "severity": exc.severity,
        "rule_code": exc.rule_code, "discrepancy_amount": _money(exc.discrepancy_amount),
        "currency": exc.currency, "engine_details": exc.details, "opened_at": case.created_at.isoformat(),
    }


def _get_case_transactions(ctx: ToolContext, _: _NoInput) -> dict[str, Any]:
    rows = ctx.session.execute(
        _txn_select().join(exception_transactions, exception_transactions.c.transaction_id == Transaction.id)
        .where(exception_transactions.c.exception_id == ctx.exception.id)
        .order_by(Transaction.transaction_date, Transaction.source_transaction_id)
    ).all()
    return {"transactions": [_txn(ctx, t, source_type, include_raw=True) for t, source_type in rows]}


def _get_match_explanation(ctx: ToolContext, _: _NoInput) -> dict[str, Any]:
    """Every match in this run touching the case's transactions: both legs, with rule-by-rule results."""
    txn_ids = select(exception_transactions.c.transaction_id).where(
        exception_transactions.c.exception_id == ctx.exception.id)
    match_ids = select(transaction_match_items.c.match_id).where(transaction_match_items.c.transaction_id.in_(txn_ids))
    matches = ctx.session.scalars(
        select(TransactionMatch).where(TransactionMatch.run_id == ctx.exception.run_id,
                                       TransactionMatch.id.in_(match_ids))
    ).all()
    result = []
    for match in matches:
        members = ctx.session.scalars(select(transaction_match_items.c.transaction_id)
                                      .where(transaction_match_items.c.match_id == match.id)).all()
        for member in members:
            ctx.remember("transaction", member)
        result.append({"match_id": str(match.id), "classification": match.classification,
                       "strategy": match.strategy, "score": str(match.score),
                       "transaction_ids": [str(m) for m in members], "explanation": match.explanation})
    return {"matches": result,
            "note": None if result else "no match involves these transactions; they were never paired"}


# --- search tools ---


class SearchTransactionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: str | None
    amount: str | None
    currency: str | None
    counterparty: str | None
    source_type: SourceType | None
    date_from: str | None
    date_to: str | None


def _search_transactions(ctx: ToolContext, args: SearchTransactionsInput) -> dict[str, Any]:
    filters = [Transaction.tenant_id == ctx.tenant_id]
    if args.reference:
        pattern = f"%{_escape_like(args.reference.strip())}%"
        filters.append(or_(Transaction.external_reference.ilike(pattern),
                           Transaction.source_transaction_id.ilike(pattern),
                           Transaction.description.ilike(pattern)))
    if args.amount:
        filters.append(Transaction.amount == _parse_amount(args.amount))
    if args.currency:
        filters.append(Transaction.currency == args.currency.strip().upper())
    if args.counterparty:
        filters.append(Transaction.counterparty.ilike(f"%{_escape_like(args.counterparty.strip())}%"))
    if args.source_type:
        filters.append(FinancialSource.source_type == args.source_type)
    if args.date_from:
        filters.append(Transaction.transaction_date >= _parse_date(args.date_from))
    if args.date_to:
        filters.append(Transaction.transaction_date <= _parse_date(args.date_to))
    if len(filters) == 1:
        raise ToolError("give at least one filter")

    rows = ctx.session.execute(
        _txn_select().where(and_(*filters)).order_by(Transaction.transaction_date).limit(MAX_RESULTS + 1)
    ).all()
    return {"transactions": [_txn(ctx, t, s) for t, s in rows[:MAX_RESULTS]],
            "truncated": len(rows) > MAX_RESULTS}


class CounterpartyHistoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    counterparty: str
    merchant: str | None


def _get_counterparty_history(ctx: ToolContext, args: CounterpartyHistoryInput) -> dict[str, Any]:
    filters = [Transaction.tenant_id == ctx.tenant_id, func.lower(Transaction.counterparty) ==
               args.counterparty.strip().lower()]
    if args.merchant:
        filters.append(func.lower(Transaction.merchant) == args.merchant.strip().lower())
    rows = ctx.session.execute(_txn_select().where(*filters).order_by(Transaction.transaction_date.desc())).all()
    if not rows:
        return {"found": False, "note": "no transactions for this counterparty"}

    by_currency: dict[str, list[Decimal]] = defaultdict(list)
    for t, _ in rows:
        by_currency[t.currency].append(t.amount)
    stats = {cur: {"count": len(a), "median": _money(statistics.median(a)), "min": _money(min(a)),
                   "max": _money(max(a))} for cur, a in sorted(by_currency.items())}
    return {"found": True, "first_seen": rows[-1][0].transaction_date.isoformat(),
            "last_seen": rows[0][0].transaction_date.isoformat(), "per_currency": stats,
            "recent": [_txn(ctx, t, s) for t, s in rows[:10]]}


class SimilarCasesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: ExceptionCategory | None


def _find_similar_cases(ctx: ToolContext, args: SimilarCasesInput) -> dict[str, Any]:
    category = args.category or ctx.exception.category
    cases = ctx.session.execute(
        select(InvestigationCase, ReconciliationException.category)
        .join(ReconciliationException, ReconciliationException.id == InvestigationCase.exception_id)
        .where(InvestigationCase.tenant_id == ctx.tenant_id, InvestigationCase.id != ctx.case.id,
               ReconciliationException.category == category)
        .order_by(InvestigationCase.closed_at.desc().nulls_last(), InvestigationCase.created_at.desc())
        .limit(10)
    ).all()
    result = []
    for case, cat in cases:
        ctx.remember("historical_case", case.id, case.case_number)
        latest = ctx.session.scalar(select(ResolutionRecommendation.summary)
                                    .where(ResolutionRecommendation.case_id == case.id)
                                    .order_by(ResolutionRecommendation.version.desc()).limit(1))
        result.append({"case_id": str(case.id), "case_number": case.case_number, "category": cat,
                       "title": case.title, "status": case.status, "closure_reason": case.closure_reason,
                       "latest_recommendation": latest})
    return {"category": category, "cases": result}


class PolicySearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    limit: int | None


def _search_policies(ctx: ToolContext, args: PolicySearchInput) -> dict[str, Any]:
    if ctx.policy_store is None:
        raise ToolError("policy search is not available in this environment")
    if not args.query.strip():
        raise ToolError("query is empty")
    limit = max(1, min(args.limit or 5, MAX_POLICY_PASSAGES))
    hits = ctx.policy_store.search(tenant_id=ctx.tenant_id, query=args.query, limit=limit)
    for hit in hits:
        ctx.remember("policy_passage", hit.chunk_id)
    return {"passages": [{"chunk_id": h.chunk_id, "document": h.title, "version": h.version,
                          "section": h.section, "page": h.page, "score": h.score, "text": h.text} for h in hits],
            "note": None if hits else "no policy passages found; say so rather than assuming what policy requires"}


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("get_case", "The case under investigation: category, severity, discrepancy and the matching "
         "engine's details for the exception.", _NoInput, _get_case),
    Tool("get_case_transactions", "All transactions linked to this case, normalized, with the original source "
         "record (raw) as imported.", _NoInput, _get_case_transactions),
    Tool("get_match_explanation", "Every match in this reconciliation run that involves the case's transactions, "
         "with each rule the engine evaluated (reference, currency, amount, fee, date window, merchant) and "
         "whether it passed.", _NoInput, _get_match_explanation),
    Tool("search_transactions", "Search this tenant's transactions, e.g. for a counterpart under a slightly "
         "different reference. Reference matches part of the reference, ID or description. Amount is an exact "
         "decimal string like \"125.40\"; dates are YYYY-MM-DD. Set unused filters to null; give at least one. "
         f"Returns at most {MAX_RESULTS}.", SearchTransactionsInput, _search_transactions),
    Tool("get_counterparty_history", "A counterparty's transaction history: per-currency count, median, min and "
         "max amount, and the 10 most recent records. Optionally restricted to one merchant.",
         CounterpartyHistoryInput, _get_counterparty_history),
    Tool("find_similar_cases", "Up to 10 other cases of the same category (or the one given), with how they were "
         "closed and the latest recommendation.", SimilarCasesInput, _find_similar_cases),
    Tool("search_policies", "Search the organisation's current financial policies and procedures by meaning. "
         "Returns passages with document title, version, section and chunk_id; cite the chunk_id. Write the query "
         f"as a topic, e.g. \"acceptable variance for gateway processing fees\". limit defaults to 5, max "
         f"{MAX_POLICY_PASSAGES}.", PolicySearchInput, _search_policies),
]}


def run_tool(ctx: ToolContext, tool: Tool, raw_input: dict[str, Any]) -> dict[str, Any]:
    try:
        args = tool.input_model.model_validate(raw_input)
    except ValueError as exc:
        raise ToolError(f"invalid input: {exc}") from None
    return tool.handler(ctx, args)


# --- helpers ---


def _txn_select():
    return select(Transaction, FinancialSource.source_type).join(FinancialSource,
                                                                 FinancialSource.id == Transaction.source_id)


def _txn(ctx: ToolContext, t: Transaction, source_type: SourceType, include_raw: bool = False) -> dict[str, Any]:
    ctx.remember("transaction", t.id, t.source_transaction_id)
    data = {
        "id": str(t.id), "source_type": source_type, "source_transaction_id": t.source_transaction_id,
        "external_reference": t.external_reference, "transaction_date": t.transaction_date.isoformat(),
        "transaction_timestamp": t.transaction_timestamp.isoformat() if t.transaction_timestamp else None,
        "settlement_date": t.settlement_date.isoformat() if t.settlement_date else None,
        "amount": _money(t.amount), "fee_amount": _money(t.fee_amount), "currency": t.currency,
        "direction": t.direction, "counterparty": t.counterparty, "merchant": t.merchant,
        "description": t.description, "status": t.status,
    }
    if include_raw:
        data["raw"] = t.raw_payload
    return data


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value.normalize():f}"


def _parse_amount(value: str) -> Decimal:
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        raise ToolError(f"amount must be a decimal string, got {value!r}") from None


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ToolError(f"date must be YYYY-MM-DD, got {value!r}") from None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
