"""Reconciliation reports (FR-11): built once as a table, rendered as JSON, CSV or PDF.

Amounts are never added across currencies (SRS §10): every total is per currency. CSV cells that a spreadsheet
would run as a formula are neutralised, because imported data is stored verbatim and can contain one.
"""

import csv
import io
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finrecon_ai.authz import Actor, Permission
from finrecon_ai.db.enums import CaseStatus, ExceptionCategory, Severity
from finrecon_ai.db.models import (
    InvestigationCase,
    ReconciliationException,
    ReconciliationRun,
    ResolutionRecommendation,
    Transaction,
    User,
    exception_transactions,
)
from finrecon_ai.db.tenancy import bind_tenant
from finrecon_ai.errors import NotFound


class ReportKind(StrEnum):
    RECONCILIATION_SUMMARY = "reconciliation-summary"
    OUTSTANDING_EXCEPTIONS = "outstanding-exceptions"
    UNMATCHED_TRANSACTIONS = "unmatched-transactions"
    DUPLICATES = "duplicates"
    RESOLUTION_STATISTICS = "resolution-statistics"


@dataclass
class Report:
    kind: ReportKind
    title: str
    generated_at: datetime
    period: str
    columns: list[str]
    rows: list[list[Any]]
    totals: dict[str, str] = field(default_factory=dict)  # currency -> total, never mixed
    notes: list[str] = field(default_factory=list)


SEVERITY_RANK = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2, Severity.CRITICAL: 3}
OPEN_STATUSES = [s for s in CaseStatus if s not in (CaseStatus.RESOLVED, CaseStatus.CLOSED)]
UNMATCHED = [ExceptionCategory.UNMATCHED, ExceptionCategory.MISSING_SETTLEMENT]
DUPLICATES = [ExceptionCategory.CONFIRMED_DUPLICATE, ExceptionCategory.POTENTIAL_DUPLICATE]


def build_report(session: Session, actor: Actor, kind: ReportKind, *, date_from: date | None = None,
                 date_to: date | None = None, run_id: uuid.UUID | None = None) -> Report:
    actor.require(Permission.CASE_READ)
    bind_tenant(session, actor.tenant_id)
    builders = {
        ReportKind.RECONCILIATION_SUMMARY: _reconciliation_summary,
        ReportKind.OUTSTANDING_EXCEPTIONS: _outstanding_exceptions,
        ReportKind.UNMATCHED_TRANSACTIONS: _unmatched_transactions,
        ReportKind.DUPLICATES: _duplicates,
        ReportKind.RESOLUTION_STATISTICS: _resolution_statistics,
    }
    period = _period_label(date_from, date_to)
    window = _window(date_from, date_to)
    return builders[kind](session, actor, window=window, period=period, run_id=run_id)


# --- reports ---


def _reconciliation_summary(session: Session, actor: Actor, *, window, period, run_id) -> Report:
    query = select(ReconciliationRun).where(ReconciliationRun.tenant_id == actor.tenant_id)
    run = session.scalar(query.where(ReconciliationRun.id == run_id) if run_id
                         else query.order_by(ReconciliationRun.created_at.desc()).limit(1))
    if run is None:
        raise NotFound("reconciliation run not found")
    summary = run.summary
    rows: list[list[Any]] = []
    for leg, counts in summary.get("matches", {}).items():
        total = sum(counts.values())
        matched = counts.get("matched", 0)
        rows.append([_label(leg), total, matched, f"{(matched / total * 100) if total else 0:.1f}%",
                     ", ".join(f"{_label(k)} {v}" for k, v in sorted(counts.items()) if k != "matched") or "none"])
    notes = [f"Run {run.id} on {run.created_at.astimezone(UTC):%Y-%m-%d %H:%M} UTC, engine {run.engine_version}.",
             "Records per source: " + ", ".join(f"{_label(k)} {v}" for k, v in summary.get("transactions", {}).items()),
             "Exceptions: " + ", ".join(f"{_label(k)} {v}" for k, v in summary.get("exceptions", {}).items())]
    for leg, totals in summary.get("matched_totals", {}).items():
        notes.append(f"Matched value, {_label(leg)}: "
                     + ", ".join(f"{_amount(Decimal(v))} {c}" for c, v in totals.items()))
    return Report(ReportKind.RECONCILIATION_SUMMARY, "Reconciliation summary", _now(), "single run",
                  ["Leg", "Pairs", "Matched", "Match rate", "Other outcomes"], rows,
                  totals={c: _amount(Decimal(v)) for c, v in summary.get("discrepancy_totals", {}).items()},
                  notes=notes)


def _outstanding_exceptions(session: Session, actor: Actor, *, window, period, run_id) -> Report:
    names = _names(session, actor)
    rows_db = sorted(
        session.execute(_case_query(actor, window).where(InvestigationCase.status.in_(OPEN_STATUSES))
                        .order_by(InvestigationCase.created_at)).all(),
        key=lambda row: -SEVERITY_RANK[row[0].priority])  # stable: oldest first within a priority
    today = _now().date()
    rows = [[c.case_number, _label(category), c.priority.value, _label(c.status.value), _amount(c.discrepancy_amount),
             c.currency or "", names.get(c.assigned_to, "Unassigned"), (today - c.created_at.date()).days]
            for c, category in rows_db]
    return Report(ReportKind.OUTSTANDING_EXCEPTIONS, "Outstanding exceptions", _now(), period,
                  ["Case", "Category", "Priority", "Status", "Amount", "Currency", "Assigned to", "Age (days)"], rows,
                  totals=_totals((c.discrepancy_amount, c.currency) for c, _ in rows_db),
                  notes=[f"{len(rows)} open cases (not resolved or closed)."])


def _unmatched_transactions(session: Session, actor: Actor, *, window, period, run_id) -> Report:
    query = (select(InvestigationCase.case_number, ReconciliationException.category, Transaction)
             .join(ReconciliationException, ReconciliationException.id == InvestigationCase.exception_id)
             .join(exception_transactions, exception_transactions.c.exception_id == ReconciliationException.id)
             .join(Transaction, Transaction.id == exception_transactions.c.transaction_id)
             .where(InvestigationCase.tenant_id == actor.tenant_id, ReconciliationException.category.in_(UNMATCHED)))
    if window:
        query = query.where(InvestigationCase.created_at >= window[0], InvestigationCase.created_at < window[1])
    rows_db = session.execute(query.order_by(Transaction.transaction_date)).all()
    rows = [[case_number, _label(category), t.source_transaction_id or "", t.external_reference or "",
             t.transaction_date.isoformat(), _amount(t.amount), t.currency, t.direction.value, t.counterparty or ""]
            for case_number, category, t in rows_db]
    return Report(ReportKind.UNMATCHED_TRANSACTIONS, "Unmatched transactions", _now(), period,
                  ["Case", "Category", "Transaction", "Reference", "Date", "Amount", "Currency", "Direction",
                   "Counterparty"], rows,
                  totals=_totals((t.amount, t.currency) for _, _, t in rows_db),
                  notes=["Transactions with no counterpart in the other source (unmatched or missing settlement)."])


def _duplicates(session: Session, actor: Actor, *, window, period, run_id) -> Report:
    rows_db = session.execute(
        _case_query(actor, window).where(ReconciliationException.category.in_(DUPLICATES))
        .order_by(InvestigationCase.case_number)).all()
    rows = [[c.case_number, _label(category), c.title, _amount(c.discrepancy_amount), c.currency or "",
             _label(c.status.value)] for c, category in rows_db]
    return Report(ReportKind.DUPLICATES, "Duplicate transactions", _now(), period,
                  ["Case", "Kind", "Records", "Amount at stake", "Currency", "Status"], rows,
                  totals=_totals((c.discrepancy_amount, c.currency) for c, _ in rows_db),
                  notes=["Confirmed duplicates repeat a reference already matched; potential duplicates are similar "
                         "charges within minutes and need confirmation."])


def _resolution_statistics(session: Session, actor: Actor, *, window, period, run_id) -> Report:
    cases = [c for c, _ in session.execute(_case_query(actor, window)).all()]
    by_status = Counter(c.status.value for c in cases)
    by_reason = Counter(c.closure_reason.value for c in cases if c.closure_reason)
    resolved = [c for c in cases if c.resolved_at]
    hours = [(c.resolved_at - c.created_at).total_seconds() / 3600 for c in resolved]
    recs = session.execute(
        select(ResolutionRecommendation.resolution_type, ResolutionRecommendation.status, func.count())
        .where(ResolutionRecommendation.tenant_id == actor.tenant_id,
               ResolutionRecommendation.case_id.in_([c.id for c in cases]))
        .group_by(ResolutionRecommendation.resolution_type, ResolutionRecommendation.status)).all()
    rows: list[list[Any]] = [["Cases", "Total", len(cases)]]
    rows += [["Case status", _label(s), n] for s, n in sorted(by_status.items())]
    rows += [["Closure reason", _label(r), n] for r, n in sorted(by_reason.items())]
    rows += [["Recommendation", f"{_label(t.value)} ({_label(s.value)})", n] for t, s, n in sorted(recs)]
    if hours:
        rows.append(["Time to resolve", "Average hours", f"{sum(hours) / len(hours):.1f}"])
    return Report(ReportKind.RESOLUTION_STATISTICS, "Investigation resolution statistics", _now(), period,
                  ["Measure", "Breakdown", "Value"], rows,
                  notes=[f"{len(resolved)} of {len(cases)} cases resolved in the period."])


# --- rendering ---

_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def to_csv(report: Report) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([report.title])
    writer.writerow([f"Period: {report.period}", f"Generated: {report.generated_at:%Y-%m-%d %H:%M} UTC"])
    writer.writerow([])
    writer.writerow([safe_cell(c) for c in report.columns])
    writer.writerows([[safe_cell(v) for v in row] for row in report.rows])
    if report.totals:
        writer.writerow([])
        for currency, total in report.totals.items():
            writer.writerow(["Total", currency, total])
    for note in report.notes:
        writer.writerow([safe_cell(note)])
    return buffer.getvalue()


def safe_cell(value: Any) -> str:
    """OWASP CSV injection guard: text a spreadsheet would execute gets a leading quote; real numbers stay intact."""
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_START) and not _NUMBER.match(text):
        return "'" + text
    return text


def to_pdf(report: Report) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(orientation="landscape" if len(report.columns) > 6 else "portrait", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 9, _latin1(report.title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=9)
    pdf.cell(0, 6, _latin1(f"Period: {report.period}  |  Generated {report.generated_at:%Y-%m-%d %H:%M} UTC  |  "
                           "FinRecon AI"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("Helvetica", size=8)
    if report.rows:
        with pdf.table(text_align="LEFT", line_height=5) as table:
            table.row([_latin1(c) for c in report.columns])
            for row in report.rows:
                table.row([_latin1("" if v is None else str(v)) for v in row])
    else:
        pdf.cell(0, 6, "No rows for this period.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    if report.totals:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, "Totals (per currency, never combined)", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=9)
        for currency, total in report.totals.items():
            pdf.cell(0, 5, _latin1(f"{currency}: {total}"), new_x="LMARGIN", new_y="NEXT")
    for note in report.notes:
        pdf.multi_cell(0, 5, _latin1(note), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


# --- helpers ---


def _case_query(actor: Actor, window):
    query = (select(InvestigationCase, ReconciliationException.category)
             .join(ReconciliationException, ReconciliationException.id == InvestigationCase.exception_id)
             .where(InvestigationCase.tenant_id == actor.tenant_id))
    if window:
        query = query.where(InvestigationCase.created_at >= window[0], InvestigationCase.created_at < window[1])
    return query


def _window(date_from: date | None, date_to: date | None):
    if not date_from and not date_to:
        return None
    start = datetime.combine(date_from or date.min, time.min, tzinfo=UTC)
    end = datetime.combine(date_to or date.max, time.max, tzinfo=UTC)
    return start, end


def _period_label(date_from: date | None, date_to: date | None) -> str:
    if not date_from and not date_to:
        return "all time"
    return f"{date_from.isoformat() if date_from else 'start'} to {date_to.isoformat() if date_to else 'today'}"


def _totals(pairs) -> dict[str, str]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for amount, currency in pairs:
        if amount is not None and currency:
            totals[currency] += amount
    return {c: _amount(v) for c, v in sorted(totals.items())}


def _names(session: Session, actor: Actor) -> dict[Any, str]:
    return dict(session.execute(select(User.id, User.full_name).where(User.tenant_id == actor.tenant_id)).all())


def _amount(value: Decimal | None) -> str:
    return "" if value is None else f"{value.quantize(Decimal('0.01'))}"


def _label(value: str) -> str:
    text = str(value).replace("_", " ").replace("-", " ")
    return text[:1].upper() + text[1:]


def _latin1(text: str) -> str:
    """The PDF core fonts cover Latin-1; replace anything else rather than fail."""
    return text.replace("›", ">").replace("—", "-").replace("–", "-").encode("latin-1", "replace").decode("latin-1")


def _now() -> datetime:
    return datetime.now(UTC)
