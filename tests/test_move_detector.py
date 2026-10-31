"""Move detection (spec 11.1): the pure rules, the database candidate and the
poller's case-only rename."""

import os
from datetime import datetime
from pathlib import Path

from metascan.core import folder_poller as fp
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from metascan.core.move_detector import Candidate, match_moves, parse_mtime
from metascan.utils.fs_compat import path_key


def C(path, size=100, mtime=1000.0, phash=None):
    return Candidate(path, size, mtime, phash)


def test_same_size_and_mtime_single_candidate_is_a_move():
    assert match_moves([C("/n")], [C("/o")]) == [("/o", "/n")]


def test_two_candidates_is_not_a_move():  # Review Focus 4
    assert match_moves([C("/n")], [C("/o1"), C("/o2")]) == []
    # Nor is one vanished file that two new files could each be.
    assert match_moves([C("/n1"), C("/n2")], [C("/o")]) == []


def test_phash_mismatch_is_not_a_move():
    assert match_moves([C("/n", phash="aa")], [C("/o", phash="bb")]) == []


def test_different_mtime_needs_phash_and_one_candidate():
    new = C("/n", mtime=5000.0, phash="aa")
    assert match_moves([new], [C("/o", phash="aa")]) == [("/o", "/n")]
    assert match_moves([new], [C("/o", phash="bb")]) == []
    assert match_moves([new], [C("/o")]) == []  # no phash: rule 1 only
    both = [C("/o1", phash="aa"), C("/o2", phash="aa")]
    assert match_moves([new], both) == []
    assert match_moves([C("/n", size=7, phash="aa")], [C("/o", phash="aa")]) == []


def test_mtime_within_2s_counts():
    assert match_moves([C("/n", mtime=1002.0)], [C("/o")]) == [("/o", "/n")]
    assert match_moves([C("/n", mtime=1002.5)], [C("/o")]) == []


def test_epoch_and_iso_modified_at_both_read(tmp_path):
    assert parse_mtime(1775691760.0) == 1775691760.0
    assert parse_mtime("1775691760.0") == 1775691760.0
    iso = datetime(2026, 4, 9, 1, 2, 3)
    assert parse_mtime(iso.isoformat()) == iso.timestamp()
    assert parse_mtime("2026-04-09 01:02:03") == iso.timestamp()
    assert parse_mtime("not a date") is None and parse_mtime(None) is None

    db = DatabaseManager(tmp_path / "db")
    media = Media(
        file_path=Path("/lib/a.png"),
        file_size=321,
        width=1,
        height=1,
        format="png",
        created_at=datetime.now(),
        modified_at=iso,
    )
    db.save_media(media)
    db.save_media_hash(Path("/lib/a.png"), "abcd")
    got = db.candidate_for("/lib/a.png")
    assert got == Candidate("/lib/a.png", 321, iso.timestamp(), "abcd")
    # A row back-filled from the old blob holds a stringified epoch.
    with db._get_connection() as conn:
        conn.execute("UPDATE media SET modified_at = '1775691760.0'")
        conn.commit()
    assert db.candidate_for("/lib/a.png").mtime == 1775691760.0
    assert db.candidate_for("/lib/none.png") is None


def test_no_phash_matches_by_rule_one_only():
    assert match_moves([C("/n", phash=None)], [C("/o", phash="aa")]) == [("/o", "/n")]
    assert match_moves([C("/n", mtime=9999.0)], [C("/o", phash="aa")]) == []


def test_case_only_rename_keeps_the_item_on_windows_and_macos(tmp_path, monkeypatch):
    # Review Focus 3: both spellings key equal, so the poller sees neither a new
    # nor a vanished file; it reports the respelling for the caller to apply.
    monkeypatch.setattr(fp, "path_key", lambda p: path_key(p, "win32"))
    folder = tmp_path / "lib"
    folder.mkdir()
    (folder / "Pic.png").write_bytes(b"x" * 10)
    poller = fp.FolderPoller(
        [fp.FolderSpec(str(folder), True)],
        lambda p: fp.PROBE_PRESENT,
        lambda p: p.endswith(".png"),
    )
    poller.seed([str(folder / "Pic.png")])
    assert poller.poll().respelled == []
    os.rename(folder / "Pic.png", folder / "pic.png")
    res = poller.poll()
    assert res.respelled == [(str(folder / "Pic.png"), str(folder / "pic.png"))]
    assert res.vanished == [] and res.settled_new == []
    assert poller.poll().respelled == []  # once


def test_unicode_normalisation_alone_is_not_a_respelling(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "path_key", lambda p: path_key(p, "darwin"))
    folder = tmp_path / "lib"
    folder.mkdir()
    nfd = "é.png"
    (folder / nfd).write_bytes(b"x")
    poller = fp.FolderPoller(
        [fp.FolderSpec(str(folder), True)],
        lambda p: fp.PROBE_PRESENT,
        lambda p: True,
    )
    poller.seed([str(folder / "é.png")])
    assert poller.poll().respelled == []
