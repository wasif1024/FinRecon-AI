import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from finrecon_ai.api.deps import current_actor, get_session
from finrecon_ai.api.schemas import ReportOut
from finrecon_ai.authz import Actor
from finrecon_ai.reports.service import ReportKind, build_report, to_csv, to_pdf

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/{kind}", response_model=ReportOut, responses={200: {"content": {
    "text/csv": {}, "application/pdf": {}}}})
def report(kind: ReportKind, format: Literal["json", "csv", "pdf"] = "json",
           date_from: date | None = Query(None, alias="from"), date_to: date | None = Query(None, alias="to"),
           run_id: uuid.UUID | None = None, actor: Actor = Depends(current_actor),
           session: Session = Depends(get_session)):
    """FR-11 reports. Totals are per currency; CSV cells that would run as spreadsheet formulas are neutralised."""
    built = build_report(session, actor, kind, date_from=date_from, date_to=date_to, run_id=run_id)
    if format == "json":
        return ReportOut(kind=built.kind.value, title=built.title, generated_at=built.generated_at,
                         period=built.period, columns=built.columns, rows=built.rows, totals=built.totals,
                         notes=built.notes)
    name = f"finrecon-{kind.value}-{built.generated_at:%Y%m%d-%H%M}"
    if format == "csv":
        return Response(to_csv(built), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    return Response(to_pdf(built), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})
