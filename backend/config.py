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


def get_models_config(config: dict) -> dict:
    """Return the ``models`` section with defaults filled in.

    Shape:
        {
            "preload_at_startup": ["clip-large", ...],  # model ids
            "huggingface_token": "<str>"                # "" if unset
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
