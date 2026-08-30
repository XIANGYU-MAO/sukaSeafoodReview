import csv
from datetime import datetime, timezone
import io
from typing import AsyncIterator

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Candidate, Species
from app.schemas.collector import CollectorConfig, CollectorSpecies


class NoActiveSpecies(Exception):
    pass


CANDIDATE_MANIFEST_COLUMNS = (
    "image_id",
    "seafood_code",
    "app_label",
    "scientific_name",
    "source_dataset",
    "source_record_id",
    "source_taxon_match",
    "source_url",
    "image_url",
    "creator",
    "license",
    "license_url",
    "attribution",
    "source_observation_quality",
    "source_country",
    "source_location",
    "source_date",
    "source_split",
    "image_context",
    "whole_fish",
    "exact_species_verified",
    "verified_by",
    "verification_notes",
    "original_group_id",
    "sha256",
    "perceptual_hash",
    "local_path",
    "split",
    "status",
    "rejection_reason",
)


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


def _candidate_manifest_row(candidate: Candidate, species: Species) -> dict[str, str]:
    manifest_row = {column: "" for column in CANDIDATE_MANIFEST_COLUMNS}
    manifest_row.update(
        {
            "image_id": str(candidate.id),
            "seafood_code": species.code,
            "app_label": species.name_en,
            "scientific_name": species.scientific_name,
            "source_dataset": candidate.source_dataset,
            "source_record_id": candidate.source_record_id,
            "source_url": candidate.source_url,
            "image_url": candidate.original_url,
            "creator": candidate.creator or "",
            "license": candidate.license,
            "license_url": candidate.license_url or "",
            "attribution": candidate.attribution,
            "source_location": candidate.location or "",
            "source_date": (
                candidate.observed_on.isoformat()
                if candidate.observed_on is not None
                else ""
            ),
            "image_context": "REVIEW",
            "whole_fish": "REVIEW",
            "exact_species_verified": "REVIEW",
            "split": "UNASSIGNED",
            "status": "CANDIDATE",
        }
    )
    return manifest_row


def _manifest_csv_chunk(
    rows: list[tuple[Candidate, Species]], *, include_header: bool
) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=CANDIDATE_MANIFEST_COLUMNS,
        lineterminator="\r\n",
    )
    if include_header:
        writer.writeheader()
    for candidate, species in rows:
        writer.writerow(_candidate_manifest_row(candidate, species))
    content = output.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" + content) if include_header else content


async def stream_candidate_manifest(
    session: AsyncSession, *, chunk_rows: int = 250
) -> AsyncIterator[bytes]:
    """Stream every candidate in bounded chunks without mutating server state."""

    if chunk_rows < 1:
        raise ValueError("chunk_rows must be >= 1")
    yield _manifest_csv_chunk([], include_header=True)
    result = await session.stream(
        select(Candidate, Species)
        .join(Species, Species.id == Candidate.species_id)
        .order_by(
            Species.sort_order,
            Species.code,
            Candidate.source_dataset,
            Candidate.id,
        )
        .execution_options(yield_per=chunk_rows)
    )
    try:
        async for partition in result.partitions(chunk_rows):
            yield _manifest_csv_chunk(list(partition), include_header=False)
    finally:
        await result.close()
