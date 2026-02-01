from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses_json import dataclass_json, config
from dataclasses_json.cfg import LetterCase

from metascan.utils.path_utils import to_posix_path, to_native_path


@dataclass_json
@dataclass
class LoRA:
    lora_name: str
    lora_weight: float


@dataclass_json
@dataclass
class Media:
    file_path: Path = field(
        metadata=config(
            encoder=lambda p: to_posix_path(p),
            decoder=lambda s: Path(to_native_path(s)),
        )
    )
    file_size: int
    width: int
    height: int
    format: str
    created_at: datetime
    modified_at: datetime

    metadata_source: Optional[str] = None  # ComfyUI, SwarmUI, Fooocus
    generation_data: Dict[str, Any] = field(default_factory=dict)

    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    model: List[str] = field(default_factory=list)
    sampler: Optional[str] = None
    scheduler: Optional[str] = None
    steps: Optional[int] = None
    cfg_scale: Optional[float] = None
    seed: Optional[int] = None

    frame_rate: Optional[float] = None
    duration: Optional[float] = None
    video_length: Optional[int] = None  # Number of frames for ComfyUI videos

    tags: List[str] = field(default_factory=list)

    loras: List[LoRA] = field(default_factory=list)

    is_favorite: bool = False

    playback_speed: Optional[float] = None  # Per-file playback speed, None uses default

    thumbnail_path: Optional[Path] = field(
        default=None,
        metadata=config(
            encoder=lambda x: to_posix_path(x) if x else None,
            decoder=lambda x: Path(to_native_path(x)) if x else None,
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
        return self.file_extension in {".mp4", ".webm"}

    @property
    def is_image(self) -> bool:
        """Check if this is an image file"""
        return not self.is_video

    @property
    def media_type(self) -> str:
        return "video" if self.is_video else "image"

    def __hash__(self) -> int:
        return hash(str(self.file_path))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Media):
            return False
        return self.file_path == other.file_path

    @staticmethod
    def from_dict_fast(data: Dict[str, Any]) -> "Media":
        """Fast deserialization bypassing dataclasses_json overhead.

        This method directly constructs a Media object without the reflection
        and type introspection overhead of dataclasses_json, providing ~10x
        faster deserialization for bulk loading.
        """
        # Parse nested LoRA objects
        loras = []
        if data.get("loras"):
            loras = [
                LoRA(lora_name=lora["lora_name"], lora_weight=lora["lora_weight"])
                for lora in data["loras"]
            ]

        # Parse datetime fields (handle string ISO format, float timestamp, or datetime)
        created_at = data["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif isinstance(created_at, (int, float)):
            created_at = datetime.fromtimestamp(created_at)

        modified_at = data["modified_at"]
        if isinstance(modified_at, str):
            modified_at = datetime.fromisoformat(modified_at)
        elif isinstance(modified_at, (int, float)):
            modified_at = datetime.fromtimestamp(modified_at)

        # Parse Path fields (convert from POSIX storage format to native)
        thumbnail_path = None
        if data.get("thumbnail_path"):
            thumbnail_path = Path(to_native_path(data["thumbnail_path"]))

        return Media(
            file_path=Path(to_native_path(data["file_path"])),
            file_size=data["file_size"],
            width=data["width"],
            height=data["height"],
            format=data["format"],
            created_at=created_at,
            modified_at=modified_at,
            metadata_source=data.get("metadata_source"),
            generation_data=data.get("generation_data", {}),
            prompt=data.get("prompt"),
            negative_prompt=data.get("negative_prompt"),
            model=data.get("model", []),
            sampler=data.get("sampler"),
            scheduler=data.get("scheduler"),
            steps=data.get("steps"),
            cfg_scale=data.get("cfg_scale"),
            seed=data.get("seed"),
            frame_rate=data.get("frame_rate"),
            duration=data.get("duration"),
            video_length=data.get("video_length"),
            tags=data.get("tags", []),
            loras=loras,
            is_favorite=data.get("is_favorite", False),
            playback_speed=data.get("playback_speed"),
            thumbnail_path=thumbnail_path,
        )

    @staticmethod
    def from_json_fast(json_str: str) -> "Media":
        """Fast JSON deserialization using orjson.

        Combines orjson's fast parsing with direct object construction
        for optimal deserialization performance.
        """
        import orjson

        data = orjson.loads(json_str)
        return Media.from_dict_fast(data)
