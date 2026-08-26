from __future__ import annotations

from fastapi import HTTPException, Request, status


async def read_bounded_json_body(request: Request, maximum_bytes: int) -> bytes:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"code": "RECEIPT_CONTENT_TYPE_INVALID"},
        )
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > maximum_bytes:
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
        if len(chunks) > maximum_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={"code": "RECEIPT_BODY_TOO_LARGE"},
            )
    return bytes(chunks)
