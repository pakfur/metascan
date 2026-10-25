"""Archived thumbnails of deleted graded images (spec 2026-10-24-pass-fail-
grades, "Deleting keeps the signal", item 4) and the delete order every
service follows: (1) archive the graded image from its live file, (2) trash
the file, (3) the DB delete with the thumbnail's path -- a failed trash drops
the archive again and keeps the row.

Invented images in an isolated temp DB; ``send2trash`` is always mocked (no
test may move a file into the real OS trash)."""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import duplicates
from backend.services.i2v_service import I2vService
from backend.services.media_service import MediaService
from backend.services.t2i_service import T2iService
from metascan.core import training_archive
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from metascan.core.training_archive import (
    ARCHIVE_MAX_PX,
    archive_dir,
    archive_thumbnail,
)

_NAME = re.compile(r"[0-9a-f]{32}\.jpg")


@pytest.fixture
def db(tmp_path: Path) -> Any:
    mgr = DatabaseManager(tmp_path / "data")
    yield mgr
    mgr.close()


def _png(path: Path, size=(1200, 800), color=(200, 30, 30)) -> Path:
    Image.new("RGB", size, color).save(path, "PNG")
    return path


def _save(db: DatabaseManager, path: Path) -> str:
    db.save_media(
        Media(
            file_path=path,
            file_size=1,
            width=8,
            height=8,
            format=path.suffix.lstrip(".") or "png",
            created_at=datetime(2026, 10, 1),
            modified_at=datetime(2026, 10, 1),
        )
    )
    return str(path)


def _archived(db: DatabaseManager) -> List[Path]:
    root = archive_dir(Path(db.db_path))
    return sorted(root.iterdir()) if root.is_dir() else []


def _tomb(db: DatabaseManager, path: str) -> Dict[str, Any]:
    [t] = [t for t in db.list_tombstones() if t["file_path"] == path]
    return t


def _jpeg_tables(quality: int) -> Any:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (90, 120, 150)).save(buf, "JPEG", quality=quality)
    buf.seek(0)
    with Image.open(buf) as img:
        return img.quantization


# ---- the archive itself ----------------------------------------------------


def test_archive_is_at_most_512_and_jpeg(tmp_path: Path) -> None:
    out_dir = tmp_path / "archive" / "thumbs"

    # A transparent RGBA landscape with a red square in the middle.
    rgba = Image.new("RGBA", (1200, 800), (0, 0, 0, 0))
    rgba.paste((220, 0, 0, 255), (500, 300, 700, 500))
    rgba.save(tmp_path / "wide.png", "PNG")
    wide = archive_thumbnail(tmp_path / "wide.png", out_dir)
    assert wide is not None and wide.parent == out_dir
    assert _NAME.fullmatch(wide.name)
    with Image.open(wide) as img:
        assert img.format == "JPEG" and img.mode == "RGB"
        assert img.size == (ARCHIVE_MAX_PX, 341)
        # Quality 85: the quantization tables are the ones q85 writes.
        assert img.quantization == _jpeg_tables(85)
        assert img.quantization != _jpeg_tables(90)
        # Transparency is flattened on white, never black.
        assert min(img.getpixel((5, 5))) >= 245
        r, g, b = img.getpixel((256, 170))
        assert r > 180 and g < 60 and b < 60

    # A portrait JPEG stored sideways (EXIF orientation 6): the archive is
    # the picture as displayed.
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (600, 1200), (10, 160, 40)).save(
        tmp_path / "turned.jpg", "JPEG", exif=exif.tobytes()
    )
    turned = archive_thumbnail(tmp_path / "turned.jpg", out_dir)
    assert turned is not None
    with Image.open(turned) as img:
        assert img.size == (ARCHIVE_MAX_PX, 256)
        assert img.getexif().get(0x0112) in (None, 1)

    # Small images are never scaled up; every archive has its own name.
    small = archive_thumbnail(_png(tmp_path / "small.png", (100, 50)), out_dir)
    assert small is not None
    with Image.open(small) as img:
        assert img.size == (100, 50)
    assert len({wide.name, turned.name, small.name}) == 3
    assert sorted(p.name for p in out_dir.iterdir()) == sorted(
        [wide.name, turned.name, small.name]
    )


def test_an_archive_failure_is_logged_and_never_raises(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    out_dir = tmp_path / "archive" / "thumbs"
    (tmp_path / "junk.png").write_bytes(b"not an image at all")
    with caplog.at_level(logging.WARNING):
        assert archive_thumbnail(tmp_path / "junk.png", out_dir) is None
        assert archive_thumbnail(tmp_path / "missing.png", out_dir) is None
        # The archive cannot be written (a file sits where its dir goes).
        (tmp_path / "blocked").write_bytes(b"")
        src = _png(tmp_path / "fine.png")
        assert archive_thumbnail(src, tmp_path / "blocked" / "thumbs") is None
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 3
    leftovers = list(out_dir.iterdir()) if out_dir.exists() else []
    assert leftovers == []  # no partial file under any name


# ---- the delete order in every service ---------------------------------------


def _media_service(db: DatabaseManager) -> MediaService:
    return MediaService(db, thumbnail_cache=None)  # type: ignore[arg-type]


async def test_thumbnail_is_made_before_trash(db: Any, tmp_path: Path) -> None:
    """Library, duplicates, T2I and I2V deletes: the archive comes from the
    live file before it is trashed, and the DB delete (which writes the
    tombstone) comes last, given the thumbnail's path."""
    order: List[str] = []
    real_archive = training_archive.archive_thumbnail

    def archive_spy(src: Path, out: Path) -> Optional[Path]:
        assert src.is_file(), "archived from the live file"
        order.append("archive")
        return real_archive(src, out)

    def trash_spy(path: str) -> None:
        assert Path(path).is_file()
        order.append("trash")
        Path(path).unlink()

    def db_spy(name: str) -> None:
        original = getattr(db, name)

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            order.append("db")
            return original(*args, **kwargs)

        setattr(db, name, wrapper)

    for name in ("delete_media", "delete_t2i_image", "delete_i2v_video"):
        db_spy(name)

    lib = _save(db, _png(tmp_path / "lib.png"))
    db.set_grade(lib, "pass")
    dup = _save(db, _png(tmp_path / "dup.png"))
    db.set_grade(dup, "fail")
    gen = _save(db, _png(tmp_path / "gen.png"))
    gen_id = db.create_t2i_image(file_path=gen, model="krea2", writer_id="base")
    db.set_grade(gen, "pass")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00video")
    _save(db, clip)
    clip_id = db.create_i2v_video(source_path=lib, file_path=str(clip))
    db.set_grade(str(clip), "pass")

    with mock.patch.object(
        training_archive, "archive_thumbnail", archive_spy
    ), mock.patch(
        "backend.services.media_service.send2trash", side_effect=trash_spy
    ), mock.patch(
        "metascan.utils.trash.send2trash", side_effect=trash_spy
    ):
        assert await _media_service(db).delete_media(lib) is True
        assert order == ["archive", "trash", "db"]
        order.clear()

        app = FastAPI()
        app.include_router(duplicates.router)
        app.dependency_overrides[duplicates._get_service] = lambda: _media_service(db)
        with TestClient(app) as client:
            resp = client.post("/api/duplicates/delete", json={"file_paths": [dup]})
        assert resp.json() == {"deleted": 1}
        assert order == ["archive", "trash", "db"]
        order.clear()

        assert await T2iService(db).delete_image(gen_id) is True
        assert order == ["archive", "trash", "db"]
        order.clear()

        # A clip is a video: nothing to archive, still trashed before the DB.
        assert await I2vService(db).delete_video(clip_id) is True
        assert order == ["trash", "db"]

    for path in (lib, dup, gen):
        thumb = _tomb(db, path)["thumb_path"]
        assert thumb and (Path(db.db_path) / thumb).is_file()
    assert _tomb(db, str(clip))["thumb_path"] is None
    assert len(_archived(db)) == 3


async def test_delete_with_unreadable_file_still_tombstones(
    db: Any, tmp_path: Path
) -> None:
    """Review Focus 4: a graded image whose file cannot be read, or is
    already gone, is still deleted and tombstoned -- without a thumbnail."""
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"\x89PNG but truncated")
    junk_path = _save(db, junk)
    gone_path = _save(db, tmp_path / "gone.png")  # never written to disk
    gen_path = _save(db, tmp_path / "gen_gone.png")
    gen_id = db.create_t2i_image(file_path=gen_path, model="krea2", writer_id="base")
    for path, grade in ((junk_path, "pass"), (gone_path, "fail"), (gen_path, "pass")):
        db.set_grade(path, grade)

    with mock.patch("backend.services.media_service.send2trash") as trash, mock.patch(
        "metascan.utils.trash.send2trash"
    ) as gen_trash:
        assert await _media_service(db).delete_media(junk_path) is True
        assert await _media_service(db).delete_media(gone_path) is True
        assert await T2iService(db).delete_image(gen_id) is True
    trash.assert_called_once_with(junk_path)  # the gone file has nothing to trash
    gen_trash.assert_not_called()

    for path, grade in ((junk_path, "pass"), (gone_path, "fail"), (gen_path, "pass")):
        tomb = _tomb(db, path)
        assert (tomb["grade"], tomb["thumb_path"]) == (grade, None)
        assert db.get_media(Path(path)) is None
    assert _tomb(db, gen_path)["lineage"]["t2i"]["id"] == gen_id
    assert _archived(db) == []


async def test_only_graded_images_are_archived(db: Any, tmp_path: Path) -> None:
    """✓ and ✗ images are archived; an ungraded (viewed) image, a ★-only
    image and a graded video never are -- no archive is even attempted."""
    paths = {
        name: _save(db, _png(tmp_path / f"{name}.png"))
        for name in ("pass", "fail", "seen", "star")
    }
    db.set_grade(paths["pass"], "pass")
    db.set_grade(paths["fail"], "fail")
    db.mark_seen(paths["seen"])
    assert db.set_favorite(Path(paths["star"]), True)
    video = tmp_path / "graded.mp4"
    video.write_bytes(b"\x00video")
    paths["video"] = _save(db, video)
    db.set_grade(paths["video"], "pass")
    clip = tmp_path / "clip.webm"
    clip.write_bytes(b"\x00clip")
    clip_path = _save(db, clip)
    clip_id = db.create_i2v_video(source_path=paths["pass"], file_path=clip_path)
    db.set_grade(clip_path, "fail")

    tried: List[str] = []
    real_archive = training_archive.archive_thumbnail

    def archive_spy(src: Path, out: Path) -> Optional[Path]:
        tried.append(src.name)
        return real_archive(src, out)

    with mock.patch.object(
        training_archive, "archive_thumbnail", archive_spy
    ), mock.patch("backend.services.media_service.send2trash"), mock.patch(
        "metascan.utils.trash.send2trash"
    ):
        for name in ("seen", "star", "video", "pass", "fail"):
            assert await _media_service(db).delete_media(paths[name]) is True
        assert await I2vService(db).delete_video(clip_id) is True

    assert sorted(tried) == ["fail.png", "pass.png"]
    archived = _archived(db)
    assert len(archived) == 2
    for name in ("pass", "fail"):
        thumb = _tomb(db, paths[name])["thumb_path"]
        assert thumb.startswith("archive/thumbs/")
        with Image.open(Path(db.db_path) / thumb) as img:
            assert img.format == "JPEG" and max(img.size) <= ARCHIVE_MAX_PX
    assert _tomb(db, paths["seen"])["thumb_path"] is None
    assert _tomb(db, paths["video"])["thumb_path"] is None
    assert _tomb(db, clip_path)["thumb_path"] is None
    # ★ alone is no judgment: nothing to tombstone.
    assert [t for t in db.list_tombstones() if t["file_path"] == paths["star"]] == []


# ---- failures keep the row ---------------------------------------------------


async def test_a_failed_trash_keeps_the_row_and_drops_the_archive(
    db: Any, tmp_path: Path
) -> None:
    lib = _save(db, _png(tmp_path / "lib.png"))
    db.set_grade(lib, "pass")
    gen = _save(db, _png(tmp_path / "gen.png"))
    gen_id = db.create_t2i_image(file_path=gen, model="krea2", writer_id="base")
    db.set_grade(gen, "fail")

    def refuse(*_args: Any) -> None:
        raise OSError("trash refused")

    with mock.patch("backend.services.media_service.send2trash", side_effect=refuse):
        with pytest.raises(OSError):
            await _media_service(db).delete_media(lib)
    # The T2I delete's trash (send2trash, else unlink) could not remove it.
    with mock.patch("backend.services.t2i_service.trash_file", side_effect=refuse):
        with pytest.raises(OSError):
            await T2iService(db).delete_image(gen_id)

    assert db.get_media(Path(lib)) is not None and db.get_media(Path(gen)) is not None
    assert db.get_t2i_image(gen_id) is not None
    assert db.get_judgment(lib).grade == "pass"
    assert db.list_tombstones() == []
    assert _archived(db) == []
    assert Path(lib).is_file() and Path(gen).is_file()


async def test_an_archive_no_tombstone_took_is_removed(db: Any, tmp_path: Path) -> None:
    """The DB delete did not happen (row gone meanwhile, or a DB error): the
    archive it was made for is not left behind."""
    lib = _save(db, _png(tmp_path / "lib.png"))
    db.set_grade(lib, "pass")
    with mock.patch("backend.services.media_service.send2trash"), mock.patch.object(
        db, "delete_media", return_value=False
    ):
        assert await _media_service(db).delete_media(lib) is False
    assert _archived(db) == []
    assert db.list_tombstones() == []


async def test_a_t2i_image_a_storyboard_still_uses_is_released_untouched(
    db: Any, tmp_path: Path
) -> None:
    """The T2I delete keeps its survival rule: a file a storyboard subject
    still references is released (row kept, file kept), so it is neither
    archived nor trashed."""
    gen = _save(db, _png(tmp_path / "gen.png"))
    gen_id = db.create_t2i_image(file_path=gen, model="krea2", writer_id="base")
    db.set_grade(gen, "pass")
    sb = db.create_storyboard(
        name="Yard", target_model="sd", architecture="t2i", base_seed=1
    )
    db.create_subject(sb, name="Ann", description="", reference_path=gen)
    with mock.patch("metascan.utils.trash.send2trash") as trash:
        assert await T2iService(db).delete_image(gen_id) is True
    trash.assert_not_called()
    assert Path(gen).is_file() and db.get_media(Path(gen)) is not None
    assert db.get_t2i_image(gen_id) is None
    assert db.list_tombstones() == [] and _archived(db) == []


async def test_a_storyboard_that_changes_its_mind_mid_delete(
    db: Any, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The T2I delete checks the purge rule before trashing; the DB delete
    decides again in its transaction. A storyboard letting go of the file in
    between: the file is trashed afterwards. One taking it: the file is
    already trashed, the row is released, its archive dropped, and a
    WARNING says so."""
    sb = db.create_storyboard(
        name="Yard", target_model="sd", architecture="t2i", base_seed=1
    )
    let_go = _save(db, _png(tmp_path / "let_go.png"))
    let_go_id = db.create_t2i_image(file_path=let_go, model="krea2", writer_id="base")
    taken = _save(db, _png(tmp_path / "taken.png"))
    taken_id = db.create_t2i_image(file_path=taken, model="krea2", writer_id="base")
    db.set_grade(taken, "pass")
    db.create_subject(sb, name="Ann", description="", reference_path=taken)

    trashed: List[str] = []
    with mock.patch(
        "metascan.utils.trash.send2trash", side_effect=lambda p: trashed.append(p)
    ):
        with mock.patch.object(db, "purgeable_paths", return_value=[]):
            assert await T2iService(db).delete_image(let_go_id) is True
        assert trashed == [let_go]  # trashed after the purge
        trashed.clear()
        with mock.patch.object(db, "purgeable_paths", return_value=[taken]):
            with caplog.at_level(logging.WARNING):
                assert await T2iService(db).delete_image(taken_id) is True
        assert trashed == [taken]
    assert any("storyboard started using it" in r.getMessage() for r in caplog.records)
    assert db.get_media(Path(taken)) is not None  # released, not purged
    # Only the purged image is tombstoned (no archive: it looked kept).
    assert [(t["file_path"], t["thumb_path"]) for t in db.list_tombstones()] == [
        (let_go, None)
    ]
    assert _archived(db) == []


async def test_a_failed_clip_trash_keeps_the_clip(db: Any, tmp_path: Path) -> None:
    source = _save(db, _png(tmp_path / "src.png"))
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00video")
    _save(db, clip)
    clip_id = db.create_i2v_video(source_path=source, file_path=str(clip))
    db.set_grade(str(clip), "fail")
    with mock.patch(
        "backend.services.i2v_service.trash_file", side_effect=OSError("busy")
    ):
        with pytest.raises(OSError):
            await I2vService(db).delete_video(clip_id)
    assert db.get_i2v_video(clip_id) is not None
    assert db.get_media(clip) is not None and db.list_tombstones() == []


def test_purgeable_paths_follow_the_purge_survival_rule(db: Any) -> None:
    for name in ("plain", "beat", "subject"):
        _save(db, Path(f"/p/{name}.png"))
    sb = db.create_storyboard(
        name="Yard", target_model="sd", architecture="t2i", base_seed=1
    )
    sc = db.create_scene(sb, name="s", sort_order=0)
    pa = db.create_panel(sc, action="a", sort_order=0)
    beat = db.create_beat(pa, action="a beat")
    db.create_beat_image(beat, file_path="/p/beat.png")
    db.create_subject(sb, name="Ann", description="", reference_path="/p/subject.png")
    assert db.purgeable_paths(
        ["/p/plain.png", "/p/beat.png", "/p/subject.png", "/p/none.png"]
    ) == ["/p/plain.png", "/p/none.png"]


def test_trash_file_falls_back_to_unlink_and_raises_when_both_fail(
    tmp_path: Path,
) -> None:
    from metascan.utils.trash import trash_file

    kept = _png(tmp_path / "kept.png", (8, 8))
    with mock.patch("metascan.utils.trash.send2trash") as trash:
        trash_file(tmp_path / "missing.png")  # nothing to remove: fine
        trash.assert_not_called()
        trash_file(kept)
        trash.assert_called_once_with(str(kept))
    with mock.patch(
        "metascan.utils.trash.send2trash", side_effect=OSError("no trash here")
    ):
        trash_file(kept)  # unlinked instead
    assert not kept.exists()
    stuck = _png(tmp_path / "stuck.png", (8, 8))
    with mock.patch(
        "metascan.utils.trash.send2trash", side_effect=OSError("no trash here")
    ), mock.patch.object(Path, "unlink", side_effect=OSError("busy")):
        with pytest.raises(OSError):
            trash_file(stuck)
    assert stuck.exists()
