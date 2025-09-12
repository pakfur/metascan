import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any, Set
from contextlib import contextmanager
import logging
from datetime import datetime
from threading import Lock

from metascan.core.media import Media
from metascan.core.prompt_tokenizer import PromptTokenizer

logger = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.db_file = self.db_path / "metascan.db"
        self.lock = Lock()

        # Initialize prompt tokenizer
        self.prompt_tokenizer = PromptTokenizer()

        self._init_database()

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
                conn.execute(
                    "ALTER TABLE media ADD COLUMN is_favorite INTEGER DEFAULT 0"
                )
                logger.info("Added is_favorite column to media table")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS indices (
                    index_type TEXT NOT NULL,
                    index_key TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    PRIMARY KEY (index_type, index_key, file_path),
                    FOREIGN KEY (file_path) REFERENCES media(file_path) ON DELETE CASCADE
                )
            """
            )

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_indices_lookup ON indices(index_type, index_key)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_created ON media(created_at)"
            )

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

    def save_media(self, media: Media) -> bool:
        try:
            with self.lock:
                with self._get_connection() as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO media (file_path, data, is_favorite, updated_at)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                        (
                            str(media.file_path),
                            media.to_json(),  # type: ignore[attr-defined]
                            1 if media.is_favorite else 0,
                        ),
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
                    # Save media with favorite status
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO media (file_path, data, is_favorite, updated_at)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                        (
                            str(media.file_path),
                            media.to_json(),  # type: ignore[attr-defined]
                            1 if media.is_favorite else 0,
                        ),
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
                row = conn.execute(
                    "SELECT data FROM media WHERE file_path = ?", (str(file_path),)
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

    def delete_media(self, file_path: Path) -> bool:
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Delete from media table (indices will cascade delete)
                    cursor = conn.execute(
                        "DELETE FROM media WHERE file_path = ?", (str(file_path),)
                    )
                    conn.commit()
                    return cursor.rowcount > 0  # type: ignore[no-any-return]
        except Exception as e:
            logger.error(f"Failed to delete media {file_path}: {e}")
            return False

    def get_existing_file_paths(self) -> Set[str]:
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT file_path FROM media")
                return {row["file_path"] for row in cursor}
        except Exception as e:
            logger.error(f"Failed to get existing file paths: {e}")
            return set()

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

                return {row["file_path"] for row in rows}
        except Exception as e:
            logger.error(f"Index search failed for {index_type}:{term}: {e}")
            return set()

    def _update_indices(self, conn: sqlite3.Connection, media: Media) -> None:
        conn.execute("DELETE FROM indices WHERE file_path = ?", (str(media.file_path),))

        indices_to_insert = []
        for index_type, index_key in self._generate_indices(media):
            indices_to_insert.append((index_type, index_key, str(media.file_path)))

        if indices_to_insert:
            conn.executemany(
                "INSERT INTO indices (index_type, index_key, file_path) VALUES (?, ?, ?)",
                indices_to_insert,
            )

    def _generate_indices(self, media: Media) -> List[tuple]:
        indices = []

        if media.metadata_source:
            indices.append(("source", media.metadata_source.lower()))

        if media.model:
            indices.append(("model", media.model.lower()))

        indices.append(("ext", media.file_extension))

        # Add reverse index for the fully qualified file path
        path_str = str(media.file_path).lower()
        indices.append(("path", path_str))

        for tag in media.tags:
            indices.append(("tag", tag.lower()))

        if media.prompt:
            filtered_words = self.prompt_tokenizer.tokenize(media.prompt)
            for word in filtered_words:
                indices.append(("prompt", word))

        for lora in media.loras:
            indices.append(("lora", lora.lora_name.lower()))

        return indices

    def get_filter_data(
        self, sort_order: str = "count"
    ) -> Dict[str, List[Dict[str, Any]]]:
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

                rows = conn.execute(query)

                filter_data: Dict[str, List[Dict[str, Any]]] = {}
                for row in rows:
                    index_type = row["index_type"]
                    if index_type not in filter_data:
                        filter_data[index_type] = []

                    filter_data[index_type].append(
                        {"key": row["index_key"], "count": row["count"]}
                    )

                return filter_data
        except Exception as e:
            logger.error(f"Failed to get filter data: {e}")
            return {}

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
                            path_prefix = index_keys[0].lower()
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

                        # Get paths for this filter
                        current_paths = {row["file_path"] for row in rows}

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
                    # Get current favorite status
                    row = conn.execute(
                        "SELECT is_favorite FROM media WHERE file_path = ?",
                        (str(file_path),),
                    ).fetchone()

                    if row is not None:
                        new_status = 0 if row["is_favorite"] else 1
                        conn.execute(
                            """
                            UPDATE media 
                            SET is_favorite = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE file_path = ?
                        """,
                            (new_status, str(file_path)),
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
                    cursor = conn.execute(
                        """
                        UPDATE media 
                        SET is_favorite = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE file_path = ?
                    """,
                        (1 if is_favorite else 0, str(file_path)),
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
                return {row["file_path"] for row in rows}
        except Exception as e:
            logger.error(f"Failed to get favorite media paths: {e}")
            return set()

    def load_favorite_status(self, media_list: List[Media]) -> None:
        try:
            with self._get_connection() as conn:
                for media in media_list:
                    row = conn.execute(
                        "SELECT is_favorite FROM media WHERE file_path = ?",
                        (str(media.file_path),),
                    ).fetchone()
                    if row:
                        media.is_favorite = bool(row["is_favorite"])
        except Exception as e:
            logger.error(f"Failed to load favorite status: {e}")

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

                    # Update the database record
                    conn.execute(
                        """UPDATE media SET data = ? WHERE file_path = ?""",
                        (media.to_json(), str(file_path)),  # type: ignore[attr-defined]
                    )

                    conn.commit()
                    return True

        except Exception as e:
            logger.error(f"Error updating media dimensions for {file_path}: {e}")
            return False

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
