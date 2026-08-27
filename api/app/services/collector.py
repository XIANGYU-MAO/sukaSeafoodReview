from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Candidate, Species
from app.schemas.collector import CollectorConfig, CollectorSpecies


class NoActiveSpecies(Exception):
    pass


async def build_collector_config(session: AsyncSession) -> CollectorConfig:
    rows = list(
        (
            await session.scalars(
                select(Species)
                .where(Species.active.is_(True))
                .order_by(Species.sort_order, Species.code, Species.id)
            )
        ).all()
    )
    if not rows:
        raise NoActiveSpecies
    candidate_counts = dict(
        (
            await session.execute(
                select(Candidate.species_id, func.count(Candidate.id))
                .group_by(Candidate.species_id)
            )
        ).all()
    )
    return CollectorConfig(
        generated_at=datetime.now(timezone.utc),
        species=[
            CollectorSpecies(
                seafood_code=row.code,
                name_zh=row.name_zh,
                name_en=row.name_en,
                scientific_name=row.scientific_name,
                candidate_count=int(candidate_counts.get(row.id, 0)),
                inat_taxon_id=row.inat_taxon_id,
                gbif_taxon_key=row.gbif_taxon_key,
                commons_category=row.commons_category,
                fish_vista_filter=row.fish_vista_filter,
            )
            for row in rows
        ],
    )
