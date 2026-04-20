import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any, Set
from contextlib import contextmanager
import logging
from datetime import datetime
from threading import Lock

from metascan.utils.startup_profiler import log_startup
from metascan.utils.path_utils import to_posix_path, to_native_path
from metascan.core.media import Media
from metascan.core.prompt_tokenizer import PromptTokenizer

logger = logging.getLogger(__name__)


def _idempotent_add_column(
    conn: sqlite3.Connection, table: str, column: str, ddl: str
) -> None:
    """Run ``ddl`` (an ``ALTER TABLE ... ADD COLUMN``) and swallow the
    duplicate-column error so concurrent ``DatabaseManager`` instances can
    both try the migration without one crashing the other.

    ``get_db()`` constructs a new manager per request and FastAPI resolves
    dependencies in a threadpool, so it's normal for two inits to enter
    ``_init_database`` simultaneously on a cold DB. The PRAGMA-then-ALTER
    pattern has a TOCTOU race — the loser raises
    ``sqlite3.OperationalError: duplicate column name``, which is benign."""
    try:
        conn.execute(ddl)
        logger.info("Added %s column to %s table", column, table)
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug(
                "Migration for %s.%s lost race with another init; " "already present.",
                table,
                column,
            )
            return
        raise


class DatabaseManager:
    def __init__(self, db_path: Path):
        log_startup("    DatabaseManager.__init__: Starting")
        self.db_path = db_path
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.db_file = self.db_path / "metascan.db"
        self.lock = Lock()

        # Lazy-initialize prompt tokenizer (deferred until first use)
        self._prompt_tokenizer: Optional[PromptTokenizer] = None

        log_startup("    DatabaseManager: Initializing database schema...")
        self._init_database()
        log_startup("    DatabaseManager.__init__: Complete")

    @property
    def prompt_tokenizer(self) -> PromptTokenizer:
        """Lazy-load the PromptTokenizer on first access."""
        if self._prompt_tokenizer is None:
            log_startup("    DatabaseManager: Lazy-loading PromptTokenizer...")
            self._prompt_tokenizer = PromptTokenizer()
        return self._prompt_tokenizer

    def _init_database(self) -> None:
        with self._get_connection() as conn:
            # Main media table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS media (
                    file_path TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    is_favorite INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )

            cursor = conn.execute("PRAGMA table_info(media)")
            columns = [column[1] for column in cursor.fetchall()]
            if "is_favorite" not in columns:
                _idempotent_add_column(
                    conn,
                    "media",
                    "is_favorite",
                    "ALTER TABLE media ADD COLUMN is_favorite INTEGER DEFAULT 0",
                )
            if "playback_speed" not in columns:
                _idempotent_add_column(
                    conn,
                    "media",
                    "playback_speed",
                    "ALTER TABLE media ADD COLUMN playback_speed REAL DEFAULT NULL",
                )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS indices (
                    index_type TEXT NOT NULL,
                    index_key TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    source TEXT,
                    PRIMARY KEY (index_type, index_key, file_path),
                    FOREIGN KEY (file_path) REFERENCES media(file_path) ON DELETE CASCADE
                )
            """
            )

            # Migration for existing DBs created before the `source` column
            # was added. For non-tag rows ``source`` is always NULL; for tag
            # rows it's one of 'prompt', 'clip', 'both' — see
            # `_generate_indices` and `add_tag_indices`.
            cursor = conn.execute("PRAGMA table_info(indices)")
            index_cols = [column[1] for column in cursor.fetchall()]
            if "source" not in index_cols:
                _idempotent_add_column(
                    conn,
                    "indices",
                    "source",
                    "ALTER TABLE indices ADD COLUMN source TEXT",
                )

            # Materialize summary fields that would otherwise live inside the
            # ``data`` JSON blob. Without these columns, the list endpoint
            # had to pull the entire blob off disk through ``json_extract``
            # (710 MB of overflow pages for a 12 K-row library on WSL
            # /mnt/c). Adding columns means the summary SELECT touches only
            # narrow rows — orders of magnitude faster.
            #
            # We detect the migration using ``modified_at`` as a sentinel:
            # if it's absent we assume *none* of the six are present and
            # treat them as one group so the backfill does a single pass
            # over the blob instead of six.
            if "modified_at" not in columns:
                _idempotent_add_column(
                    conn, "media", "width", "ALTER TABLE media ADD COLUMN width INTEGER"
                )
                _idempotent_add_column(
                    conn,
                    "media",
                    "height",
                    "ALTER TABLE media ADD COLUMN height INTEGER",
                )
                _idempotent_add_column(
                    conn,
                    "media",
                    "file_size",
                    "ALTER TABLE media ADD COLUMN file_size INTEGER",
                )
                _idempotent_add_column(
                    conn,
                    "media",
                    "frame_rate",
                    "ALTER TABLE media ADD COLUMN frame_rate REAL",
                )
                _idempotent_add_column(
                    conn,
                    "media",
                    "duration",
                    "ALTER TABLE media ADD COLUMN duration REAL",
                )
                _idempotent_add_column(
                    conn,
                    "media",
                    "modified_at",
                    "ALTER TABLE media ADD COLUMN modified_at TEXT",
                )
                logger.info(
                    "Backfilling materialized summary columns from Media "
                    "JSON blob (one-time, may take a minute on large "
                    "libraries)…"
                )
                conn.execute(
                    """
                    UPDATE media SET
                        width       = json_extract(data, '$.width'),
                        height      = json_extract(data, '$.height'),
                        file_size   = json_extract(data, '$.file_size'),
                        frame_rate  = json_extract(data, '$.frame_rate'),
                        duration    = json_extract(data, '$.duration'),
                        modified_at = json_extract(data, '$.modified_at')
                    """
                )
                logger.info("Summary-column backfill complete.")

            # Covering indexes for the grid list endpoint. The `media` row
            # layout is `[file_path][data][is_favorite]...[width]...`, so
            # reading any column positioned after `data` would force SQLite
            # to seek through `data`'s 700+ MB overflow-page chain. A
            # covering index holds every summary column at the leaf level,
            # letting the query planner answer list requests without
            # touching the main table — ~6 ms instead of ~25 s.
            #
            # Both indexes must include *every* column the summary SELECT
            # projects. When a new column (e.g. ``modified_at`` /
            # ``created_at``) is added to the summary response, the old
            # covering index silently stops covering and each list request
            # falls back to the main table — the 20-second regression we're
            # guarding against. Drop any pre-existing index whose DDL is
            # missing the current column set so the CREATE below rebuilds.
            required_cols = ("modified_at", "created_at")
            for idx_name in ("idx_media_summary_added", "idx_media_summary_modified"):
                ddl_row = conn.execute(
                    "SELECT sql FROM sqlite_master " "WHERE type='index' AND name=?",
                    (idx_name,),
                ).fetchone()
                ddl_sql = (ddl_row["sql"] or "") if ddl_row else ""
                if ddl_row and any(col not in ddl_sql for col in required_cols):
                    conn.execute(f"DROP INDEX IF EXISTS {idx_name}")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_media_summary_added
                ON media(
                    created_at, file_path, is_favorite, playback_speed,
                    width, height, file_size, frame_rate, duration,
                    modified_at
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_media_summary_modified
                ON media(
                    modified_at, file_path, is_favorite, playback_speed,
                    width, height, file_size, frame_rate, duration,
                    created_at
                )
                """
            )

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_indices_lookup ON indices(index_type, index_key)"
            )
            # Composite index serving the two per-file workloads that were
            # otherwise SQLite's worst cases here:
            #
            # (1) `DELETE/UPDATE ... WHERE file_path = ?` inside
            #     `_update_indices` — the compound PK starts with
            #     index_type, so it can't serve a file_path lookup and
            #     writes degrade to O(total rows) per file, making a full
            #     scan O(N²).
            # (2) `get_tags_for_file` (`SELECT index_key ... WHERE
            #     file_path=? AND index_type='tag' ORDER BY index_key`) —
            #     the SQLite planner prefers the autoindex on
            #     (index_type, index_key, file_path) as a covering index
            #     for `index_type='tag'` alone, which means scanning every
            #     `tag` row (~115 K on a 12 K-file library) to filter down
            #     to the ~30-150 rows actually matching. That took ~1.8 s
            #     per detail-panel open.
            #
            # A composite `(file_path, index_type, index_key)` covers both:
            # DELETE seeks on the file_path prefix, and the tag SELECT is
            # answered entirely from the index (prefix match + covered
            # projection, and `index_key` is already sorted so no explicit
            # sort step). Result: DELETE ~ms instead of O(N), tag lookup
            # ~0.3 ms instead of 1.8 s.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_indices_by_file_type "
                "ON indices(file_path, index_type, index_key)"
            )
            # Supersedes the earlier narrower file_path-only index — the
            # composite above handles the same queries plus more. Drop
            # idempotently so existing DBs reclaim the space on next boot.
            conn.execute("DROP INDEX IF EXISTS idx_indices_file_path")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_created ON media(created_at)"
            )

            # Similarity / embedding tracking table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS media_hashes (
                    file_path TEXT PRIMARY KEY,
                    phash TEXT,
                    clip_model TEXT,
                    has_embedding INTEGER DEFAULT 0,
                    embedding_updated_at TIMESTAMP,
                    FOREIGN KEY (file_path) REFERENCES media(file_path) ON DELETE CASCADE
                )
            """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_hashes_phash ON media_hashes(phash)"
            )

            # One-shot backfill: ``created_at`` previously tracked the last
            # rescan (INSERT OR REPLACE was DELETE+INSERT, firing the
            # ``DEFAULT CURRENT_TIMESTAMP`` every time). Smart-folder "Added"
            # rules now need first-ingest semantics; the upsert was fixed to
            # preserve ``created_at`` on update, but existing rows already
            # carry the collapsed rescan timestamp. Copy ``modified_at``
            # (file mtime on disk) over as a reasonable proxy — it's the
            # closest signal we have to when the row really became part of
            # the library. Gated on ``PRAGMA user_version`` so it runs once.
            version_row = conn.execute("PRAGMA user_version").fetchone()
            user_version = int(version_row[0]) if version_row else 0
            if user_version < 1:
                logger.info(
                    "Backfilling created_at from modified_at (one-time; "
                    "fixes 'Added' smart-folder rule collapsing onto the "
                    "last rescan date)…"
                )
                cur = conn.execute(
                    "UPDATE media SET created_at = modified_at "
                    "WHERE modified_at IS NOT NULL"
                )
                logger.info(f"created_at backfill updated {cur.rowcount} row(s).")
                conn.execute("PRAGMA user_version = 1")

            conn.commit()

    @contextmanager
    def _get_connection(self):  # type: ignore[no-untyped-def]
        conn = sqlite3.connect(str(self.db_file))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")  # Better concurrency
        try:
            yield conn
        finally:
            conn.close()

    def close(self) -> None:
        pass

    @contextmanager
    def batch_writer(self):  # type: ignore[no-untyped-def]
        with self.lock:
            with self._get_connection() as conn:
                try:
                    yield conn
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    logger.error(f"Batch write failed: {e}")
                    raise

    # True upsert — preserves the row's original ``created_at`` on rescan.
    # ``INSERT OR REPLACE`` is DELETE+INSERT under the hood, which fires the
    # ``DEFAULT CURRENT_TIMESTAMP`` every time, making ``created_at`` track
    # the most recent rescan rather than the first ingest. Smart folders
    # keying off "Added" need that first-ingest semantic.
    _MEDIA_UPSERT_SQL = """
        INSERT INTO media (
            file_path, data, is_favorite, playback_speed,
            width, height, file_size, frame_rate, duration, modified_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(file_path) DO UPDATE SET
            data = excluded.data,
            is_favorite = excluded.is_favorite,
            playback_speed = excluded.playback_speed,
            width = excluded.width,
            height = excluded.height,
            file_size = excluded.file_size,
            frame_rate = excluded.frame_rate,
            duration = excluded.duration,
            modified_at = excluded.modified_at,
            updated_at = CURRENT_TIMESTAMP
    """

    @staticmethod
    def _media_upsert_params(media: Media, posix_path: str) -> tuple:
        return (
            posix_path,
            media.to_json(),  # type: ignore[attr-defined]
            1 if media.is_favorite else 0,
            media.playback_speed,
            media.width,
            media.height,
            media.file_size,
            media.frame_rate,
            media.duration,
            media.modified_at.isoformat() if media.modified_at else None,
        )

    def save_media(self, media: Media) -> bool:
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Convert file_path to POSIX format for storage
                    posix_path = to_posix_path(media.file_path)
                    conn.execute(
                        self._MEDIA_UPSERT_SQL,
                        self._media_upsert_params(media, posix_path),
                    )

                    self._update_indices(conn, media)
                    conn.commit()
                    return True
        except Exception as e:
            logger.error(f"Failed to save media {media.file_path}: {e}")
            return False

    def save_media_batch(self, media_list: List[Media]) -> int:
        saved_count = 0
        with self.batch_writer() as conn:
            for media in media_list:
                try:
                    # Convert file_path to POSIX format for storage
                    posix_path = to_posix_path(media.file_path)
                    conn.execute(
                        self._MEDIA_UPSERT_SQL,
                        self._media_upsert_params(media, posix_path),
                    )

                    self._update_indices(conn, media)
                    saved_count += 1
                except Exception as e:
                    logger.error(
                        f"Failed to save media in batch {media.file_path}: {e}"
                    )

        return saved_count

    def get_media(self, file_path: Path) -> Optional[Media]:
        try:
            with self._get_connection() as conn:
                # Convert to POSIX format for database lookup
                posix_path = to_posix_path(file_path)
                row = conn.execute(
                    "SELECT data FROM media WHERE file_path = ?", (posix_path,)
                ).fetchone()

                if row:
                    return Media.from_json(row["data"])  # type: ignore[attr-defined,no-any-return]
                return None
        except Exception as e:
            logger.error(f"Failed to get media {file_path}: {e}")
            return None

    def get_all_media(self) -> List[Media]:
        media_list = []
        try:
            with self._get_connection() as conn:
                rows = conn.execute("SELECT data FROM media ORDER BY created_at DESC")
                for row in rows:
                    try:
                        media = Media.from_json(row["data"])  # type: ignore[attr-defined]
                        media_list.append(media)
                    except Exception as e:
                        logger.error(f"Failed to decode media: {e}")
        except Exception as e:
            logger.error(f"Failed to get all media: {e}")

        return media_list

    def get_all_media_with_details(self) -> List[Media]:
        """Load all media with favorite status and playback speed in a single query.

        This eliminates N+1 query problems by fetching data, is_favorite, and
        playback_speed together instead of making separate queries per item.
        """
        media_list = []
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT data, is_favorite, playback_speed FROM media ORDER BY created_at DESC"
                )
                for row in rows:
                    try:
                        media = Media.from_json_fast(row["data"])
                        media.is_favorite = bool(row["is_favorite"])
                        if row["playback_speed"] is not None:
                            media.playback_speed = float(row["playback_speed"])
                        media_list.append(media)
                    except Exception as e:
                        logger.error(f"Failed to decode media: {e}")
        except Exception as e:
            logger.error(f"Failed to get all media with details: {e}")

        return media_list

    def get_all_media_summaries(
        self,
        favorites_only: bool = False,
        sort: str = "date_added",
    ) -> List[Dict[str, Any]]:
        """Return a per-file summary tailored for the thumbnail grid.

        All fields live on columns (see the ``_init_database`` migration
        that materializes ``width``, ``height``, ``file_size``,
        ``frame_rate``, ``duration`` and ``modified_at`` off the JSON
        blob). A projection across these narrow columns skips the 700+ MB
        of overflow pages a ``json_extract`` query would have to page in.
        """
        order_clause = {
            "date_modified": "modified_at DESC",
            # file_name sort happens in the service layer (Python basename
            # extraction) — SQLite has no cheap basename function.
        }.get(sort, "created_at DESC")
        where = "WHERE is_favorite = 1" if favorites_only else ""
        sql = (
            "SELECT file_path, is_favorite, playback_speed, "
            "width, height, file_size, frame_rate, duration, "
            "modified_at, created_at "
            f"FROM media {where} ORDER BY {order_clause}"
        )
        out: List[Dict[str, Any]] = []
        video_exts = {".mp4", ".webm"}
        try:
            with self._get_connection() as conn:
                rows = conn.execute(sql).fetchall()
                for row in rows:
                    file_path = to_native_path(row["file_path"])
                    ext = Path(file_path).suffix.lower()
                    playback = row["playback_speed"]
                    out.append(
                        {
                            "file_path": file_path,
                            "is_favorite": bool(row["is_favorite"]),
                            "is_video": ext in video_exts,
                            "playback_speed": (
                                float(playback) if playback is not None else None
                            ),
                            "width": row["width"],
                            "height": row["height"],
                            "file_size": row["file_size"],
                            "frame_rate": row["frame_rate"],
                            "duration": row["duration"],
                            "modified_at": row["modified_at"],
                            "created_at": row["created_at"],
                        }
                    )
        except Exception as e:
            logger.error(f"Failed to get media summaries: {e}")
        return out

    def get_media_with_details(self, file_path: Path) -> Optional[Media]:
        """Load a single media item with favorite status and playback speed.

        Args:
            file_path: Path to the media file

        Returns:
            Media object with is_favorite and playback_speed set, or None if not found
        """
        try:
            with self._get_connection() as conn:
                # Convert to POSIX format for database lookup
                posix_path = to_posix_path(file_path)
                row = conn.execute(
                    "SELECT data, is_favorite, playback_speed FROM media WHERE file_path = ?",
                    (posix_path,),
                ).fetchone()

                if row:
                    media = Media.from_json_fast(row["data"])
                    media.is_favorite = bool(row["is_favorite"])
                    if row["playback_speed"] is not None:
                        media.playback_speed = float(row["playback_speed"])
                    return media
                return None
        except Exception as e:
            logger.error(f"Failed to get media with details {file_path}: {e}")
            return None

    def delete_media(self, file_path: Path) -> bool:
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Convert to POSIX format for database lookup
                    posix_path = to_posix_path(file_path)
                    # Delete from media table (indices will cascade delete)
                    cursor = conn.execute(
                        "DELETE FROM media WHERE file_path = ?", (posix_path,)
                    )
                    conn.commit()
                    return cursor.rowcount > 0  # type: ignore[no-any-return]
        except Exception as e:
            logger.error(f"Failed to delete media {file_path}: {e}")
            return False

    def delete_media_batch(self, file_paths: List[Path]) -> int:
        """Delete multiple media items in a single transaction.

        This is much faster than calling delete_media() repeatedly because
        it uses a single commit for all deletes instead of one per item.

        Returns the number of items successfully deleted.
        """
        if not file_paths:
            return 0

        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Convert paths to POSIX format for database lookup
                    path_tuples = [(to_posix_path(fp),) for fp in file_paths]
                    conn.executemany(
                        "DELETE FROM media WHERE file_path = ?", path_tuples
                    )
                    conn.commit()
                    return len(file_paths)
        except Exception as e:
            logger.error(f"Failed to batch delete media: {e}")
            return 0

    def get_existing_file_paths(self) -> Set[str]:
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT file_path FROM media")
                # Convert paths from POSIX storage format to native format
                return {to_native_path(row["file_path"]) for row in cursor}
        except Exception as e:
            logger.error(f"Failed to get existing file paths: {e}")
            return set()

    def get_favorite_file_paths(self) -> List[str]:
        """Return all file paths flagged as favorite."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT file_path FROM media WHERE is_favorite = 1"
                )
                return [to_native_path(row["file_path"]) for row in cursor]
        except Exception as e:
            logger.error(f"Failed to get favorite file paths: {e}")
            return []

    def search_by_index(self, index_type: str, term: str) -> Set[str]:
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT DISTINCT file_path
                    FROM indices
                    WHERE index_type = ? AND index_key = ?
                """,
                    (index_type, term.lower()),
                )

                # Convert paths from POSIX storage format to native format
                return {to_native_path(row["file_path"]) for row in rows}
        except Exception as e:
            logger.error(f"Index search failed for {index_type}:{term}: {e}")
            return set()

    def get_tag_path_index(self, keys: List[str]) -> Dict[str, List[str]]:
        """Return ``{tag_key: [file_path, ...]}`` for the given tag keys only.

        Smart folders usually reference a handful of tags, not the whole
        universe. Fetching every tag row (``~100k+`` on real libraries) just
        to populate a client-side cache put a multi-megabyte JSON blob
        through the DB lock on every app refresh, blocking the media list
        query behind it. The caller hands us the exact keys it needs.
        """
        if not keys:
            return {}
        out: Dict[str, List[str]] = {k: [] for k in keys}
        try:
            with self._get_connection() as conn:
                placeholders = ",".join("?" for _ in keys)
                rows = conn.execute(
                    f"SELECT index_key, file_path FROM indices "
                    f"WHERE index_type = 'tag' AND index_key IN ({placeholders})",
                    list(keys),
                ).fetchall()
                for row in rows:
                    out[row["index_key"]].append(to_native_path(row["file_path"]))
        except Exception as e:
            logger.error(f"Failed to fetch tag path index: {e}")
        return out

    def get_tags_for_file(self, file_path: Path) -> List[str]:
        """Return every tag attached to ``file_path``, regardless of source.

        Tags are stored in ``indices`` with ``index_type='tag'`` and a
        ``source`` of ``'prompt'``, ``'clip'``, or ``'both'``. The UI wants
        the union, so callers merge by querying this single view instead of
        relying on the prompt-only ``media.tags`` field that lives inside the
        serialized ``media.data`` blob.
        """
        try:
            with self._get_connection() as conn:
                posix_path = to_posix_path(file_path)
                rows = conn.execute(
                    "SELECT index_key FROM indices "
                    "WHERE file_path = ? AND index_type = 'tag' "
                    "ORDER BY index_key",
                    (posix_path,),
                )
                return [row["index_key"] for row in rows]
        except Exception as e:
            logger.error(f"Failed to get tags for {file_path}: {e}")
            return []

    def _update_indices(self, conn: sqlite3.Connection, media: Media) -> None:
        # Use POSIX path for consistency with storage format
        posix_path = to_posix_path(media.file_path)
        # Preserve tag rows contributed by CLIP so a rescan / metadata update
        # doesn't blow away the embedding-derived tags. A `both` row had
        # contributions from prompt AND clip; we're about to rewrite the
        # prompt half, so temporarily demote those to `clip`-only — the
        # UPSERT below will upgrade back to `both` for tags the new prompt
        # still contains.
        conn.execute(
            "DELETE FROM indices WHERE file_path = ? AND "
            "(index_type != 'tag' OR source IS NULL OR source = 'prompt')",
            (posix_path,),
        )
        conn.execute(
            "UPDATE indices SET source = 'clip' "
            "WHERE file_path = ? AND index_type = 'tag' AND source = 'both'",
            (posix_path,),
        )

        non_tag_rows: List[tuple] = []
        prompt_tag_keys: List[str] = []
        for index_type, index_key, source in self._generate_indices(media):
            if index_type == "tag" and source == "prompt":
                prompt_tag_keys.append(index_key)
            else:
                non_tag_rows.append((index_type, index_key, posix_path, source))

        if non_tag_rows:
            conn.executemany(
                "INSERT INTO indices (index_type, index_key, file_path, source) "
                "VALUES (?, ?, ?, ?)",
                non_tag_rows,
            )
        # Prompt-sourced tags may collide with pre-existing clip rows — merge
        # via ON CONFLICT so the resulting row records both sources.
        for key in prompt_tag_keys:
            conn.execute(
                "INSERT INTO indices (index_type, index_key, file_path, source) "
                "VALUES ('tag', ?, ?, 'prompt') "
                "ON CONFLICT(index_type, index_key, file_path) DO UPDATE SET "
                "source = CASE "
                "  WHEN indices.source = 'clip' THEN 'both' "
                "  WHEN indices.source = 'both' THEN 'both' "
                "  ELSE 'prompt' END",
                (key, posix_path),
            )

    def add_tag_indices(
        self, file_path: Path, tags: List[str], source: str = "clip"
    ) -> None:
        """Add or merge tag rows sourced from ``source`` for ``file_path``.

        Called by the embedding worker after CLIP-based tag generation. If a
        tag already exists for the file from the *other* source (typically
        ``prompt``), the row is upgraded to ``both`` rather than duplicated.
        """
        if not tags:
            return
        if source not in ("prompt", "clip"):
            raise ValueError(f"tag source must be 'prompt' or 'clip', got {source!r}")
        posix_path = to_posix_path(file_path)
        other = "prompt" if source == "clip" else "clip"
        sql = (
            "INSERT INTO indices (index_type, index_key, file_path, source) "
            "VALUES ('tag', ?, ?, ?) "
            "ON CONFLICT(index_type, index_key, file_path) DO UPDATE SET "
            "source = CASE "
            "  WHEN indices.source = ? THEN 'both' "
            "  WHEN indices.source = 'both' THEN 'both' "
            "  ELSE excluded.source END"
        )
        rows = [(t.lower(), posix_path, source, other) for t in tags if t]
        try:
            with self.lock:
                with self._get_connection() as conn:
                    conn.executemany(sql, rows)
                    conn.commit()
        except Exception as e:
            logger.error(f"Failed to add {source} tag indices for {file_path}: {e}")

    def _generate_indices(self, media: Media) -> List[tuple]:
        """Returns ``(index_type, index_key, source)`` triples. ``source`` is
        non-NULL only for tag rows — see the ``indices.source`` column.
        Callers writing non-tag rows should pass ``source=None``."""
        indices: List[tuple] = []

        if media.metadata_source:
            indices.append(("source", media.metadata_source.lower(), None))

        # Add index for each model in the list
        if media.model:
            for model_name in media.model:
                if model_name:  # Skip empty strings
                    indices.append(("model", model_name.lower(), None))

        indices.append(("ext", media.file_extension, None))

        # Add reverse index for the fully qualified file path (in POSIX format)
        path_str = to_posix_path(media.file_path).lower()
        indices.append(("path", path_str, None))

        # Tag rows coming from media.tags are sourced from the prompt
        # tokenizer (see scanner.py). CLIP-sourced tags are written later
        # by the embedding worker via add_tag_indices().
        for tag in media.tags:
            indices.append(("tag", tag.lower(), "prompt"))

        if media.prompt:
            filtered_words = self.prompt_tokenizer.tokenize(media.prompt)
            for word in filtered_words:
                indices.append(("prompt", word, None))

        for lora in media.loras:
            indices.append(("lora", lora.lora_name.lower(), None))

        return indices

    def get_filter_data(
        self, sort_order: str = "count"
    ) -> Dict[str, List[Dict[str, Any]]]:
        filter_data: Dict[str, List[Dict[str, Any]]] = {}
        try:
            with self._get_connection() as conn:
                # Choose sort order
                if sort_order == "alphabetical":
                    order_clause = "ORDER BY index_type, index_key ASC"
                else:  # default to count
                    order_clause = "ORDER BY index_type, count DESC, index_key"

                query = f"""
                    SELECT index_type, index_key, COUNT(*) as count
                    FROM indices
                    GROUP BY index_type, index_key
                    {order_clause}
                """

                rows = conn.execute(query).fetchall()

                for row in rows:
                    index_type = row["index_type"]
                    if index_type not in filter_data:
                        filter_data[index_type] = []

                    filter_data[index_type].append(
                        {"key": row["index_key"], "count": row["count"]}
                    )
        except Exception as e:
            logger.error(f"Failed to get filter data: {e}")
        return filter_data

    def get_filtered_media_paths(self, filters: Dict[str, List[str]]) -> Set[str]:
        if not filters:
            return set()

        try:
            with self._get_connection() as conn:
                # Start with all media paths
                result_set = None

                for index_type, index_keys in filters.items():
                    if index_keys:  # Skip empty lists
                        # Get paths matching this filter type
                        if index_type == "path":
                            # Special handling for path filters - use LIKE for prefix matching
                            # Convert path to POSIX format for database query
                            path_prefix = to_posix_path(index_keys[0]).lower()
                            query = """
                                SELECT DISTINCT file_path
                                FROM indices
                                WHERE index_type = 'path' AND index_key LIKE ?
                            """
                            rows = conn.execute(query, [f"{path_prefix}%"])
                        else:
                            # Regular filter handling for other types
                            # Multiple values within same type use OR logic
                            placeholders = ",".join(["?" for _ in index_keys])
                            query = f"""
                                SELECT DISTINCT file_path
                                FROM indices
                                WHERE index_type = ? AND index_key IN ({placeholders})
                            """
                            params = [index_type] + list(index_keys)
                            rows = conn.execute(query, params)

                        # Get paths for this filter (convert from POSIX to native)
                        current_paths = {
                            to_native_path(row["file_path"]) for row in rows
                        }

                        # Apply AND logic between different filter types
                        if result_set is None:
                            result_set = current_paths
                        else:
                            result_set = result_set.intersection(current_paths)

                        # Early exit if no matches
                        if not result_set:
                            return set()

                return result_set if result_set is not None else set()
        except Exception as e:
            logger.error(f"Failed to get filtered media paths: {e}")
            return set()

    def toggle_favorite(self, file_path: Path) -> bool:
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Convert to POSIX format for database lookup
                    posix_path = to_posix_path(file_path)
                    # Get current favorite status
                    row = conn.execute(
                        "SELECT is_favorite FROM media WHERE file_path = ?",
                        (posix_path,),
                    ).fetchone()

                    if row is not None:
                        new_status = 0 if row["is_favorite"] else 1
                        conn.execute(
                            """
                            UPDATE media
                            SET is_favorite = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE file_path = ?
                        """,
                            (new_status, posix_path),
                        )
                        conn.commit()
                        return bool(new_status)
                    return False
        except Exception as e:
            logger.error(f"Failed to toggle favorite for {file_path}: {e}")
            return False

    def set_favorite(self, file_path: Path, is_favorite: bool) -> bool:
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Convert to POSIX format for database lookup
                    posix_path = to_posix_path(file_path)
                    cursor = conn.execute(
                        """
                        UPDATE media
                        SET is_favorite = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE file_path = ?
                    """,
                        (1 if is_favorite else 0, posix_path),
                    )
                    conn.commit()
                    return cursor.rowcount > 0  # type: ignore[no-any-return]
        except Exception as e:
            logger.error(f"Failed to set favorite for {file_path}: {e}")
            return False

    def get_favorite_media_paths(self) -> Set[str]:
        try:
            with self._get_connection() as conn:
                rows = conn.execute("SELECT file_path FROM media WHERE is_favorite = 1")
                # Convert paths from POSIX storage format to native format
                return {to_native_path(row["file_path"]) for row in rows}
        except Exception as e:
            logger.error(f"Failed to get favorite media paths: {e}")
            return set()

    def load_favorite_status(self, media_list: List[Media]) -> None:
        try:
            with self._get_connection() as conn:
                for media in media_list:
                    # Convert to POSIX format for database lookup
                    posix_path = to_posix_path(media.file_path)
                    row = conn.execute(
                        "SELECT is_favorite FROM media WHERE file_path = ?",
                        (posix_path,),
                    ).fetchone()
                    if row:
                        media.is_favorite = bool(row["is_favorite"])
        except Exception as e:
            logger.error(f"Failed to load favorite status: {e}")

    def load_playback_speed(self, media_list: List[Media]) -> None:
        """Load playback_speed for a list of media objects from the database."""
        try:
            with self._get_connection() as conn:
                for media in media_list:
                    # Convert to POSIX format for database lookup
                    posix_path = to_posix_path(media.file_path)
                    row = conn.execute(
                        "SELECT playback_speed FROM media WHERE file_path = ?",
                        (posix_path,),
                    ).fetchone()
                    if row and row["playback_speed"] is not None:
                        media.playback_speed = float(row["playback_speed"])
        except Exception as e:
            logger.error(f"Failed to load playback speed: {e}")

    def update_playback_speed(self, file_path: Path, speed: float) -> bool:
        """Update the playback speed for a specific media file."""
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Convert to POSIX format for database lookup
                    posix_path = to_posix_path(file_path)
                    conn.execute(
                        """
                        UPDATE media
                        SET playback_speed = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE file_path = ?
                    """,
                        (speed, posix_path),
                    )
                    conn.commit()
                    return True
        except Exception as e:
            logger.error(f"Failed to update playback speed for {file_path}: {e}")
            return False

    def update_media_dimensions(self, file_path: Path, width: int, height: int) -> bool:
        """
        Update the dimensions of a media file in the database.

        Args:
            file_path: Path to the media file
            width: New width in pixels
            height: New height in pixels

        Returns:
            True if updated successfully, False otherwise
        """
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Get the current media record
                    media = self.get_media(file_path)
                    if not media:
                        return False

                    # Update the dimensions in the media object
                    media.width = width
                    media.height = height

                    # Update the database record (use POSIX format for query)
                    posix_path = to_posix_path(file_path)
                    conn.execute(
                        """UPDATE media SET data = ? WHERE file_path = ?""",
                        (media.to_json(), posix_path),  # type: ignore[attr-defined]
                    )

                    conn.commit()
                    return True

        except Exception as e:
            logger.error(f"Error updating media dimensions for {file_path}: {e}")
            return False

    def update_media_technical_metadata(
        self,
        file_path: Path,
        width: int,
        height: int,
        file_size: int,
        modified_at: datetime,
        created_at: Optional[datetime] = None,
        frame_rate: Optional[float] = None,
        duration: Optional[float] = None,
    ) -> bool:
        """
        Update only the technical metadata (dimensions, file size, timestamps, video properties)
        of a media file. Preserves all AI generation metadata (prompts, models, seeds, etc.).

        This is used after upscaling to update the file's physical properties while keeping
        the original AI generation parameters intact.

        Args:
            file_path: Path to the media file
            width: New width in pixels
            height: New height in pixels
            file_size: New file size in bytes
            modified_at: New modification timestamp
            created_at: New creation timestamp (optional, preserves original if not provided)
            frame_rate: New frame rate for videos (optional)
            duration: New duration for videos (optional)

        Returns:
            True if updated successfully, False otherwise
        """
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Get the current media record (with all AI metadata)
                    media = self.get_media(file_path)
                    if not media:
                        logger.warning(f"Media not found in database: {file_path}")
                        return False

                    # Update technical fields, preserve all AI metadata
                    media.width = width
                    media.height = height
                    media.file_size = file_size
                    media.modified_at = modified_at

                    # Update created_at only if provided
                    if created_at is not None:
                        media.created_at = created_at

                    # Update video-specific properties if provided
                    if frame_rate is not None:
                        media.frame_rate = frame_rate
                    if duration is not None:
                        media.duration = duration

                    # All AI metadata fields remain unchanged:
                    # - prompt, negative_prompt, model, sampler, scheduler
                    # - steps, cfg_scale, seed
                    # - loras, tags, generation_data
                    # - metadata_source

                    # Update the database record (use POSIX format for query)
                    posix_path = to_posix_path(file_path)
                    conn.execute(
                        """UPDATE media SET data = ?, updated_at = CURRENT_TIMESTAMP WHERE file_path = ?""",
                        (media.to_json(), posix_path),  # type: ignore[attr-defined]
                    )

                    conn.commit()

                    # Build log message
                    log_parts = [f"{width}x{height}", f"{file_size} bytes"]
                    if frame_rate is not None:
                        log_parts.append(f"{frame_rate:.2f} fps")
                    if duration is not None:
                        log_parts.append(f"{duration:.2f}s")

                    logger.info(
                        f"Updated technical metadata for {file_path.name}: "
                        f"{', '.join(log_parts)}"
                    )
                    return True

        except Exception as e:
            logger.error(f"Error updating technical metadata for {file_path}: {e}")
            return False

    # ── Similarity / embedding methods ──────────────────────────────

    def save_media_hash(
        self, file_path: Path, phash: Optional[str], clip_model: Optional[str] = None
    ) -> bool:
        """Save a perceptual hash for a media file."""
        try:
            with self.lock:
                with self._get_connection() as conn:
                    posix_path = to_posix_path(file_path)
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO media_hashes
                            (file_path, phash, clip_model, has_embedding, embedding_updated_at)
                        VALUES (?, ?, ?, 0, NULL)
                    """,
                        (posix_path, phash, clip_model),
                    )
                    conn.commit()
                    return True
        except Exception as e:
            logger.error(f"Failed to save media hash for {file_path}: {e}")
            return False

    def save_media_hash_batch(self, items: List[tuple]) -> int:
        """Save perceptual hashes in batch.

        Each item is a tuple of (file_path: Path, phash: str).
        """
        saved = 0
        try:
            with self.lock:
                with self._get_connection() as conn:
                    for file_path, phash in items:
                        try:
                            posix_path = to_posix_path(file_path)
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO media_hashes
                                    (file_path, phash, clip_model, has_embedding, embedding_updated_at)
                                VALUES (?, ?, NULL, 0, NULL)
                            """,
                                (posix_path, phash),
                            )
                            saved += 1
                        except Exception as e:
                            logger.error(f"Failed to save hash for {file_path}: {e}")
                    conn.commit()
        except Exception as e:
            logger.error(f"Failed to save media hash batch: {e}")
        return saved

    def get_all_phashes(self) -> Dict[str, str]:
        """Return dict of {file_path (native): phash_hex} for all files with a pHash."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT file_path, phash FROM media_hashes WHERE phash IS NOT NULL"
                )
                return {to_native_path(row["file_path"]): row["phash"] for row in rows}
        except Exception as e:
            logger.error(f"Failed to get all phashes: {e}")
            return {}

    def get_unembedded_file_paths(self) -> List[str]:
        """Get file paths that exist in media but don't have CLIP embeddings."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT m.file_path FROM media m
                    LEFT JOIN media_hashes mh ON m.file_path = mh.file_path
                    WHERE mh.has_embedding IS NULL OR mh.has_embedding = 0
                """
                )
                return [to_native_path(row["file_path"]) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get unembedded file paths: {e}")
            return []

    def mark_embedded(self, file_paths: List[str], clip_model: str) -> int:
        """Mark files as having CLIP embeddings in the index."""
        marked = 0
        try:
            with self.lock:
                with self._get_connection() as conn:
                    for fp in file_paths:
                        posix_path = to_posix_path(fp)
                        conn.execute(
                            """
                            INSERT INTO media_hashes (file_path, clip_model, has_embedding, embedding_updated_at)
                            VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                            ON CONFLICT(file_path) DO UPDATE SET
                                clip_model = excluded.clip_model,
                                has_embedding = 1,
                                embedding_updated_at = CURRENT_TIMESTAMP
                        """,
                            (posix_path, clip_model),
                        )
                        marked += 1
                    conn.commit()
        except Exception as e:
            logger.error(f"Failed to mark files as embedded: {e}")
        return marked

    def mark_embedding_skipped(self, file_paths: List[str]) -> int:
        """Mark files as permanently skipped for embedding (e.g. corrupt, unreadable).

        Sets has_embedding = -1 so get_unembedded_file_paths() excludes them.
        A full rebuild (clear_embeddings) resets these back to 0 for retry.
        """
        if not file_paths:
            return 0
        marked = 0
        try:
            with self.lock:
                with self._get_connection() as conn:
                    for fp in file_paths:
                        posix_path = to_posix_path(fp)
                        conn.execute(
                            """
                            INSERT INTO media_hashes (file_path, has_embedding)
                            VALUES (?, -1)
                            ON CONFLICT(file_path) DO UPDATE SET
                                has_embedding = -1
                        """,
                            (posix_path,),
                        )
                        marked += 1
                    conn.commit()
        except Exception as e:
            logger.error(f"Failed to mark files as skipped: {e}")
        return marked

    def clear_embeddings(self) -> bool:
        """Reset all has_embedding flags (used when CLIP model changes)."""
        try:
            with self.lock:
                with self._get_connection() as conn:
                    conn.execute(
                        "UPDATE media_hashes SET has_embedding = 0, clip_model = NULL, embedding_updated_at = NULL"
                    )
                    conn.commit()
                    return True
        except Exception as e:
            logger.error(f"Failed to clear embeddings: {e}")
            return False

    def get_embedding_stats(self) -> Dict[str, Any]:
        """Get statistics about embedding coverage."""
        try:
            with self._get_connection() as conn:
                total = conn.execute("SELECT COUNT(*) as c FROM media").fetchone()["c"]
                hashed = conn.execute(
                    "SELECT COUNT(*) as c FROM media_hashes WHERE phash IS NOT NULL"
                ).fetchone()["c"]
                embedded = conn.execute(
                    "SELECT COUNT(*) as c FROM media_hashes WHERE has_embedding = 1"
                ).fetchone()["c"]
                model_row = conn.execute(
                    "SELECT clip_model FROM media_hashes WHERE clip_model IS NOT NULL LIMIT 1"
                ).fetchone()
                return {
                    "total_media": total,
                    "hashed": hashed,
                    "embedded": embedded,
                    "clip_model": model_row["clip_model"] if model_row else None,
                }
        except Exception as e:
            logger.error(f"Failed to get embedding stats: {e}")
            return {
                "total_media": 0,
                "hashed": 0,
                "embedded": 0,
                "clip_model": None,
            }

    def get_stats(self) -> Dict[str, Any]:
        try:
            with self._get_connection() as conn:
                total_media = conn.execute(
                    "SELECT COUNT(*) as count FROM media"
                ).fetchone()["count"]

                sources = {}
                rows = conn.execute(
                    """
                    SELECT json_extract(data, '$.metadata_source') as source, COUNT(*) as count
                    FROM media
                    GROUP BY source
                """
                )
                for row in rows:
                    source = row["source"] or "unknown"
                    sources[source] = row["count"]

                db_size = self.db_file.stat().st_size if self.db_file.exists() else 0

                return {
                    "total_media": total_media,
                    "by_source": sources,
                    "db_size_bytes": db_size,
                }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {"total_media": 0, "by_source": {}, "db_size_bytes": 0}

    def truncate_all_data(self) -> bool:
        try:
            with self.lock:
                # First, delete data in a transaction
                with self._get_connection() as conn:
                    # Delete all data from both tables
                    conn.execute("DELETE FROM indices")
                    conn.execute("DELETE FROM media")

                    try:
                        conn.execute("DELETE FROM sqlite_sequence WHERE name='media'")
                        conn.execute("DELETE FROM sqlite_sequence WHERE name='indices'")
                    except Exception:
                        # sqlite_sequence doesn't exist if no auto-increment columns are used
                        pass

                    conn.commit()

                with self._get_connection() as conn:
                    conn.execute("VACUUM")

                logger.info("Successfully truncated all database data")
                return True
        except Exception as e:
            logger.error(f"Failed to truncate database: {e}")
            return False
