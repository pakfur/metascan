"""Entry point for running the metascan FastAPI backend server."""

import sys
from pathlib import Path

# Ensure project root is on the Python path
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import uvicorn

from backend.config import get_server_config


def main():
    config = get_server_config()
    uvicorn.run(
        "backend.main:app",
        host=config.host,
        port=config.port,
        reload=True,
        reload_dirs=[str(project_root / "backend")],
    )


if __name__ == "__main__":
    main()
