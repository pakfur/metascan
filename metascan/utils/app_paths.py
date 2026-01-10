"""Application path utilities for handling bundled and development environments."""

import os
import sys
from pathlib import Path


def is_bundled() -> bool:
    """Check if we're running in a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def get_base_path() -> Path:
    """Get the base path of the application.

    Returns the bundled app path when frozen, otherwise the project root.
    """
    if is_bundled():
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        return Path(getattr(sys, "_MEIPASS", ""))
    else:
        # Development mode - return project root (3 levels up from this file)
        return Path(__file__).parent.parent.parent


def get_data_dir() -> Path:
    """Get the data directory for the application.

    In bundled mode: Uses user's home directory under .metascan/data
    In development mode: Uses project_root/data
    """
    if is_bundled():
        # Use user's home directory for persistent data
        data_dir = Path.home() / ".metascan" / "data"
    else:
        # Development mode - use project root
        data_dir = get_base_path() / "data"

    # Ensure directory exists
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_config_path() -> Path:
    """Get the path to the config file.

    In bundled mode: First checks user's config, then falls back to bundled
    In development mode: Uses project_root/config.json, creating from config_example.json if needed
    """
    if is_bundled():
        # Check for user config first
        user_config = Path.home() / ".metascan" / "config.json"
        if user_config.exists():
            return user_config

        # Fall back to bundled config (distribution version)
        bundled_config = get_base_path() / "config_dist.json"
        if bundled_config.exists():
            # Copy bundled config to user directory on first run
            user_config.parent.mkdir(parents=True, exist_ok=True)
            import shutil

            shutil.copy2(bundled_config, user_config)
            return user_config

        return bundled_config
    else:
        # Development mode
        config_path = get_base_path() / "config.json"

        # If config.json doesn't exist but config_example.json does, copy it
        if not config_path.exists():
            example_config = get_base_path() / "config_example.json"
            if example_config.exists():
                import shutil

                shutil.copy2(example_config, config_path)
                print(f"Created config.json from config_example.json")

        return config_path


def get_icon_path() -> Path:
    """Get the path to the application icon."""
    return get_base_path() / "icon.png"


def get_thumbnail_cache_dir() -> Path:
    """Get the thumbnail cache directory.

    Uses the data directory to keep thumbnails alongside the database,
    which simplifies cross-platform compatibility (Windows/WSL).
    """
    cache_dir = get_data_dir() / "thumbnails"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir
