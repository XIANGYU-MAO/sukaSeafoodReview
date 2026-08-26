from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentAuth, get_runtime_settings, require_admin_access, require_admin_csrf
from app.api.request_body import read_bounded_json_body
from app.config import Settings
from app.database import get_db
from app.schemas.exports import (
    ExportBatchListResponse,
    ExportBatchResponse,
    ExportCreateRequest,
    ReceiptFileRequest,
    ReceiptResponse,
)
from app.services.exports import (
    MAX_RECEIPT_BYTES,
    ExportConflict,
    ExportNotFound,
    ReceiptRejected,
    apply_receipt,
    batch_response,
    create_export_batch,
    list_batches,
    pending_counts,
    render_batch_csv,
)


router = APIRouter(prefix="/admin/exports", tags=["admin-exports"])


def _receipt_secret(settings: Settings) -> str:
    if not settings.RECEIPT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "EXPORTS_NOT_CONFIGURED"},
        )
    return settings.RECEIPT_SECRET


def _raise_export_error(exc: Exception) -> None:
    if isinstance(exc, ExportNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    if isinstance(exc, ExportConflict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": exc.code,
                "batch_ids": [str(batch_id) for batch_id in exc.batch_ids],
            },
        ) from exc
    if isinstance(exc, ReceiptRejected):
        raise HTTPException(
            status_code=exc.status_code, detail={"code": exc.code}
        ) from exc
    raise exc


@router.get("", response_model=ExportBatchListResponse)
async def get_exports(
    _: CurrentAuth = Depends(require_admin_access),
    db: AsyncSession = Depends(get_db),
) -> ExportBatchListResponse:
    items = await list_batches(db)
    return ExportBatchListResponse(total=len(items), items=items)


@router.get("/pending-counts", response_model=dict[str, int])
async def get_pending_counts(
    _: CurrentAuth = Depends(require_admin_access),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    return await pending_counts(db)


@router.post("")
async def post_export(
    payload: ExportCreateRequest,
    response: Response,
    auth: CurrentAuth = Depends(require_admin_csrf),
    settings: Settings = Depends(get_runtime_settings),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await create_export_batch(
            db,
            auth.user.id,
            payload.species_code,
            _receipt_secret(settings),
        )
    except (ExportNotFound, ExportConflict) as exc:
        _raise_export_error(exc)
    if result.no_work:
        response.status_code = status.HTTP_200_OK
        return {"code": "NO_WORK", "created": False, "batch": None}
    assert result.batch is not None
    response.status_code = (
        status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    )
    return await batch_response(db, result.batch, created=result.created)


@router.get("/{batch_id}.csv")
async def get_export_csv(
    batch_id: UUID,
    _: CurrentAuth = Depends(require_admin_access),
    settings: Settings = Depends(get_runtime_settings),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        content = await render_batch_csv(db, batch_id, _receipt_secret(settings))
    except (ExportNotFound, ReceiptRejected) as exc:
        _raise_export_error(exc)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="sukaseafood-export-{batch_id}.csv"'
        },
    )


@router.post(
    "/{batch_id}/receipt-file",
    response_model=ReceiptResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": ReceiptFileRequest.model_json_schema()
                }
            },
        }
    },
)
async def post_receipt_file(
    batch_id: UUID,
    request: Request,
    auth: CurrentAuth = Depends(require_admin_csrf),
    db: AsyncSession = Depends(get_db),
) -> ReceiptResponse:
    content = await read_bounded_json_body(request, MAX_RECEIPT_BYTES)
    try:
        payload = ReceiptFileRequest.model_validate_json(content)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "RECEIPT_INVALID"},
        ) from exc
    if payload.batch_id != batch_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "RECEIPT_BATCH_MISMATCH"},
        )
    try:
        return await apply_receipt(
            db, batch_id, payload.items, actor_id=auth.user.id
        )
    except ReceiptRejected as exc:
        _raise_export_error(exc)
