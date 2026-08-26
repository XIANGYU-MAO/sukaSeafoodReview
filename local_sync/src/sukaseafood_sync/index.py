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


SCHEMA_VERSION = 1
INDEX_FILENAME = ".sukaseafood-sync.sqlite3"
BUSY_TIMEOUT_MS = 5_000
MAX_SAFE_INTEGER = 2**63 - 1
_HEX_64 = re.compile(r"[0-9a-fA-F]{64}\Z", re.ASCII)
_HEX_BOUNDED = re.compile(r"[0-9a-fA-F]{1,256}\Z", re.ASCII)
_ACTIONS = frozenset({"ADD", "MOVE", "REMOVE"})
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

_CREATE_SCHEMA = """
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
    _normalized_sql(_CREATE_SCHEMA).encode("utf-8")
).hexdigest()
_EXPECTED_OBJECTS = {
    ("index", "sqlite_autoindex_synced_items_1", "synced_items"),
    ("table", "synced_items", "synced_items"),
}
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
                raise SyncIndexError(
                    f"{label} path must remain inside the selected root"
                )
            if resolution_missing:
                if not sidecar:
                    raise SyncIndexError(
                        f"{label} path must remain inside the selected root"
                    )
                self._validate_sqlite_parent(candidate, label=label)
                if self._inspect_sqlite_path(candidate, label=label) is None:
                    return
            else:
                current = self._inspect_sqlite_path(candidate, label=label)
                if current is None:
                    if not sidecar:
                        raise SyncIndexError(
                            f"{label} path must remain inside the selected root"
                        )
                    self._validate_sqlite_parent(candidate, label=label)
                    if self._inspect_sqlite_path(candidate, label=label) is None:
                        return
                else:
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
                connection.execute(_CREATE_SCHEMA)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version != SCHEMA_VERSION:
                raise SyncIndexError(f"unsupported schema version {version}")
            self._verify_schema(connection)
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
    def _verify_schema(connection: sqlite3.Connection) -> None:
        objects = list(
            connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "ORDER BY type, name"
            )
        )
        object_keys = {(row[0], row[1], row[2]) for row in objects}
        table_sql = next(
            (row[3] for row in objects if row[0] == "table" and row[1] == "synced_items"),
            None,
        )
        table_fingerprint = (
            hashlib.sha256(_normalized_sql(table_sql).encode("utf-8")).hexdigest()
            if isinstance(table_sql, str)
            else None
        )
        table_info = list(connection.execute("PRAGMA table_info(synced_items)"))
        columns = tuple(row[1] for row in table_info)
        column_shape = tuple(
            (row[1], str(row[2]).upper(), row[3], row[5]) for row in table_info
        )
        primary_key = tuple(
            row[1]
            for row in sorted(
                table_info,
                key=lambda item: item[5] if item[5] else 99,
            )
            if row[5]
        )
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        if (
            object_keys != _EXPECTED_OBJECTS
            or table_fingerprint != _SCHEMA_FINGERPRINT
            or columns != _COLUMNS
            or column_shape != _COLUMN_SHAPE
            or primary_key
            != ("candidate_id", "review_id", "review_version", "action")
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
                    connection.commit()
                    return existing
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
                "ORDER BY completed_at DESC, rowid DESC LIMIT 1",
                (candidate,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

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
                "  ORDER BY newest.completed_at DESC, newest.rowid DESC LIMIT 1"
                ") ORDER BY current.completed_at DESC, current.rowid DESC LIMIT 1",
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
