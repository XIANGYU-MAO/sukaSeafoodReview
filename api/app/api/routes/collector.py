from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentAuth, require_admin_access
from app.database import get_db
from app.services.collector import NoActiveSpecies, build_collector_config


router = APIRouter(prefix="/admin/collector", tags=["admin-collector"])


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
