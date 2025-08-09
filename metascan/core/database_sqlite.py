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
    
    def _init_database(self):
        """Initialize database schema"""
        with self._get_connection() as conn:
            # Main media table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS media (
                    file_path TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    is_favorite INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Add favorite column to existing tables (migration)
            cursor = conn.execute("PRAGMA table_info(media)")
            columns = [column[1] for column in cursor.fetchall()]
            if 'is_favorite' not in columns:
                conn.execute("ALTER TABLE media ADD COLUMN is_favorite INTEGER DEFAULT 0")
                logger.info("Added is_favorite column to media table")
            
            # Index tables for fast searching
            conn.execute("""
                CREATE TABLE IF NOT EXISTS indices (
                    index_type TEXT NOT NULL,
                    index_key TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    PRIMARY KEY (index_type, index_key, file_path),
                    FOREIGN KEY (file_path) REFERENCES media(file_path) ON DELETE CASCADE
                )
            """)
            
            # Create indices for performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_indices_lookup ON indices(index_type, index_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_media_created ON media(created_at)")
            
            conn.commit()
    
    @contextmanager
    def _get_connection(self):
        """Get a database connection with proper settings"""
        conn = sqlite3.connect(str(self.db_file))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")  # Better concurrency
        try:
            yield conn
        finally:
            conn.close()
    
    def close(self):
        """Close database connections (compatibility method)"""
        pass  # SQLite connections are closed per-operation
    
    @contextmanager
    def batch_writer(self):
        """Context manager for batch operations"""
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
        """Save a single media object"""
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Save media with favorite status
                    conn.execute("""
                        INSERT OR REPLACE INTO media (file_path, data, is_favorite, updated_at)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """, (str(media.file_path), media.to_json(), 1 if media.is_favorite else 0))
                    
                    # Update indices
                    self._update_indices(conn, media)
                    conn.commit()
                    return True
        except Exception as e:
            logger.error(f"Failed to save media {media.file_path}: {e}")
            return False
    
    def save_media_batch(self, media_list: List[Media]) -> int:
        """Save multiple media objects efficiently"""
        saved_count = 0
        with self.batch_writer() as conn:
            for media in media_list:
                try:
                    # Save media with favorite status
                    conn.execute("""
                        INSERT OR REPLACE INTO media (file_path, data, is_favorite, updated_at)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """, (str(media.file_path), media.to_json(), 1 if media.is_favorite else 0))
                    
                    # Update indices
                    self._update_indices(conn, media)
                    saved_count += 1
                except Exception as e:
                    logger.error(f"Failed to save media in batch {media.file_path}: {e}")
        
        return saved_count
    
    def get_media(self, file_path: Path) -> Optional[Media]:
        """Retrieve a media object by file path"""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT data FROM media WHERE file_path = ?",
                    (str(file_path),)
                ).fetchone()
                
                if row:
                    return Media.from_json(row['data'])
                return None
        except Exception as e:
            logger.error(f"Failed to get media {file_path}: {e}")
            return None
    
    def get_all_media(self) -> List[Media]:
        """Get all media objects from database"""
        media_list = []
        try:
            with self._get_connection() as conn:
                rows = conn.execute("SELECT data FROM media ORDER BY created_at DESC")
                for row in rows:
                    try:
                        media = Media.from_json(row['data'])
                        media_list.append(media)
                    except Exception as e:
                        logger.error(f"Failed to decode media: {e}")
        except Exception as e:
            logger.error(f"Failed to get all media: {e}")
        
        return media_list
    
    def delete_media(self, file_path: Path) -> bool:
        """Delete a media object"""
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Delete from media table (indices will cascade delete)
                    cursor = conn.execute(
                        "DELETE FROM media WHERE file_path = ?",
                        (str(file_path),)
                    )
                    conn.commit()
                    return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to delete media {file_path}: {e}")
            return False
    
    def search_by_index(self, index_type: str, term: str) -> Set[str]:
        """Search using inverted index"""
        try:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT DISTINCT file_path 
                    FROM indices 
                    WHERE index_type = ? AND index_key = ?
                """, (index_type, term.lower()))
                
                return {row['file_path'] for row in rows}
        except Exception as e:
            logger.error(f"Index search failed for {index_type}:{term}: {e}")
            return set()
    
    def _update_indices(self, conn: sqlite3.Connection, media: Media):
        """Update inverted indices for a media object"""
        # First, remove all existing indices for this file
        conn.execute(
            "DELETE FROM indices WHERE file_path = ?",
            (str(media.file_path),)
        )
        
        # Generate and insert new indices
        indices_to_insert = []
        for index_type, index_key in self._generate_indices(media):
            indices_to_insert.append((index_type, index_key, str(media.file_path)))
        
        if indices_to_insert:
            conn.executemany(
                "INSERT INTO indices (index_type, index_key, file_path) VALUES (?, ?, ?)",
                indices_to_insert
            )
    
    def _generate_indices(self, media: Media) -> List[tuple]:
        """Generate index entries for a media object"""
        indices = []
        
        # Index by source
        if media.metadata_source:
            indices.append(("source", media.metadata_source.lower()))
        
        # Index by model
        if media.model:
            indices.append(("model", media.model.lower()))
        
        # Index by extension
        indices.append(("ext", media.file_extension))
        
        # Index by tags
        for tag in media.tags:
            indices.append(("tag", tag.lower()))
        
        # Index by date (year-month)
        date_key = media.created_at.strftime("%Y-%m")
        indices.append(("date", date_key))
        
        # Index prompt words using the tokenizer
        if media.prompt:
            filtered_words = self.prompt_tokenizer.tokenize(media.prompt)
            for word in filtered_words:
                indices.append(("prompt", word))
        
        return indices
    
    def get_filter_data(self, sort_order: str = "count") -> Dict[str, List[Dict[str, Any]]]:
        """Get all index data organized by index_type with counts
        
        Args:
            sort_order: "count" (by count desc) or "alphabetical" (by key asc)
        """
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
                
                filter_data = {}
                for row in rows:
                    index_type = row['index_type']
                    if index_type not in filter_data:
                        filter_data[index_type] = []
                    
                    filter_data[index_type].append({
                        'key': row['index_key'],
                        'count': row['count']
                    })
                
                return filter_data
        except Exception as e:
            logger.error(f"Failed to get filter data: {e}")
            return {}
    
    def get_filtered_media_paths(self, filters: Dict[str, List[str]]) -> Set[str]:
        """
        Get file paths that match the provided filters.
        
        Args:
            filters: Dict where keys are index_types and values are lists of index_keys
                    e.g., {'prompt': ['portrait', 'landscape'], 'source': ['ComfyUI']}
        
        Returns:
            Set of file paths that match the filters
        """
        if not filters:
            return set()
        
        try:
            with self._get_connection() as conn:
                # Build query dynamically based on filters
                conditions = []
                params = []
                
                for index_type, index_keys in filters.items():
                    if index_keys:  # Skip empty lists
                        placeholders = ','.join(['?' for _ in index_keys])
                        conditions.append(f"""
                            file_path IN (
                                SELECT DISTINCT file_path 
                                FROM indices 
                                WHERE index_type = ? AND index_key IN ({placeholders})
                            )
                        """)
                        params.append(index_type)
                        params.extend(index_keys)
                
                if not conditions:
                    return set()
                
                # Join conditions with AND (intersection of all filter types)
                query = f"SELECT DISTINCT file_path FROM indices WHERE {' AND '.join(conditions)}"
                
                rows = conn.execute(query, params)
                return {row['file_path'] for row in rows}
        except Exception as e:
            logger.error(f"Failed to get filtered media paths: {e}")
            return set()

    def toggle_favorite(self, file_path: Path) -> bool:
        """Toggle the favorite status of a media item"""
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Get current favorite status
                    row = conn.execute(
                        "SELECT is_favorite FROM media WHERE file_path = ?",
                        (str(file_path),)
                    ).fetchone()
                    
                    if row is not None:
                        new_status = 0 if row['is_favorite'] else 1
                        conn.execute("""
                            UPDATE media 
                            SET is_favorite = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE file_path = ?
                        """, (new_status, str(file_path)))
                        conn.commit()
                        return bool(new_status)
                    return False
        except Exception as e:
            logger.error(f"Failed to toggle favorite for {file_path}: {e}")
            return False
    
    def set_favorite(self, file_path: Path, is_favorite: bool) -> bool:
        """Set the favorite status of a media item"""
        try:
            with self.lock:
                with self._get_connection() as conn:
                    cursor = conn.execute("""
                        UPDATE media 
                        SET is_favorite = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE file_path = ?
                    """, (1 if is_favorite else 0, str(file_path)))
                    conn.commit()
                    return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to set favorite for {file_path}: {e}")
            return False
    
    def get_favorite_media_paths(self) -> Set[str]:
        """Get all file paths of favorite media"""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT file_path FROM media WHERE is_favorite = 1"
                )
                return {row['file_path'] for row in rows}
        except Exception as e:
            logger.error(f"Failed to get favorite media paths: {e}")
            return set()
    
    def delete_media(self, file_path: Path) -> bool:
        """Delete a media item from the database completely
        
        Args:
            file_path: Path to the media file to delete
            
        Returns:
            True if deletion was successful, False otherwise
        """
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Delete from indices table (will cascade due to foreign key)
                    conn.execute(
                        "DELETE FROM indices WHERE file_path = ?",
                        (str(file_path),)
                    )
                    
                    # Delete from media table
                    cursor = conn.execute(
                        "DELETE FROM media WHERE file_path = ?",
                        (str(file_path),)
                    )
                    
                    conn.commit()
                    
                    deleted = cursor.rowcount > 0
                    if deleted:
                        logger.info(f"Deleted media from database: {file_path}")
                    else:
                        logger.warning(f"Media not found in database: {file_path}")
                    
                    return deleted
        except Exception as e:
            logger.error(f"Failed to delete media {file_path}: {e}")
            return False
    
    def load_favorite_status(self, media_list: List[Media]):
        """Load favorite status from database for a list of media objects"""
        try:
            with self._get_connection() as conn:
                for media in media_list:
                    row = conn.execute(
                        "SELECT is_favorite FROM media WHERE file_path = ?",
                        (str(media.file_path),)
                    ).fetchone()
                    if row:
                        media.is_favorite = bool(row['is_favorite'])
        except Exception as e:
            logger.error(f"Failed to load favorite status: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics"""
        try:
            with self._get_connection() as conn:
                # Total media count
                total_media = conn.execute("SELECT COUNT(*) as count FROM media").fetchone()['count']
                
                # Count by source
                sources = {}
                rows = conn.execute("""
                    SELECT json_extract(data, '$.metadata_source') as source, COUNT(*) as count
                    FROM media
                    GROUP BY source
                """)
                for row in rows:
                    source = row['source'] or "unknown"
                    sources[source] = row['count']
                
                # Database file size
                db_size = self.db_file.stat().st_size if self.db_file.exists() else 0
                
                return {
                    "total_media": total_media,
                    "by_source": sources,
                    "db_size_bytes": db_size
                }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {
                "total_media": 0,
                "by_source": {},
                "db_size_bytes": 0
            }