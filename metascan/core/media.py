from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses_json import dataclass_json


@dataclass_json
@dataclass
class Media:
    file_path: Path
    file_size: int
    width: int
    height: int
    format: str
    created_at: datetime
    modified_at: datetime
    
    # Metadata fields
    metadata_source: Optional[str] = None  # ComfyUI, SwarmUI, Fooocus
    generation_data: Dict[str, Any] = field(default_factory=dict)
    
    # Extracted parameters
    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    model: Optional[str] = None
    sampler: Optional[str] = None
    steps: Optional[int] = None
    cfg_scale: Optional[float] = None
    seed: Optional[int] = None
    
    # Tags and organization
    tags: List[str] = field(default_factory=list)
    
    # Thumbnail cache path
    thumbnail_path: Optional[Path] = None
    
    @property
    def file_name(self) -> str:
        return self.file_path.name
    
    @property
    def file_extension(self) -> str:
        return self.file_path.suffix.lower()
    
    def __hash__(self):
        return hash(str(self.file_path))
    
    def __eq__(self, other):
        if not isinstance(other, Media):
            return False
        return self.file_path == other.file_path