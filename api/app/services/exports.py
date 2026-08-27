from __future__ import annotations

import base64
import csv
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import hmac
import io
import json
from pathlib import PurePosixPath
import re
from typing import Iterable
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditEvent,
    Candidate,
    Decision,
    ExportAction,
    ExportBatch,
    ExportItem,
    Review,
    Species,
)
from app.image_origins import (
    DEFAULT_IMAGE_ORIGIN_ALLOWLIST,
    ImageOriginError,
    require_approved_image_url,
)
from app.schemas.exports import ExportBatchResponse, ReceiptItem, ReceiptResponse
from app.services.auth import as_utc, utc_now
from app.services.origins import effective_image_origin_allowlist
from app.services.sync_generation import acquire_sync_generation_lock
from app.species_codes import is_safe_species_code


EXPORT_COLUMNS = [
    "batch_id",
    "receipt_token",
    "action",
    "candidate_id",
    "review_id",
    "review_version",
    "species_code",
    "target_relative_path",
    "previous_relative_path",
    "preview_url",
    "original_url",
    "source_url",
    "creator",
    "license",
    "license_url",
    "attribution",
    "image_origin_allowlist",
]
EXPORT_TTL = timedelta(days=7)
EXPORT_MAX_ROWS = 10_000
EXPORT_MAX_BYTES = 20 * 1024 * 1024
MAX_RECEIPT_BYTES = EXPORT_MAX_BYTES
FAILED_ERROR_CODE = "LOCAL_DOWNLOAD_FAILED"
KNOWN_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff", ".bmp"}
LEGACY_UNKNOWN_FINGERPRINT = "0" * 64
TOKEN_DOMAIN = b"sukaseafood:receipt:v1:"


class ExportNotFound(Exception):
    pass


@dataclass(frozen=True)
class ExportConflict(Exception):
    code: str
    batch_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class ReceiptRejected(Exception):
    code: str
    status_code: int


@dataclass(frozen=True)
class BatchCreationResult:
    batch: ExportBatch | None
    created: bool
    no_work: bool = False


@dataclass(frozen=True)
class Delta:
    candidate: Candidate
    species: Species
    review: Review
    action: ExportAction
    target_relative_path: str
    previous_relative_path: str | None
    original_fingerprint: str
    metadata_fingerprint: str


def receipt_token(batch_id: UUID, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), TOKEN_DOMAIN + batch_id.bytes, hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _token_digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _original_fingerprint(candidate: Candidate) -> str:
    return _fingerprint({"original_url": candidate.original_url})


def _metadata_fingerprint(candidate: Candidate, species: Species) -> str:
    return _fingerprint(
        {
            "candidate_version": candidate.version,
            "species_code": species.code,
            "preview_url": candidate.preview_url,
            "original_url": candidate.original_url,
            "source_url": candidate.source_url,
            "creator": candidate.creator,
            "license": candidate.license,
            "license_url": candidate.license_url,
            "attribution": candidate.attribution,
            "metadata": candidate.metadata_json,
            "active": candidate.active,
        }
    )


def _original_changed(local_item: ExportItem, candidate: Candidate) -> bool:
    """Use retained URL truth when a legacy row has no real digest."""

    if local_item.original_fingerprint == LEGACY_UNKNOWN_FINGERPRINT:
        return local_item.original_url != candidate.original_url
    return local_item.original_fingerprint != _original_fingerprint(candidate)


def _metadata_changed(
    local_item: ExportItem, candidate: Candidate, species: Species
) -> bool:
    """Compare the retained revision-05 snapshot when its digest is unknown."""

    if local_item.metadata_fingerprint != LEGACY_UNKNOWN_FINGERPRINT:
        return local_item.metadata_fingerprint != _metadata_fingerprint(
            candidate, species
        )
    return any(
        (
            local_item.candidate_version != candidate.version,
            local_item.species_code != species.code,
            local_item.preview_url != candidate.preview_url,
            local_item.original_url != candidate.original_url,
            local_item.source_url != candidate.source_url,
            local_item.creator != candidate.creator,
            local_item.license != candidate.license,
            local_item.license_url != candidate.license_url,
            local_item.attribution != candidate.attribution,
        )
    )


def _known_suffix(url: str) -> str:
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
    return suffix if suffix in KNOWN_IMAGE_EXTENSIONS else ".image"


def _desired_path(candidate: Candidate, species: Species) -> str:
    if not is_safe_species_code(species.code):
        raise ExportConflict("UNSAFE_SPECIES_CODE")
    return f"images/{species.code}/{candidate.id}{_known_suffix(candidate.original_url)}"


def _used_image_origins(*urls: str) -> str:
    hosts = sorted(
        {
            (urlsplit(url).hostname or "").rstrip(".").lower()
            for url in urls
            if url
        }
    )
    return json.dumps([host for host in hosts if host], separators=(",", ":"))


def _same_content_path(previous_path: str, species: Species) -> str:
    if not is_safe_species_code(species.code):
        raise ExportConflict("UNSAFE_SPECIES_CODE")
    return f"images/{species.code}/{PurePosixPath(previous_path).name}"


def _removed_path(batch_id: UUID, candidate: Candidate, previous_path: str) -> str:
    suffix = PurePosixPath(previous_path).suffix.lower()
    if suffix not in KNOWN_IMAGE_EXTENSIONS | {".image"}:
        suffix = _known_suffix(candidate.original_url)
    return f"_removed/{batch_id}/{candidate.id}{suffix}"


def _scope_key(species_id: UUID | None) -> str:
    return str(species_id) if species_id is not None else "ALL"


async def _expire_batches(session: AsyncSession) -> None:
    now = utc_now()
    await session.execute(
        update(ExportBatch)
        .where(
            ExportBatch.status == "pending",
            ExportBatch.expires_at <= now,
        )
        .values(status="expired", expired_at=now)
    )


def _is_postgresql(session: AsyncSession) -> bool:
    return session.bind is not None and session.bind.dialect.name == "postgresql"


async def _begin_creation_snapshot(session: AsyncSession) -> bool:
    """Begin a fresh coherent PostgreSQL snapshot and serialize exporters.

    Authentication may already have opened a transaction on the request
    session, so close it first.  The common table-lock boundary is then acquired
    before every generation/receipt writer's row locks.  Holding it for the
    complete export transaction gives the state
    reads a coherent writer-free cut.  Because ``LOCK TABLE`` is a utility
    command, a waiter does not acquire its REPEATABLE READ snapshot until after
    it owns the boundary.
    """
    if not _is_postgresql(session):
        return False
    if session.in_transaction():
        await session.commit()
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
    await acquire_sync_generation_lock(session)
    return True


async def _end_creation_snapshot_lock(session: AsyncSession, locked: bool) -> None:
    # PostgreSQL table locks are transaction-scoped.  Successful paths commit
    # before returning; this rollback is only a defensive exception cleanup.
    if locked and session.in_transaction():
        await session.rollback()


async def _state_maps(session: AsyncSession):
    candidate_rows = list(
        (
            await session.execute(
                select(Candidate, Species)
                .join(Species, Species.id == Candidate.species_id)
                .order_by(Candidate.id)
            )
        ).all()
    )
    reviews = list(
        (
            await session.scalars(
                select(Review).order_by(
                    Review.candidate_id,
                    Review.updated_at.desc(),
                    Review.created_at.desc(),
                    Review.id.desc(),
                )
            )
        ).all()
    )
    current_reviews: dict[UUID, Review] = {}
    latest_reviews: dict[UUID, Review] = {}
    for review in reviews:
        latest_reviews.setdefault(review.candidate_id, review)
        if review.is_current:
            current_reviews[review.candidate_id] = review

    succeeded = list(
        (
            await session.scalars(
                select(ExportItem)
                .where(ExportItem.status == "succeeded")
                .order_by(
                    ExportItem.candidate_id,
                    ExportItem.succeeded_at.desc(),
                    ExportItem.created_at.desc(),
                    ExportItem.id.desc(),
                )
            )
        ).all()
    )
    local: dict[UUID, ExportItem] = {}
    for item in succeeded:
        local.setdefault(item.candidate_id, item)
    return candidate_rows, current_reviews, latest_reviews, local


async def derive_deltas(
    session: AsyncSession,
    *,
    batch_id: UUID,
    species_id: UUID | None,
) -> list[Delta]:
    candidate_rows, current_reviews, latest_reviews, local = await _state_maps(session)
    deltas: list[Delta] = []
    for candidate, species in candidate_rows:
        current = current_reviews.get(candidate.id)
        local_item = local.get(candidate.id)
        local_present = local_item is not None and local_item.action != ExportAction.REMOVE
        desired_present = (
            candidate.active
            and species.active
            and current is not None
            and current.decision == Decision.APPROVED
        )

        action: ExportAction | None = None
        previous_path: str | None = None
        target_path: str | None = None
        bound_review: Review | None = current
        desired_path = _desired_path(candidate, species)

        if desired_present:
            assert current is not None
            if not local_present:
                action = ExportAction.ADD
                target_path = desired_path
            else:
                assert local_item is not None
                previous_path = local_item.local_relative_path or local_item.target_relative_path
                original_changed = _original_changed(local_item, candidate)
                if not original_changed:
                    desired_path = _same_content_path(previous_path, species)
                if previous_path != desired_path and not original_changed:
                    action = (
                        ExportAction.MOVE
                        if local_item.species_code != species.code
                        else ExportAction.ADD
                    )
                    target_path = desired_path
                    if action == ExportAction.ADD:
                        previous_path = None
                elif (
                    local_item.review_id != current.id
                    or local_item.review_version != current.version
                    or local_item.candidate_version != candidate.version
                    or original_changed
                    or _metadata_changed(local_item, candidate, species)
                ):
                    action = ExportAction.ADD
                    target_path = desired_path
                    if previous_path == desired_path and not original_changed:
                        previous_path = None
        elif local_present:
            assert local_item is not None
            previous_path = local_item.local_relative_path or local_item.target_relative_path
            action = ExportAction.REMOVE
            target_path = _removed_path(batch_id, candidate, previous_path)
            bound_review = current or latest_reviews.get(candidate.id)
            if bound_review is None:
                bound_review = await session.get(Review, local_item.review_id)

        if action is None or target_path is None or bound_review is None:
            continue
        if species_id is not None:
            matches_current = candidate.species_id == species_id
            matches_prior = local_item is not None and local_item.species_code == species.code
            if local_item is not None and local_item.species_code:
                prior_species_id = await session.scalar(
                    select(Species.id).where(Species.code == local_item.species_code)
                )
                matches_prior = prior_species_id == species_id
            if not matches_current and not matches_prior:
                continue
        deltas.append(
            Delta(
                candidate=candidate,
                species=species,
                review=bound_review,
                action=action,
                target_relative_path=target_path,
                previous_relative_path=previous_path,
                original_fingerprint=_original_fingerprint(candidate),
                metadata_fingerprint=_metadata_fingerprint(candidate, species),
            )
        )
    return sorted(deltas, key=lambda delta: (str(delta.candidate.id), delta.action.value))


def _delta_csv_row(
    batch_id: UUID, raw_token: str, delta: Delta
) -> dict[str, object]:
    candidate = delta.candidate
    return {
        "batch_id": str(batch_id),
        "receipt_token": raw_token,
        "action": delta.action.value,
        "candidate_id": str(candidate.id),
        "review_id": str(delta.review.id),
        # The fixed wire contract uses this field as the monotonic
        # candidate sync generation.  Every export-relevant mutation advances
        # Candidate.version, including same-review corrections.
        "review_version": candidate.version,
        "species_code": delta.species.code,
        "target_relative_path": delta.target_relative_path,
        "previous_relative_path": delta.previous_relative_path or "",
        "preview_url": candidate.preview_url,
        "original_url": candidate.original_url,
        "source_url": candidate.source_url,
        "creator": candidate.creator or "",
        "license": candidate.license,
        "license_url": candidate.license_url or "",
        "attribution": candidate.attribution,
        "image_origin_allowlist": _used_image_origins(
            candidate.preview_url, candidate.original_url
        ),
    }


def _item_csv_row(
    batch_id: UUID, raw_token: str, item: ExportItem
) -> dict[str, object]:
    action = item.action.value if isinstance(item.action, ExportAction) else str(item.action)
    return {
        "batch_id": str(batch_id),
        "receipt_token": raw_token,
        "action": action,
        "candidate_id": str(item.candidate_id),
        "review_id": str(item.review_id),
        "review_version": item.review_version,
        "species_code": item.species_code,
        "target_relative_path": item.target_relative_path,
        "previous_relative_path": item.previous_relative_path or "",
        "preview_url": item.preview_url,
        "original_url": item.original_url,
        "source_url": item.source_url,
        "creator": item.creator or "",
        "license": item.license,
        "license_url": item.license_url or "",
        "attribution": item.attribution,
        "image_origin_allowlist": _used_image_origins(
            item.preview_url, item.original_url
        ),
    }


def _encode_csv_rows(
    rows: Iterable[dict[str, object]], *, include_header: bool
) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=EXPORT_COLUMNS, lineterminator="\r\n")
    if include_header:
        writer.writeheader()
    for row in rows:
        writer.writerow(row)
    prefix = b"\xef\xbb\xbf" if include_header else b""
    return prefix + output.getvalue().encode("utf-8")


def _bounded_delta_chunk(
    batch_id: UUID, raw_token: str, deltas: Iterable[Delta]
) -> list[Delta]:
    selected: list[Delta] = []
    serialized_bytes = len(_encode_csv_rows((), include_header=True))
    for delta in deltas:
        if len(selected) >= EXPORT_MAX_ROWS:
            break
        row_bytes = len(
            _encode_csv_rows(
                (_delta_csv_row(batch_id, raw_token, delta),), include_header=False
            )
        )
        if serialized_bytes + row_bytes > EXPORT_MAX_BYTES:
            if not selected:
                raise ExportConflict("EXPORT_ROW_TOO_LARGE")
            break
        selected.append(delta)
        serialized_bytes += row_bytes
    return selected


async def create_export_batch(
    session: AsyncSession,
    actor_id: UUID,
    species_code: str | None,
    receipt_secret: str,
    *,
    image_origin_allowlist: tuple[str, ...] = DEFAULT_IMAGE_ORIGIN_ALLOWLIST,
) -> BatchCreationResult:
    if not receipt_secret:
        raise ValueError("RECEIPT_SECRET is required for exports")
    locked = False
    try:
        # Lock order: close the authentication transaction, acquire the common
        # generation boundary, serialize exporters at export_batches, then
        # expire and inspect batches. Export creation never locks
        # candidate/review rows, so it cannot invert writer row-lock order.
        locked = await _begin_creation_snapshot(session)
        effective_allowlist = await effective_image_origin_allowlist(
            session, image_origin_allowlist
        )
        await _expire_batches(session)
        species = None
        if species_code is not None:
            species = await session.scalar(select(Species).where(Species.code == species_code))
            if species is None:
                raise ExportNotFound
        scope = _scope_key(species.id if species is not None else None)
        existing = await session.scalar(
            select(ExportBatch)
            .where(ExportBatch.scope_key == scope, ExportBatch.status == "pending")
            .order_by(ExportBatch.created_at, ExportBatch.id)
            .limit(1)
        )
        batch_id = uuid4()
        deltas = await derive_deltas(
            session, batch_id=batch_id, species_id=species.id if species else None
        )
        try:
            for delta in deltas:
                require_approved_image_url(
                    delta.candidate.preview_url, effective_allowlist
                )
                require_approved_image_url(
                    delta.candidate.original_url, effective_allowlist
                )
        except ImageOriginError as exc:
            raise ExportConflict("IMAGE_ORIGIN_NOT_ALLOWED") from exc
        if not deltas:
            await session.commit()
            if existing is not None:
                return BatchCreationResult(existing, created=False)
            return BatchCreationResult(None, created=False, no_work=True)

        candidate_ids = {delta.candidate.id for delta in deltas}
        scheduled_rows = list(
            (
                await session.execute(
                    select(
                        ExportBatch.id,
                        ExportBatch.scope_key,
                        ExportItem.candidate_id,
                    )
                    .join(ExportItem, ExportItem.batch_id == ExportBatch.id)
                    .where(
                        ExportBatch.status == "pending",
                        ExportBatch.expires_at > utc_now(),
                        ExportItem.status == "pending",
                        ExportItem.candidate_id.in_(candidate_ids),
                    )
                    .order_by(ExportBatch.id, ExportItem.candidate_id)
                )
            ).all()
        )
        cross_scope_overlaps = tuple(
            sorted(
                {
                    row.id
                    for row in scheduled_rows
                    if row.scope_key != scope
                },
                key=str,
            )
        )
        if cross_scope_overlaps:
            raise ExportConflict("EXPORT_SCOPE_OVERLAP", cross_scope_overlaps)
        scheduled_ids = {row.candidate_id for row in scheduled_rows}
        unscheduled = [
            delta for delta in deltas if delta.candidate.id not in scheduled_ids
        ]
        if not unscheduled:
            await session.commit()
            if existing is not None:
                return BatchCreationResult(existing, created=False)
            return BatchCreationResult(None, created=False, no_work=True)

        raw_token = receipt_token(batch_id, receipt_secret)
        selected = _bounded_delta_chunk(batch_id, raw_token, unscheduled)
        if not selected:
            raise ExportConflict("EXPORT_ROW_TOO_LARGE")
        now = utc_now()
        batch = ExportBatch(
            id=batch_id,
            created_by_id=actor_id,
            species_id=species.id if species else None,
            scope_key=scope,
            receipt_token_hash=_token_digest(raw_token),
            status="pending",
            expires_at=now + EXPORT_TTL,
        )
        session.add(batch)
        for delta in selected:
            candidate = delta.candidate
            session.add(
                ExportItem(
                    batch_id=batch.id,
                    candidate_id=candidate.id,
                    review_id=delta.review.id,
                    review_version=candidate.version,
                    candidate_version=candidate.version,
                    action=delta.action,
                    status="pending",
                    target_relative_path=delta.target_relative_path,
                    previous_relative_path=delta.previous_relative_path,
                    species_code=delta.species.code,
                    preview_url=candidate.preview_url,
                    original_url=candidate.original_url,
                    source_url=candidate.source_url,
                    creator=candidate.creator,
                    license=candidate.license,
                    license_url=candidate.license_url,
                    attribution=candidate.attribution,
                    original_fingerprint=delta.original_fingerprint,
                    metadata_fingerprint=delta.metadata_fingerprint,
                )
            )
        session.add(
            AuditEvent(
                actor_id=actor_id,
                action="EXPORT_BATCH_CREATE",
                object_type="ExportBatch",
                object_id=str(batch.id),
                reason="Incremental local training-set export",
                before_json=None,
                after_json={
                    "species_code": species.code if species else None,
                    "item_count": len(selected),
                    "actions": {
                        action.value: sum(delta.action == action for delta in selected)
                        for action in ExportAction
                    },
                    "expires_at": batch.expires_at.isoformat(),
                },
            )
        )
        await session.commit()
        return BatchCreationResult(batch, created=True)
    except (ExportNotFound, ExportConflict):
        if session.in_transaction():
            await session.rollback()
        raise
    except BaseException:
        if session.in_transaction():
            await session.rollback()
        raise
    finally:
        await _end_creation_snapshot_lock(session, locked)


async def batch_response(
    session: AsyncSession, batch: ExportBatch, *, created: bool = False
) -> ExportBatchResponse:
    species_code = None
    if batch.species_id is not None:
        species_code = await session.scalar(
            select(Species.code).where(Species.id == batch.species_id)
        )
    counts = (
        await session.execute(
            select(
                func.count(ExportItem.id),
                func.count(ExportItem.id).filter(ExportItem.status == "pending"),
            ).where(ExportItem.batch_id == batch.id)
        )
    ).one()
    effective_status = batch.status
    if batch.status == "pending" and as_utc(batch.expires_at) <= utc_now():
        effective_status = "expired"
    return ExportBatchResponse(
        id=batch.id,
        species_code=species_code,
        status=effective_status,
        created_at=batch.created_at,
        expires_at=batch.expires_at,
        completed_at=batch.completed_at,
        expired_at=batch.expired_at,
        item_count=int(counts[0]),
        pending_count=int(counts[1]),
        created=created,
    )


async def list_batches(
    session: AsyncSession, *, limit: int = 100, offset: int = 0
) -> tuple[int, list[ExportBatchResponse]]:
    total = int(await session.scalar(select(func.count()).select_from(ExportBatch)) or 0)
    batches = list(
        (
            await session.scalars(
                select(ExportBatch).order_by(
                    ExportBatch.created_at.desc(), ExportBatch.id.desc()
                ).limit(limit).offset(offset)
            )
        ).all()
    )
    return total, [await batch_response(session, batch) for batch in batches]


async def pending_counts(session: AsyncSession) -> dict[str, int]:
    now = utc_now()
    active_species = list(
        (
            await session.scalars(
                select(Species).where(Species.active.is_(True)).order_by(Species.sort_order, Species.code)
            )
        ).all()
    )
    counts = {species.code: 0 for species in active_species}
    active_items = list(
        (
            await session.scalars(
                select(ExportItem)
                .join(ExportBatch, ExportBatch.id == ExportItem.batch_id)
                .where(
                    ExportBatch.status == "pending",
                    ExportBatch.expires_at > now,
                    ExportItem.status == "pending",
                )
            )
        ).all()
    )
    scheduled = {item.candidate_id for item in active_items}
    for item in active_items:
        if item.species_code in counts:
            counts[item.species_code] += 1
    draft_id = uuid4()
    for delta in await derive_deltas(session, batch_id=draft_id, species_id=None):
        if delta.candidate.id not in scheduled and delta.species.code in counts:
            counts[delta.species.code] += 1
    return counts


async def render_batch_csv(
    session: AsyncSession,
    batch_id: UUID,
    secret: str,
    *,
    image_origin_allowlist: tuple[str, ...] = DEFAULT_IMAGE_ORIGIN_ALLOWLIST,
) -> bytes:
    batch = await session.get(ExportBatch, batch_id)
    if batch is None:
        raise ExportNotFound
    if batch.status == "expired" or as_utc(batch.expires_at) <= utc_now():
        raise ReceiptRejected("EXPORT_BATCH_EXPIRED", 410)
    raw_token = receipt_token(batch.id, secret)
    if not hmac.compare_digest(_token_digest(raw_token), batch.receipt_token_hash):
        raise ReceiptRejected("EXPORT_TOKEN_UNAVAILABLE", 500)
    items = list(
        (
            await session.scalars(
                select(ExportItem)
                .where(ExportItem.batch_id == batch.id)
                .order_by(ExportItem.candidate_id, ExportItem.action, ExportItem.id)
            )
        ).all()
    )
    effective_allowlist = await effective_image_origin_allowlist(
        session, image_origin_allowlist
    )
    try:
        for item in items:
            require_approved_image_url(item.preview_url, effective_allowlist)
            require_approved_image_url(item.original_url, effective_allowlist)
    except ImageOriginError as exc:
        raise ReceiptRejected("IMAGE_ORIGIN_NOT_ALLOWED", 409) from exc
    content = _encode_csv_rows(
        (_item_csv_row(batch.id, raw_token, item) for item in items),
        include_header=True,
    )
    if len(items) > EXPORT_MAX_ROWS or len(content) > EXPORT_MAX_BYTES:
        raise ReceiptRejected("EXPORT_BATCH_ENVELOPE_INVALID", 500)
    return content


def _safe_receipt_path(
    value: str, expected: str, *, allow_extension_adjustment: bool
) -> str:
    if "\\" in value or any(ord(character) < 0x20 for character in value):
        raise ReceiptRejected("RECEIPT_PATH_INVALID", 422)
    if not allow_extension_adjustment and value != expected:
        raise ReceiptRejected("RECEIPT_PATH_INVALID", 422)
    path = PurePosixPath(value)
    expected_path = PurePosixPath(expected)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReceiptRejected("RECEIPT_PATH_INVALID", 422)
    if path.parent != expected_path.parent or path.stem != expected_path.stem:
        raise ReceiptRejected("RECEIPT_PATH_INVALID", 422)
    if path.suffix.lower() not in KNOWN_IMAGE_EXTENSIONS | {".image"}:
        raise ReceiptRejected("RECEIPT_PATH_INVALID", 422)
    return path.as_posix()


def _validate_receipt_items(
    stored_items: list[ExportItem],
    payload_items: Iterable[ReceiptItem],
) -> list[tuple[ExportItem, ReceiptItem, str | None, str | None]]:
    by_triple = {
        (item.candidate_id, item.review_id, item.review_version): item
        for item in stored_items
    }
    seen: set[tuple[UUID, UUID, int]] = set()
    validated = []
    for payload in payload_items:
        triple = (payload.candidate_id, payload.review_id, payload.review_version)
        if triple in seen:
            raise ReceiptRejected("RECEIPT_DUPLICATE_ITEM", 422)
        seen.add(triple)
        item = by_triple.get(triple)
        if item is None:
            raise ReceiptRejected("RECEIPT_ITEM_NOT_IN_BATCH", 409)
        normalized_hash = None
        normalized_path = None
        if payload.status == "SUCCEEDED":
            assert payload.sha256 is not None and payload.relative_path is not None
            normalized_hash = payload.sha256.lower()
            if re.fullmatch(r"[0-9a-f]{64}", normalized_hash) is None:
                raise ReceiptRejected("RECEIPT_HASH_INVALID", 422)
            normalized_path = _safe_receipt_path(
                payload.relative_path,
                item.target_relative_path,
                allow_extension_adjustment=item.action == ExportAction.ADD,
            )
            if item.status == "succeeded" and (
                not hmac.compare_digest(item.sha256 or "", normalized_hash)
                or item.local_relative_path != normalized_path
            ):
                raise ReceiptRejected("RECEIPT_SUCCESS_CONFLICT", 409)
        elif item.status == "succeeded":
            raise ReceiptRejected("RECEIPT_SUCCESS_CONFLICT", 409)
        else:
            assert payload.error is not None
            normalized_path = FAILED_ERROR_CODE
        validated.append((item, payload, normalized_hash, normalized_path))
    return validated


async def apply_receipt(
    session: AsyncSession,
    batch_id: UUID,
    items: list[ReceiptItem],
    *,
    raw_token: str | None = None,
    actor_id: UUID | None = None,
) -> ReceiptResponse:
    try:
        await acquire_sync_generation_lock(session)
        batch = await session.scalar(
            select(ExportBatch)
            .where(ExportBatch.id == batch_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if batch is None:
            raise ReceiptRejected("RECEIPT_NOT_AUTHORIZED", 401)
        now = utc_now()
        if batch.status == "expired" or as_utc(batch.expires_at) <= now:
            raise ReceiptRejected("RECEIPT_NOT_AUTHORIZED", 401)
        if raw_token is not None:
            supplied = _token_digest(raw_token)
            if not hmac.compare_digest(supplied, batch.receipt_token_hash):
                raise ReceiptRejected("RECEIPT_NOT_AUTHORIZED", 401)
        elif actor_id is None:
            raise ReceiptRejected("RECEIPT_NOT_AUTHORIZED", 401)

        stored_items = list(
            (
                await session.scalars(
                    select(ExportItem)
                    .where(ExportItem.batch_id == batch.id)
                    .order_by(ExportItem.candidate_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        validated = _validate_receipt_items(stored_items, items)
        changed_ids: list[str] = []
        accepted: list[UUID] = []
        for item, payload, normalized_hash, normalized_value in validated:
            if payload.status == "SUCCEEDED":
                accepted.append(item.candidate_id)
                if item.status != "succeeded":
                    item.status = "succeeded"
                    item.sha256 = normalized_hash
                    item.local_relative_path = normalized_value
                    item.error = None
                    item.succeeded_at = now
                    changed_ids.append(str(item.candidate_id))
            else:
                if item.error != normalized_value:
                    item.error = normalized_value
                    changed_ids.append(str(item.candidate_id))

        pending = [item.candidate_id for item in stored_items if item.status != "succeeded"]
        new_status = "pending" if pending else "completed"
        if batch.status != new_status:
            batch.status = new_status
            batch.completed_at = now if new_status == "completed" else None
        if changed_ids:
            session.add(
                AuditEvent(
                    actor_id=actor_id or batch.created_by_id,
                    action="EXPORT_RECEIPT_APPLY",
                    object_type="ExportBatch",
                    object_id=str(batch.id),
                    reason="Local training-set receipt applied",
                    before_json=None,
                    after_json={
                        "changed_candidate_ids": sorted(changed_ids),
                        "accepted_count": len(accepted),
                        "pending_count": len(pending),
                        "batch_status": new_status,
                    },
                )
            )
        await session.commit()
        return ReceiptResponse(
            batch_id=batch.id,
            status=new_status,
            accepted_candidate_ids=sorted(set(accepted), key=str),
            pending_candidate_ids=sorted(pending, key=str),
        )
    except ReceiptRejected:
        if session.in_transaction():
            await session.rollback()
        raise
    except BaseException:
        if session.in_transaction():
            await session.rollback()
        raise
