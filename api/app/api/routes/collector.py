from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.api.dependencies import CurrentAuth, require_admin_access
from app.database import get_db
from app.services.collector import (
    NoActiveSpecies,
    build_collector_config,
    stream_candidate_manifest,
)


router = APIRouter(prefix="/admin/collector", tags=["admin-collector"])


@router.get("/candidates.csv")
async def download_candidate_manifest(
    request: Request,
    _: CurrentAuth = Depends(require_admin_access),
) -> StreamingResponse:
    async def generate_manifest():
        async with request.app.state.session_factory() as session:
            async for chunk in stream_candidate_manifest(session):
                yield chunk

    return StreamingResponse(
        generate_manifest(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                'attachment; filename="sukaseafood-all-candidates.csv"'
            ),
            "Cache-Control": "no-store",
        },
    )


@router.get("/config")
async def download_collector_config(
    _: CurrentAuth = Depends(require_admin_access),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        config = await build_collector_config(db)
    except NoActiveSpecies as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "NO_ACTIVE_SPECIES"},
        ) from exc
    return Response(
        content=config.model_dump_json(indent=2).encode("utf-8") + b"\n",
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="species_config.json"',
            "Cache-Control": "no-store",
        },
    )
