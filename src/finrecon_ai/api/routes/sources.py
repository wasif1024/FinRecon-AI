import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from finrecon_ai.api.deps import current_actor, get_session
from finrecon_ai.api.schemas import ImportBatchDetailOut, ImportBatchOut, SourceOut
from finrecon_ai.authz import Actor, Permission
from finrecon_ai.db.models import FinancialSource, ImportBatch
from finrecon_ai.errors import NotFound
from finrecon_ai.ingestion.parsers import MAX_FILE_BYTES
from finrecon_ai.ingestion.service import ingest_file

router = APIRouter(tags=["data"])


@router.get("/sources", response_model=list[SourceOut])
def list_sources(actor: Actor = Depends(current_actor), session: Session = Depends(get_session)):
    actor.require(Permission.CASE_READ)
    return session.scalars(select(FinancialSource).where(FinancialSource.tenant_id == actor.tenant_id)
                           .order_by(FinancialSource.name)).all()


@router.post("/sources/{source_id}/imports", response_model=ImportBatchOut, status_code=status.HTTP_201_CREATED)
async def upload_import(source_id: uuid.UUID, file: UploadFile, response: Response,
                        actor: Actor = Depends(current_actor), session: Session = Depends(get_session)):
    actor.require(Permission.DATA_IMPORT)
    content = await file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file too large")
    result = ingest_file(session, tenant_id=actor.tenant_id, source_id=source_id,
                         file_name=file.filename or "upload", content=content, uploaded_by=actor.user_id)
    if not result.created:
        response.status_code = status.HTTP_200_OK  # same file already imported: nothing new was created
    return result.batch


@router.get("/imports/{batch_id}", response_model=ImportBatchDetailOut)
def get_import(batch_id: uuid.UUID, actor: Actor = Depends(current_actor), session: Session = Depends(get_session)):
    actor.require(Permission.CASE_READ)
    batch = session.scalar(select(ImportBatch).where(ImportBatch.id == batch_id,
                                                     ImportBatch.tenant_id == actor.tenant_id))
    if batch is None:
        raise NotFound("import batch not found")
    return batch
