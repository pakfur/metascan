"""run.bat's decision: install, rebuild, or start."""

import os
from pathlib import Path

import pytest

from scripts import frontend_status as fs


def _age(path: Path, seconds: int) -> None:
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime - seconds))


@pytest.fixture
def frontend(tmp_path):
    """Installed and built, every source older than the build."""
    root = tmp_path / "frontend"
    (root / "src" / "components").mkdir(parents=True)
    (root / "public").mkdir()
    (root / "dist").mkdir()
    (root / "node_modules").mkdir()
    for name in (
        "src/components/A.vue",
        "public/icon.svg",
        "index.html",
        "package.json",
        "package-lock.json",
        "vite.config.ts",
        "tsconfig.app.json",
    ):
        (root / name).write_text("x", encoding="utf-8")
        _age(root / name, 100)
    (root / "dist" / "index.html").write_text("built", encoding="utf-8")
    fs.mark_installed(root)
    return root


def test_an_installed_current_build_needs_nothing(frontend):
    assert fs.status(frontend) == fs.UP_TO_DATE


@pytest.mark.parametrize(
    "name",
    [
        "src/components/A.vue",
        "public/icon.svg",
        "index.html",
        "package.json",
        "vite.config.ts",
        "tsconfig.app.json",
    ],
)
def test_a_newer_source_needs_a_rebuild(frontend, name):
    os.utime(frontend / name)
    (frontend / "dist" / "index.html").write_text("built", encoding="utf-8")
    _age(frontend / "dist" / "index.html", 10)
    assert fs.status(frontend) == fs.REBUILD


def test_a_new_source_file_needs_a_rebuild(frontend):
    _age(frontend / "dist" / "index.html", 10)
    (frontend / "src" / "New.vue").write_text("x", encoding="utf-8")
    assert fs.status(frontend) == fs.REBUILD


def test_no_build_needs_a_rebuild(frontend):
    (frontend / "dist" / "index.html").unlink()
    assert fs.status(frontend) == fs.REBUILD


def test_files_outside_the_sources_never_count(frontend):
    _age(frontend / "dist" / "index.html", 10)
    (frontend / "README.md").write_text("x", encoding="utf-8")
    (frontend / "node_modules" / "pkg.js").write_text("x", encoding="utf-8")
    (frontend / "dist" / "asset.js").write_text("x", encoding="utf-8")
    assert fs.status(frontend) == fs.UP_TO_DATE


def test_never_installed_needs_an_install(frontend):
    (frontend / "node_modules" / fs.STAMP_NAME).unlink()
    assert fs.status(frontend) == fs.INSTALL


def test_no_node_modules_needs_an_install(frontend):
    (frontend / "node_modules" / fs.STAMP_NAME).unlink()
    (frontend / "node_modules").rmdir()
    assert fs.status(frontend) == fs.INSTALL


def test_a_changed_lockfile_needs_an_install(frontend):
    (frontend / "package-lock.json").write_text("y", encoding="utf-8")
    assert fs.status(frontend) == fs.INSTALL


def test_a_lockfile_touched_but_unchanged_needs_no_install(frontend):
    """The reason for the hash: npm leaves node_modules alone in this case,
    so comparing times would install on every start."""
    os.utime(frontend / "package-lock.json")
    assert fs.needs_install(frontend) is False


def test_an_install_is_recorded_and_ends_the_request(frontend):
    (frontend / "package-lock.json").write_text("y", encoding="utf-8")
    fs.mark_installed(frontend)
    assert fs.needs_install(frontend) is False
