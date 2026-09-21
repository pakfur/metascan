"""Tests for the shared log-file policy (metascan/utils/log_files.py).

Every log file the application writes is capped: a 10 MB live file plus
the three most recent rollovers. The metadata extraction report once grew
to 4.25 GB during a single full import because it was a bare
``open(path, "a")`` with no bound at all.
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

import pytest

from metascan.utils import log_files
from metascan.utils.log_files import (
    LOG_BACKUP_COUNT,
    LOG_MAX_BYTES,
    close_file_loggers,
    get_file_logger,
    rotating_file_handler,
)
from metascan.utils.metadata_logger import MetadataParsingLogger

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _release_handlers():
    yield
    close_file_loggers()


def _family(path: Path) -> list:
    return sorted(p.name for p in path.parent.glob(path.name + "*"))


def test_policy_is_ten_megabytes_live_and_three_rollovers():
    assert LOG_MAX_BYTES == 10 * 1024 * 1024
    assert LOG_BACKUP_COUNT == 3


def test_handler_defaults_to_the_policy(tmp_path):
    handler = rotating_file_handler(tmp_path / "a.log")
    try:
        assert handler.maxBytes == LOG_MAX_BYTES
        assert handler.backupCount == LOG_BACKUP_COUNT
    finally:
        handler.close()


def test_rollover_keeps_only_the_three_most_recent_files(tmp_path):
    path = tmp_path / "a.log"
    handler = rotating_file_handler(path, max_bytes=200)
    logger = logging.getLogger("test_log_files.rollover")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        for i in range(200):
            logger.info("line %04d %s", i, "x" * 40)
    finally:
        logger.removeHandler(handler)
        handler.close()

    assert _family(path) == ["a.log", "a.log.1", "a.log.2", "a.log.3"]
    assert all(p.stat().st_size <= 200 for p in tmp_path.iterdir())
    assert "line 0199" in path.read_text()  # newest lines are in the live file


def test_header_is_rewritten_into_every_rolled_file(tmp_path):
    path = tmp_path / "rows.csv"
    logger = get_file_logger(path, header="a,b", max_bytes=120)
    for i in range(40):
        logger.info("%d,%s", i, "y" * 20)
    close_file_loggers()

    family = [tmp_path / name for name in _family(path)]
    assert len(family) == 4
    assert all(p.read_text().splitlines()[0] == "a,b" for p in family)


def test_a_log_file_deleted_underneath_the_handler_is_recreated(tmp_path):
    path = tmp_path / "a.log"
    logger = get_file_logger(path)
    logger.info("before")
    path.unlink()  # e.g. cleared by hand while the server is running
    logger.info("after")
    close_file_loggers()

    assert path.read_text().strip() == "after"


def test_file_loggers_are_shared_per_path_and_do_not_propagate(tmp_path):
    first = get_file_logger(tmp_path / "a.log")
    second = get_file_logger(tmp_path / "a.log")
    assert first is second
    assert len(first.handlers) == 1
    assert first.propagate is False


def test_metadata_report_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(log_files, "LOG_MAX_BYTES", 4096)
    parsing_logger = MetadataParsingLogger(tmp_path)
    big = {"source": "ComfyUI", "raw_metadata": {"prompt": "z" * 1500}}
    for i in range(60):
        parsing_logger.log_extraction_attempt(
            Path(f"/media/img_{i}.png"), "ComfyUIExtractor", True, metadata=big
        )
    close_file_loggers()

    report = tmp_path / "metadata_extraction_report.txt"
    assert len(_family(report)) == 1 + LOG_BACKUP_COUNT
    total = sum((tmp_path / n).stat().st_size for n in _family(report))
    assert total <= (1 + LOG_BACKUP_COUNT) * 4096
    assert "img_59.png" in report.read_text()


def test_metadata_error_csv_rolls_over_and_stays_parseable(tmp_path, monkeypatch):
    monkeypatch.setattr(log_files, "LOG_MAX_BYTES", 2048)
    parsing_logger = MetadataParsingLogger(tmp_path)
    for i in range(40):
        parsing_logger.log_extraction_attempt(
            Path(f"/media/bad_{i}.png"),
            "ComfyUIExtractor",
            False,
            error=ValueError(f"boom {i}, with a comma\nand a newline"),
        )

    errors = parsing_logger.get_all_errors()
    assert errors, "live CSV must still parse after rollovers"
    assert errors[-1]["file_name"] == "bad_39.png"
    assert errors[-1]["error_type"] == "ValueError"
    assert "and a newline" in errors[-1]["error_message"]

    csv_path = tmp_path / "metadata_extraction_errors.csv"
    assert len(_family(csv_path)) == 1 + LOG_BACKUP_COUNT
    for name in _family(csv_path):
        with open(tmp_path / name, newline="", encoding="utf-8") as fh:
            assert next(csv.reader(fh))[0] == "timestamp"


def test_clear_logs_removes_rollovers_and_logging_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(log_files, "LOG_MAX_BYTES", 2048)
    parsing_logger = MetadataParsingLogger(tmp_path)
    for i in range(40):
        parsing_logger.log_extraction_attempt(
            Path(f"/media/a_{i}.png"), "X", False, error=ValueError("boom " * 20)
        )

    parsing_logger.clear_logs()
    assert parsing_logger.get_all_errors() == []
    assert not list(tmp_path.glob("*.1"))

    parsing_logger.log_extraction_attempt(
        Path("/media/after.png"), "X", False, error=ValueError("again")
    )
    assert [e["file_name"] for e in parsing_logger.get_all_errors()] == ["after.png"]


def test_no_log_file_bypasses_the_shared_policy():
    """Every rotating handler must come from log_files.py, so the 10 MB /
    3-file policy cannot drift per call site (upscale_worker kept 5)."""
    offenders = []
    for root in ("metascan", "backend"):
        for path in (REPO_ROOT / root).rglob("*.py"):
            if path.name == "log_files.py":
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"\b(Rotating|TimedRotating)?FileHandler\(", text):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


@pytest.fixture
def clean_root():
    root = logging.getLogger()
    before = list(root.handlers)
    yield root
    for handler in list(root.handlers):
        if handler not in before:
            root.removeHandler(handler)
            handler.close()


def test_server_log_mirrors_the_root_logger_once(tmp_path, monkeypatch, clean_root):
    monkeypatch.setenv("METASCAN_LOG_FILE", "1")
    before = len(clean_root.handlers)

    first = log_files.install_server_log(tmp_path)
    second = log_files.install_server_log(tmp_path)  # uvicorn reload / re-entry

    assert first == second == tmp_path / "server.log"
    assert len(clean_root.handlers) == before + 1
    logging.getLogger("metascan.core.comfy_client").warning("socket dropped")
    assert "socket dropped" in (tmp_path / "server.log").read_text()


def test_server_log_can_be_disabled(tmp_path, monkeypatch, clean_root):
    monkeypatch.setenv("METASCAN_LOG_FILE", "0")
    before = len(clean_root.handlers)

    assert log_files.install_server_log(tmp_path) is None
    assert len(clean_root.handlers) == before
    assert not (tmp_path / "server.log").exists()
