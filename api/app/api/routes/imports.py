from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentAuth, require_admin_csrf
from app.database import get_db
from app.schemas.imports import ImportCommitRequest, ImportPreview, ImportResult
from app.services.imports import (
    ImportConflict,
    MAX_UPLOAD_BYTES,
    commit_candidate_csv,
    stage_candidate_csv,
)


router = APIRouter(prefix="/admin/imports", tags=["admin-imports"])


def _raise_import_conflict(exc: ImportConflict) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": exc.code},
    ) from exc


@router.post("/preview", response_model=ImportPreview)
async def preview_import(
    file: UploadFile = File(...),
    auth: CurrentAuth = Depends(require_admin_csrf),
    db: AsyncSession = Depends(get_db),
) -> ImportPreview:
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        return await stage_candidate_csv(
            db,
            content,
            actor_id=auth.user.id,
            filename=file.filename,
        )
    except ImportConflict as exc:
        _raise_import_conflict(exc)


@router.post("/commit", response_model=ImportResult)
async def commit_import(
    payload: ImportCommitRequest,
    auth: CurrentAuth = Depends(require_admin_csrf),
    db: AsyncSession = Depends(get_db),
) -> ImportResult:
    try:
        return await commit_candidate_csv(
            db,
            payload.preview_token,
            auth.user.id,
        )
    except ImportConflict as exc:
        _raise_import_conflict(exc)
