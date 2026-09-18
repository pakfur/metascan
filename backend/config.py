"""Server configuration for the metascan backend."""

import json
import os
from dataclasses import dataclass, field
from typing import List, Optional

from metascan.utils.app_paths import get_config_path


@dataclass
class DirectoryConfig:
    filepath: str
    search_subfolders: bool = True


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8700
    api_key: Optional[str] = None
    cors_origins: List[str] = field(default_factory=lambda: ["*"])


def load_app_config() -> dict:
    """Load the metascan config.json file."""
    config_path = get_config_path()
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def save_app_config(config: dict) -> None:
    """Save the metascan config.json file."""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


def get_server_config() -> ServerConfig:
    """Load server-specific config from environment variables or defaults."""
    return ServerConfig(
        host=os.environ.get("METASCAN_HOST", "0.0.0.0"),
        port=int(os.environ.get("METASCAN_PORT", "8700")),
        api_key=os.environ.get("METASCAN_API_KEY"),
        cors_origins=os.environ.get("METASCAN_CORS_ORIGINS", "*").split(","),
    )


def get_directories(config: dict) -> List[DirectoryConfig]:
    """Extract directory configurations from app config."""
    return [
        DirectoryConfig(
            filepath=d["filepath"],
            search_subfolders=d.get("search_subfolders", True),
        )
        for d in config.get("directories", [])
    ]


def get_ui_config(config: dict) -> dict:
    """UI section of config.json. Currently exposes:
    - map_tile_url: MapLibre GL style URL for the location panel.
                    Defaults to OpenFreeMap liberty.
    """
    ui = config.get("ui") or {}
    return {
        "map_tile_url": ui.get(
            "map_tile_url",
            "https://tiles.openfreemap.org/styles/liberty",
        ),
    }


def get_models_config(config: dict) -> dict:
    """Return the ``models`` section with defaults filled in.

    Shape:
        {
            "preload_at_startup": ["clip-large", ...],  # model ids
            "huggingface_token": "<str>",               # "" if unset
        }
    """
    raw = config.get("models", {}) or {}
    preload = raw.get("preload_at_startup") or []
    if not isinstance(preload, list):
        preload = []
    return {
        "preload_at_startup": [str(x) for x in preload],
        "huggingface_token": str(raw.get("huggingface_token") or ""),
    }


def get_comfy_config(config: dict) -> dict:
    """Return the ``comfy`` section with defaults filled in.

    Shape:
        {
            "base_url": "http://127.0.0.1:8188",
            "in_flight": 2,                  # jobs held inside ComfyUI at once
            "unload_vlm_during_generation": True,
            "output_root": "data/storyboards",
            "request_timeout_s": 30.0,
        }
    """
    raw = config.get("comfy", {}) or {}
    return {
        "base_url": str(raw.get("base_url") or "http://127.0.0.1:8188"),
        "in_flight": max(1, int(raw.get("in_flight") or 2)),
        "unload_vlm_during_generation": bool(
            raw.get("unload_vlm_during_generation", True)
        ),
        "output_root": str(raw.get("output_root") or "data/storyboards"),
        "request_timeout_s": float(raw.get("request_timeout_s") or 30.0),
    }


def get_i2v_config(config: dict) -> dict:
    """Return the ``i2v`` section with defaults filled in.

    Shape:
        {
            "fast_preset_id": None,      # workflow_presets.id or None
            "quality_preset_id": None,
            "durations": [6.0, 10.0, 15.0, 20.0],
            "default_duration": 6.0,
            "default_quality": "fast",   # "fast" | "quality"
        }
    """
    raw = config.get("i2v", {}) or {}

    def _preset_id(v: object) -> Optional[int]:
        try:
            return int(v) if v else None
        except (TypeError, ValueError):
            return None

    fallback = [6.0, 10.0, 15.0, 20.0]
    try:
        durations = [float(d) for d in (raw.get("durations") or [])]
    except (TypeError, ValueError):
        durations = []
    if not durations:
        durations = fallback
    try:
        default_duration = float(raw.get("default_duration") or durations[0])
    except (TypeError, ValueError):
        default_duration = durations[0]
    quality = str(raw.get("default_quality") or "fast")
    return {
        "fast_preset_id": _preset_id(raw.get("fast_preset_id")),
        "quality_preset_id": _preset_id(raw.get("quality_preset_id")),
        "durations": durations,
        "default_duration": default_duration,
        "default_quality": quality if quality in ("fast", "quality") else "fast",
    }
