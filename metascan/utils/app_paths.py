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

    Checks DATA_DIR environment variable first, then:
    In bundled mode: Uses user's home directory under .metascan/data
    In development mode: Uses project_root/data
    """
    # Check for environment variable override
    if env_data_dir := os.environ.get("DATA_DIR"):
        data_dir = Path(env_data_dir)
    elif is_bundled():
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

    Uses data directory for config storage.
    Falls back to config_example.json if config.json doesn't exist.
    """
    # Always use data directory for config
    config_path = get_data_dir() / "config.json"

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

    Always uses user's home directory for cache.
    """
    cache_dir = Path.home() / ".metascan" / "thumbnails"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_models_dir() -> Path:
    """Get the models directory for AI models.

    Checks MODELS_DIR environment variable first, then uses data/models.
    """
    # Check for environment variable override
    if env_models_dir := os.environ.get("MODELS_DIR"):
        models_dir = Path(env_models_dir)
    else:
        # Default to data directory
        models_dir = get_data_dir() / "models"

    # Ensure directory exists
    models_dir.mkdir(parents=True, exist_ok=True)
    return models_dir


def get_database_path() -> Path:
    """Get the path to the SQLite database file."""
    return get_data_dir() / "metascan.db"


def get_queue_dir() -> Path:
    """Get the queue directory for processing queues."""
    queue_dir = get_data_dir() / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    return queue_dir
