from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.exports import ReceiptRequest, ReceiptResponse
from app.services.exports import MAX_RECEIPT_BYTES, ReceiptRejected, apply_receipt


router = APIRouter(prefix="/sync", tags=["sync"])


async def _bounded_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_RECEIPT_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail={"code": "RECEIPT_BODY_TOO_LARGE"},
                )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "RECEIPT_CONTENT_LENGTH_INVALID"},
            )
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > MAX_RECEIPT_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={"code": "RECEIPT_BODY_TOO_LARGE"},
            )
    return bytes(chunks)


@router.post("/batches/{batch_id}/receipt", response_model=ReceiptResponse)
async def post_batch_receipt(
    batch_id: UUID,
    request: Request,
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
    content = await _bounded_body(request)
    try:
        payload = ReceiptRequest.model_validate_json(content)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "RECEIPT_INVALID"},
        ) from exc
    try:
        return await apply_receipt(
            db, batch_id, payload.items, raw_token=raw_token
        )
    except ReceiptRejected as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code},
        ) from exc
