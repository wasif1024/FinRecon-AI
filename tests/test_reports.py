"""FR-11 reports: content, per-currency totals, period filters, formula-safe CSV and PDF output."""

import csv
import io
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select, update

from finrecon_ai.db.enums import ExceptionCategory, RoleName
from finrecon_ai.db.models import InvestigationCase, ReconciliationException, Transaction, exception_transactions
from finrecon_ai.errors import NotFound
from finrecon_ai.reports.service import ReportKind, build_report, safe_cell, to_csv, to_pdf
from tests.test_api import auth, client, scripts  # noqa: F401 (fixtures)
from tests.test_workflows import store  # noqa: F401 (fixture)


@pytest.fixture
def manager(world):
    return world[2][RoleName.MANAGER]


@pytest.mark.parametrize("kind", list(ReportKind))
def test_every_report_builds_and_renders(session, case_ids, manager, kind):
    report = build_report(session, manager, kind)
    assert report.title and report.columns
    assert all(len(row) == len(report.columns) for row in report.rows)
    assert to_csv(report).startswith(report.title)
    assert to_pdf(report).startswith(b"%PDF")


def test_summary_reports_match_rate_per_leg(session, case_ids, manager):
    report = build_report(session, manager, ReportKind.RECONCILIATION_SUMMARY)
    legs = {row[0]: row for row in report.rows}
    assert set(legs) == {"Gateway to settlement", "Settlement to bank"}
    for _, pairs, matched, rate, _ in legs.values():
        assert 0 < matched <= pairs and rate.endswith("%")


def test_summary_uses_utc_and_two_decimals(session, case_ids, manager):
    from datetime import UTC

    from finrecon_ai.db.models import ReconciliationRun

    report = build_report(session, manager, ReportKind.RECONCILIATION_SUMMARY)
    run = session.scalar(select(ReconciliationRun).where(ReconciliationRun.tenant_id == manager.tenant_id))
    assert f"{run.created_at.astimezone(UTC):%Y-%m-%d %H:%M} UTC" in report.notes[0]
    assert all(len(total.split(".")[1]) == 2 for total in report.totals.values())


def test_totals_are_per_currency(session, case_ids, manager):
    report = build_report(session, manager, ReportKind.OUTSTANDING_EXCEPTIONS)
    assert report.totals and set(report.totals) <= {"USD", "EUR", "GBP"}
    for currency, total in report.totals.items():
        rows = [r for r in report.rows if r[5] == currency and r[4]]
        assert sum(float(r[4]) for r in rows) == pytest.approx(float(total))


def test_outstanding_sorted_by_priority(session, case_ids, manager):
    priorities = [row[2] for row in build_report(session, manager, ReportKind.OUTSTANDING_EXCEPTIONS).rows]
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    assert priorities == sorted(priorities, key=rank.__getitem__)


def test_category_reports_only_hold_their_categories(session, case_ids, manager):
    unmatched = build_report(session, manager, ReportKind.UNMATCHED_TRANSACTIONS)
    assert {r[1] for r in unmatched.rows} <= {"Unmatched", "Missing settlement"}
    duplicates = build_report(session, manager, ReportKind.DUPLICATES)
    assert duplicates.rows and {r[1] for r in duplicates.rows} <= {"Confirmed duplicate", "Potential duplicate"}


def test_period_filter(session, case_ids, manager):
    future = date.today() + timedelta(days=30)
    report = build_report(session, manager, ReportKind.OUTSTANDING_EXCEPTIONS, date_from=future)
    assert report.rows == [] and report.period.startswith(future.isoformat())
    everything = build_report(session, manager, ReportKind.OUTSTANDING_EXCEPTIONS)
    assert len(everything.rows) == len(case_ids)


def test_run_of_another_tenant_is_not_found(session, case_ids, manager):
    with pytest.raises(NotFound):
        build_report(session, manager, ReportKind.RECONCILIATION_SUMMARY, run_id=uuid.uuid4())


@pytest.mark.parametrize("value, expected", [
    ("=HYPERLINK(\"http://evil\")", "'=HYPERLINK(\"http://evil\")"),
    ("+cmd|' /C calc'!A0", "'+cmd|' /C calc'!A0"),
    ("@SUM(A1)", "'@SUM(A1)"),
    ("-2+3", "'-2+3"),
    ("\t=1", "'\t=1"),
    ("-25.00", "-25.00"),  # a real negative amount stays a number
    ("1250.40", "1250.40"),
    ("Fee discrepancy", "Fee discrepancy"),
    (None, ""),
])
def test_csv_cells_are_formula_safe(value, expected):
    assert safe_cell(value) == expected


def test_csv_export_neutralises_formula_planted_in_source_data(session, case_ids, manager):
    """A counterparty imported as a formula must not run when the report is opened in a spreadsheet."""
    txn_id = session.scalar(
        select(exception_transactions.c.transaction_id)
        .join(ReconciliationException, ReconciliationException.id == exception_transactions.c.exception_id)
        .where(ReconciliationException.category == ExceptionCategory.UNMATCHED).limit(1))
    session.execute(update(Transaction).where(Transaction.id == txn_id).values(counterparty="=IMPORTXML(\"x\")"))
    rows = list(csv.reader(io.StringIO(to_csv(build_report(session, manager, ReportKind.UNMATCHED_TRANSACTIONS)))))
    cells = [cell for row in rows for cell in row]
    assert "'=IMPORTXML(\"x\")" in cells and "=IMPORTXML(\"x\")" not in cells


def test_pdf_survives_text_outside_latin1(session, case_ids, manager):
    case = session.scalar(select(InvestigationCase).where(InvestigationCase.id == case_ids[0]))
    case.title = "Settlement › 東京 — ✓ Müller"
    session.flush()
    assert to_pdf(build_report(session, manager, ReportKind.DUPLICATES)).startswith(b"%PDF")


# --- over HTTP ---


def test_report_downloads(client, case_ids):
    headers = auth(client, RoleName.ANALYST)
    as_json = client.get("/reports/outstanding-exceptions", headers=headers)
    assert as_json.status_code == 200 and as_json.json()["rows"]
    as_csv = client.get("/reports/duplicates", params={"format": "csv"}, headers=headers)
    assert as_csv.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="finrecon-duplicates-' in as_csv.headers["content-disposition"]
    as_pdf = client.get("/reports/resolution-statistics", params={"format": "pdf"}, headers=headers)
    assert as_pdf.content.startswith(b"%PDF")
    assert client.get("/reports/not-a-report", headers=headers).status_code == 422
    assert client.get("/reports/duplicates").status_code == 401


def test_import_list_is_tenant_scoped(client, case_ids):
    imports = client.get("/imports", headers=auth(client, RoleName.ANALYST)).json()
    assert len(imports) == 3 and {i["status"] for i in imports} <= {"completed", "completed_with_errors"}
