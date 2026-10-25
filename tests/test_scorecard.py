"""Writer-adapter scorecard (taste learning, task 9).

A scorecard renders every developer-written brief twice -- once with the
base writer, once with the adapter, same seed -- tagged ``scorecard_id``.
The owner grades the images blind (✓ pass / ✗ fail; ★ is no grade); the
adapter goes ``untested -> ready`` once 30 pairs are judged, deleted
images included. The fakes are the T2I runner tests' (FakeVlm, FakeComfy)
through the real runner; captions and prompts are synthetic or the
committed briefs file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config import get_t2i_config
from backend.services import scorecard as sc
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.gpu_guard import set_gpu_reservation
from metascan.core.media import Media
from metascan.core.prompt_store import get_prompt_store
from metascan.core.t2i_characters import has_tokens
from metascan.core.t2i_runner import T2iRequestError
from metascan.core.t2i_wildcards import caption_reject_reason
from metascan.core.writer_adapters import adapter_writer_id
from metascan.core.writer_dataset import build_examples
from tests.test_writer_adapter_serving import BASE, AdapterCase


def _media(path: str, prompt: Optional[str] = None) -> Media:
    return Media(
        file_path=Path(path),
        file_size=1,
        width=8,
        height=8,
        format="png",
        created_at=datetime.now(),
        modified_at=datetime.now(),
        prompt=prompt,
        negative_prompt="SYNTH negative" if prompt else None,
    )


def _rows(db: Any) -> List[Dict[str, Any]]:
    with db.lock, db._get_connection() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT file_path, batch_id, caption, seed, writer_id, writer_model, "
                "variant_index, scorecard_id FROM t2i_images ORDER BY id"
            )
        ]


# ---- the briefs file -----------------------------------------------------------


def test_every_committed_brief_passes_the_caption_screen() -> None:
    briefs, rejected = sc.load_briefs(sc.SCORECARD_BRIEFS_PATH)
    assert rejected == 0
    assert len(briefs) == 40
    assert len(set(briefs)) == 40
    for brief in briefs:
        assert caption_reject_reason(brief) is None
        assert not has_tokens(brief)
        assert "(" not in brief and ")" not in brief


def test_a_brief_that_fails_the_screen_is_dropped(tmp_path: Path) -> None:
    path = tmp_path / "briefs.txt"
    path.write_text(
        "# comment\n\nA quiet harbour at dawn\n"
        "A teen on a skateboard\nA lamp (bright) on a desk\n"
        "__ALICE__ by a window\nA quiet harbour at dawn\n",
        encoding="utf-8",
    )
    briefs, rejected = sc.load_briefs(path)
    assert briefs == ["A quiet harbour at dawn"]
    assert rejected == 3  # the minor term, the parentheses, the token


def test_a_missing_briefs_file_gives_no_briefs(tmp_path: Path) -> None:
    assert sc.load_briefs(tmp_path / "nope.txt") == ([], 0)


def test_seeds_and_order_are_deterministic_per_scorecard_and_brief() -> None:
    seeds = [sc.brief_seed("card-a", i) for i in range(40)]
    assert seeds == [sc.brief_seed("card-a", i) for i in range(40)]
    assert seeds != [sc.brief_seed("card-b", i) for i in range(40)]
    assert len(set(seeds)) == 40
    order = [sc.adapter_first("card-a", i) for i in range(40)]
    assert order == [sc.adapter_first("card-a", i) for i in range(40)]
    # Position must not give the writer away: both orders occur.
    assert any(order) and not all(order)


# ---- results math ------------------------------------------------------------------


def _row(
    batch: str,
    writer: str,
    *,
    grade: Optional[str] = None,
    score: Optional[float] = None,
) -> Dict[str, Any]:
    return {"batch_id": batch, "writer_id": writer, "grade": grade, "score": score}


# Every key of one adapter's result (compute_results).
RESULT_KEYS = {
    "adapter_id",
    "n_pairs",
    "n_judged",
    "pass_base",
    "pass_adapter",
    "pass_rate_base",
    "pass_rate_adapter",
    "mean_score_base",
    "mean_score_adapter",
}


def test_results_math() -> None:
    a = adapter_writer_id(7)
    rows = [
        # pair 1: both graded, the adapter's passed
        _row("b1", "base", grade="fail", score=0.2),
        _row("b1", a, grade="pass", score=0.8),
        # pair 2: both graded, the base's passed
        _row("b2", "base", grade="pass", score=0.6),
        _row("b2", a, grade="fail", score=0.4),
        # pair 3: the adapter's image has no grade -> not a judged pair
        _row("b3", "base", grade="pass"),
        _row("b3", a),
        # pair 5: neither image has a grade
        _row("b5", "base"),
        _row("b5", a),
        # an unpaired image (its partner never rendered)
        _row("b4", a, grade="pass", score=1.0),
        # another adapter's image never counts
        _row("b1", adapter_writer_id(8), grade="pass", score=0.0),
    ]
    out = sc.compute_results(rows, 7)
    assert out["adapter_id"] == 7
    assert out["n_pairs"] == 4
    assert out["n_judged"] == 2
    assert out["pass_rate_base"] == pytest.approx(0.5)
    assert out["pass_rate_adapter"] == pytest.approx(0.5)
    assert out["mean_score_base"] == pytest.approx(0.4)
    assert out["mean_score_adapter"] == pytest.approx((0.8 + 0.4 + 1.0) / 3)


def test_a_brief_rendered_twice_counts_once_with_its_latest_batch() -> None:
    # A resumed scorecard re-rendered brief 0 while its first batch was
    # still queued; both batches ingested. One pair per (scorecard, brief):
    # the most recent batch holding both images.
    a = adapter_writer_id(7)

    def row(batch: str, writer: str, index: Optional[int], **kw: Any) -> Any:
        return {**_row(batch, writer, **kw), "scorecard_id": "c", "brief_index": index}

    rows = [
        row("old", "base", 0, grade="pass"),
        row("old", a, 0, grade="pass"),
        row("b1", "base", 1, grade="fail"),
        row("b1", a, 1, grade="pass"),
        row("new", "base", 0, grade="fail"),
        row("new", a, 0, grade="fail"),
        # A later batch of brief 1 with only one image: the complete one stays.
        row("half", a, 1, grade="pass"),
        # Another scorecard's brief 0 is its own pair.
        {**row("other", "base", 0, grade="pass"), "scorecard_id": "d"},
        {**row("other", a, 0, grade="pass"), "scorecard_id": "d"},
    ]
    out = sc.compute_results(rows, 7)
    assert (out["n_pairs"], out["n_judged"]) == (3, 3)
    assert (out["pass_base"], out["pass_adapter"]) == (1, 2)
    assert sc._paired_briefs(rows, 7) == {0, 1}


def test_results_with_nothing_judged_are_null() -> None:
    out = sc.compute_results([_row("b1", "base"), _row("b1", "adapter:7")], 7)
    assert out["n_pairs"] == 1 and out["n_judged"] == 0
    assert out["pass_rate_base"] is None and out["pass_rate_adapter"] is None
    assert out["mean_score_base"] is None and out["mean_score_adapter"] is None


# ---- DB-level fixtures for blinding and promotion -----------------------------------


class _Lib:
    """A temp DB with an untested adapter and hand-made scorecard images."""

    def __init__(self, root: Path) -> None:
        self.db = DatabaseManager(root / "db")
        self.root = root
        self.adapter = self.db.create_writer_adapter(
            name="taste-all-v1",
            scope="all",
            kind="t2i",
            base_model=BASE,
            status="untested",
        )
        self.card = "card-1"
        self.db.create_scorecard(
            self.card, self.adapter, "krea2", preset_id=None, total=40
        )
        self.n = 0

    def image(
        self,
        writer: str,
        batch: str,
        scorecard: Optional[str],
        *,
        prompt: str = "SYNTH prompt",
        group: str = "group-1",
        index: Optional[int] = None,
    ) -> str:
        self.n += 1
        path = str(self.root / f"img{self.n}.png")
        self.db.save_media(_media(path, prompt=prompt))
        self.db.create_t2i_image(
            file_path=path,
            batch_id=batch,
            model="krea2",
            caption="A quiet harbour at dawn",
            prompt_used=prompt,
            negative_used="SYNTH negative",
            seed=5,
            writer_id=writer,
            writer_model=BASE,
            variant_group=group,
            variant_index=self.n % 2 if index is None else index,
            draft_prompt="SYNTH draft",
            edited=0,
            resolved_caption="A quiet harbour at dawn",
            direction="SYNTH direction",
            prompt_categories=["SYNTH category"],
            scorecard_id=scorecard,
        )
        return path

    def pair(self, index: int) -> Tuple[str, str]:
        batch = f"batch-{index}"
        self.db.add_scorecard_batch(batch, self.card, index)
        return (
            self.image("base", batch, self.card),
            self.image(adapter_writer_id(self.adapter), batch, self.card),
        )


@pytest.fixture
def lib(tmp_path: Path) -> Any:
    out = _Lib(tmp_path)
    yield out
    out.db.close()


def test_blind_flag_on_t2i_rows_until_a_grade(lib: Any) -> None:
    base, adapter = lib.pair(0)
    plain = lib.image("base", "other", None)
    rows = {r["file_path"]: r for r in lib.db.list_t2i_images()}
    assert rows[os.path.normpath(base)]["scorecard_blind"] is True
    assert rows[os.path.normpath(adapter)]["scorecard_blind"] is True
    assert rows[os.path.normpath(plain)]["scorecard_blind"] is False
    assert rows[os.path.normpath(base)]["scorecard_id"] == lib.card
    # Seen only: neither judged nor un-blinded. ✗ lifts the blind.
    lib.db.mark_seen(base)
    lib.db.set_grade(adapter, "fail")
    rows = {r["file_path"]: r for r in lib.db.list_t2i_images()}
    assert rows[os.path.normpath(base)]["scorecard_blind"] is True
    assert rows[os.path.normpath(adapter)]["scorecard_blind"] is False
    assert rows[os.path.normpath(adapter)]["grade"] == "fail"
    # ✓ lifts it as well.
    lib.db.set_grade(base, "pass")
    rows = {r["file_path"]: r for r in lib.db.list_t2i_images()}
    assert rows[os.path.normpath(base)]["scorecard_blind"] is False
    info = lib.db.scorecard_for_path(base)
    assert info == {"scorecard_id": lib.card, "adapter_id": lib.adapter, "blind": False}
    assert lib.db.scorecard_for_path(plain) is None
    # Clearing the grade hides it again.
    lib.db.set_grade(base, None)
    assert lib.db.scorecard_for_path(base)["blind"] is True


def test_results_are_pass_rates(lib: Any) -> None:
    """The rates are ✓ among each writer's images of the judged pairs; a
    pair is judged when both its images have a grade. ★, a view and the
    pre-grades reject flag judge nothing and pass nothing, even on a row
    that carries them; ★ + ✗ is a fail."""
    a = adapter_writer_id(7)
    star = {"is_favorite": True, "seen_at": "2026-10-01T00:00:00+00:00"}
    rows = [
        _row("b1", "base", grade="pass"),
        _row("b1", a, grade="pass"),
        _row("b2", "base", grade="fail"),
        _row("b2", a, grade="pass"),
        {**_row("b3", "base", grade="fail"), **star},
        _row("b3", a, grade="fail"),
        _row("b4", "base", grade="fail"),
        {**_row("b4", a, grade="fail"), **star},
        _row("b5", "base", grade="pass"),
        {**_row("b5", a), **star, "passed": True},
    ]
    out = sc.compute_results(rows, 7)
    assert set(out) == RESULT_KEYS
    assert (out["n_pairs"], out["n_judged"]) == (5, 4)
    assert (out["pass_base"], out["pass_adapter"]) == (1, 2)
    assert (out["pass_rate_base"], out["pass_rate_adapter"]) == (0.25, 0.5)

    # The same marks through the database: one adapter's result, the
    # summary's row and its base writer pooled over every judged pair.
    marks = [("pass", "pass"), ("fail", "pass"), ("fail", "fail"), ("fail", "fail")]
    pairs = [lib.pair(i) for i in range(5)]
    for (base, adapter), (graded_base, graded_adapter) in zip(pairs, marks):
        lib.db.set_grade(base, graded_base)
        lib.db.set_grade(adapter, graded_adapter)
    lib.db.set_favorite(pairs[2][0], True)
    lib.db.set_favorite(pairs[3][1], True)
    base, adapter = pairs[4]
    lib.db.set_grade(base, "pass")
    lib.db.set_favorite(adapter, True)
    lib.db.mark_seen(adapter)
    expected = {**out, "adapter_id": lib.adapter}
    one = sc.adapter_scorecard(lib.db, lib.adapter)
    assert set(one) == RESULT_KEYS | {"ready_pairs", "latest", "running"}
    assert {k: one[k] for k in RESULT_KEYS} == expected
    summary = sc.scorecard_summary(lib.db)
    (row,) = summary["adapters"]
    assert set(row) == RESULT_KEYS | {"name", "status", "latest"}
    assert {k: row[k] for k in RESULT_KEYS} == expected
    assert summary["base"] == {"n_judged": 4, "pass_rate": 0.25, "mean_score": None}


async def test_favorite_neither_judges_nor_reveals(lib: Any) -> None:
    """★ is no grade: starring (and viewing) both images of 30 pairs judges
    no pair, promotes nothing and lifts no blind -- else the writer would
    show before the grade. A grade does both, and un-starring a graded
    image afterwards changes neither."""
    sent: List[Tuple[str, Dict[str, Any]]] = []
    pairs = [lib.pair(i) for i in range(30)]
    for base, adapter in pairs:
        for path in (base, adapter):
            assert lib.db.set_favorite(path, True)
            lib.db.mark_seen(path)
            await sc.note_scorecard_judgment(
                lib.db, path, lambda e, d: sent.append((e, d))
            )
    result = await sc.evaluate_adapter(lib.db, lib.adapter, lambda e, d: None)
    assert (result["n_pairs"], result["n_judged"]) == (30, 0)
    assert result["pass_rate_base"] is None and result["pass_rate_adapter"] is None
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "untested"
    assert [s for s in sent if s[0] == "adapters_changed"] == []
    rows = {r["file_path"]: r for r in lib.db.list_t2i_images(limit=100)}
    for base, adapter in pairs:
        for path in (base, adapter):
            row = rows[os.path.normpath(path)]
            assert (row["is_favorite"], row["scorecard_blind"]) == (True, True)
            assert lib.db.scorecard_for_path(path)["blind"] is True
    for base, adapter in pairs:
        lib.db.set_grade(base, "fail")
        lib.db.set_grade(adapter, "pass")
        await sc.note_scorecard_judgment(
            lib.db, adapter, lambda e, d: sent.append((e, d))
        )
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "ready"
    base, _adapter = pairs[0]
    assert lib.db.set_favorite(base, False)
    assert lib.db.scorecard_for_path(base)["blind"] is False
    result = await sc.evaluate_adapter(lib.db, lib.adapter, lambda e, d: None)
    assert result["n_judged"] == 30
    assert (result["pass_rate_base"], result["pass_rate_adapter"]) == (0.0, 1.0)


async def test_deleted_judged_images_still_count(lib: Any) -> None:
    """Deleting a graded scorecard image changes nothing: its grade comes
    from its tombstone and its writer, batch and brief from the lineage, so
    the results, the promotion and a resume see it as before -- through
    the library delete (it leaves the T2I row behind, pruned later) and
    the T2I delete (the row goes with the file)."""
    sent: List[Tuple[str, Dict[str, Any]]] = []
    pairs = [lib.pair(i) for i in range(30)]
    ids = {
        os.path.normpath(r["file_path"]): int(r["id"])
        for r in lib.db.list_t2i_images(limit=100)
    }
    # Unequal ✓ / ✗ per writer, so a lost or inverted grade moves a rate.
    for i, (base, adapter) in enumerate(pairs):
        lib.db.set_grade(base, "pass" if i % 3 == 0 else "fail")
        if i < 29:
            lib.db.set_grade(adapter, "pass" if i % 2 == 0 else "fail")
    before = sc.adapter_scorecard(lib.db, lib.adapter)
    assert (before["n_pairs"], before["n_judged"]) == (30, 29)
    assert (before["pass_base"], before["pass_adapter"]) == (10, 15)

    def t2i_delete(path: str) -> None:
        deleted, purged = lib.db.delete_t2i_image(ids[os.path.normpath(path)])
        assert deleted and purged

    for base, adapter in pairs[0:3]:
        assert lib.db.delete_media(Path(base)) and lib.db.delete_media(Path(adapter))
    for base, adapter in pairs[3:6]:
        t2i_delete(base)
        t2i_delete(adapter)
    assert lib.db.delete_media(Path(pairs[6][0]))
    t2i_delete(pairs[7][1])
    assert lib.db.delete_media(Path(pairs[29][0]))  # graded; its partner is not
    lib.db.list_t2i_images(limit=100)  # prunes the rows library deletes left

    def results() -> Dict[str, Any]:
        out = sc.adapter_scorecard(lib.db, lib.adapter)
        return {k: out[k] for k in RESULT_KEYS}

    assert results() == {k: before[k] for k in RESULT_KEYS}
    rows = lib.db.scorecard_image_rows(lib.adapter)
    assert len(rows) == 60
    assert len([r for r in rows if r["tombstone"]]) == 15
    # Tombstones follow the same filters as live rows.
    assert lib.db.scorecard_image_rows(lib.adapter + 1) == []
    assert lib.db.scorecard_image_rows(None, "another-card") == []
    for r in rows:
        assert set(r) == {
            "adapter_id",
            "scorecard_id",
            "batch_id",
            "brief_index",
            "writer_id",
            "grade",
            "score",
            "tombstone",
        }
    # A resume sees every brief paired: no judged pair is rendered again.
    card_rows = lib.db.scorecard_image_rows(None, lib.card)
    assert sc._paired_briefs(card_rows, lib.adapter) == set(range(30))

    # The 30th judged pair promotes the adapter, deleted images included.
    lib.db.set_grade(pairs[29][1], "pass")
    await sc.note_scorecard_judgment(
        lib.db, pairs[29][1], lambda e, d: sent.append((e, d))
    )
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "ready"
    assert [s for s in sent if s[0] == "adapters_changed"] == [("adapters_changed", {})]
    judged = results()
    assert (judged["n_judged"], judged["pass_base"], judged["pass_adapter"]) == (
        30,
        10,
        16,
    )
    # Deleting every image still in the library changes nothing either.
    for path in [p for pair in pairs for p in pair]:
        lib.db.delete_media(Path(path))
    rows = lib.db.scorecard_image_rows(lib.adapter)
    assert len(rows) == 60 and all(r["tombstone"] for r in rows)
    assert results() == judged
    await sc.evaluate_adapter(lib.db, lib.adapter, lambda e, d: sent.append((e, d)))
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "ready"
    summary = sc.scorecard_summary(lib.db)
    assert summary["base"]["n_judged"] == 30
    assert summary["base"]["pass_rate"] == 10 / 30


def test_a_deleted_ungraded_image_frees_its_brief(lib: Any) -> None:
    """A deleted image that was never graded judged nothing and is left
    out: its brief has no complete pair, so a resume renders it again (as
    before tombstones counted) -- also when its partner is a deleted graded
    image, which is no pair on its own."""
    (b0, a0), (b1, a1), _pair2 = [lib.pair(i) for i in range(3)]
    lib.db.set_grade(b0, "pass")
    lib.db.set_grade(b1, "pass")
    lib.db.set_favorite(a1, True)  # ★ and a view are no grade
    lib.db.mark_seen(a1)
    for path in (a0, b1, a1):
        assert lib.db.delete_media(Path(path))
    rows = lib.db.scorecard_image_rows(None, lib.card)
    assert sc._paired_briefs(rows, lib.adapter) == {2}
    assert [r["tombstone"] for r in rows].count(True) == 1  # b1
    out = sc.compute_results(rows, lib.adapter)
    assert (out["n_pairs"], out["n_judged"]) == (1, 0)


def test_a_brief_counts_its_latest_batch_when_an_older_one_was_deleted(
    lib: Any,
) -> None:
    """Deleted images keep their place in image order, so a brief rendered
    twice still counts its latest batch when the older batch was graded
    and deleted."""
    old_base, old_adapter = lib.pair(0)
    lib.db.add_scorecard_batch("batch-0-again", lib.card, 0)
    newer = [
        lib.image("base", "batch-0-again", lib.card),
        lib.image(adapter_writer_id(lib.adapter), "batch-0-again", lib.card),
    ]
    for path in (old_base, old_adapter):
        lib.db.set_grade(path, "pass")
        assert lib.db.delete_media(Path(path))
    for path in newer:
        lib.db.set_grade(path, "fail")
    rows = lib.db.scorecard_image_rows(lib.adapter)
    assert [r["tombstone"] for r in rows] == [True, True, False, False]
    out = sc.compute_results(rows, lib.adapter)
    assert (out["n_pairs"], out["n_judged"]) == (1, 1)
    assert (out["pass_base"], out["pass_adapter"]) == (0, 0)


def test_a_live_image_beats_its_tombstone(lib: Any) -> None:
    """A path that holds a live T2I image again (the file restored from
    the trash and rescanned while its T2I row was still there) counts once,
    as the live row: ungraded and blind until graded again; graded and
    deleted again, its newest tombstone counts. With no live T2I row at the
    path (the T2I delete dropped it), the tombstone keeps counting."""
    (base, adapter), (b1, a1) = lib.pair(0), lib.pair(1)
    for path, grade in ((base, "pass"), (adapter, "fail"), (b1, "pass"), (a1, "pass")):
        lib.db.set_grade(path, grade)
    ids = {
        os.path.normpath(r["file_path"]): int(r["id"]) for r in lib.db.list_t2i_images()
    }
    assert lib.db.delete_media(Path(base))
    assert lib.db.delete_t2i_image(ids[os.path.normpath(a1)])[0]

    def result() -> Dict[str, Any]:
        return sc.compute_results(lib.db.scorecard_image_rows(lib.adapter), lib.adapter)

    assert (result()["n_pairs"], result()["n_judged"]) == (2, 2)
    lib.db.save_media(_media(base, prompt="SYNTH prompt"))
    lib.db.save_media(_media(a1, prompt="SYNTH prompt"))
    rows = lib.db.scorecard_image_rows(lib.adapter)
    assert len(rows) == 4
    assert [r["tombstone"] for r in rows] == [False, False, False, True]
    assert (result()["n_pairs"], result()["n_judged"]) == (2, 1)
    assert lib.db.scorecard_for_path(base)["blind"] is True
    assert lib.db.scorecard_for_path(a1) is None  # a plain library image now
    lib.db.set_grade(base, "fail")
    assert lib.db.delete_media(Path(base))
    out = result()
    assert (out["n_pairs"], out["n_judged"], out["pass_base"]) == (2, 2, 1)


async def test_untested_becomes_ready_at_30_judged_pairs_and_broadcasts(
    lib: Any,
) -> None:
    sent: List[Tuple[str, Dict[str, Any]]] = []
    pairs = [lib.pair(i) for i in range(31)]
    for i, (base, adapter) in enumerate(pairs[:29]):
        lib.db.set_grade(base, "fail")
        lib.db.set_grade(adapter, "pass" if i % 2 else "fail")
        await sc.note_scorecard_judgment(
            lib.db, adapter, lambda e, d: sent.append((e, d))
        )
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "untested"
    assert [s for s in sent if s[0] != "scorecard_judged"] == []
    base, adapter = pairs[29]
    lib.db.set_grade(base, "pass")
    await sc.note_scorecard_judgment(lib.db, base, lambda e, d: sent.append((e, d)))
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "untested"
    lib.db.mark_seen(adapter)  # seen without a grade: NOT judged
    await sc.note_scorecard_judgment(lib.db, adapter, lambda e, d: sent.append((e, d)))
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "untested"
    lib.db.set_grade(adapter, "fail")  # the grade: pair 30 is judged
    await sc.note_scorecard_judgment(lib.db, adapter, lambda e, d: sent.append((e, d)))
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "ready"
    changed = [s for s in sent if s[0] == "adapters_changed"]
    assert changed == [("adapters_changed", {})]
    # Every scorecard judgment is announced (the window refetches results).
    assert ("scorecard_judged", {"adapter_id": lib.adapter}) in sent
    assert len([s for s in sent if s[0] == "scorecard_judged"]) == 32
    # Ready stays ready (and is not re-announced) even when grades go.
    lib.db.set_grade(base, None)
    await sc.note_scorecard_judgment(lib.db, base, lambda e, d: sent.append((e, d)))
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "ready"
    assert [s for s in sent if s[0] == "adapters_changed"] == changed
    result = await sc.evaluate_adapter(lib.db, lib.adapter, lambda e, d: None)
    assert result["n_pairs"] == 31


async def test_a_non_scorecard_judgment_is_ignored(lib: Any) -> None:
    plain = lib.image("base", "other", None)
    lib.db.set_grade(plain, "pass")
    with mock.patch.object(sc, "evaluate_adapter") as evaluate:
        await sc.note_scorecard_judgment(lib.db, plain, lambda e, d: None)
    evaluate.assert_not_called()


async def test_seen_only_pairs_never_count_as_judged(lib: Any) -> None:
    """Ruling: a scorecard image is judged only by a grade, like the blind
    lift; a view teaches nothing and never counts toward n_judged or
    promotion."""
    sent: List[Tuple[str, Dict[str, Any]]] = []
    pairs = [lib.pair(i) for i in range(30)]
    for base, adapter in pairs:
        lib.db.mark_seen(base)
        lib.db.mark_seen(adapter)
        await sc.note_scorecard_judgment(
            lib.db, adapter, lambda e, d: sent.append((e, d))
        )
    result = await sc.evaluate_adapter(lib.db, lib.adapter, lambda e, d: None)
    assert (result["n_pairs"], result["n_judged"]) == (30, 0)
    assert result["pass_rate_base"] is None and result["pass_rate_adapter"] is None
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "untested"
    assert [s for s in sent if s[0] == "adapters_changed"] == []
    for base, adapter in pairs:
        lib.db.set_grade(base, "fail")
        lib.db.set_grade(adapter, "pass")
        await sc.note_scorecard_judgment(
            lib.db, adapter, lambda e, d: sent.append((e, d))
        )
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "ready"
    result = await sc.evaluate_adapter(lib.db, lib.adapter, lambda e, d: None)
    assert result["n_judged"] == 30
    assert (result["pass_rate_base"], result["pass_rate_adapter"]) == (0.0, 1.0)


def test_summary_takes_the_running_state_from_its_caller(lib: Any) -> None:
    """scorecard_summary / adapter_scorecard run in a worker thread: they
    never read the asyncio task state themselves."""
    running = {"adapter_id": lib.adapter, "scorecard_id": "x"}
    with mock.patch.object(
        sc, "get_scorecard_service", side_effect=AssertionError("off the loop")
    ):
        assert sc.scorecard_summary(lib.db, running=running)["running"] == running
        assert sc.scorecard_summary(lib.db)["running"] is None
        one = sc.adapter_scorecard(lib.db, lib.adapter, running=running)
        assert one["running"] == running
        other = sc.adapter_scorecard(lib.db, lib.adapter + 1, running=running)
        assert other["running"] is None


@pytest.mark.parametrize("tagged", [True, False], ids=["scorecard", "control"])
def test_scorecard_images_never_become_training_data(lib: Any, tagged: bool) -> None:
    """Scorecard pairs that would otherwise train the writer -- a ✓ variant
    against a ✗ one with another prompt, and a briefed ✓ (hindsight) -- give
    no example. The control is the same images without the scorecard tag:
    it gets both kinds, so the exclusion is what removes them."""
    card = lib.card if tagged else None
    for i in range(3):
        batch, group = f"batch-{i}", f"group-{i}"
        if tagged:
            lib.db.add_scorecard_batch(batch, lib.card, i)
        base = lib.image(
            "base", batch, card, prompt=f"SYNTH base {i}", group=group, index=0
        )
        adapter = lib.image(
            adapter_writer_id(lib.adapter),
            batch,
            card,
            prompt=f"SYNTH adapter {i}",
            group=group,
            index=1,
        )
        lib.db.set_grade(base, "fail")
        lib.db.set_grade(adapter, "pass")
        lib.db.set_hindsight_brief(adapter, "A quiet harbour", "qwen3vl-8b")
    sft, prefs, counts = build_examples(
        lib.db,
        "all",
        get_prompt_store(),
        datetime.now(timezone.utc) + timedelta(days=1),
        content_mode="default",
    )
    if tagged:
        assert sft == [] and prefs == []
        assert counts.pref_pairs == 0 and counts.hindsight == 0
    else:
        assert (counts.pref_pairs, counts.hindsight) == (3, 3)
        assert sorted((p.chosen, p.rejected) for p in prefs) == [
            (f"SYNTH adapter {i}", f"SYNTH base {i}") for i in range(3)
        ]


# ---- the run through the real runner ---------------------------------------------


class ScorecardCase(AdapterCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cfg = get_t2i_config(
            {"t2i": {"window": 50, "model_workflows": {"krea2": self.plain}}}
        )
        self.sent: List[Tuple[str, Dict[str, Any]]] = []
        self.briefs_path = sc.SCORECARD_BRIEFS_PATH
        self.service = self.make_service()
        self._renderer: Optional["asyncio.Task[None]"] = None
        self.fail_jobs: set = set()

    def make_service(self, path: Optional[Path] = None) -> sc.ScorecardService:
        return sc.ScorecardService(
            self.db,
            self.runner,
            broadcast=lambda e, d: self.sent.append((e, dict(d))),
            get_config=lambda: self.cfg,
            briefs_path=path or self.briefs_path,
            retry_delay_s=0.01,
        )

    async def asyncTearDown(self) -> None:
        await self.service.aclose()
        if self._renderer is not None:
            self._renderer.cancel()
            await asyncio.gather(self._renderer, return_exceptions=True)
        set_gpu_reservation(None)
        await super().asyncTearDown()

    def autorender(self) -> None:
        """Play ComfyUI: every submitted job renders an image (with its
        library row), except the n-th jobs listed in ``fail_jobs``."""

        async def loop() -> None:
            done: set = set()
            while True:
                for n, job_id in enumerate(list(self.comfy.job_ids)):
                    if job_id in done:
                        continue
                    done.add(job_id)
                    if n in self.fail_jobs:
                        self.job_failed(job_id)
                        continue
                    self.db.save_media(_media(self.image(job_id)))
                    await self.job_done(job_id)
                await asyncio.sleep(0.001)

        self._renderer = asyncio.create_task(loop())

    def briefs_file(self, *lines: str) -> Path:
        path = self.root / "briefs.txt"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def sent_events(self, name: str) -> List[Dict[str, Any]]:
        return [d for e, d in self.sent if e == name]


class TestScorecardRun(ScorecardCase):
    async def test_a_run_renders_every_brief_with_base_and_adapter(self) -> None:
        assert self.vlm is not None
        self.autorender()
        started = await self.service.start(self.adapter)
        card = started["scorecard_id"]
        self.assertEqual(started["total"], 40)
        await self.service.wait()

        rows = _rows(self.db)
        self.assertEqual(len(rows), 80)
        self.assertEqual({r["scorecard_id"] for r in rows}, {card})
        briefs, _ = sc.load_briefs(self.briefs_path)
        by_batch: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            by_batch.setdefault(r["batch_id"], []).append(r)
        self.assertEqual(len(by_batch), 40)
        for index, brief in enumerate(briefs):
            (batch,) = [b for b, rs in by_batch.items() if rs[0]["caption"] == brief]
            pair = by_batch[batch]
            self.assertEqual(
                sorted(r["writer_id"] for r in pair), sorted(["base", self.wid])
            )
            self.assertEqual({r["seed"] for r in pair}, {sc.brief_seed(card, index)})
            self.assertEqual({r["writer_model"] for r in pair}, {BASE})
            adapter_row = next(r for r in pair if r["writer_id"] == self.wid)
            self.assertEqual(
                adapter_row["variant_index"],
                0 if sc.adapter_first(card, index) else 1,
            )
        # 80 VLM calls: the adapter's carry its lora, the base ones none;
        # neither gets an angle (one variant each).
        self.assertEqual(len(self.vlm.calls), 80)
        with_lora = [c for c in self.vlm.calls if "lora_path" in c]
        self.assertEqual(len(with_lora), 40)
        for call in self.vlm.calls:
            self.assertNotIn("ANGLE", call["user_prompt"])
        # Every prompt was written before the first render, then the VLM went.
        first_submit = self.log.index(("comfy", "submit"))
        self.assertNotIn(("vlm", "generate_text"), self.log[first_submit:])
        self.assertIn(("vlm", "shutdown"), self.log[:first_submit])

        card_row = self.db.get_scorecard(card)
        self.assertEqual((card_row["status"], card_row["done"]), ("complete", 40))
        (complete,) = self.sent_events("scorecard_complete")
        self.assertEqual(complete["adapter_id"], self.adapter)
        self.assertEqual((complete["done"], complete["total"]), (40, 40))
        progress = self.sent_events("scorecard_progress")
        self.assertTrue(progress)
        self.assertEqual(progress[-1]["done"], 40)
        self.assertEqual(progress[-1]["total"], 40)
        self.assertIsNone(self.service.running())
        # Nothing judged yet: everything blind, still untested.
        self.assertTrue(all(r["scorecard_blind"] for r in self.db.list_t2i_images()))
        self.assertEqual(self.db.get_writer_adapter(self.adapter)["status"], "untested")

    async def test_resume_renders_only_the_briefs_without_both_images(self) -> None:
        path = self.briefs_file("A quiet harbour", "A red kite", "A tin lantern")
        self.service = self.make_service(path)
        self.fail_jobs = {2}  # the second brief's first image fails
        self.autorender()
        card = (await self.service.start(self.adapter))["scorecard_id"]
        await self.service.wait()
        row = self.db.get_scorecard(card)
        self.assertEqual((row["status"], row["done"]), ("incomplete", 2))
        self.assertEqual(len(_rows(self.db)), 5)

        self.fail_jobs = set()
        again = await self.service.start(self.adapter)
        self.assertEqual(again["scorecard_id"], card)
        self.assertTrue(again["resumed"])
        self.assertEqual(again["todo"], 1)
        await self.service.wait()
        rows = _rows(self.db)
        self.assertEqual(len(rows), 7)
        redone = [r for r in rows[5:]]
        self.assertEqual({r["caption"] for r in redone}, {"A red kite"})
        self.assertEqual({r["seed"] for r in redone}, {sc.brief_seed(card, 1)})
        self.assertEqual(self.db.get_scorecard(card)["status"], "complete")

        # A complete scorecard is rendered afresh: a new id, every brief.
        fresh = await self.service.start(self.adapter)
        self.assertNotEqual(fresh["scorecard_id"], card)
        self.assertEqual(fresh["todo"], 3)
        await self.service.wait()

    async def test_resume_skips_a_brief_whose_render_is_still_queued(self) -> None:
        path = self.briefs_file("A quiet harbour", "A red kite", "A tin lantern")
        self.service = self.make_service(path)
        self.fail_jobs = {2, 4}  # briefs 1 and 2 lose an image
        self.autorender()
        card = (await self.service.start(self.adapter))["scorecard_id"]
        await self.service.wait()
        self.assertEqual(self.db.get_scorecard(card)["done"], 1)
        # Brief 1's batch is still queued in ComfyUI (e.g. it survived a
        # restart): re-rendering it would pair the brief twice.
        job = self.db.create_generation_job(self.plain, "{}")
        self.db.add_scorecard_job(job, "queued-batch", card, 1, "base", BASE, 0)
        again = await self.service.start(self.adapter)
        self.assertTrue(again["resumed"])
        self.assertEqual(again["todo"], 1)
        await self.service.wait()
        captions = {r["caption"] for r in _rows(self.db)[3:]}
        self.assertEqual(captions, {"A tin lantern"})
        # Only the queued brief is left: nothing to render now.
        with self.assertRaises(sc.ScorecardError) as caught:
            await self.service.start(self.adapter)
        self.assertEqual(caught.exception.code, "scorecard_pending")
        # Once the job ends, the brief renders again.
        self.db.update_generation_job(job, state="failed")
        again = await self.service.start(self.adapter)
        self.assertEqual((again["resumed"], again["todo"]), (True, 1))
        await self.service.wait()

    async def test_only_one_scorecard_at_a_time_and_cancel(self) -> None:
        assert self.vlm is not None
        gate = asyncio.Event()
        self.vlm.gates.append(gate)
        await self.service.start(self.adapter)
        with self.assertRaises(sc.ScorecardError) as caught:
            await self.service.start(self.adapter)
        self.assertEqual(caught.exception.code, "scorecard_running")
        self.assertTrue(sc.scorecard_busy(self.service))
        self.assertTrue(await self.service.cancel())
        await self.service.wait()
        self.assertIsNone(self.service.running())
        (card,) = self.db.list_scorecards(self.adapter)
        self.assertEqual(card["status"], "cancelled")
        self.assertTrue(self.sent_events("scorecard_cancelled"))
        self.assertEqual(self.comfy.submits, [])

    async def test_refused_while_training_holds_the_gpu(self) -> None:
        set_gpu_reservation(lambda: True)
        with self.assertRaises(sc.ScorecardError) as caught:
            await self.service.start(self.adapter)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.code, "gpu_busy_training")
        self.assertEqual(self.db.list_scorecards(self.adapter), [])

    async def test_training_taking_the_gpu_mid_run_fails_the_scorecard(self) -> None:
        assert self.vlm is not None
        held = {"on": False}
        set_gpu_reservation(lambda: held["on"])
        gate = asyncio.Event()
        self.vlm.gates.extend([None, None, gate])
        await self.service.start(self.adapter)
        await self.until(lambda: len(self.vlm.calls) >= 3, "the third prompt")
        held["on"] = True
        gate.set()
        await self.service.wait()
        (failed,) = self.sent_events("scorecard_failed")
        self.assertEqual(failed["code"], "gpu_busy_training")
        self.assertEqual(self.comfy.submits, [])
        (card,) = self.db.list_scorecards(self.adapter)
        self.assertEqual(card["status"], "failed")

    async def test_unknown_or_unservable_adapters_are_refused(self) -> None:
        with self.assertRaises(sc.ScorecardError) as caught:
            await self.service.start(9999)
        self.assertEqual(
            (caught.exception.status, caught.exception.code), (404, "unknown_adapter")
        )
        failed = self.db.create_writer_adapter(
            name="broken", scope="all", kind="t2i", base_model=BASE, status="failed"
        )
        with self.assertRaises(sc.ScorecardError) as caught:
            await self.service.start(failed)
        self.assertEqual(caught.exception.code, "adapter_unavailable")
        with self.assertRaises(sc.ScorecardError) as caught:
            await self.service.start(self.adapter, target_model="nope")
        self.assertEqual(caught.exception.code, "unknown_model")
        with self.assertRaises(sc.ScorecardError) as caught:
            await self.service.start(self.adapter, target_model="qwen")
        self.assertEqual(caught.exception.code, "no_workflow")

    async def test_no_writer_fails_instead_of_scoring_fallbacks(self) -> None:
        self.vlm = None
        await self.service.start(self.adapter)
        await self.service.wait()
        (failed,) = self.sent_events("scorecard_failed")
        self.assertEqual(failed["code"], "writer_unavailable")
        self.assertEqual(self.comfy.submits, [])

    async def test_recover_marks_a_running_scorecard_interrupted(self) -> None:
        self.db.create_scorecard("old", self.adapter, "krea2", preset_id=None, total=40)
        self.assertEqual(self.db.get_scorecard("old")["status"], "running")
        self.assertEqual(await self.service.recover(), 1)
        self.assertEqual(self.db.get_scorecard("old")["status"], "interrupted")

    async def test_shutdown_interrupts_and_leaves_the_jobs_to_comfyui(self) -> None:
        self.service = self.make_service(self.briefs_file("A quiet harbour"))
        card = (await self.service.start(self.adapter))["scorecard_id"]
        await self.until(lambda: len(self.comfy.job_ids) == 2, "both jobs")
        await self.service.aclose()
        self.assertEqual(self.db.get_scorecard(card)["status"], "interrupted")
        self.assertEqual(self.comfy.cancelled, [])
        self.assertEqual(self.sent_events("scorecard_cancelled"), [])

    async def test_a_late_ingest_after_a_restart_keeps_its_scorecard_id(self) -> None:
        path = self.briefs_file("A quiet harbour")
        self.service = self.make_service(path)
        card = (await self.service.start(self.adapter))["scorecard_id"]
        await self.until(lambda: len(self.comfy.job_ids) == 2, "both jobs")
        # A restart: the runner's per-job snapshot is gone.
        self.runner._job_meta.clear()
        for job_id in list(self.comfy.job_ids):
            self.db.save_media(_media(self.image(job_id)))
            await self.job_done(job_id)
        await self.service.wait()
        rows = _rows(self.db)
        self.assertEqual({r["scorecard_id"] for r in rows}, {card})
        # The writer lineage came back from the persisted per-job record.
        self.assertEqual(
            sorted(r["writer_id"] for r in rows), sorted(["base", self.wid])
        )
        self.assertEqual({r["writer_model"] for r in rows}, {BASE})
        adapter_row = next(r for r in rows if r["writer_id"] == self.wid)
        base_row = next(r for r in rows if r["writer_id"] == "base")
        first = 0 if sc.adapter_first(card, 0) else 1
        self.assertEqual(adapter_row["variant_index"], first)
        self.assertEqual(base_row["variant_index"], 1 - first)
        # ... so the brief pairs and counts in the results.
        self.assertEqual(self.db.get_scorecard(card)["done"], 1)
        for r in rows:
            self.db.set_grade(r["file_path"], "fail")
        result = await sc.evaluate_adapter(self.db, self.adapter, lambda e, d: None)
        self.assertEqual((result["n_pairs"], result["n_judged"]), (1, 1))

    async def test_a_scorecard_batch_needs_its_brief(self) -> None:
        with self.assertRaises(T2iRequestError):
            await self.runner.start_batch(
                self.manual(scorecard_id="card-x", scorecard_brief=None)
            )
        self.assertIsNone(self.db.scorecard_id_for_batch("card-x"))
        self.assertEqual(self.events, [])
        self.assertEqual(self.comfy.submits, [])

    async def test_scorecard_batch_frames_and_listing_are_blind(self) -> None:
        assert self.vlm is not None
        self.vlm.default_reply = "SECRET written prompt"
        self.service = self.make_service(self.briefs_file("A quiet harbour"))
        card = (await self.service.start(self.adapter))["scorecard_id"]
        await self.until(lambda: len(self.comfy.job_ids) == 2, "both jobs")
        (listed,) = self.runner.active_batches()
        self.assertEqual(listed["scorecard_id"], card)
        (started,) = self.frames("batch_started")
        self.assertEqual(started["scorecard_id"], card)
        steps = self.frames("batch_step")
        self.assertTrue(steps)
        for blob in [listed["step"], *steps]:
            text = json.dumps(blob)
            self.assertNotIn("SECRET", text)
            self.assertNotIn(self.wid, text)
            self.assertNotIn(BASE, text)
            for key in ("prompt", "negative", "draft", "writer_id", "writer_model"):
                self.assertIsNone(blob.get(key), key)
            self.assertIsNone(blob.get("variant_index"))
            self.assertIsNone(blob.get("lineage"))
        # Nothing on the t2i channel names the writer or carries the prompt.
        for _, data in self.events:
            text = json.dumps(data)
            self.assertNotIn("SECRET", text)
            self.assertNotIn(self.wid, text)
        self.autorender()
        await self.service.wait()

    async def test_briefs_and_prompts_are_not_logged_at_info(self) -> None:
        path = self.briefs_file("A SECRET harbour brief")
        self.service = self.make_service(path)
        assert self.vlm is not None
        self.vlm.default_reply = "SECRET written prompt"
        self.autorender()
        with self.assertLogs(level=logging.INFO) as logs:
            logging.getLogger("backend.services.scorecard").info("probe")
            await self.service.start(self.adapter)
            await self.service.wait()
        for line in logs.output:
            self.assertNotIn("SECRET", line)


class TestTrainingHook(ScorecardCase):
    async def test_the_hook_starts_a_scorecard_only_when_requested(self) -> None:
        with mock.patch.object(
            self.service, "start", new_callable=mock.AsyncMock
        ) as start:
            start.return_value = {"scorecard_id": "x"}
            await self.service.on_training_completed(self.adapter, False)
            start.assert_not_called()
            await self.service.on_training_completed(self.adapter, True)
            start.assert_called_once_with(self.adapter)

    async def test_a_refused_hook_start_is_announced_not_raised(self) -> None:
        set_gpu_reservation(lambda: True)
        await self.service.on_training_completed(self.adapter, True)
        (failed,) = self.sent_events("scorecard_failed")
        self.assertEqual(failed["adapter_id"], self.adapter)
        self.assertEqual(failed["code"], "gpu_busy_training")

    async def test_no_scorecard_starts_while_the_app_is_closing(self) -> None:
        self.service._closing = True
        with self.assertRaises(sc.ScorecardError) as caught:
            await self.service.start(self.adapter)
        self.assertEqual(caught.exception.code, "shutting_down")
        # The post-training hook skips quietly: no scorecard_failed noise.
        await self.service.on_training_completed(self.adapter, True)
        self.assertEqual(self.sent_events("scorecard_failed"), [])
        self.assertEqual(self.db.list_scorecards(self.adapter), [])

    async def test_training_is_refused_while_a_scorecard_runs(self) -> None:
        from backend.main import _build_training_manager
        from tests.test_training_manager import _FakeComfy, _FakeT2i, _FakeUnloadable

        m = _build_training_manager(
            db=self.db,
            data_dir=self.root / "data",
            comfy_client=_FakeComfy(),
            t2i_runner=_FakeT2i(),
            inference_client=_FakeUnloadable(),
            vlm_roles=_FakeUnloadable(),
            on_completed=self.service.on_training_completed,
        )
        self.assertEqual(m._gpu_busy(), [])
        self.assertEqual(m.on_completed, self.service.on_training_completed)
        with mock.patch.object(sc, "_SERVICE", self.service), mock.patch.object(
            self.service, "running", return_value={"adapter_id": 1}
        ):
            self.assertEqual(m._gpu_busy(), ["scorecard"])


# ---- routes -------------------------------------------------------------------------


class _StubService:
    def __init__(self) -> None:
        self.started: List[Tuple[int, Dict[str, Any]]] = []
        self.error: Optional[sc.ScorecardError] = None
        self.cancelled = 0

    async def start(self, adapter_id: int, **kw: Any) -> Dict[str, Any]:
        if self.error is not None:
            raise self.error
        self.started.append((adapter_id, kw))
        return {"scorecard_id": "abc", "adapter_id": adapter_id, "total": 40}

    async def cancel(self) -> bool:
        self.cancelled += 1
        return True

    def running(self) -> Optional[Dict[str, Any]]:
        return None


@pytest.fixture
def api(tmp_path: Path) -> Any:
    os.environ.setdefault("METASCAN_API_KEY", "")
    lib = _Lib(tmp_path)
    import backend.dependencies as deps

    old = deps._db_singleton
    deps._db_singleton = lib.db  # type: ignore[attr-defined]
    stub = _StubService()
    sc.set_scorecard_service(stub)  # type: ignore[arg-type]
    from backend.api import judgments, media, t2i, writers

    app = FastAPI()
    for r in (media.router, judgments.router, writers.router, t2i.router):
        app.include_router(r)
    with mock.patch("backend.dependencies.get_data_dir", return_value=tmp_path):
        yield TestClient(app), lib, stub
    sc.set_scorecard_service(None)
    deps._db_singleton = old  # type: ignore[attr-defined]
    lib.db.close()


def _grade(client: Any, path: str, grade: Optional[str]) -> None:
    r = client.patch(f"/api/judgments/{path}", json={"grade": grade})
    assert r.status_code == 200


def test_post_scorecard_route(api: Any) -> None:
    client, lib, stub = api
    r = client.post(
        f"/api/writers/adapters/{lib.adapter}/scorecard",
        json={"target_model": "qwen", "fresh": True},
    )
    assert r.status_code == 202
    assert r.json()["scorecard_id"] == "abc"
    assert stub.started == [
        (lib.adapter, {"target_model": "qwen", "preset_id": None, "fresh": True})
    ]
    stub.error = sc.ScorecardError(409, "scorecard_running", adapter_id=3)
    r = client.post(f"/api/writers/adapters/{lib.adapter}/scorecard", json={})
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "scorecard_running", "adapter_id": 3}
    assert client.post("/api/writers/scorecard/cancel").json() == {
        "status": "cancelling"
    }


def test_post_scorecard_answers_409_while_training_holds_the_gpu(api: Any) -> None:
    client, lib, stub = api
    set_gpu_reservation(lambda: True)
    try:
        r = client.post(f"/api/writers/adapters/{lib.adapter}/scorecard", json={})
    finally:
        set_gpu_reservation(None)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "gpu_busy_training"
    assert stub.started == []


def test_results_routes_and_blind_payloads(api: Any) -> None:
    client, lib, _ = api
    base, adapter = lib.pair(0)
    r = client.get(f"/api/judgments/{base}")
    assert r.json()["scorecard"] == {"id": lib.card, "blind": True}
    assert client.get(f"/api/media/{base}").json()["scorecard_blind"] is True
    _grade(client, base, "pass")
    assert client.get(f"/api/judgments/{base}").json()["scorecard"]["blind"] is False
    assert client.get(f"/api/media/{base}").json()["scorecard_blind"] is False
    client.post("/api/judgments/seen", json={"file_path": adapter})
    # Seen only: the pair is not judged yet.
    one = client.get(f"/api/writers/adapters/{lib.adapter}/scorecard").json()
    assert one["n_pairs"] == 1 and one["n_judged"] == 0
    _grade(client, adapter, "fail")

    one = client.get(f"/api/writers/adapters/{lib.adapter}/scorecard").json()
    assert one["n_pairs"] == 1 and one["n_judged"] == 1
    assert (one["pass_base"], one["pass_adapter"]) == (1, 0)
    assert one["pass_rate_base"] == 1.0 and one["pass_rate_adapter"] == 0.0
    assert one["ready_pairs"] == 30
    assert one["latest"]["id"] == lib.card
    everything = client.get("/api/writers/scorecards").json()
    assert everything["briefs"] == 40
    assert everything["running"] is None
    (row,) = everything["adapters"]
    assert row["adapter_id"] == lib.adapter and row["name"] == "taste-all-v1"
    assert row["pass_rate_adapter"] == 0.0
    assert everything["base"] == {"n_judged": 1, "pass_rate": 1.0, "mean_score": None}
    plain = lib.image("base", "x", None)
    assert client.get(f"/api/judgments/{plain}").json()["scorecard"] is None
    assert client.get(f"/api/media/{plain}").json()["scorecard_blind"] is False


def test_a_grade_through_the_routes_promotes_the_adapter(api: Any) -> None:
    client, lib, _ = api
    sent: List[Tuple[str, str, Dict[str, Any]]] = []
    pairs = [lib.pair(i) for i in range(30)]
    with mock.patch(
        "backend.ws.manager.ws_manager.broadcast_sync",
        side_effect=lambda c, e, d: sent.append((c, e, d)),
    ):
        for base, adapter in pairs[:29]:
            lib.db.set_grade(base, "fail")
            lib.db.set_grade(adapter, "pass")
        base, adapter = pairs[29]
        client.patch(f"/api/judgments/{base}", json={"grade": "fail"})
        assert lib.db.get_writer_adapter(lib.adapter)["status"] == "untested"
        # Seen without a grade never judges a scorecard image.
        client.post("/api/judgments/seen", json={"file_path": adapter})
        assert lib.db.get_writer_adapter(lib.adapter)["status"] == "untested"
        assert [s for s in sent if s[1] == "adapters_changed"] == []
        # ★ is not a grade: neither star route reaches the scorecard check.
        checks = len([s for s in sent if s[1] == "scorecard_judged"])
        assert checks == 1  # the base's grade above
        client.patch(f"/api/media/{adapter}", json={"is_favorite": True})
        client.patch(f"/api/judgments/{adapter}", json={"favorite": False})
        assert lib.db.get_writer_adapter(lib.adapter)["status"] == "untested"
        assert len([s for s in sent if s[1] == "scorecard_judged"]) == checks
        client.patch(f"/api/judgments/{adapter}", json={"grade": "fail"})
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "ready"
    assert ("training", "adapters_changed", {}) in sent


_REVEALING = ("SYNTH", "adapter:", BASE)


def _assert_blind(blob: Any) -> None:
    text = json.dumps(blob)
    for needle in _REVEALING:
        assert needle not in text, needle


def test_blind_payloads_lift_on_grade_only(api: Any) -> None:
    """Through the routes: neither ★ route reveals a scorecard image -- the
    judgment, the media detail and the T2I row stay blind -- a grade does,
    clearing it hides the image again (★ still on), ✗ reveals it as ✓
    does, and un-starring a graded image keeps it revealed."""
    client, lib, _ = api
    base, _adapter = lib.pair(0)
    lib.db.add_tag_indices(base, ["harbour"], source="clip")

    def payloads() -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        judgment = client.get(f"/api/judgments/{base}").json()
        detail = client.get(f"/api/media/{base}").json()
        rows = {
            os.path.normpath(r["file_path"]): r
            for r in client.get("/api/t2i/images").json()
        }
        return judgment, detail, rows[os.path.normpath(base)]

    def assert_hidden() -> None:
        judgment, detail, row = payloads()
        assert judgment["scorecard"] == {"id": lib.card, "blind": True}
        assert detail["scorecard_blind"] is True
        assert detail["prompt"] is None and detail["tags"] == []
        assert row["scorecard_blind"] is True
        assert row["prompt_used"] is None and row["writer_id"] is None
        for blob in (judgment, detail, row):
            _assert_blind(blob)

    def assert_shown() -> None:
        judgment, detail, row = payloads()
        assert judgment["scorecard"] == {"id": lib.card, "blind": False}
        assert detail["scorecard_blind"] is False
        assert detail["prompt"] == "SYNTH prompt"
        assert [t["name"] for t in detail["tags"]] == ["harbour"]
        assert row["scorecard_blind"] is False
        assert row["prompt_used"] == "SYNTH prompt" and row["writer_id"] == "base"

    assert_hidden()
    r = client.patch(f"/api/judgments/{base}", json={"favorite": True})
    assert r.status_code == 200 and r.json()["favorite"] is True
    assert_hidden()
    assert (
        client.patch(f"/api/media/{base}", json={"is_favorite": False}).status_code
        == 200
    )
    assert (
        client.patch(f"/api/media/{base}", json={"is_favorite": True}).status_code
        == 200
    )
    assert_hidden()
    _grade(client, base, "pass")
    assert_shown()
    _grade(client, base, None)
    assert_hidden()
    _grade(client, base, "fail")
    assert_shown()
    r = client.patch(f"/api/judgments/{base}", json={"favorite": False})
    assert r.status_code == 200 and r.json()["grade"] == "fail"
    assert_shown()


def test_media_detail_is_blind_until_a_grade(api: Any) -> None:
    client, lib, _ = api
    base, _adapter = lib.pair(0)
    lib.db.add_tag_indices(base, ["harbour"], source="clip")
    detail = client.get(f"/api/media/{base}").json()
    assert detail["scorecard_blind"] is True
    assert detail["scorecard_id"] == lib.card
    assert detail["prompt"] is None and detail["negative_prompt"] is None
    assert detail["tags"] == []
    # What the viewer needs to render it stays.
    assert (detail["width"], detail["height"], detail["format"]) == (8, 8, "png")
    assert detail["file_path"]
    _assert_blind(detail)
    client.patch(f"/api/judgments/{base}", json={"grade": "fail"})
    detail = client.get(f"/api/media/{base}").json()
    assert detail["scorecard_blind"] is False
    assert detail["prompt"] == "SYNTH prompt"
    assert [t["name"] for t in detail["tags"]] == ["harbour"]


def test_t2i_image_rows_are_blind_until_a_grade(api: Any) -> None:
    client, lib, _ = api
    base, adapter = lib.pair(0)
    plain = lib.image("base", "other", None)
    rows = {
        os.path.normpath(r["file_path"]): r
        for r in client.get("/api/t2i/images").json()
    }
    for path in (base, adapter):
        row = rows[os.path.normpath(path)]
        assert row["scorecard_blind"] is True
        assert row["scorecard_id"] == lib.card
        for key in (
            "prompt_used",
            "negative_used",
            "writer_id",
            "writer_model",
            "variant_index",
            "variant_group",
            "draft_prompt",
            "edited",
            "resolved_caption",
            "direction",
            "prompt_categories",
        ):
            assert row[key] is None, key
        # Still a complete form shape, without the prompt.
        assert row["form_state"]["prompt"] in ("", None)
        assert row["seed"] == 5 and row["file_name"]
        _assert_blind(row)
    assert rows[os.path.normpath(plain)]["prompt_used"] == "SYNTH prompt"
    assert rows[os.path.normpath(plain)]["writer_id"] == "base"
    # The PATCH answer of a blind image is blind too.
    image_id = rows[os.path.normpath(base)]["id"]
    patched = client.patch(f"/api/t2i/images/{image_id}", json={"seed": 9})
    assert patched.status_code == 200
    _assert_blind(patched.json())
    # After a grade the next fetch is full.
    _grade(client, base, "pass")
    rows = {
        os.path.normpath(r["file_path"]): r
        for r in client.get("/api/t2i/images").json()
    }
    row = rows[os.path.normpath(base)]
    assert row["scorecard_blind"] is False
    assert row["prompt_used"] == "SYNTH prompt"
    assert row["writer_id"] == "base" and row["writer_model"] == BASE
    assert row["draft_prompt"] == "SYNTH draft"
    assert rows[os.path.normpath(adapter)]["writer_id"] is None


def test_writer_stats_carry_scorecard_rows(lib: Any) -> None:
    from backend.services.taste_service import TasteService

    base, adapter = lib.pair(0)
    lib.db.set_grade(base, "fail")
    lib.db.set_grade(adapter, "pass")

    class _Runner:
        training = False

    service = TasteService(
        lib.db, _Runner(), get_config=lambda: {}, broadcast=lambda *a: None
    )
    out = asyncio.run(service.writer_stats())
    (row,) = out["scorecards"]
    assert row["adapter_id"] == lib.adapter
    assert row["name"] == "taste-all-v1"
    assert (row["n_judged"], row["pass_rate_base"], row["pass_rate_adapter"]) == (
        1,
        0.0,
        1.0,
    )
