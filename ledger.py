from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence


SCHEMA_VERSION = 1
DEFAULT_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".avif",
)
ITEM_STATES = ("pending", "running", "done")
JOB_STATES = ("running", "done", "failed", "interrupted")


class LedgerError(RuntimeError):
    pass


class NoPendingImages(LedgerError):
    pass


class UnknownJob(LedgerError):
    pass


@dataclass(frozen=True)
class FileRecord:
    source_root: str
    rel_path: str
    abs_path: str
    size_bytes: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class ReservedJob:
    token: str
    campaign: str
    sha256: str
    rel_path: str
    abs_path: str
    size_bytes: int
    mtime_ns: int
    attempts: int
    lease_until: float
    auto_queue: bool


@dataclass(frozen=True)
class SyncResult:
    scanned_paths: int
    unique_images: int
    duplicate_paths: int
    newly_hashed: int
    cached_hashes: int


def normalize_extensions(raw: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(raw, str):
        values = raw.replace(";", ",").split(",")
    else:
        values = list(raw)
    normalized: list[str] = []
    for value in values:
        ext = str(value).strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = f".{ext}"
        if ext not in normalized:
            normalized.append(ext)
    return tuple(normalized or DEFAULT_EXTENSIONS)


def sha256_file(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _random_rank(campaign: str, shuffle_seed: int, content_hash: str) -> str:
    value = f"{campaign}\0{shuffle_seed}\0{content_hash}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _now() -> float:
    return time.time()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


class Ledger:
    """Transactional image-use ledger.

    A Ledger instance is cheap. Each operation opens its own SQLite connection so
    ComfyUI worker threads never share a connection.
    """

    def __init__(self, db_path: str | os.PathLike[str], session_id: str | None = None):
        self.db_path = Path(db_path).expanduser().resolve()
        self.session_id = session_id or uuid.uuid4().hex
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS campaigns (
                    campaign TEXT PRIMARY KEY,
                    source_root TEXT NOT NULL,
                    recursive INTEGER NOT NULL,
                    selection_mode TEXT NOT NULL,
                    shuffle_seed INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS file_cache (
                    source_root TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    last_seen_at REAL NOT NULL,
                    PRIMARY KEY (source_root, rel_path)
                );

                CREATE TABLE IF NOT EXISTS items (
                    campaign TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    abs_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'done')),
                    queue_rank TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    current_token TEXT,
                    reserved_at REAL,
                    lease_until REAL,
                    completed_at REAL,
                    output_path TEXT,
                    last_error TEXT,
                    imported INTEGER NOT NULL DEFAULT 0,
                    available INTEGER NOT NULL DEFAULT 1,
                    first_seen_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (campaign, sha256),
                    FOREIGN KEY (campaign) REFERENCES campaigns(campaign)
                        ON DELETE CASCADE
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_items_current_token
                    ON items(current_token)
                    WHERE current_token IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_items_pick
                    ON items(campaign, state, available, queue_rank);

                CREATE TABLE IF NOT EXISTS aliases (
                    campaign TEXT NOT NULL,
                    source_root TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    abs_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    available INTEGER NOT NULL DEFAULT 1,
                    last_seen_at REAL NOT NULL,
                    PRIMARY KEY (campaign, source_root, rel_path),
                    FOREIGN KEY (campaign, sha256)
                        REFERENCES items(campaign, sha256)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_aliases_hash
                    ON aliases(campaign, sha256, available);

                CREATE TABLE IF NOT EXISTS jobs (
                    token TEXT PRIMARY KEY,
                    campaign TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    state TEXT NOT NULL
                        CHECK (state IN ('running', 'done', 'failed', 'interrupted')),
                    auto_queue INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    lease_until REAL NOT NULL,
                    completed_at REAL,
                    output_path TEXT,
                    error TEXT,
                    FOREIGN KEY (campaign, sha256)
                        REFERENCES items(campaign, sha256)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_campaign_state
                    ON jobs(campaign, state);

                CREATE TABLE IF NOT EXISTS outputs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    token TEXT,
                    output_path TEXT NOT NULL UNIQUE,
                    size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    completed_at REAL NOT NULL,
                    imported INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (campaign, sha256)
                        REFERENCES items(campaign, sha256)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS history_files (
                    path TEXT PRIMARY KEY,
                    size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    processed_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS global_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    abs_path TEXT NOT NULL,
                    output_path TEXT,
                    workflow_name TEXT,
                    moved_to TEXT,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_global_events_sha
                    ON global_events(campaign, sha256, id);
                """
            )
            connection.execute(
                """
                INSERT INTO meta(key, value) VALUES('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )
            connection.execute(
                """
                INSERT INTO meta(key, value) VALUES('revision', '0')
                ON CONFLICT(key) DO NOTHING
                """
            )

    @staticmethod
    def _begin(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _bump_revision(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            INSERT INTO meta(key, value) VALUES('revision', '1')
            ON CONFLICT(key) DO UPDATE
            SET value=CAST(CAST(meta.value AS INTEGER) + 1 AS TEXT)
            """
        )

    def revision(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key='revision'"
            ).fetchone()
            return int(row["value"]) if row else 0

    @staticmethod
    def discover_files(
        source_root: str | os.PathLike[str],
        recursive: bool,
        extensions: str | Iterable[str] = DEFAULT_EXTENSIONS,
    ) -> list[Path]:
        root = Path(source_root).expanduser().resolve()
        if not root.is_dir():
            raise LedgerError(f"Source folder does not exist: {root}")
        allowed = set(normalize_extensions(extensions))
        iterator: Iterator[Path]
        iterator = root.rglob("*") if recursive else root.iterdir()
        skip_dirs = {"_used", "_trash", "_thumbs", "clipspace", "3d", ".git"}
        files = []
        for path in iterator:
            if not path.is_file() or path.suffix.lower() not in allowed:
                continue
            try:
                rel_parts = path.relative_to(root).parts
            except ValueError:
                rel_parts = path.parts
            if any(part in skip_dirs or part.startswith(".") for part in rel_parts[:-1]):
                continue
            files.append(path)
        files.sort(key=lambda path: path.relative_to(root).as_posix().casefold())
        return files

    def _build_file_records(
        self,
        source_root: Path,
        files: Sequence[Path],
    ) -> tuple[list[FileRecord], int, int]:
        root_text = str(source_root)
        with self._connection() as connection:
            cached_rows = connection.execute(
                """
                SELECT rel_path, size_bytes, mtime_ns, sha256
                FROM file_cache
                WHERE source_root=?
                """,
                (root_text,),
            ).fetchall()
        cache = {row["rel_path"]: row for row in cached_rows}
        records: list[FileRecord] = []
        newly_hashed = 0
        cached_hashes = 0
        for path in files:
            stat = path.stat()
            rel_path = path.relative_to(source_root).as_posix()
            cached = cache.get(rel_path)
            if (
                cached
                and int(cached["size_bytes"]) == stat.st_size
                and int(cached["mtime_ns"]) == stat.st_mtime_ns
            ):
                content_hash = str(cached["sha256"])
                cached_hashes += 1
            else:
                content_hash = sha256_file(path)
                newly_hashed += 1
            records.append(
                FileRecord(
                    source_root=root_text,
                    rel_path=rel_path,
                    abs_path=str(path.resolve()),
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    sha256=content_hash,
                )
            )
        return records, newly_hashed, cached_hashes

    def sync_campaign(
        self,
        campaign: str,
        source_root: str | os.PathLike[str],
        recursive: bool,
        selection_mode: str,
        shuffle_seed: int,
        extensions: str | Iterable[str] = DEFAULT_EXTENSIONS,
    ) -> SyncResult:
        campaign = campaign.strip()
        if not campaign:
            raise LedgerError("Campaign name cannot be empty.")
        if selection_mode not in {
            "random_no_repeat",
            "name_asc",
            "name_desc",
            "oldest",
            "newest",
        }:
            raise LedgerError(f"Unsupported selection mode: {selection_mode}")

        root = Path(source_root).expanduser().resolve()
        files = self.discover_files(root, recursive, extensions)
        if not files:
            raise LedgerError(f"No supported images found in: {root}")
        records, newly_hashed, cached_hashes = self._build_file_records(root, files)

        best_by_hash: dict[str, FileRecord] = {}
        for record in records:
            previous = best_by_hash.get(record.sha256)
            if previous is None or record.rel_path.casefold() < previous.rel_path.casefold():
                best_by_hash[record.sha256] = record

        timestamp = _now()
        with self._connection() as connection:
            self._begin(connection)
            connection.execute(
                """
                INSERT INTO campaigns(
                    campaign, source_root, recursive, selection_mode,
                    shuffle_seed, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(campaign) DO UPDATE SET
                    source_root=excluded.source_root,
                    recursive=excluded.recursive,
                    selection_mode=excluded.selection_mode,
                    shuffle_seed=excluded.shuffle_seed,
                    updated_at=excluded.updated_at
                """,
                (
                    campaign,
                    str(root),
                    int(recursive),
                    selection_mode,
                    int(shuffle_seed),
                    timestamp,
                    timestamp,
                ),
            )

            connection.execute(
                "UPDATE aliases SET available=0 WHERE campaign=?",
                (campaign,),
            )
            connection.execute(
                "UPDATE items SET available=0 WHERE campaign=?",
                (campaign,),
            )

            for record in records:
                connection.execute(
                    """
                    INSERT INTO file_cache(
                        source_root, rel_path, size_bytes, mtime_ns,
                        sha256, last_seen_at
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_root, rel_path) DO UPDATE SET
                        size_bytes=excluded.size_bytes,
                        mtime_ns=excluded.mtime_ns,
                        sha256=excluded.sha256,
                        last_seen_at=excluded.last_seen_at
                    """,
                    (
                        record.source_root,
                        record.rel_path,
                        record.size_bytes,
                        record.mtime_ns,
                        record.sha256,
                        timestamp,
                    ),
                )

            for content_hash, record in best_by_hash.items():
                # IMPORTANT: never rewrite queue_rank on update.
                # Preview "换一张" moves skipped images to the end via queue_rank;
                # sync runs on every pick/skip and used to reset ranks back to the
                # fixed random order, causing a 2-image loop at the front of the queue.
                connection.execute(
                    """
                    INSERT INTO items(
                        campaign, sha256, rel_path, abs_path, size_bytes,
                        mtime_ns, state, queue_rank, available,
                        first_seen_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, 'pending', ?, 1, ?, ?)
                    ON CONFLICT(campaign, sha256) DO UPDATE SET
                        rel_path=excluded.rel_path,
                        abs_path=excluded.abs_path,
                        size_bytes=excluded.size_bytes,
                        mtime_ns=excluded.mtime_ns,
                        available=1,
                        updated_at=excluded.updated_at
                    """,
                    (
                        campaign,
                        content_hash,
                        record.rel_path,
                        record.abs_path,
                        record.size_bytes,
                        record.mtime_ns,
                        _random_rank(campaign, int(shuffle_seed), content_hash),
                        timestamp,
                        timestamp,
                    ),
                )

            for record in records:
                connection.execute(
                    """
                    INSERT INTO aliases(
                        campaign, source_root, rel_path, sha256, abs_path,
                        size_bytes, mtime_ns, available, last_seen_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(campaign, source_root, rel_path) DO UPDATE SET
                        sha256=excluded.sha256,
                        abs_path=excluded.abs_path,
                        size_bytes=excluded.size_bytes,
                        mtime_ns=excluded.mtime_ns,
                        available=1,
                        last_seen_at=excluded.last_seen_at
                    """,
                    (
                        campaign,
                        record.source_root,
                        record.rel_path,
                        record.sha256,
                        record.abs_path,
                        record.size_bytes,
                        record.mtime_ns,
                        timestamp,
                    ),
                )

            self._bump_revision(connection)
            connection.commit()

        return SyncResult(
            scanned_paths=len(records),
            unique_images=len(best_by_hash),
            duplicate_paths=len(records) - len(best_by_hash),
            newly_hashed=newly_hashed,
            cached_hashes=cached_hashes,
        )

    def _recover_interrupted_locked(
        self,
        connection: sqlite3.Connection,
        campaign: str,
        timestamp: float,
    ) -> int:
        rows = connection.execute(
            """
            SELECT token, sha256, session_id, lease_until
            FROM jobs
            WHERE campaign=? AND state='running'
              AND (session_id<>? OR lease_until<=?)
            """,
            (campaign, self.session_id, timestamp),
        ).fetchall()
        for row in rows:
            reason = (
                "ComfyUI session restarted before final video commit."
                if row["session_id"] != self.session_id
                else "Reservation lease expired before final video commit."
            )
            connection.execute(
                """
                UPDATE jobs
                SET state='interrupted', completed_at=?, error=?
                WHERE token=? AND state='running'
                """,
                (timestamp, reason, row["token"]),
            )
            connection.execute(
                """
                UPDATE items
                SET state='pending', current_token=NULL, reserved_at=NULL,
                    lease_until=NULL, last_error=?, updated_at=?
                WHERE campaign=? AND sha256=? AND current_token=?
                """,
                (
                    reason,
                    timestamp,
                    campaign,
                    row["sha256"],
                    row["token"],
                ),
            )
        return len(rows)

    @staticmethod
    def _selection_order(selection_mode: str) -> str:
        return {
            "random_no_repeat": "queue_rank ASC",
            "name_asc": "rel_path COLLATE NOCASE ASC",
            "name_desc": "rel_path COLLATE NOCASE DESC",
            "oldest": "mtime_ns ASC, rel_path COLLATE NOCASE ASC",
            "newest": "mtime_ns DESC, rel_path COLLATE NOCASE ASC",
        }[selection_mode]

    def find_item_by_abs_path(
        self,
        campaign: str,
        abs_path: str | os.PathLike[str],
    ) -> sqlite3.Row | None:
        """Lookup a campaign item by absolute path (resolved or logical)."""
        candidates = {
            str(Path(abs_path)),
            str(Path(abs_path).expanduser()),
            os.path.abspath(str(abs_path)),
        }
        try:
            candidates.add(str(Path(abs_path).resolve()))
        except OSError:
            pass
        with self._connection() as connection:
            for candidate in candidates:
                row = connection.execute(
                    """
                    SELECT campaign, sha256, rel_path, abs_path, size_bytes,
                           mtime_ns, attempts, state, available, current_token
                    FROM items
                    WHERE campaign=? AND abs_path=?
                    LIMIT 1
                    """,
                    (campaign, candidate),
                ).fetchone()
                if row is not None:
                    return row
            # Fallback: match by normalized suffix (junction path variants).
            needle = os.path.normcase(os.path.abspath(str(abs_path)))
            rows = connection.execute(
                """
                SELECT campaign, sha256, rel_path, abs_path, size_bytes,
                       mtime_ns, attempts, state, available, current_token
                FROM items
                WHERE campaign=? AND available=1
                """,
                (campaign,),
            ).fetchall()
            for row in rows:
                try:
                    if os.path.normcase(os.path.abspath(str(row["abs_path"]))) == needle:
                        return row
                except OSError:
                    continue
            return None

    def reserve_existing_item(
        self,
        campaign: str,
        sha256: str,
        lease_minutes: int,
        auto_queue: bool,
        *,
        allow_from_states: Sequence[str] = ("pending",),
    ) -> ReservedJob:
        """Reserve a specific known item (used by manual path selection)."""
        timestamp = _now()
        lease_until = timestamp + max(1, int(lease_minutes)) * 60
        allowed = tuple(str(s) for s in allow_from_states) or ("pending",)
        with self._connection() as connection:
            self._begin(connection)
            self._recover_interrupted_locked(connection, campaign, timestamp)
            placeholders = ",".join("?" for _ in allowed)
            row = connection.execute(
                f"""
                SELECT campaign, sha256, rel_path, abs_path, size_bytes,
                       mtime_ns, attempts, state
                FROM items
                WHERE campaign=? AND sha256=? AND available=1
                  AND state IN ({placeholders})
                """,
                (campaign, sha256, *allowed),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise LedgerError(
                    f"Image {sha256[:12]}… is not available to reserve "
                    f"(campaign={campaign}, allowed={allowed})."
                )
            # If already running with a live job for this session, reuse it.
            if row["state"] == "running":
                job = connection.execute(
                    """
                    SELECT token, auto_queue, lease_until
                    FROM jobs
                    WHERE campaign=? AND sha256=? AND state='running'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (campaign, sha256),
                ).fetchone()
                if job is not None:
                    connection.execute(
                        "UPDATE jobs SET auto_queue=? WHERE token=?",
                        (int(bool(auto_queue)), job["token"]),
                    )
                    connection.commit()
                    return ReservedJob(
                        token=str(job["token"]),
                        campaign=campaign,
                        sha256=str(row["sha256"]),
                        rel_path=str(row["rel_path"]),
                        abs_path=str(row["abs_path"]),
                        size_bytes=int(row["size_bytes"]),
                        mtime_ns=int(row["mtime_ns"]),
                        attempts=int(row["attempts"]),
                        lease_until=float(job["lease_until"] or lease_until),
                        auto_queue=bool(auto_queue),
                    )

            token = uuid.uuid4().hex
            changed = connection.execute(
                f"""
                UPDATE items
                SET state='running', attempts=attempts+1,
                    current_token=?, reserved_at=?, lease_until=?,
                    last_error=NULL, updated_at=?
                WHERE campaign=? AND sha256=?
                  AND available=1 AND state IN ({placeholders})
                """,
                (
                    token,
                    timestamp,
                    lease_until,
                    timestamp,
                    campaign,
                    sha256,
                    *allowed,
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise LedgerError("Failed to reserve the manually selected image.")
            connection.execute(
                """
                INSERT INTO jobs(
                    token, campaign, sha256, session_id, state, auto_queue,
                    created_at, lease_until
                ) VALUES(?, ?, ?, ?, 'running', ?, ?, ?)
                """,
                (
                    token,
                    campaign,
                    sha256,
                    self.session_id,
                    int(bool(auto_queue)),
                    timestamp,
                    lease_until,
                ),
            )
            self._bump_revision(connection)
            connection.commit()
            return ReservedJob(
                token=token,
                campaign=campaign,
                sha256=str(row["sha256"]),
                rel_path=str(row["rel_path"]),
                abs_path=str(row["abs_path"]),
                size_bytes=int(row["size_bytes"]),
                mtime_ns=int(row["mtime_ns"]),
                attempts=int(row["attempts"]) + 1,
                lease_until=lease_until,
                auto_queue=bool(auto_queue),
            )

    def reserve_next(
        self,
        campaign: str,
        selection_mode: str,
        lease_minutes: int,
        auto_queue: bool,
        exclude_sha256: Sequence[str] | None = None,
    ) -> ReservedJob:
        timestamp = _now()
        lease_until = timestamp + max(1, int(lease_minutes)) * 60
        excluded = [
            str(value).strip().lower()
            for value in (exclude_sha256 or [])
            if str(value).strip()
        ]
        with self._connection() as connection:
            self._begin(connection)
            recovered = self._recover_interrupted_locked(
                connection, campaign, timestamp
            )
            # Prefer images not recently skipped in this preview session.
            # If every pending image was skipped, fall back to the full pending set.
            row = None
            order_sql = self._selection_order(selection_mode)
            if excluded:
                placeholders = ",".join("?" for _ in excluded)
                row = connection.execute(
                    f"""
                    SELECT campaign, sha256, rel_path, abs_path, size_bytes,
                           mtime_ns, attempts
                    FROM items
                    WHERE campaign=? AND state='pending' AND available=1
                      AND lower(sha256) NOT IN ({placeholders})
                    ORDER BY {order_sql}
                    LIMIT 1
                    """,
                    (campaign, *excluded),
                ).fetchone()
            if row is None:
                row = connection.execute(
                    f"""
                    SELECT campaign, sha256, rel_path, abs_path, size_bytes,
                           mtime_ns, attempts
                    FROM items
                    WHERE campaign=? AND state='pending' AND available=1
                    ORDER BY {order_sql}
                    LIMIT 1
                    """,
                    (campaign,),
                ).fetchone()
            if row is None:
                if recovered:
                    self._bump_revision(connection)
                connection.commit()
                raise NoPendingImages(
                    f"No pending images remain in campaign '{campaign}'."
                )

            token = uuid.uuid4().hex
            changed = connection.execute(
                """
                UPDATE items
                SET state='running', attempts=attempts+1,
                    current_token=?, reserved_at=?, lease_until=?,
                    last_error=NULL, updated_at=?
                WHERE campaign=? AND sha256=?
                  AND state='pending' AND available=1
                """,
                (
                    token,
                    timestamp,
                    lease_until,
                    timestamp,
                    campaign,
                    row["sha256"],
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise LedgerError("The selected image was reserved concurrently.")
            connection.execute(
                """
                INSERT INTO jobs(
                    token, campaign, sha256, session_id, state, auto_queue,
                    created_at, lease_until
                ) VALUES(?, ?, ?, ?, 'running', ?, ?, ?)
                """,
                (
                    token,
                    campaign,
                    row["sha256"],
                    self.session_id,
                    int(auto_queue),
                    timestamp,
                    lease_until,
                ),
            )
            self._bump_revision(connection)
            connection.commit()

        return ReservedJob(
            token=token,
            campaign=campaign,
            sha256=str(row["sha256"]),
            rel_path=str(row["rel_path"]),
            abs_path=str(row["abs_path"]),
            size_bytes=int(row["size_bytes"]),
            mtime_ns=int(row["mtime_ns"]),
            attempts=int(row["attempts"]) + 1,
            lease_until=lease_until,
            auto_queue=bool(auto_queue),
        )

    def get_job(self, token: str) -> ReservedJob | None:
        """Return a still-running job by token, or None if missing/not running."""
        token = str(token or "").strip()
        if len(token) != 32:
            return None
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT j.token, j.campaign, j.sha256, j.auto_queue, j.lease_until,
                       i.rel_path, i.abs_path, i.size_bytes, i.mtime_ns, i.attempts,
                       i.state AS item_state, j.state AS job_state
                FROM jobs j
                JOIN items i
                  ON i.campaign=j.campaign AND i.sha256=j.sha256
                WHERE j.token=?
                """,
                (token,),
            ).fetchone()
            if row is None:
                return None
            if row["job_state"] != "running" or row["item_state"] != "running":
                return None
            return ReservedJob(
                token=str(row["token"]),
                campaign=str(row["campaign"]),
                sha256=str(row["sha256"]),
                rel_path=str(row["rel_path"]),
                abs_path=str(row["abs_path"]),
                size_bytes=int(row["size_bytes"]),
                mtime_ns=int(row["mtime_ns"]),
                attempts=int(row["attempts"]),
                lease_until=float(row["lease_until"] or 0),
                auto_queue=bool(row["auto_queue"]),
            )

    def set_job_auto_queue(self, token: str, auto_queue: bool) -> None:
        with self._connection() as connection:
            self._begin(connection)
            connection.execute(
                "UPDATE jobs SET auto_queue=? WHERE token=? AND state='running'",
                (int(bool(auto_queue)), token),
            )
            connection.commit()

    def skip_job_to_end(self, token: str, reason: str = "Skipped during preview") -> str | None:
        """Release a running preview reservation back to pending at the end of the queue.

        Returns the skipped sha256 on success, or None if the job was not running.
        """
        timestamp = _now()
        error = str(reason)[:4000]
        with self._connection() as connection:
            self._begin(connection)
            job = connection.execute(
                """
                SELECT campaign, sha256, state
                FROM jobs WHERE token=?
                """,
                (token,),
            ).fetchone()
            if job is None or job["state"] != "running":
                connection.rollback()
                return None
            # Put behind every current pending rank. Use a high prefix so it sorts
            # after hex random ranks (0-9a-f) and normal path ranks.
            max_rank_row = connection.execute(
                """
                SELECT queue_rank FROM items
                WHERE campaign=? AND available=1
                ORDER BY queue_rank DESC
                LIMIT 1
                """,
                (job["campaign"],),
            ).fetchone()
            max_rank = str(max_rank_row["queue_rank"]) if max_rank_row else ""
            end_rank = f"~skip~{timestamp:.6f}~{token}"
            if max_rank and max_rank > end_rank:
                end_rank = f"{max_rank}~skip~{token}"
            connection.execute(
                """
                UPDATE jobs
                SET state='failed', completed_at=?, error=?
                WHERE token=? AND state='running'
                """,
                (timestamp, error, token),
            )
            changed = connection.execute(
                """
                UPDATE items
                SET state='pending', current_token=NULL, reserved_at=NULL,
                    lease_until=NULL, queue_rank=?, last_error=?, updated_at=?
                WHERE campaign=? AND sha256=?
                """,
                (
                    end_rank,
                    error,
                    timestamp,
                    job["campaign"],
                    job["sha256"],
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                return None
            self._bump_revision(connection)
            connection.commit()
            return str(job["sha256"])

    def fail_job(self, token: str, error: str, release: bool = True) -> bool:
        timestamp = _now()
        error = str(error)[:4000]
        with self._connection() as connection:
            self._begin(connection)
            job = connection.execute(
                """
                SELECT campaign, sha256, state
                FROM jobs WHERE token=?
                """,
                (token,),
            ).fetchone()
            if job is None:
                connection.rollback()
                return False
            if job["state"] == "done":
                connection.commit()
                return False
            connection.execute(
                """
                UPDATE jobs
                SET state='failed', completed_at=?, error=?
                WHERE token=?
                """,
                (timestamp, error, token),
            )
            if release:
                connection.execute(
                    """
                    UPDATE items
                    SET state='pending', current_token=NULL, reserved_at=NULL,
                        lease_until=NULL, last_error=?, updated_at=?
                    WHERE campaign=? AND sha256=? AND current_token=?
                    """,
                    (
                        error,
                        timestamp,
                        job["campaign"],
                        job["sha256"],
                        token,
                    ),
                )
            self._bump_revision(connection)
            connection.commit()
            return True

    def commit_job(
        self,
        token: str,
        output_path: str | os.PathLike[str],
    ) -> dict[str, object]:
        output = Path(output_path).expanduser().resolve()
        stat = output.stat()
        timestamp = _now()
        with self._connection() as connection:
            self._begin(connection)
            job = connection.execute(
                """
                SELECT campaign, sha256, state, auto_queue, output_path
                FROM jobs WHERE token=?
                """,
                (token,),
            ).fetchone()
            if job is None:
                connection.rollback()
                raise UnknownJob(f"Unknown image-ledger job token: {token}")
            if job["state"] == "done":
                connection.commit()
                return {
                    "committed": False,
                    "idempotent": True,
                    "campaign": job["campaign"],
                    "sha256": job["sha256"],
                    "output_path": job["output_path"] or str(output),
                    "auto_queue": bool(job["auto_queue"]),
                }
            if job["state"] != "running":
                connection.rollback()
                raise LedgerError(
                    f"Job {token} cannot be committed from state {job['state']}."
                )

            connection.execute(
                """
                UPDATE jobs
                SET state='done', completed_at=?, output_path=?, error=NULL
                WHERE token=?
                """,
                (timestamp, str(output), token),
            )
            connection.execute(
                """
                UPDATE items
                SET state='done', current_token=NULL, reserved_at=NULL,
                    lease_until=NULL, completed_at=?, output_path=?,
                    last_error=NULL, updated_at=?
                WHERE campaign=? AND sha256=?
                """,
                (
                    timestamp,
                    str(output),
                    timestamp,
                    job["campaign"],
                    job["sha256"],
                ),
            )
            connection.execute(
                """
                INSERT INTO outputs(
                    campaign, sha256, token, output_path, size_bytes,
                    mtime_ns, completed_at, imported
                ) VALUES(?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(output_path) DO NOTHING
                """,
                (
                    job["campaign"],
                    job["sha256"],
                    token,
                    str(output),
                    stat.st_size,
                    stat.st_mtime_ns,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO history_files(
                    path, size_bytes, mtime_ns, state, detail_json, processed_at
                ) VALUES(?, ?, ?, 'tracked', ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    size_bytes=excluded.size_bytes,
                    mtime_ns=excluded.mtime_ns,
                    state=excluded.state,
                    detail_json=excluded.detail_json,
                    processed_at=excluded.processed_at
                """,
                (
                    str(output),
                    stat.st_size,
                    stat.st_mtime_ns,
                    json.dumps(
                        {
                            "path": str(output),
                            "state": "tracked",
                            "sha256": str(job["sha256"]),
                            "newly_done": True,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    timestamp,
                ),
            )
            self._bump_revision(connection)
            connection.commit()
            return {
                "committed": True,
                "idempotent": False,
                "campaign": job["campaign"],
                "sha256": job["sha256"],
                "output_path": str(output),
                "auto_queue": bool(job["auto_queue"]),
            }

    def status(self, campaign: str) -> dict[str, object]:
        with self._connection() as connection:
            state_rows = connection.execute(
                """
                SELECT state, COUNT(*) AS count
                FROM items
                WHERE campaign=?
                GROUP BY state
                """,
                (campaign,),
            ).fetchall()
            states = {state: 0 for state in ITEM_STATES}
            states.update({str(row["state"]): int(row["count"]) for row in state_rows})
            available = connection.execute(
                """
                SELECT COUNT(*) AS count FROM items
                WHERE campaign=? AND available=1
                """,
                (campaign,),
            ).fetchone()["count"]
            alias_count = connection.execute(
                """
                SELECT COUNT(*) AS count FROM aliases
                WHERE campaign=? AND available=1
                """,
                (campaign,),
            ).fetchone()["count"]
            failed_jobs = connection.execute(
                """
                SELECT COUNT(*) AS count FROM jobs
                WHERE campaign=? AND state IN ('failed', 'interrupted')
                """,
                (campaign,),
            ).fetchone()["count"]
            outputs = connection.execute(
                """
                SELECT COUNT(*) AS count FROM outputs WHERE campaign=?
                """,
                (campaign,),
            ).fetchone()["count"]
            revision = connection.execute(
                "SELECT value FROM meta WHERE key='revision'"
            ).fetchone()
        return {
            "campaign": campaign,
            "pending": states["pending"],
            "running": states["running"],
            "done": states["done"],
            "total_unique": sum(states.values()),
            "available_unique": int(available),
            "missing_unique": sum(states.values()) - int(available),
            "source_paths": int(alias_count),
            "duplicate_paths": max(0, int(alias_count) - int(available)),
            "failed_or_interrupted_jobs": int(failed_jobs),
            "outputs": int(outputs),
            "revision": int(revision["value"]) if revision else 0,
        }

    def item_by_hash(self, campaign: str, content_hash: str) -> dict[str, object] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM items WHERE campaign=? AND sha256=?
                """,
                (campaign, content_hash.lower()),
            ).fetchone()
        return dict(row) if row else None

    def hash_for_source_name(
        self,
        campaign: str,
        source_name: str,
    ) -> str | None:
        normalized = source_name.replace("\\", "/").strip("/").casefold()
        basename = Path(normalized).name.casefold()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT sha256, rel_path
                FROM aliases
                WHERE campaign=? AND available=1
                """,
                (campaign,),
            ).fetchall()
        exact = {
            str(row["sha256"])
            for row in rows
            if str(row["rel_path"]).replace("\\", "/").casefold() == normalized
        }
        if len(exact) == 1:
            return next(iter(exact))
        by_name = {
            str(row["sha256"])
            for row in rows
            if Path(str(row["rel_path"])).name.casefold() == basename
        }
        return next(iter(by_name)) if len(by_name) == 1 else None

    def import_completed(
        self,
        campaign: str,
        content_hash: str,
        output_path: str | os.PathLike[str],
    ) -> dict[str, object]:
        content_hash = content_hash.lower()
        output = Path(output_path).expanduser().resolve()
        stat = output.stat()
        timestamp = _now()
        with self._connection() as connection:
            self._begin(connection)
            item = connection.execute(
                """
                SELECT state, output_path FROM items
                WHERE campaign=? AND sha256=?
                """,
                (campaign, content_hash),
            ).fetchone()
            if item is None:
                connection.rollback()
                return {"matched": False, "newly_done": False, "repeated": False}
            was_done = item["state"] == "done"
            connection.execute(
                """
                UPDATE items
                SET state='done', current_token=NULL, reserved_at=NULL,
                    lease_until=NULL, completed_at=COALESCE(completed_at, ?),
                    output_path=COALESCE(output_path, ?), imported=1,
                    last_error=NULL, updated_at=?
                WHERE campaign=? AND sha256=?
                """,
                (
                    timestamp,
                    str(output),
                    timestamp,
                    campaign,
                    content_hash,
                ),
            )
            connection.execute(
                """
                INSERT INTO outputs(
                    campaign, sha256, token, output_path, size_bytes,
                    mtime_ns, completed_at, imported
                ) VALUES(?, ?, NULL, ?, ?, ?, ?, 1)
                ON CONFLICT(output_path) DO NOTHING
                """,
                (
                    campaign,
                    content_hash,
                    str(output),
                    stat.st_size,
                    stat.st_mtime_ns,
                    timestamp,
                ),
            )
            self._bump_revision(connection)
            connection.commit()
        return {
            "matched": True,
            "newly_done": not was_done,
            "repeated": was_done,
        }

    def history_file_cache(
        self,
        path: str | os.PathLike[str],
        size_bytes: int,
        mtime_ns: int,
    ) -> dict[str, object] | None:
        normalized = str(Path(path).expanduser().resolve())
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT state, detail_json
                FROM history_files
                WHERE path=? AND size_bytes=? AND mtime_ns=?
                """,
                (normalized, int(size_bytes), int(mtime_ns)),
            ).fetchone()
        if not row:
            return None
        detail = json.loads(str(row["detail_json"]))
        detail["cache_state"] = str(row["state"])
        return detail

    def record_history_file(
        self,
        path: str | os.PathLike[str],
        size_bytes: int,
        mtime_ns: int,
        state: str,
        detail: dict[str, object],
    ) -> None:
        normalized = str(Path(path).expanduser().resolve())
        with self._connection() as connection:
            self._begin(connection)
            connection.execute(
                """
                INSERT INTO history_files(
                    path, size_bytes, mtime_ns, state, detail_json, processed_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    size_bytes=excluded.size_bytes,
                    mtime_ns=excluded.mtime_ns,
                    state=excluded.state,
                    detail_json=excluded.detail_json,
                    processed_at=excluded.processed_at
                """,
                (
                    normalized,
                    int(size_bytes),
                    int(mtime_ns),
                    state,
                    json.dumps(detail, ensure_ascii=False, sort_keys=True),
                    _now(),
                ),
            )
            self._bump_revision(connection)
            connection.commit()

    def release_running(
        self,
        campaign: str,
        include_current_session: bool = False,
    ) -> int:
        timestamp = _now()
        with self._connection() as connection:
            self._begin(connection)
            if include_current_session:
                rows = connection.execute(
                    """
                    SELECT token, sha256 FROM jobs
                    WHERE campaign=? AND state='running'
                    """,
                    (campaign,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT token, sha256 FROM jobs
                    WHERE campaign=? AND state='running' AND session_id<>?
                    """,
                    (campaign, self.session_id),
                ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE jobs SET state='interrupted', completed_at=?,
                        error='Manually released for retry.'
                    WHERE token=?
                    """,
                    (timestamp, row["token"]),
                )
                connection.execute(
                    """
                    UPDATE items
                    SET state='pending', current_token=NULL, reserved_at=NULL,
                        lease_until=NULL,
                        last_error='Manually released for retry.',
                        updated_at=?
                    WHERE campaign=? AND sha256=? AND current_token=?
                    """,
                    (
                        timestamp,
                        campaign,
                        row["sha256"],
                        row["token"],
                    ),
                )
            if rows:
                self._bump_revision(connection)
            connection.commit()
            return len(rows)

    def hash_file_cached(
        self,
        path: str | os.PathLike[str],
        source_root: str = "__abs__",
    ) -> str:
        file_path = Path(path)
        stat = file_path.stat()
        rel = os.path.abspath(str(file_path))
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT size_bytes, mtime_ns, sha256
                FROM file_cache
                WHERE source_root=? AND rel_path=?
                """,
                (source_root, rel),
            ).fetchone()
        if (
            row
            and int(row["size_bytes"]) == stat.st_size
            and int(row["mtime_ns"]) == stat.st_mtime_ns
        ):
            return str(row["sha256"])
        digest = sha256_file(file_path)
        timestamp = _now()
        with self._connection() as connection:
            self._begin(connection)
            connection.execute(
                """
                INSERT INTO file_cache(
                    source_root, rel_path, size_bytes, mtime_ns,
                    sha256, last_seen_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_root, rel_path) DO UPDATE SET
                    size_bytes=excluded.size_bytes,
                    mtime_ns=excluded.mtime_ns,
                    sha256=excluded.sha256,
                    last_seen_at=excluded.last_seen_at
                """,
                (source_root, rel, stat.st_size, stat.st_mtime_ns, digest, timestamp),
            )
            connection.commit()
        return digest

    def ensure_campaign(self, campaign: str, source_root: str = "") -> None:
        campaign = campaign.strip()
        if not campaign:
            raise LedgerError("Campaign name cannot be empty.")
        timestamp = _now()
        with self._connection() as connection:
            self._begin(connection)
            connection.execute(
                """
                INSERT INTO campaigns(
                    campaign, source_root, recursive, selection_mode,
                    shuffle_seed, created_at, updated_at
                ) VALUES(?, ?, 1, 'random_no_repeat', 0, ?, ?)
                ON CONFLICT(campaign) DO UPDATE SET
                    source_root=CASE
                        WHEN excluded.source_root <> '' THEN excluded.source_root
                        ELSE campaigns.source_root
                    END,
                    updated_at=excluded.updated_at
                """,
                (campaign, source_root or "", timestamp, timestamp),
            )
            connection.commit()

    def upsert_done_source(
        self,
        *,
        campaign: str,
        sha256: str,
        rel_path: str,
        abs_path: str,
        source_root: str,
        output_path: str | None = None,
        workflow_name: str = "",
        moved_to: str = "",
        size_bytes: int = 0,
        mtime_ns: int = 0,
    ) -> dict[str, object]:
        campaign = campaign.strip()
        sha256 = str(sha256).strip().lower()
        if not campaign or not sha256:
            raise LedgerError("Campaign and sha256 are required.")
        timestamp = _now()
        rel_path = str(rel_path or "").replace("\\", "/").strip("/")
        abs_text = str(abs_path or "")
        output_text = str(output_path) if output_path else ""
        self.ensure_campaign(campaign, source_root)
        with self._connection() as connection:
            self._begin(connection)
            item = connection.execute(
                """
                SELECT state, output_path FROM items
                WHERE campaign=? AND sha256=?
                """,
                (campaign, sha256),
            ).fetchone()
            newly_done = item is None or str(item["state"]) != "done"
            connection.execute(
                """
                INSERT INTO items(
                    campaign, sha256, rel_path, abs_path, size_bytes,
                    mtime_ns, state, queue_rank, available,
                    first_seen_at, updated_at, completed_at, output_path,
                    imported
                ) VALUES(?, ?, ?, ?, ?, ?, 'done', ?, 1, ?, ?, ?, ?, 0)
                ON CONFLICT(campaign, sha256) DO UPDATE SET
                    rel_path=excluded.rel_path,
                    abs_path=excluded.abs_path,
                    size_bytes=CASE
                        WHEN excluded.size_bytes > 0 THEN excluded.size_bytes
                        ELSE items.size_bytes
                    END,
                    mtime_ns=CASE
                        WHEN excluded.mtime_ns > 0 THEN excluded.mtime_ns
                        ELSE items.mtime_ns
                    END,
                    state='done',
                    current_token=NULL,
                    reserved_at=NULL,
                    lease_until=NULL,
                    completed_at=COALESCE(items.completed_at, excluded.completed_at),
                    output_path=COALESCE(NULLIF(excluded.output_path, ''), items.output_path),
                    last_error=NULL,
                    updated_at=excluded.updated_at
                """,
                (
                    campaign,
                    sha256,
                    rel_path,
                    abs_text,
                    int(size_bytes),
                    int(mtime_ns),
                    sha256,
                    timestamp,
                    timestamp,
                    timestamp,
                    output_text,
                ),
            )
            if rel_path:
                connection.execute(
                    """
                    INSERT INTO aliases(
                        campaign, source_root, rel_path, sha256, abs_path,
                        size_bytes, mtime_ns, available, last_seen_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(campaign, source_root, rel_path) DO UPDATE SET
                        sha256=excluded.sha256,
                        abs_path=excluded.abs_path,
                        size_bytes=excluded.size_bytes,
                        mtime_ns=excluded.mtime_ns,
                        available=1,
                        last_seen_at=excluded.last_seen_at
                    """,
                    (
                        campaign,
                        source_root,
                        rel_path,
                        sha256,
                        abs_text,
                        int(size_bytes),
                        int(mtime_ns),
                        timestamp,
                    ),
                )
            if output_text:
                try:
                    out = Path(output_text)
                    stat = out.stat() if out.is_file() else None
                except OSError:
                    stat = None
                connection.execute(
                    """
                    INSERT INTO outputs(
                        campaign, sha256, token, output_path, size_bytes,
                        mtime_ns, completed_at, imported
                    ) VALUES(?, ?, NULL, ?, ?, ?, ?, 0)
                    ON CONFLICT(output_path) DO NOTHING
                    """,
                    (
                        campaign,
                        sha256,
                        output_text,
                        int(stat.st_size) if stat else int(size_bytes),
                        int(stat.st_mtime_ns) if stat else int(mtime_ns),
                        timestamp,
                    ),
                )
            connection.execute(
                """
                INSERT INTO global_events(
                    campaign, sha256, rel_path, abs_path, output_path,
                    workflow_name, moved_to, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    campaign,
                    sha256,
                    rel_path,
                    abs_text,
                    output_text or None,
                    workflow_name or "",
                    moved_to or "",
                    timestamp,
                ),
            )
            self._bump_revision(connection)
            connection.commit()
        return {
            "newly_done": newly_done,
            "sha256": sha256,
            "rel_path": rel_path,
            "abs_path": abs_text,
            "moved_to": moved_to,
            "output_path": output_text,
            "workflow_name": workflow_name,
        }

    def done_hashes(self, campaign: str) -> set[str]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT sha256 FROM items
                WHERE campaign=? AND state='done'
                """,
                (campaign,),
            ).fetchall()
        return {str(row["sha256"]).lower() for row in rows if row["sha256"]}

    def done_rel_paths(self, campaign: str) -> set[str]:
        """Case-folded input-relative paths of completed sources."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT rel_path FROM items
                WHERE campaign=? AND state='done'
                """,
                (campaign,),
            ).fetchall()
        return {
            str(row["rel_path"]).replace("\\", "/").strip("/").casefold()
            for row in rows
            if row["rel_path"]
        }

    def list_used_rel_paths(self, campaign: str) -> list[str]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT rel_path FROM items
                WHERE campaign=? AND state='done'
                """,
                (campaign,),
            ).fetchall()
            moved = connection.execute(
                """
                SELECT rel_path, moved_to FROM global_events
                WHERE campaign=? AND IFNULL(moved_to, '') <> ''
                """,
                (campaign,),
            ).fetchall()
        paths: set[str] = set()
        for row in rows:
            rel = str(row["rel_path"] or "").replace("\\", "/").strip("/")
            if rel:
                paths.add(rel)
                paths.add(Path(rel).name)
        for row in moved:
            for key in ("rel_path", "moved_to"):
                rel = str(row[key] or "").replace("\\", "/").strip("/")
                if rel:
                    paths.add(rel)
                    paths.add(Path(rel).name)
        return sorted(paths)

    def is_used_rel(self, campaign: str, relative: str) -> bool:
        rel = str(relative or "").replace("\\", "/").strip("/")
        if not rel:
            return False
        name = Path(rel).name.casefold()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM items
                WHERE campaign=? AND state='done'
                  AND (
                    replace(rel_path, '\\', '/') = ?
                    OR lower(rel_path) LIKE ?
                  )
                LIMIT 1
                """,
                (campaign, rel, f"%/{name}" if name else rel),
            ).fetchone()
            if row:
                return True
            if name:
                row = connection.execute(
                    """
                    SELECT rel_path FROM items
                    WHERE campaign=? AND state='done'
                    """,
                    (campaign,),
                ).fetchall()
                return any(Path(str(item["rel_path"])).name.casefold() == name for item in row)
        return False

    def last_global_event(self, campaign: str) -> dict[str, object] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM global_events
                WHERE campaign=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (campaign,),
            ).fetchone()
        return dict(row) if row else None

    def undo_last_global_event(self, campaign: str) -> dict[str, object] | None:
        timestamp = _now()
        with self._connection() as connection:
            self._begin(connection)
            row = connection.execute(
                """
                SELECT * FROM global_events
                WHERE campaign=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (campaign,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            connection.execute(
                "DELETE FROM global_events WHERE id=?",
                (row["id"],),
            )
            remaining = connection.execute(
                """
                SELECT COUNT(*) AS count FROM global_events
                WHERE campaign=? AND sha256=?
                """,
                (campaign, row["sha256"]),
            ).fetchone()["count"]
            if int(remaining) == 0:
                connection.execute(
                    """
                    UPDATE items
                    SET state='pending', completed_at=NULL, output_path=NULL,
                        last_error='Unmarked by user.', updated_at=?
                    WHERE campaign=? AND sha256=?
                    """,
                    (timestamp, campaign, row["sha256"]),
                )
            self._bump_revision(connection)
            connection.commit()
        return dict(row)

    def recent_global_events(self, campaign: str, limit: int = 20) -> list[dict[str, object]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM global_events
                WHERE campaign=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (campaign, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]


def validate_output_path(
    output_path: str | os.PathLike[str],
    allowed_root: str | os.PathLike[str],
    minimum_size: int = 1,
) -> Path:
    path = Path(output_path).expanduser().resolve()
    root = Path(allowed_root).expanduser().resolve()
    if not _is_relative_to(path, root):
        raise LedgerError(f"Final output is outside the ComfyUI output folder: {path}")
    if not path.is_file():
        raise LedgerError(f"Final video file does not exist: {path}")
    if path.stat().st_size < minimum_size:
        raise LedgerError(f"Final video file is empty: {path}")
    return path
