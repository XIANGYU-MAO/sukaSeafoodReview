from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentAuth, get_runtime_settings, require_admin_csrf
from app.config import Settings
from app.database import get_db
from app.schemas.imports import (
    ImportCommitRequest,
    ImportOriginApprovalReceipt,
    ImportOriginApprovalRequest,
    ImportPreview,
    ImportResult,
)
from app.services.imports import (
    ImportConflict,
    ImportFileFatal,
    MAX_UPLOAD_BYTES,
    approve_import_image_origin,
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
    settings: Settings = Depends(get_runtime_settings),
    db: AsyncSession = Depends(get_db),
) -> ImportPreview:
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        return await stage_candidate_csv(
            db,
            content,
            actor_id=auth.user.id,
            actor_session_id=auth.session.id,
            filename=file.filename,
            image_origin_allowlist=settings.IMAGE_ORIGIN_ALLOWLIST,
        )
    except ImportFileFatal as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "report": exc.report},
        ) from exc
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
            actor_session_id=auth.session.id,
            skip_blocking_rows=payload.skip_blocking_rows,
        )
    except ImportConflict as exc:
        _raise_import_conflict(exc)


@router.post("/approve-origin", response_model=ImportOriginApprovalReceipt)
async def approve_origin(
    payload: ImportOriginApprovalRequest,
    auth: CurrentAuth = Depends(require_admin_csrf),
    db: AsyncSession = Depends(get_db),
) -> ImportOriginApprovalReceipt:
    try:
        return await approve_import_image_origin(
            db,
            payload.preview_token,
            payload.hostname,
            actor_id=auth.user.id,
            actor_session_id=auth.session.id,
        )
    except ImportConflict as exc:
        _raise_import_conflict(exc)
