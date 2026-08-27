from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import time
from typing import Iterator, Literal
from uuid import UUID

from .manifest import ManifestError, resolve_inside, validate_relative_path


SCHEMA_VERSION = 3
INDEX_FILENAME = ".sukaseafood-sync.sqlite3"
BUSY_TIMEOUT_MS = 5_000
MAX_SAFE_INTEGER = 2**63 - 1
_HEX_64 = re.compile(r"[0-9a-fA-F]{64}\Z", re.ASCII)
_HEX_BOUNDED = re.compile(r"[0-9a-fA-F]{1,256}\Z", re.ASCII)
_ACTIONS = frozenset({"ADD", "MOVE", "REMOVE"})
_ADD_DECODED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_SQLITE_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")
_SQLITE_PATH_VALIDATION_ATTEMPTS = 2
_COLUMNS = (
    "candidate_id",
    "review_id",
    "review_version",
    "action",
    "batch_id",
    "relative_path",
    "sha256",
    "perceptual_hash",
    "completed_at",
    "receipt_submitted_at",
)
_COLUMN_SHAPE = (
    ("candidate_id", "TEXT", 1, 1),
    ("review_id", "TEXT", 1, 2),
    ("review_version", "INTEGER", 1, 3),
    ("action", "TEXT", 1, 4),
    ("batch_id", "TEXT", 1, 0),
    ("relative_path", "TEXT", 1, 0),
    ("sha256", "TEXT", 1, 0),
    ("perceptual_hash", "TEXT", 1, 0),
    ("completed_at", "TEXT", 1, 0),
    ("receipt_submitted_at", "TEXT", 0, 0),
)

_CREATE_SYNCED_ITEMS = """
CREATE TABLE synced_items (
    candidate_id TEXT NOT NULL,
    review_id TEXT NOT NULL,
    review_version INTEGER NOT NULL CHECK (
        review_version >= 1 AND review_version <= 9223372036854775807
    ),
    action TEXT NOT NULL CHECK (action IN ('ADD', 'MOVE', 'REMOVE')),
    batch_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    perceptual_hash TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    receipt_submitted_at TEXT,
    PRIMARY KEY (candidate_id, review_id, review_version, action)
)
"""

_CREATE_PENDING_ADDS_V2 = """
CREATE TABLE pending_adds (
    candidate_id TEXT NOT NULL,
    review_id TEXT NOT NULL,
    review_version INTEGER NOT NULL CHECK (
        review_version >= 1 AND review_version <= 9223372036854775807
    ),
    action TEXT NOT NULL CHECK (action = 'ADD'),
    batch_id TEXT NOT NULL,
    target_relative_path TEXT NOT NULL,
    actual_relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    perceptual_hash TEXT NOT NULL,
    PRIMARY KEY (candidate_id, review_id, review_version, action)
)
"""

_CREATE_PENDING_ADDS = """
CREATE TABLE "pending_adds" (
    candidate_id TEXT NOT NULL,
    review_id TEXT NOT NULL,
    review_version INTEGER NOT NULL CHECK (
        review_version >= 1 AND review_version <= 9223372036854775807
    ),
    action TEXT NOT NULL CHECK (action = 'ADD'),
    batch_id TEXT NOT NULL,
    target_relative_path TEXT NOT NULL,
    actual_relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    perceptual_hash TEXT NOT NULL,
    prior_relative_path TEXT,
    prior_sha256 TEXT,
    backup_relative_path TEXT,
    CHECK (
        (
            prior_relative_path IS NULL
            AND prior_sha256 IS NULL
            AND backup_relative_path IS NULL
        ) OR (
            prior_relative_path IS NOT NULL
            AND prior_sha256 IS NOT NULL
            AND backup_relative_path IS NOT NULL
            AND prior_sha256 <> sha256
        )
    ),
    PRIMARY KEY (candidate_id, review_id, review_version, action)
)
"""

_CREATE_PENDING_ADDS_MIGRATION = _CREATE_PENDING_ADDS.replace(
    '"pending_adds"', '"pending_adds_v3"', 1
)


def _normalized_sql(value: str) -> str:
    normalized: list[str] = []
    quote_end: str | None = None
    pending_space = False
    position = 0
    while position < len(value):
        character = value[position]
        if quote_end is not None:
            normalized.append(character)
            if character == quote_end:
                if (
                    quote_end != "]"
                    and position + 1 < len(value)
                    and value[position + 1] == quote_end
                ):
                    normalized.append(value[position + 1])
                    position += 1
                else:
                    quote_end = None
            position += 1
            continue
        if character.isspace():
            pending_space = True
            position += 1
            continue
        if pending_space and normalized:
            normalized.append(" ")
        pending_space = False
        normalized.append(character)
        if character in {"'", '"', "`"}:
            quote_end = character
        elif character == "[":
            quote_end = "]"
        position += 1
    return "".join(normalized).strip()


_SCHEMA_FINGERPRINT = hashlib.sha256(
    _normalized_sql(_CREATE_SYNCED_ITEMS).encode("utf-8")
).hexdigest()
_PENDING_SCHEMA_FINGERPRINT_V2 = hashlib.sha256(
    _normalized_sql(_CREATE_PENDING_ADDS_V2).encode("utf-8")
).hexdigest()
_PENDING_SCHEMA_FINGERPRINT = hashlib.sha256(
    _normalized_sql(_CREATE_PENDING_ADDS).encode("utf-8")
).hexdigest()
_V1_EXPECTED_OBJECTS = {
    ("index", "sqlite_autoindex_synced_items_1", "synced_items"),
    ("table", "synced_items", "synced_items"),
}
_EXPECTED_OBJECTS = _V1_EXPECTED_OBJECTS | {
    ("index", "sqlite_autoindex_pending_adds_1", "pending_adds"),
    ("table", "pending_adds", "pending_adds"),
}
_PENDING_COLUMNS_V2 = (
    "candidate_id",
    "review_id",
    "review_version",
    "action",
    "batch_id",
    "target_relative_path",
    "actual_relative_path",
    "sha256",
    "perceptual_hash",
)
_PENDING_COLUMN_SHAPE_V2 = (
    ("candidate_id", "TEXT", 1, 1),
    ("review_id", "TEXT", 1, 2),
    ("review_version", "INTEGER", 1, 3),
    ("action", "TEXT", 1, 4),
    ("batch_id", "TEXT", 1, 0),
    ("target_relative_path", "TEXT", 1, 0),
    ("actual_relative_path", "TEXT", 1, 0),
    ("sha256", "TEXT", 1, 0),
    ("perceptual_hash", "TEXT", 1, 0),
)
_PENDING_COLUMNS = _PENDING_COLUMNS_V2 + (
    "prior_relative_path",
    "prior_sha256",
    "backup_relative_path",
)
_PENDING_COLUMN_SHAPE = _PENDING_COLUMN_SHAPE_V2 + (
    ("prior_relative_path", "TEXT", 0, 0),
    ("prior_sha256", "TEXT", 0, 0),
    ("backup_relative_path", "TEXT", 0, 0),
)
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class SyncIndexError(ValueError):
    """The local index or supplied index data is invalid."""


class IndexConflict(SyncIndexError):
    """A completed key already has a different result."""


class IndexNotFound(SyncIndexError):
    """The requested completed key does not exist."""


@dataclass(frozen=True, slots=True)
class SyncResult:
    candidate_id: UUID | str
    review_id: UUID | str
    review_version: int
    action: Literal["ADD", "MOVE", "REMOVE"] | str
    batch_id: UUID | str
    relative_path: PurePosixPath | str
    sha256: str
    perceptual_hash: str
    completed_at: datetime | None = None
    status: Literal["SUCCEEDED", "SKIPPED_ALREADY_COMPLETED"] = "SUCCEEDED"


@dataclass(frozen=True, slots=True)
class SyncRecord:
    candidate_id: UUID
    review_id: UUID
    review_version: int
    action: Literal["ADD", "MOVE", "REMOVE"]
    batch_id: UUID
    relative_path: PurePosixPath
    sha256: str
    perceptual_hash: str
    completed_at: datetime
    receipt_submitted_at: datetime | None

    @property
    def present(self) -> bool:
        return self.action in {"ADD", "MOVE"}


@dataclass(frozen=True, slots=True)
class AddIntent:
    candidate_id: UUID
    review_id: UUID
    review_version: int
    action: Literal["ADD"]
    batch_id: UUID
    target_relative_path: PurePosixPath
    actual_relative_path: PurePosixPath
    sha256: str
    perceptual_hash: str
    prior_relative_path: PurePosixPath | None = None
    prior_sha256: str | None = None
    backup_relative_path: PurePosixPath | None = None


def _uuid(value: UUID | str, field_name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise SyncIndexError(f"{field_name} must be a UUID")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise SyncIndexError(f"{field_name} must be a UUID") from exc
    if str(parsed) != value:
        raise SyncIndexError(f"{field_name} must be a canonical UUID")
    return parsed


def _version(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SyncIndexError("review_version must be a positive safe integer")
    if not 1 <= value <= MAX_SAFE_INTEGER:
        raise SyncIndexError("review_version must be a positive safe integer")
    return value


def _action(value: str) -> Literal["ADD", "MOVE", "REMOVE"]:
    if value not in _ACTIONS:
        raise SyncIndexError("action must be exactly ADD, MOVE, or REMOVE")
    return value  # type: ignore[return-value]


def _hash(value: str, field_name: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise SyncIndexError(f"{field_name} must be bounded hexadecimal text")
    return value.lower()


def _timestamp(value: datetime | None, field_name: str) -> datetime:
    timestamp = value if value is not None else datetime.now(timezone.utc)
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        raise SyncIndexError(f"{field_name} must be timezone-aware")
    return timestamp.astimezone(timezone.utc)


def _serialize_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _parse_timestamp(value: str | None, field_name: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise SyncIndexError(f"stored {field_name} is invalid") from exc
    if parsed.tzinfo is None:
        raise SyncIndexError(f"stored {field_name} is invalid")
    return parsed.astimezone(timezone.utc)


class SyncIndex:
    """Durable per-root index using owned, sidecar-checked connections.

    The main file and SQLite's predictable single-database sidecars are checked
    immediately before and after connection opens and around WAL activation.
    Python's sqlite3 API does not expose SQLITE_OPEN_NOFOLLOW, so a same-user
    process able to swap directory entries inside the remaining check/use
    window is outside this boundary and must be excluded by root permissions.
    This class never issues ATTACH, so SQLite super-journals are not used.
    """

    def __init__(self, root: Path) -> None:
        selected = Path(root)
        try:
            selected.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SyncIndexError("training root cannot be created") from exc
        if not selected.is_dir():
            raise SyncIndexError("training root must be a directory")
        self.root = selected.resolve()
        self.path = self.root / INDEX_FILENAME
        self._sidecar_paths = tuple(
            Path(f"{self.path}{suffix}") for suffix in _SQLITE_SIDECAR_SUFFIXES
        )
        self._validate_sqlite_paths()
        self._initialize()

    @staticmethod
    def _inspect_sqlite_path(
        candidate: Path, *, label: str
    ) -> os.stat_result | None:
        try:
            return os.lstat(candidate)
        except FileNotFoundError:
            return None
        except OSError:
            pass
        raise SyncIndexError(f"{label} path cannot be inspected safely")

    @staticmethod
    def _validate_sqlite_metadata(metadata: os.stat_result, *, label: str) -> None:
        attributes = getattr(metadata, "st_file_attributes", 0)
        if stat.S_ISLNK(metadata.st_mode):
            raise SyncIndexError(f"{label} path must not be a symlink or reparse point")
        if attributes & _REPARSE_POINT:
            raise SyncIndexError(f"{label} path must not be a reparse point")
        if not stat.S_ISREG(metadata.st_mode):
            raise SyncIndexError(f"{label} path must be a regular file")

    def _validate_sqlite_parent(self, candidate: Path, *, label: str) -> None:
        resolved_parent = None
        try:
            resolved_parent = candidate.parent.resolve(strict=True)
        except (OSError, RuntimeError):
            pass
        if resolved_parent != self.root:
            raise SyncIndexError(
                f"{label} path must remain inside the selected root"
            )

    def _validate_sqlite_path(self, candidate: Path, *, sidecar: bool) -> None:
        label = "SQLite sidecar" if sidecar else "index"
        if candidate.parent != self.root:
            raise SyncIndexError(f"{label} path must remain inside the selected root")
        attempts = _SQLITE_PATH_VALIDATION_ATTEMPTS if sidecar else 1
        for attempt in range(attempts):
            metadata = self._inspect_sqlite_path(candidate, label=label)
            if metadata is None:
                self._validate_sqlite_parent(candidate, label=label)
                metadata = self._inspect_sqlite_path(candidate, label=label)
                if metadata is None:
                    return
            self._validate_sqlite_metadata(metadata, label=label)

            resolution_missing = False
            resolution_failed = False
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(self.root)
            except FileNotFoundError:
                resolution_missing = True
            except (OSError, RuntimeError, ValueError):
                resolution_failed = True
            if resolution_failed:
                if not sidecar:
                    raise SyncIndexError(
                        f"{label} path must remain inside the selected root"
                    )
                self._validate_sqlite_parent(candidate, label=label)
                current = self._inspect_sqlite_path(candidate, label=label)
                if current is None:
                    return
                self._validate_sqlite_metadata(current, label=label)
                if attempt + 1 == attempts:
                    raise SyncIndexError(
                        f"{label} path must remain inside the selected root"
                    )
                if os.path.samestat(metadata, current):
                    time.sleep(0)
                continue
            if resolution_missing:
                if not sidecar:
                    raise SyncIndexError(
                        f"{label} path must remain inside the selected root"
                    )
                self._validate_sqlite_parent(candidate, label=label)
                current = self._inspect_sqlite_path(candidate, label=label)
                if current is None:
                    return
                self._validate_sqlite_metadata(current, label=label)
                if os.path.samestat(metadata, current):
                    raise SyncIndexError(
                        f"{label} path must remain inside the selected root"
                    )
            else:
                current = self._inspect_sqlite_path(candidate, label=label)
                if current is None:
                    if not sidecar:
                        raise SyncIndexError(
                            f"{label} path must remain inside the selected root"
                        )
                    self._validate_sqlite_parent(candidate, label=label)
                    current = self._inspect_sqlite_path(candidate, label=label)
                    if current is None:
                        return
                self._validate_sqlite_metadata(current, label=label)
                if os.path.samestat(metadata, current):
                    return

            if attempt + 1 == attempts:
                raise SyncIndexError(f"{label} path changed during validation")

    def _validate_sqlite_paths(self) -> None:
        self._validate_sqlite_path(self.path, sidecar=False)
        for sidecar in self._sidecar_paths:
            self._validate_sqlite_path(sidecar, sidecar=True)

    def _open_connection(self) -> sqlite3.Connection:
        self._validate_sqlite_paths()
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=BUSY_TIMEOUT_MS / 1_000,
                isolation_level=None,
            )
        except sqlite3.Error as exc:
            raise SyncIndexError("index database cannot be opened") from exc
        connection.row_factory = sqlite3.Row
        try:
            self._validate_sqlite_paths()
            connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
        except Exception:
            connection.close()
            raise
        return connection

    @staticmethod
    def _verify_connection_pragmas(
        connection: sqlite3.Connection, *, require_wal: bool
    ) -> None:
        busy_timeout = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
        foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
        synchronous = int(connection.execute("PRAGMA synchronous").fetchone()[0])
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        if (
            busy_timeout < BUSY_TIMEOUT_MS
            or foreign_keys != 1
            or synchronous < 2
            or (require_wal and journal_mode.casefold() != "wal")
        ):
            raise SyncIndexError("required SQLite durability settings are unavailable")

    def _enable_wal(self, connection: sqlite3.Connection) -> None:
        deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1_000
        while True:
            try:
                self._validate_sqlite_paths()
                mode = str(
                    connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                )
                self._validate_sqlite_paths()
                if mode.casefold() == "wal":
                    return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).casefold() and "busy" not in str(exc).casefold():
                    raise SyncIndexError("SQLite WAL mode cannot be enabled") from exc
            if time.monotonic() >= deadline:
                raise SyncIndexError("SQLite WAL mode cannot be enabled under contention")
            time.sleep(0.01)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = self._open_connection()
        try:
            self._enable_wal(connection)
            self._verify_connection_pragmas(connection, require_wal=True)
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _migrate_v2_pending_adds(connection: sqlite3.Connection) -> None:
        """Replace the v2 intent table only after proving an exact copy."""

        legacy_columns = ", ".join(_PENDING_COLUMNS_V2)
        connection.execute(_CREATE_PENDING_ADDS_MIGRATION)
        connection.execute(
            f'INSERT INTO "pending_adds_v3" ({legacy_columns}) '
            f"SELECT {legacy_columns} FROM pending_adds"
        )
        legacy_count = int(
            connection.execute("SELECT count(*) FROM pending_adds").fetchone()[0]
        )
        migrated_count = int(
            connection.execute('SELECT count(*) FROM "pending_adds_v3"').fetchone()[0]
        )
        missing_from_migration = connection.execute(
            f"SELECT 1 FROM (SELECT {legacy_columns} FROM pending_adds "
            f"EXCEPT SELECT {legacy_columns} FROM \"pending_adds_v3\") LIMIT 1"
        ).fetchone()
        added_by_migration = connection.execute(
            f"SELECT 1 FROM (SELECT {legacy_columns} FROM \"pending_adds_v3\" "
            f"EXCEPT SELECT {legacy_columns} FROM pending_adds) LIMIT 1"
        ).fetchone()
        unexpected_evidence = connection.execute(
            'SELECT 1 FROM "pending_adds_v3" '
            "WHERE prior_relative_path IS NOT NULL "
            "OR prior_sha256 IS NOT NULL OR backup_relative_path IS NOT NULL LIMIT 1"
        ).fetchone()
        if (
            legacy_count != migrated_count
            or missing_from_migration is not None
            or added_by_migration is not None
            or unexpected_evidence is not None
        ):
            raise SyncIndexError("pending ADD migration verification failed")
        connection.execute("DROP TABLE pending_adds")
        connection.execute(
            'ALTER TABLE "pending_adds_v3" RENAME TO "pending_adds"'
        )

    def _initialize(self) -> None:
        connection = self._open_connection()
        try:
            self._validate_sqlite_paths()
            connection.execute("BEGIN IMMEDIATE")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            objects = list(
                connection.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master "
                    "ORDER BY type, name"
                )
            )
            if version > SCHEMA_VERSION:
                raise SyncIndexError(
                    f"index uses newer schema version {version}; supported version is {SCHEMA_VERSION}"
                )
            if version == 0:
                if objects:
                    raise SyncIndexError("refusing to rewrite an unversioned existing database")
                connection.execute(_CREATE_SYNCED_ITEMS)
                connection.execute(_CREATE_PENDING_ADDS)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version == 1:
                self._verify_schema(connection, version=1)
                connection.execute(_CREATE_PENDING_ADDS)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version == 2:
                self._verify_schema(connection, version=2)
                self._migrate_v2_pending_adds(connection)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version != SCHEMA_VERSION:
                raise SyncIndexError(f"unsupported schema version {version}")
            self._verify_schema(connection, version=SCHEMA_VERSION)
            self._verify_connection_pragmas(connection, require_wal=False)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        wal_connection = self._open_connection()
        try:
            self._enable_wal(wal_connection)
            self._verify_connection_pragmas(wal_connection, require_wal=True)
        finally:
            wal_connection.close()

    @staticmethod
    def _verify_schema(connection: sqlite3.Connection, *, version: int) -> None:
        objects = list(
            connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "ORDER BY type, name"
            )
        )
        object_keys = {(row[0], row[1], row[2]) for row in objects}
        synced_sql = next(
            (row[3] for row in objects if row[0] == "table" and row[1] == "synced_items"),
            None,
        )
        synced_fingerprint = (
            hashlib.sha256(_normalized_sql(synced_sql).encode("utf-8")).hexdigest()
            if isinstance(synced_sql, str)
            else None
        )
        synced_info = list(connection.execute("PRAGMA table_info(synced_items)"))
        synced_columns = tuple(row[1] for row in synced_info)
        synced_shape = tuple(
            (row[1], str(row[2]).upper(), row[3], row[5]) for row in synced_info
        )
        synced_primary_key = tuple(
            row[1]
            for row in sorted(
                synced_info,
                key=lambda item: item[5] if item[5] else 99,
            )
            if row[5]
        )
        pending_sql = next(
            (row[3] for row in objects if row[0] == "table" and row[1] == "pending_adds"),
            None,
        )
        pending_fingerprint = (
            hashlib.sha256(_normalized_sql(pending_sql).encode("utf-8")).hexdigest()
            if isinstance(pending_sql, str)
            else None
        )
        pending_info = list(connection.execute("PRAGMA table_info(pending_adds)"))
        pending_columns = tuple(row[1] for row in pending_info)
        pending_shape = tuple(
            (row[1], str(row[2]).upper(), row[3], row[5]) for row in pending_info
        )
        pending_primary_key = tuple(
            row[1]
            for row in sorted(
                pending_info,
                key=lambda item: item[5] if item[5] else 99,
            )
            if row[5]
        )
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        if version == 1:
            expected_objects = _V1_EXPECTED_OBJECTS
            expected_pending_fingerprint = None
            expected_pending_columns: tuple[str, ...] = ()
            expected_pending_shape: tuple[tuple[str, str, int, int], ...] = ()
        elif version == 2:
            expected_objects = _EXPECTED_OBJECTS
            expected_pending_fingerprint = _PENDING_SCHEMA_FINGERPRINT_V2
            expected_pending_columns = _PENDING_COLUMNS_V2
            expected_pending_shape = _PENDING_COLUMN_SHAPE_V2
        elif version == SCHEMA_VERSION:
            expected_objects = _EXPECTED_OBJECTS
            expected_pending_fingerprint = _PENDING_SCHEMA_FINGERPRINT
            expected_pending_columns = _PENDING_COLUMNS
            expected_pending_shape = _PENDING_COLUMN_SHAPE
        else:
            raise SyncIndexError(f"unsupported schema version {version}")
        if (
            object_keys != expected_objects
            or synced_fingerprint != _SCHEMA_FINGERPRINT
            or synced_columns != _COLUMNS
            or synced_shape != _COLUMN_SHAPE
            or synced_primary_key
            != ("candidate_id", "review_id", "review_version", "action")
            or (
                version != 1
                and (
                    pending_fingerprint != expected_pending_fingerprint
                    or pending_columns != expected_pending_columns
                    or pending_shape != expected_pending_shape
                    or pending_primary_key
                    != ("candidate_id", "review_id", "review_version", "action")
                )
            )
            or integrity != "ok"
        ):
            raise SyncIndexError("existing index schema is incompatible")

    def _safe_path(self, value: PurePosixPath | str) -> PurePosixPath:
        try:
            safe = validate_relative_path(value, "relative_path")
            resolve_inside(self.root, safe)
        except ManifestError as exc:
            raise SyncIndexError(str(exc)) from exc
        return safe

    def _validated_result(self, result: SyncResult) -> SyncRecord:
        if not isinstance(result, SyncResult):
            raise SyncIndexError("result must be a SyncResult")
        if result.status != "SUCCEEDED":
            raise SyncIndexError("result status must be exactly SUCCEEDED")
        return SyncRecord(
            candidate_id=_uuid(result.candidate_id, "candidate_id"),
            review_id=_uuid(result.review_id, "review_id"),
            review_version=_version(result.review_version),
            action=_action(result.action),
            batch_id=_uuid(result.batch_id, "batch_id"),
            relative_path=self._safe_path(result.relative_path),
            sha256=_hash(result.sha256, "sha256", _HEX_64),
            perceptual_hash=_hash(
                result.perceptual_hash, "perceptual_hash", _HEX_BOUNDED
            ),
            completed_at=_timestamp(result.completed_at, "completed_at"),
            receipt_submitted_at=None,
        )

    @staticmethod
    def _key(record: SyncRecord) -> tuple[str, str, int, str]:
        return (
            str(record.candidate_id),
            str(record.review_id),
            record.review_version,
            record.action,
        )

    def _from_row(self, row: sqlite3.Row) -> SyncRecord:
        try:
            return SyncRecord(
                candidate_id=_uuid(row["candidate_id"], "candidate_id"),
                review_id=_uuid(row["review_id"], "review_id"),
                review_version=_version(row["review_version"]),
                action=_action(row["action"]),
                batch_id=_uuid(row["batch_id"], "batch_id"),
                relative_path=self._safe_path(row["relative_path"]),
                sha256=_hash(row["sha256"], "sha256", _HEX_64),
                perceptual_hash=_hash(
                    row["perceptual_hash"], "perceptual_hash", _HEX_BOUNDED
                ),
                completed_at=_parse_timestamp(row["completed_at"], "completed_at")
                or _timestamp(None, "completed_at"),
                receipt_submitted_at=_parse_timestamp(
                    row["receipt_submitted_at"], "receipt_submitted_at"
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, SyncIndexError):
                raise
            raise SyncIndexError("stored index row is invalid") from exc

    def _validated_add_intent(
        self,
        result: SyncResult,
        target_relative_path: PurePosixPath | str,
        *,
        prior_relative_path: PurePosixPath | str | None = None,
        prior_sha256: str | None = None,
        backup_relative_path: PurePosixPath | str | None = None,
    ) -> AddIntent:
        desired = self._validated_result(result)
        if desired.action != "ADD":
            raise SyncIndexError("pending intent action must be exactly ADD")
        target = self._safe_path(target_relative_path)
        actual = desired.relative_path
        if (
            target.parent != actual.parent
            or target.stem != actual.stem
            or actual.suffix.casefold() not in _ADD_DECODED_SUFFIXES
        ):
            raise SyncIndexError("pending ADD paths are incompatible")
        replacement_presence = (
            prior_relative_path is not None,
            prior_sha256 is not None,
            backup_relative_path is not None,
        )
        if any(replacement_presence) and not all(replacement_presence):
            raise SyncIndexError("replacement evidence must be all present or all absent")
        prior: PurePosixPath | None = None
        prior_digest: str | None = None
        backup: PurePosixPath | None = None
        if all(replacement_presence):
            assert prior_relative_path is not None
            assert prior_sha256 is not None
            assert backup_relative_path is not None
            prior = self._safe_path(prior_relative_path)
            prior_digest = _hash(prior_sha256, "prior_sha256", _HEX_64)
            backup = self._safe_path(backup_relative_path)
            candidate_name = str(desired.candidate_id)
            if any(
                path.stem != candidate_name for path in (target, actual, prior, backup)
            ):
                raise SyncIndexError(
                    "replacement paths must identify the same candidate"
                )
            if prior.as_posix().casefold() != actual.as_posix().casefold():
                raise SyncIndexError(
                    "replacement paths must identify the same managed path"
                )
            if (
                len(backup.parts) != 3
                or backup.parts[:2] != ("_removed", str(desired.batch_id))
                or backup.name.casefold() != prior.name.casefold()
            ):
                raise SyncIndexError(
                    "replacement backup path must be inside the batch recovery area"
                )
            if prior_digest == desired.sha256:
                raise SyncIndexError("replacement prior and new sha256 must differ")
        return AddIntent(
            candidate_id=desired.candidate_id,
            review_id=desired.review_id,
            review_version=desired.review_version,
            action="ADD",
            batch_id=desired.batch_id,
            target_relative_path=target,
            actual_relative_path=actual,
            sha256=desired.sha256,
            perceptual_hash=desired.perceptual_hash,
            prior_relative_path=prior,
            prior_sha256=prior_digest,
            backup_relative_path=backup,
        )

    def _from_intent_row(self, row: sqlite3.Row) -> AddIntent:
        try:
            action = _action(row["action"])
            if action != "ADD":
                raise SyncIndexError("stored pending intent action is invalid")
            result = SyncResult(
                candidate_id=row["candidate_id"],
                review_id=row["review_id"],
                review_version=row["review_version"],
                action=action,
                batch_id=row["batch_id"],
                relative_path=row["actual_relative_path"],
                sha256=row["sha256"],
                perceptual_hash=row["perceptual_hash"],
            )
            return self._validated_add_intent(
                result,
                row["target_relative_path"],
                prior_relative_path=row["prior_relative_path"],
                prior_sha256=row["prior_sha256"],
                backup_relative_path=row["backup_relative_path"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, SyncIndexError):
                raise
            raise SyncIndexError("stored pending ADD intent is invalid") from exc

    @staticmethod
    def _same_result(existing: SyncRecord, desired: SyncRecord) -> bool:
        return (
            existing.candidate_id == desired.candidate_id
            and existing.review_id == desired.review_id
            and existing.review_version == desired.review_version
            and existing.action == desired.action
            and existing.batch_id == desired.batch_id
            and existing.relative_path == desired.relative_path
            and existing.sha256 == desired.sha256
            and existing.perceptual_hash == desired.perceptual_hash
        )

    def is_completed(
        self,
        candidate_id: UUID | str,
        review_id: UUID | str,
        review_version: int,
        action: str,
    ) -> bool:
        return self.get_completed(
            candidate_id, review_id, review_version, action
        ) is not None

    def get_completed(
        self,
        candidate_id: UUID | str,
        review_id: UUID | str,
        review_version: int,
        action: str,
    ) -> SyncRecord | None:
        """Return the successful record for an exact operation key, if present."""

        key = (
            str(_uuid(candidate_id, "candidate_id")),
            str(_uuid(review_id, "review_id")),
            _version(review_version),
            _action(action),
        )
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM synced_items "
                "WHERE candidate_id = ? AND review_id = ? "
                "AND review_version = ? AND action = ?",
                key,
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def get_add_intent(
        self,
        candidate_id: UUID | str,
        review_id: UUID | str,
        review_version: int,
        action: str,
    ) -> AddIntent | None:
        """Return durable promotion evidence for an exact ADD operation key."""

        key = (
            str(_uuid(candidate_id, "candidate_id")),
            str(_uuid(review_id, "review_id")),
            _version(review_version),
            _action(action),
        )
        if key[3] != "ADD":
            raise SyncIndexError("pending intent action must be exactly ADD")
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pending_adds "
                "WHERE candidate_id = ? AND review_id = ? "
                "AND review_version = ? AND action = ?",
                key,
            ).fetchone()
        return self._from_intent_row(row) if row is not None else None

    def record_add_intent(
        self,
        result: SyncResult,
        target_relative_path: PurePosixPath | str,
        *,
        prior_relative_path: PurePosixPath | str | None = None,
        prior_sha256: str | None = None,
        backup_relative_path: PurePosixPath | str | None = None,
    ) -> AddIntent:
        """Durably bind an alternate decoded target to one exact ADD operation."""

        desired = self._validated_add_intent(
            result,
            target_relative_path,
            prior_relative_path=prior_relative_path,
            prior_sha256=prior_sha256,
            backup_relative_path=backup_relative_path,
        )
        key = (
            str(desired.candidate_id),
            str(desired.review_id),
            desired.review_version,
            desired.action,
        )
        with self.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM pending_adds "
                    "WHERE candidate_id = ? AND review_id = ? "
                    "AND review_version = ? AND action = ?",
                    key,
                ).fetchone()
                if row is not None:
                    existing = self._from_intent_row(row)
                    if existing != desired:
                        raise IndexConflict("pending ADD intent conflict")
                    connection.commit()
                    return existing
                connection.execute(
                    "INSERT INTO pending_adds ("
                    "candidate_id, review_id, review_version, action, batch_id, "
                    "target_relative_path, actual_relative_path, sha256, perceptual_hash, "
                    "prior_relative_path, prior_sha256, backup_relative_path"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        *key,
                        str(desired.batch_id),
                        desired.target_relative_path.as_posix(),
                        desired.actual_relative_path.as_posix(),
                        desired.sha256,
                        desired.perceptual_hash,
                        (
                            desired.prior_relative_path.as_posix()
                            if desired.prior_relative_path is not None
                            else None
                        ),
                        desired.prior_sha256,
                        (
                            desired.backup_relative_path.as_posix()
                            if desired.backup_relative_path is not None
                            else None
                        ),
                    ),
                )
                connection.commit()
                return desired
            except Exception:
                connection.rollback()
                raise

    def clear_add_intent_if_matches(self, expected: AddIntent) -> bool:
        """Delete only the complete, unchanged durable ADD intent value."""

        if not isinstance(expected, AddIntent):
            raise SyncIndexError("expected pending ADD intent is invalid")
        desired = self._validated_add_intent(
            SyncResult(
                candidate_id=expected.candidate_id,
                review_id=expected.review_id,
                review_version=expected.review_version,
                action=expected.action,
                batch_id=expected.batch_id,
                relative_path=expected.actual_relative_path,
                sha256=expected.sha256,
                perceptual_hash=expected.perceptual_hash,
            ),
            expected.target_relative_path,
            prior_relative_path=expected.prior_relative_path,
            prior_sha256=expected.prior_sha256,
            backup_relative_path=expected.backup_relative_path,
        )
        if desired != expected:
            raise SyncIndexError("expected pending ADD intent is invalid")
        values = (
            str(desired.candidate_id),
            str(desired.review_id),
            desired.review_version,
            desired.action,
            str(desired.batch_id),
            desired.target_relative_path.as_posix(),
            desired.actual_relative_path.as_posix(),
            desired.sha256,
            desired.perceptual_hash,
            (
                desired.prior_relative_path.as_posix()
                if desired.prior_relative_path is not None
                else None
            ),
            desired.prior_sha256,
            (
                desired.backup_relative_path.as_posix()
                if desired.backup_relative_path is not None
                else None
            ),
        )
        with self.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                deleted = connection.execute(
                    "DELETE FROM pending_adds WHERE candidate_id = ? "
                    "AND review_id = ? AND review_version = ? AND action = ? "
                    "AND batch_id = ? AND target_relative_path = ? "
                    "AND actual_relative_path = ? AND sha256 = ? "
                    "AND perceptual_hash = ? AND prior_relative_path IS ? "
                    "AND prior_sha256 IS ? AND backup_relative_path IS ?",
                    values,
                ).rowcount
                connection.commit()
                return deleted == 1
            except Exception:
                connection.rollback()
                raise

    def record_success(self, result: SyncResult) -> SyncRecord:
        desired = self._validated_result(result)
        key = self._key(desired)
        with self.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM synced_items "
                    "WHERE candidate_id = ? AND review_id = ? "
                    "AND review_version = ? AND action = ?",
                    key,
                ).fetchone()
                if row is not None:
                    existing = self._from_row(row)
                    if not self._same_result(existing, desired):
                        raise IndexConflict("completed operation result conflict")
                    if desired.action == "ADD":
                        connection.execute(
                            "DELETE FROM pending_adds WHERE candidate_id = ? "
                            "AND review_id = ? AND review_version = ? AND action = ?",
                            key,
                        )
                    connection.commit()
                    return existing
                newest = connection.execute(
                    "SELECT review_id, review_version, action FROM synced_items "
                    "WHERE candidate_id = ? "
                    "ORDER BY review_version DESC, rowid DESC LIMIT 1",
                    (str(desired.candidate_id),),
                ).fetchone()
                if newest is not None and (
                    desired.review_version < int(newest["review_version"])
                    or (
                        desired.review_version == int(newest["review_version"])
                        and (
                            str(desired.review_id) != newest["review_id"]
                            or desired.action != newest["action"]
                        )
                    )
                ):
                    raise IndexConflict("candidate generation is stale or conflicting")
                connection.execute(
                    "INSERT INTO synced_items ("
                    "candidate_id, review_id, review_version, action, batch_id, "
                    "relative_path, sha256, perceptual_hash, completed_at, receipt_submitted_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                    (
                        *key,
                        str(desired.batch_id),
                        desired.relative_path.as_posix(),
                        desired.sha256,
                        desired.perceptual_hash,
                        _serialize_timestamp(desired.completed_at),
                    ),
                )
                if desired.action == "ADD":
                    connection.execute(
                        "DELETE FROM pending_adds WHERE candidate_id = ? "
                        "AND review_id = ? AND review_version = ? AND action = ?",
                        key,
                    )
                connection.commit()
                return desired
            except Exception:
                connection.rollback()
                raise

    def latest_for_candidate(
        self, candidate_id: UUID | str
    ) -> SyncRecord | None:
        candidate = str(_uuid(candidate_id, "candidate_id"))
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM synced_items WHERE candidate_id = ? "
                "ORDER BY review_version DESC, completed_at DESC, rowid DESC LIMIT 1",
                (candidate,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def max_generation(self, candidate_id: UUID | str) -> int | None:
        """Return the highest durable server generation for one candidate."""

        candidate = str(_uuid(candidate_id, "candidate_id"))
        with self.connect() as connection:
            value = connection.execute(
                "SELECT MAX(review_version) FROM synced_items WHERE candidate_id = ?",
                (candidate,),
            ).fetchone()[0]
        return int(value) if value is not None else None

    def find_present_by_sha256(self, sha256: str) -> SyncRecord | None:
        digest = _hash(sha256, "sha256", _HEX_64)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT current.* FROM synced_items AS current "
                "WHERE current.sha256 = ? "
                "AND current.action IN ('ADD', 'MOVE') "
                "AND current.rowid = ("
                "  SELECT newest.rowid FROM synced_items AS newest "
                "  WHERE newest.candidate_id = current.candidate_id "
                "  ORDER BY newest.review_version DESC, newest.completed_at DESC, "
                "newest.rowid DESC LIMIT 1"
                ") ORDER BY current.review_version DESC, current.completed_at DESC, "
                "current.rowid DESC LIMIT 1",
                (digest,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def mark_receipt_submitted(
        self,
        candidate_id: UUID | str,
        review_id: UUID | str,
        review_version: int,
        action: str,
        *,
        submitted_at: datetime | None = None,
    ) -> SyncRecord:
        key = (
            str(_uuid(candidate_id, "candidate_id")),
            str(_uuid(review_id, "review_id")),
            _version(review_version),
            _action(action),
        )
        submitted = _timestamp(submitted_at, "submitted_at")
        with self.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM synced_items "
                    "WHERE candidate_id = ? AND review_id = ? "
                    "AND review_version = ? AND action = ?",
                    key,
                ).fetchone()
                if row is None:
                    raise IndexNotFound("completed operation was not found")
                existing = self._from_row(row)
                if existing.receipt_submitted_at is None:
                    connection.execute(
                        "UPDATE synced_items SET receipt_submitted_at = ? "
                        "WHERE candidate_id = ? AND review_id = ? "
                        "AND review_version = ? AND action = ?",
                        (_serialize_timestamp(submitted), *key),
                    )
                    row = connection.execute(
                        "SELECT * FROM synced_items "
                        "WHERE candidate_id = ? AND review_id = ? "
                        "AND review_version = ? AND action = ?",
                        key,
                    ).fetchone()
                    assert row is not None
                    existing = self._from_row(row)
                connection.commit()
                return existing
            except Exception:
                connection.rollback()
                raise
