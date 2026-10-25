"""Archived thumbnails of deleted graded images (spec
docs/superpowers/specs/2026-10-24-pass-fail-grades-design.md, "Deleting
keeps the signal", items 4 and 7).

Deleting a graded image (✓ pass or ✗ fail; images only, never videos) keeps
a small JPEG of it in ``<data dir>/archive/thumbs/``, made from the live
file BEFORE the file goes to the trash. Its path relative to the data dir
(``archive/thumbs/<uuid4 hex>.jpg``) is stored on the image's tombstone
(``media_tombstones.thumb_path``): the hindsight-briefs job can brief a
deleted ✓ image from it, and a deleted image's CLIP vector can be computed
again from it for a new CLIP model. The longest side is at most
``ARCHIVE_MAX_PX`` -- the size ``EmbeddingManager`` downsizes to before
CLIP -- so a vector computed from the archive is close to the original's.
About 50 KB each and uncapped: it is training data, like the tombstones.
The data dir is the database's directory (``DatabaseManager.db_path``).

Every delete that removes a graded image's file (library, duplicates, T2I,
I2V) runs :func:`delete_keeping_signal`:

1. archive the graded images among the files (from the live files);
2. trash the files -- if that fails, the archives are removed again and the
   rows are kept (the error propagates);
3. the DB delete, given ``{posix path: thumb path}``, which writes the
   tombstones; an archive no tombstone took (the row was already gone, the
   delete failed) is removed afterwards.

A purging storyboard delete or replace runs :func:`purge_keeping_signal`
instead, which keeps the storyboard's own order: the graded images among
the beat images it may delete are archived, the DB purge runs with their
thumb paths (its survival rule alone decides what is deleted), archives it
did not take are removed, and only then are the purged files trashed.

A failed archive (an unreadable or missing file, a full disk) is logged and
the delete goes on: the tombstone simply has no thumbnail.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path, PurePosixPath
from typing import (
    TYPE_CHECKING,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
    Union,
)

from PIL import Image, ImageOps

from metascan.utils.heic import register_heif_opener
from metascan.utils.path_utils import to_native_path

if TYPE_CHECKING:
    from metascan.core.database_sqlite import DatabaseManager

register_heif_opener()

logger = logging.getLogger(__name__)

ARCHIVE_MAX_PX = 512
ARCHIVE_JPEG_QUALITY = 85
# Where the archive lives, relative to the data dir (stored paths start so).
ARCHIVE_SUBDIR = PurePosixPath("archive/thumbs")

_T = TypeVar("_T")


def archive_dir(data_dir: Path) -> Path:
    """``<data dir>/archive/thumbs``."""
    return data_dir.joinpath(*ARCHIVE_SUBDIR.parts)


def _flatten(img: Image.Image) -> Image.Image:
    """An RGB (or grayscale) copy: transparency is composited on white."""
    if img.mode in ("RGBA", "LA", "PA", "P") or "transparency" in img.info:
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat
    if img.mode not in ("RGB", "L"):
        return img.convert("RGB")
    return img


def archive_thumbnail(src: Path, archive_dir: Path) -> Optional[Path]:
    """Save a JPEG of the image ``src`` -- EXIF orientation applied,
    transparency flattened on white, longest side at most
    ``ARCHIVE_MAX_PX`` (never scaled up), quality 85 -- as
    ``archive_dir/<uuid4 hex>.jpg`` and return its path. None when ``src``
    cannot be read or the archive cannot be written: logged, never raises,
    and no partial file is left behind."""
    partial: Optional[Path] = None
    try:
        with Image.open(src) as opened:
            # A JPEG decodes at a reduced scale (no effect on other formats).
            opened.draft(None, (ARCHIVE_MAX_PX * 2, ARCHIVE_MAX_PX * 2))
            img = _flatten(ImageOps.exif_transpose(opened))
            img.thumbnail((ARCHIVE_MAX_PX, ARCHIVE_MAX_PX), Image.Resampling.LANCZOS)
            archive_dir.mkdir(parents=True, exist_ok=True)
            name = uuid.uuid4().hex
            partial = archive_dir / f".{name}.jpg.partial"
            img.save(partial, "JPEG", quality=ARCHIVE_JPEG_QUALITY, optimize=True)
            target = archive_dir / f"{name}.jpg"
            os.replace(partial, target)
            return target
    except Exception as exc:
        logger.warning(
            "Could not archive a thumbnail of %s (%s): %s",
            src,
            type(exc).__name__,
            exc,
        )
        if partial is not None:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                logger.debug("Could not remove %s", partial, exc_info=True)
        return None


def archived_file(data_dir: Path, thumb_path: Optional[str]) -> Optional[Path]:
    """The archive file a tombstone's ``thumb_path`` names, when it is one
    (a plain file name inside the archive dir; anything else -- blank, a
    sub-path, ``..`` -- is None). Existence is not checked."""
    if not isinstance(thumb_path, str) or not thumb_path:
        return None
    rel = PurePosixPath(thumb_path)
    if rel.parent != ARCHIVE_SUBDIR or rel.name in ("", ".", ".."):
        return None
    return archive_dir(data_dir) / rel.name


def resolve_thumb(data_dir: Path, thumb_path: Optional[str]) -> Optional[Path]:
    """Like :func:`archived_file`, and only when the file is there."""
    path = archived_file(data_dir, thumb_path)
    return path if path is not None and path.is_file() else None


def discard_archives(data_dir: Path, thumb_paths: Iterable[str]) -> None:
    """Remove archived thumbnails no tombstone points at (best effort)."""
    for thumb_path in thumb_paths:
        path = archived_file(data_dir, thumb_path)
        if path is None:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove the unused archive %s", path)


def archive_graded_images(
    db: "DatabaseManager", paths: Iterable[Union[str, Path]]
) -> Dict[str, str]:
    """Step 1 of a delete: archive every graded image among ``paths`` from
    its live file. ``{stored posix path: thumb path relative to the data
    dir}`` for the ones that could be archived; an image that could not
    is left out (logged), as is everything when the grades cannot be read."""
    try:
        graded = db.graded_image_paths(list(paths))
    except Exception:
        logger.exception("Could not read grades before a delete; no archive")
        return {}
    data_dir = Path(db.db_path)
    target = archive_dir(data_dir)
    thumbs: Dict[str, str] = {}
    for posix in graded:
        made = archive_thumbnail(Path(to_native_path(posix)), target)
        if made is not None:
            thumbs[posix] = (ARCHIVE_SUBDIR / made.name).as_posix()
    return thumbs


def _drop_unused(db: "DatabaseManager", thumbs: Mapping[str, str]) -> None:
    """Remove the archives of ``thumbs`` that no tombstone took."""
    if not thumbs:
        return
    try:
        used = db.tombstone_thumbs_in_use(list(thumbs.values()))
    except Exception:
        logger.exception("Could not check which archived thumbnails are in use")
        return
    discard_archives(Path(db.db_path), [t for t in thumbs.values() if t not in used])


def delete_keeping_signal(
    db: "DatabaseManager",
    files: Sequence[Union[str, Path]],
    trash: Callable[[Path], None],
    delete_rows: Callable[[Dict[str, str]], _T],
) -> _T:
    """One delete in the order that keeps a graded image's training signal
    (see the module docstring). ``files`` are what this delete removes from
    disk; ``trash(path)`` must raise when a file could not be removed (a
    missing file is fine). ``delete_rows(thumbs)`` is the DB delete, given
    ``{posix path: thumb path}``; its result is returned. Synchronous: the
    services run it in a worker thread."""
    thumbs = archive_graded_images(db, files)
    try:
        for path in files:
            trash(Path(path))
    except Exception:
        discard_archives(Path(db.db_path), thumbs.values())
        raise
    try:
        return delete_rows(thumbs)
    finally:
        _drop_unused(db, thumbs)


def purge_keeping_signal(
    db: "DatabaseManager",
    scope: str,
    scope_id: int,
    purge: bool,
    run: Callable[[Optional[Dict[str, str]]], _T],
) -> _T:
    """A storyboard delete or replace (``run(thumbs)``, the DB operation)
    that purges its generated media when ``purge`` (else it just runs).
    The graded images among the beat images it may delete
    (``DatabaseManager.purge_image_candidates(scope, scope_id)``) are
    archived from their live files first -- the caller trashes the purged
    files only after ``run`` returns, as before. ``run``'s
    ``_purge_media_rows`` alone decides what is deleted (the survival rule
    is never repeated here) and keeps the thumbnails of those; an archive no
    tombstone took (a file a surviving beat, subject or scene still uses is
    released, not deleted) is removed again. A failure to list or archive
    is logged and never blocks the operation. Synchronous: callers run it
    in a worker thread."""
    if not purge:
        return run(None)
    try:
        candidates = db.purge_image_candidates(scope, scope_id)
    except Exception:
        logger.exception("Could not list a storyboard purge's images; no archive")
        candidates = []
    thumbs = archive_graded_images(db, candidates)
    try:
        return run(thumbs)
    finally:
        _drop_unused(db, thumbs)


def delete_generated(
    db: "DatabaseManager",
    file_path: str,
    trash: Callable[[Path], None],
    delete_rows: Callable[[Dict[str, str]], Tuple[bool, List[str]]],
) -> bool:
    """A T2I image / I2V clip delete through :func:`delete_keeping_signal`.
    Its DB delete purges the media row only when nothing in a storyboard
    still uses the file (``_purge_media_rows``); otherwise the file is
    released into the library, untouched. So only the files the purge will
    delete (``purgeable_paths``) are archived and trashed, before
    ``delete_rows(thumbs)`` -- ``(deleted, purged native paths)``. Should a
    storyboard let go of the file in the moment between, it is trashed
    afterwards; should one take it, the file is already in the trash and a
    WARNING says so. True when the row was deleted."""
    doomed = db.purgeable_paths([file_path])
    deleted, purged = delete_keeping_signal(db, doomed, trash, delete_rows)
    for path in purged:
        if path not in doomed:
            try:
                trash(Path(path))
            except OSError:
                logger.warning("Could not remove the purged file %s", path)
    if deleted and any(path not in purged for path in doomed):
        logger.warning(
            "A file was trashed while a storyboard started using it: %s",
            file_path,
        )
    return bool(deleted)
