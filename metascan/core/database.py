import plyvel
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Set
from contextlib import contextmanager
import logging
from datetime import datetime

from metascan.core.media import Media

logger = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.mkdir(parents=True, exist_ok=True)
        
        # Main database for media objects
        self.media_db = plyvel.DB(
            str(self.db_path / "media"), 
            create_if_missing=True
        )
        
        # Inverted indices for fast searching
        self.index_db = plyvel.DB(
            str(self.db_path / "indices"),
            create_if_missing=True
        )
        
    def close(self):
        self.media_db.close()
        self.index_db.close()
    
    @contextmanager
    def batch_writer(self):
        """Context manager for batch operations"""
        media_batch = self.media_db.write_batch()
        index_batch = self.index_db.write_batch()
        try:
            yield media_batch, index_batch
            media_batch.write()
            index_batch.write()
        except Exception as e:
            logger.error(f"Batch write failed: {e}")
            raise
    
    def save_media(self, media: Media) -> bool:
        """Save a single media object"""
        try:
            key = str(media.file_path).encode('utf-8')
            value = media.to_json().encode('utf-8')
            self.media_db.put(key, value)
            
            # Update indices
            self._update_indices(media)
            return True
        except Exception as e:
            logger.error(f"Failed to save media {media.file_path}: {e}")
            return False
    
    def save_media_batch(self, media_list: List[Media]) -> int:
        """Save multiple media objects efficiently"""
        saved_count = 0
        with self.batch_writer() as (media_batch, index_batch):
            for media in media_list:
                try:
                    key = str(media.file_path).encode('utf-8')
                    value = media.to_json().encode('utf-8')
                    media_batch.put(key, value)
                    
                    # Update indices in batch
                    self._update_indices_batch(media, index_batch)
                    saved_count += 1
                except Exception as e:
                    logger.error(f"Failed to save media in batch {media.file_path}: {e}")
        
        return saved_count
    
    def get_media(self, file_path: Path) -> Optional[Media]:
        """Retrieve a media object by file path"""
        try:
            key = str(file_path).encode('utf-8')
            value = self.media_db.get(key)
            if value:
                return Media.from_json(value.decode('utf-8'))
            return None
        except Exception as e:
            logger.error(f"Failed to get media {file_path}: {e}")
            return None
    
    def get_all_media(self) -> List[Media]:
        """Get all media objects from database"""
        media_list = []
        for key, value in self.media_db:
            try:
                media = Media.from_json(value.decode('utf-8'))
                media_list.append(media)
            except Exception as e:
                logger.error(f"Failed to decode media: {e}")
        return media_list
    
    def delete_media(self, file_path: Path) -> bool:
        """Delete a media object"""
        try:
            media = self.get_media(file_path)
            if media:
                # Remove from indices first
                self._remove_from_indices(media)
                
                # Delete from main db
                key = str(file_path).encode('utf-8')
                self.media_db.delete(key)
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete media {file_path}: {e}")
            return False
    
    def search_by_index(self, index_type: str, term: str) -> Set[str]:
        """Search using inverted index"""
        try:
            key = f"{index_type}:{term.lower()}".encode('utf-8')
            value = self.index_db.get(key)
            if value:
                return set(json.loads(value.decode('utf-8')))
            return set()
        except Exception as e:
            logger.error(f"Index search failed for {index_type}:{term}: {e}")
            return set()
    
    def _update_indices(self, media: Media):
        """Update inverted indices for a media object"""
        indices_to_update = self._generate_indices(media)
        
        for index_key, file_path in indices_to_update:
            key = index_key.encode('utf-8')
            
            # Get existing entries
            existing = self.index_db.get(key)
            if existing:
                file_paths = set(json.loads(existing.decode('utf-8')))
            else:
                file_paths = set()
            
            # Add new file path
            file_paths.add(str(file_path))
            
            # Save updated index
            self.index_db.put(key, json.dumps(list(file_paths)).encode('utf-8'))
    
    def _update_indices_batch(self, media: Media, batch: plyvel.WriteBatch):
        """Update indices in batch mode"""
        indices_to_update = self._generate_indices(media)
        
        for index_key, file_path in indices_to_update:
            key = index_key.encode('utf-8')
            
            # Get existing entries
            existing = self.index_db.get(key)
            if existing:
                file_paths = set(json.loads(existing.decode('utf-8')))
            else:
                file_paths = set()
            
            # Add new file path
            file_paths.add(str(file_path))
            
            # Add to batch
            batch.put(key, json.dumps(list(file_paths)).encode('utf-8'))
    
    def _remove_from_indices(self, media: Media):
        """Remove media from all indices"""
        indices_to_update = self._generate_indices(media)
        
        for index_key, file_path in indices_to_update:
            key = index_key.encode('utf-8')
            
            # Get existing entries
            existing = self.index_db.get(key)
            if existing:
                file_paths = set(json.loads(existing.decode('utf-8')))
                file_paths.discard(str(file_path))
                
                if file_paths:
                    # Update with remaining paths
                    self.index_db.put(key, json.dumps(list(file_paths)).encode('utf-8'))
                else:
                    # Remove empty index
                    self.index_db.delete(key)
    
    def _generate_indices(self, media: Media) -> List[tuple]:
        """Generate index entries for a media object"""
        indices = []
        
        # Index by source
        if media.metadata_source:
            indices.append((f"source:{media.metadata_source.lower()}", media.file_path))
        
        # Index by model
        if media.model:
            indices.append((f"model:{media.model.lower()}", media.file_path))
        
        # Index by extension
        indices.append((f"ext:{media.file_extension}", media.file_path))
        
        # Index by tags
        for tag in media.tags:
            indices.append((f"tag:{tag.lower()}", media.file_path))
        
        # Index by date (year-month)
        date_key = media.created_at.strftime("%Y-%m")
        indices.append((f"date:{date_key}", media.file_path))
        
        # Index prompt words (simple tokenization)
        if media.prompt:
            words = set(word.lower() for word in media.prompt.split() 
                       if len(word) > 2)
            for word in words:
                indices.append((f"prompt:{word}", media.file_path))
        
        return indices
    
    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics"""
        total_media = sum(1 for _ in self.media_db)
        
        # Count by source
        sources = {}
        for key, value in self.media_db:
            try:
                media = Media.from_json(value.decode('utf-8'))
                source = media.metadata_source or "unknown"
                sources[source] = sources.get(source, 0) + 1
            except:
                pass
        
        return {
            "total_media": total_media,
            "by_source": sources,
            "db_size_bytes": self._get_db_size()
        }
    
    def _get_db_size(self) -> int:
        """Calculate total database size"""
        total = 0
        for db_dir in [self.db_path / "media", self.db_path / "indices"]:
            if db_dir.exists():
                total += sum(f.stat().st_size for f in db_dir.rglob("*") if f.is_file())
        return total