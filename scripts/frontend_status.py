"""What the frontend needs before the backend can serve it (run.bat).

Exit code: 0 nothing, 1 rebuild, 2 ``npm install`` then rebuild.
``--mark-installed`` records the lockfile after a successful ``npm install``.

The build is stale when any source file is newer than ``dist/index.html``,
which every build rewrites; git stamps the files a pull changes. Dependencies
are compared by the lockfile's hash, not its time: ``npm install`` leaves
``node_modules`` alone when nothing changed, so a touched lockfile would ask
for an install on every start.

Standard library only.
"""

import hashlib
import sys
from pathlib import Path
from typing import Iterable, Optional

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
STAMP_NAME = ".metascan-lock-sha256"
SOURCE_DIRS = ("src", "public")
SOURCE_PATTERNS = (
    "index.html",
    "package.json",
    "package-lock.json",
    "vite.config.ts",
    "tsconfig*.json",
)

UP_TO_DATE, REBUILD, INSTALL = 0, 1, 2


def _lock_hash(frontend: Path) -> str:
    return hashlib.sha256((frontend / "package-lock.json").read_bytes()).hexdigest()


def _sources(frontend: Path) -> Iterable[Path]:
    for name in SOURCE_DIRS:
        yield from (p for p in (frontend / name).rglob("*") if p.is_file())
    for pattern in SOURCE_PATTERNS:
        yield from frontend.glob(pattern)


def needs_install(frontend: Path) -> bool:
    stamp = frontend / "node_modules" / STAMP_NAME
    try:
        return stamp.read_text(encoding="ascii").strip() != _lock_hash(frontend)
    except OSError:
        return True


def newer_source(frontend: Path) -> Optional[Path]:
    """A source file newer than the build (None: the build is current). With
    no build at all, the frontend folder itself."""
    built = frontend / "dist" / "index.html"
    if not built.exists():
        return frontend
    built_at = built.stat().st_mtime
    return next((p for p in _sources(frontend) if p.stat().st_mtime > built_at), None)


def status(frontend: Path) -> int:
    if needs_install(frontend):
        return INSTALL
    return UP_TO_DATE if newer_source(frontend) is None else REBUILD


def mark_installed(frontend: Path) -> None:
    (frontend / "node_modules" / STAMP_NAME).write_text(
        _lock_hash(frontend), encoding="ascii"
    )


def main(argv: Iterable[str]) -> int:
    if "--mark-installed" in argv:
        mark_installed(FRONTEND)
        return UP_TO_DATE
    return status(FRONTEND)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
