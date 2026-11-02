from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from finrecon_ai.api.deps import Runtime, current_actor, get_session, runtime
from finrecon_ai.api.schemas import PolicyHitOut, PolicyOut
from finrecon_ai.authz import Actor
from finrecon_ai.config import get_settings
from finrecon_ai.policies.parsing import MAX_DOCUMENT_BYTES
from finrecon_ai.policies.service import list_policies, search_policies, upload_policy

router = APIRouter(prefix="/policies", tags=["policies"])


@router.get("", response_model=list[PolicyOut])
def list_documents(include_archived: bool = False, actor: Actor = Depends(current_actor),
                   session: Session = Depends(get_session)):
    return list_policies(session, actor, include_archived=include_archived)


@router.post("", response_model=PolicyOut, status_code=status.HTTP_201_CREATED)
async def upload(file: UploadFile, response: Response, title: str | None = None,
                 actor: Actor = Depends(current_actor), session: Session = Depends(get_session),
                 rt: Runtime = Depends(runtime)):
    content = await file.read(MAX_DOCUMENT_BYTES + 1)
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file too large")
    result = upload_policy(session, actor, rt.policy_store(), file_name=file.filename or "upload", content=content,
                           storage_dir=Path(get_settings().document_storage_dir), title=title)
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return result.document


@router.get("/search", response_model=list[PolicyHitOut])
def search(q: str = Query(min_length=2, max_length=500), limit: int = Query(5, ge=1, le=20),
           actor: Actor = Depends(current_actor), rt: Runtime = Depends(runtime)):
    return [PolicyHitOut(**hit.__dict__) for hit in search_policies(actor, rt.policy_store(), q, limit)]
