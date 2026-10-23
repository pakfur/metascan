"""Writer-adapter scorecard (taste learning, task 9).

A scorecard renders every developer-written brief twice -- once with the
base writer, once with the adapter, same seed -- tagged ``scorecard_id``.
The owner judges the images blind; the adapter goes ``untested -> ready``
once 30 pairs are judged. The fakes are the T2I runner tests' (FakeVlm,
FakeComfy) through the real runner; captions and prompts are synthetic or
the committed briefs file.
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
    fav: bool = False,
    passed: bool = False,
    seen: bool = False,
    score: Optional[float] = None,
) -> Dict[str, Any]:
    return {
        "batch_id": batch,
        "writer_id": writer,
        "is_favorite": fav,
        "passed": passed,
        "seen_at": "2026-10-01T00:00:00+00:00" if seen else None,
        "score": score,
    }


def test_results_math() -> None:
    a = adapter_writer_id(7)
    rows = [
        # pair 1: both judged, adapter favorited
        _row("b1", "base", passed=True, score=0.2),
        _row("b1", a, fav=True, score=0.8),
        # pair 2: both judged, base favorited
        _row("b2", "base", fav=True, score=0.6),
        _row("b2", a, passed=True, score=0.4),
        # pair 3: adapter only seen (no verdict) -> not a judged pair
        _row("b3", "base", fav=True),
        _row("b3", a, seen=True),
        # pair 5: neither has an explicit verdict
        _row("b5", "base", seen=True),
        _row("b5", a, seen=True),
        # an unpaired image (its partner never rendered)
        _row("b4", a, fav=True, score=1.0),
        # another adapter's image never counts
        _row("b1", adapter_writer_id(8), fav=True, score=0.0),
    ]
    out = sc.compute_results(rows, 7)
    assert out["adapter_id"] == 7
    assert out["n_pairs"] == 4
    assert out["n_judged"] == 2
    assert out["fav_rate_base"] == pytest.approx(0.5)
    assert out["fav_rate_adapter"] == pytest.approx(0.5)
    assert out["mean_score_base"] == pytest.approx(0.4)
    assert out["mean_score_adapter"] == pytest.approx((0.8 + 0.4 + 1.0) / 3)


def test_results_with_nothing_judged_are_null() -> None:
    out = sc.compute_results([_row("b1", "base"), _row("b1", "adapter:7")], 7)
    assert out["n_pairs"] == 1 and out["n_judged"] == 0
    assert out["fav_rate_base"] is None and out["fav_rate_adapter"] is None
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

    def image(self, writer: str, batch: str, scorecard: Optional[str]) -> str:
        self.n += 1
        path = str(self.root / f"img{self.n}.png")
        self.db.save_media(_media(path, prompt="SYNTH prompt"))
        self.db.create_t2i_image(
            file_path=path,
            batch_id=batch,
            model="krea2",
            caption="A quiet harbour at dawn",
            prompt_used="SYNTH prompt",
            negative_used="SYNTH negative",
            seed=5,
            writer_id=writer,
            writer_model=BASE,
            variant_group="group-1",
            variant_index=self.n % 2,
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


def test_blind_flag_on_t2i_rows_until_an_explicit_verdict(lib: Any) -> None:
    base, adapter = lib.pair(0)
    plain = lib.image("base", "other", None)
    rows = {r["file_path"]: r for r in lib.db.list_t2i_images()}
    assert rows[os.path.normpath(base)]["scorecard_blind"] is True
    assert rows[os.path.normpath(adapter)]["scorecard_blind"] is True
    assert rows[os.path.normpath(plain)]["scorecard_blind"] is False
    assert rows[os.path.normpath(base)]["scorecard_id"] == lib.card
    # Seen only: neither judged nor un-blinded.
    lib.db.mark_seen(base)
    lib.db.set_verdict(adapter, "pass")
    rows = {r["file_path"]: r for r in lib.db.list_t2i_images()}
    assert rows[os.path.normpath(base)]["scorecard_blind"] is True
    assert rows[os.path.normpath(adapter)]["scorecard_blind"] is False
    assert rows[os.path.normpath(adapter)]["passed"] is True
    lib.db.set_verdict(base, "favorite")
    rows = {r["file_path"]: r for r in lib.db.list_t2i_images()}
    assert rows[os.path.normpath(base)]["scorecard_blind"] is False
    info = lib.db.scorecard_for_path(base)
    assert info == {"scorecard_id": lib.card, "adapter_id": lib.adapter, "blind": False}
    assert lib.db.scorecard_for_path(plain) is None


async def test_untested_becomes_ready_at_30_judged_pairs_and_broadcasts(
    lib: Any,
) -> None:
    sent: List[Tuple[str, Dict[str, Any]]] = []
    pairs = [lib.pair(i) for i in range(31)]
    for i, (base, adapter) in enumerate(pairs[:29]):
        lib.db.set_verdict(base, "pass")
        lib.db.set_verdict(adapter, "favorite" if i % 2 else "pass")
        await sc.note_scorecard_judgment(
            lib.db, adapter, lambda e, d: sent.append((e, d))
        )
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "untested"
    assert [s for s in sent if s[0] != "scorecard_judged"] == []
    base, adapter = pairs[29]
    lib.db.set_verdict(base, "favorite")
    await sc.note_scorecard_judgment(lib.db, base, lambda e, d: sent.append((e, d)))
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "untested"
    lib.db.mark_seen(adapter)  # seen without a verdict: NOT judged
    await sc.note_scorecard_judgment(lib.db, adapter, lambda e, d: sent.append((e, d)))
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "untested"
    lib.db.set_verdict(adapter, "pass")  # the explicit verdict: pair 30 is judged
    await sc.note_scorecard_judgment(lib.db, adapter, lambda e, d: sent.append((e, d)))
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "ready"
    changed = [s for s in sent if s[0] == "adapters_changed"]
    assert changed == [("adapters_changed", {})]
    # Every scorecard judgment is announced (the window refetches results).
    assert ("scorecard_judged", {"adapter_id": lib.adapter}) in sent
    assert len([s for s in sent if s[0] == "scorecard_judged"]) == 32
    # Ready stays ready (and is not re-announced) even when verdicts go.
    lib.db.set_verdict(base, None)
    await sc.note_scorecard_judgment(lib.db, base, lambda e, d: sent.append((e, d)))
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "ready"
    assert [s for s in sent if s[0] == "adapters_changed"] == changed
    result = await sc.evaluate_adapter(lib.db, lib.adapter, lambda e, d: None)
    assert result["n_pairs"] == 31


async def test_a_non_scorecard_judgment_is_ignored(lib: Any) -> None:
    plain = lib.image("base", "other", None)
    lib.db.set_verdict(plain, "favorite")
    with mock.patch.object(sc, "evaluate_adapter") as evaluate:
        await sc.note_scorecard_judgment(lib.db, plain, lambda e, d: None)
    evaluate.assert_not_called()


async def test_seen_only_pairs_never_count_as_judged(lib: Any) -> None:
    """Ruling: a scorecard image is judged only by an explicit verdict
    (favorite or pass), like the blind lift; seen-only stays a taste label
    elsewhere but never counts toward n_judged or promotion."""
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
    assert result["fav_rate_base"] is None and result["fav_rate_adapter"] is None
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "untested"
    assert [s for s in sent if s[0] == "adapters_changed"] == []
    for base, adapter in pairs:
        lib.db.set_verdict(base, "pass")
        lib.db.set_verdict(adapter, "favorite")
        await sc.note_scorecard_judgment(
            lib.db, adapter, lambda e, d: sent.append((e, d))
        )
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "ready"
    result = await sc.evaluate_adapter(lib.db, lib.adapter, lambda e, d: None)
    assert result["n_judged"] == 30
    assert (result["fav_rate_base"], result["fav_rate_adapter"]) == (0.0, 1.0)


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


def test_scorecard_images_never_become_training_data(lib: Any) -> None:
    for i in range(3):
        base, adapter = lib.pair(i)
        lib.db.set_verdict(adapter, "favorite")
        lib.db.mark_seen(base)
        lib.db.set_hindsight_brief(adapter, "A quiet harbour", "qwen3vl-8b")
    sft, prefs, counts = build_examples(
        lib.db,
        "all",
        get_prompt_store(),
        datetime.now(timezone.utc) + timedelta(days=1),
        content_mode="default",
    )
    assert sft == [] and prefs == []
    assert counts.pref_pairs == 0 and counts.hindsight == 0


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
            self.db.set_verdict(r["file_path"], "pass")
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
    r = client.patch(f"/api/judgments/{base}", json={"verdict": "favorite"})
    assert r.status_code == 200
    assert client.get(f"/api/judgments/{base}").json()["scorecard"]["blind"] is False
    assert client.get(f"/api/media/{base}").json()["scorecard_blind"] is False
    client.post("/api/judgments/seen", json={"file_path": adapter})
    # Seen only: the pair is not judged yet.
    one = client.get(f"/api/writers/adapters/{lib.adapter}/scorecard").json()
    assert one["n_pairs"] == 1 and one["n_judged"] == 0
    client.patch(f"/api/judgments/{adapter}", json={"verdict": "pass"})

    one = client.get(f"/api/writers/adapters/{lib.adapter}/scorecard").json()
    assert one["n_pairs"] == 1 and one["n_judged"] == 1
    assert one["fav_rate_base"] == 1.0 and one["fav_rate_adapter"] == 0.0
    assert one["ready_pairs"] == 30
    assert one["latest"]["id"] == lib.card
    everything = client.get("/api/writers/scorecards").json()
    assert everything["briefs"] == 40
    assert everything["running"] is None
    (row,) = everything["adapters"]
    assert row["adapter_id"] == lib.adapter and row["name"] == "taste-all-v1"
    assert everything["base"]["n_judged"] == 1
    assert everything["base"]["fav_rate"] == 1.0
    plain = lib.image("base", "x", None)
    assert client.get(f"/api/judgments/{plain}").json()["scorecard"] is None
    assert client.get(f"/api/media/{plain}").json()["scorecard_blind"] is False


def test_a_verdict_through_the_routes_promotes_the_adapter(api: Any) -> None:
    client, lib, _ = api
    sent: List[Tuple[str, str, Dict[str, Any]]] = []
    pairs = [lib.pair(i) for i in range(30)]
    with mock.patch(
        "backend.ws.manager.ws_manager.broadcast_sync",
        side_effect=lambda c, e, d: sent.append((c, e, d)),
    ):
        for base, adapter in pairs[:29]:
            lib.db.set_verdict(base, "pass")
            lib.db.set_verdict(adapter, "pass")
        base, adapter = pairs[29]
        client.patch(f"/api/judgments/{base}", json={"verdict": "pass"})
        assert lib.db.get_writer_adapter(lib.adapter)["status"] == "untested"
        # Seen without a verdict never judges a scorecard image.
        client.post("/api/judgments/seen", json={"file_path": adapter})
        assert lib.db.get_writer_adapter(lib.adapter)["status"] == "untested"
        assert [s for s in sent if s[1] == "adapters_changed"] == []
        client.patch(f"/api/media/{adapter}", json={"is_favorite": True})
    assert lib.db.get_writer_adapter(lib.adapter)["status"] == "ready"
    assert ("training", "adapters_changed", {}) in sent


_REVEALING = ("SYNTH", "adapter:", BASE)


def _assert_blind(blob: Any) -> None:
    text = json.dumps(blob)
    for needle in _REVEALING:
        assert needle not in text, needle


def test_media_detail_is_blind_until_a_verdict(api: Any) -> None:
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
    client.patch(f"/api/judgments/{base}", json={"verdict": "pass"})
    detail = client.get(f"/api/media/{base}").json()
    assert detail["scorecard_blind"] is False
    assert detail["prompt"] == "SYNTH prompt"
    assert [t["name"] for t in detail["tags"]] == ["harbour"]


def test_t2i_image_rows_are_blind_until_a_verdict(api: Any) -> None:
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
    # After a verdict the next fetch is full.
    client.patch(f"/api/judgments/{base}", json={"verdict": "favorite"})
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
    lib.db.set_verdict(base, "pass")
    lib.db.set_verdict(adapter, "favorite")

    class _Runner:
        training = False

    service = TasteService(
        lib.db, _Runner(), get_config=lambda: {}, broadcast=lambda *a: None
    )
    out = asyncio.run(service.writer_stats())
    (row,) = out["scorecards"]
    assert row["adapter_id"] == lib.adapter
    assert row["name"] == "taste-all-v1"
    assert (row["n_judged"], row["fav_rate_base"], row["fav_rate_adapter"]) == (
        1,
        0.0,
        1.0,
    )
