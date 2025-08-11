from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses_json import dataclass_json, config
from dataclasses_json.cfg import LetterCase


@dataclass_json
@dataclass
class LoRA:
    """Represents a LoRA (Low-Rank Adaptation) with name and weight."""

    lora_name: str
    lora_weight: float


@dataclass_json
@dataclass
class Media:
    file_path: Path = field(metadata=config(encoder=str, decoder=Path))
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
    scheduler: Optional[str] = None
    steps: Optional[int] = None
    cfg_scale: Optional[float] = None
    seed: Optional[int] = None

    # Video-specific metadata
    frame_rate: Optional[float] = None
    duration: Optional[float] = None
    video_length: Optional[int] = None  # Number of frames for ComfyUI videos

    # Tags and organization
    tags: List[str] = field(default_factory=list)

    # LoRAs (Low-Rank Adaptations)
    loras: List[LoRA] = field(default_factory=list)

    # Favorite status
    is_favorite: bool = False

    # Thumbnail cache path
    thumbnail_path: Optional[Path] = field(
        default=None,
        metadata=config(
            encoder=lambda x: str(x) if x else None,
            decoder=lambda x: Path(x) if x else None,
        ),
    )

    @property
    def file_name(self) -> str:
        return self.file_path.name

    @property
    def file_extension(self) -> str:
        return self.file_path.suffix.lower()

    @property
    def is_video(self) -> bool:
        """Check if this is a video file"""
        return self.file_extension == ".mp4"

    @property
    def is_image(self) -> bool:
        """Check if this is an image file"""
        return not self.is_video

    @property
    def media_type(self) -> str:
        """Get the media type string"""
        return "video" if self.is_video else "image"

    def __hash__(self) -> int:
        return hash(str(self.file_path))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Media):
            return False
        return self.file_path == other.file_path
