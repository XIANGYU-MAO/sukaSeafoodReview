from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_runtime_settings
from app.database import get_db
from app.api.request_body import read_bounded_json_body
from app.config import Settings
from app.schemas.exports import ReceiptRequest, ReceiptResponse
from app.services.exports import MAX_RECEIPT_BYTES, ReceiptRejected, apply_receipt


router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/batches/{batch_id}/receipt", response_model=ReceiptResponse)
async def post_batch_receipt(
    batch_id: UUID,
    request: Request,
    settings: Settings = Depends(get_runtime_settings),
    db: AsyncSession = Depends(get_db),
) -> ReceiptResponse:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Batch ") or len(authorization) <= 6:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "RECEIPT_NOT_AUTHORIZED"},
        )
    raw_token = authorization[6:]
    if raw_token != raw_token.strip() or len(raw_token) > 512:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "RECEIPT_NOT_AUTHORIZED"},
        )
    content = await read_bounded_json_body(request, MAX_RECEIPT_BYTES)
    try:
        payload = ReceiptRequest.model_validate_json(content)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "RECEIPT_INVALID"},
        ) from exc
    try:
        if not settings.RECEIPT_SECRET:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "EXPORTS_NOT_CONFIGURED"},
            )
        return await apply_receipt(
            db,
            batch_id,
            payload.items,
            receipt_secret=settings.RECEIPT_SECRET,
            raw_token=raw_token,
        )
    except ReceiptRejected as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code},
        ) from exc
