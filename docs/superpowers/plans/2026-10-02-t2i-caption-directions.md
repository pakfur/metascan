# T2I Caption Directions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a caption's classification scores pass their thresholds, hand the t2i prompt-writing model a short DIRECTION (snippets picked from wildcard files per act, kissing and emotion) so the generated prompt carries the emotion and act the caption only implies.

**Architecture:** A one-off `merge.py` appends classification columns to the captions CSV. `CaptionStore` parses them into `CaptionRow.classification` and gains `find(caption)`. A new pure module `metascan/core/t2i_directions.py` loads screened snippet lists from `data/t2i_captions/directions/` and builds a `Direction` from a row, the seed, the content mode and the `t2i.directions` settings. `compose_t2i_prompts` / `fallback_prompt` take the direction text; `T2iRunner` wires it into Manual prompts and Random steps; the dialog gets a toggle and a read-only Direction line.

**Tech Stack:** Python 3.11, numpy, unittest/pytest; FastAPI/pydantic; Vue 3 + TypeScript + Pinia.

**Spec:** `docs/superpowers/specs/2026-10-02-t2i-caption-directions-design.md`

## Global Constraints

- Work on branch `feature/caption-classifier` (not merged; do not merge). Run tools from the main venv directly: `venv/bin/pytest`, `venv/bin/black`, `venv/bin/mypy` — never `make` (it re-runs `python3 -m venv venv`).
- The caption text is never edited; only columns are appended, by `merge.py`, and only with `--replace` is the CSV swapped (with a timestamped backup).
- No direction, a row without classification, or directions disabled → the system and user prompts are byte-identical to today's.
- Every snippet line passes `t2i_wildcards._reject_reason` (the adult-only screen and the no-parentheses rule). Adult-only is a load-time invariant: never loosen it.
- Snippet choice is `sha256(f"{seed}|direction|{file_key}|0")` modulo the list length (via `t2i_characters._draw`). Never `hash()`.
- `content_mode == "sfw"` → no act and no kissing snippets, and emotion snippets only from `emotion.txt`.
- Thresholds (defaults): `emotion_missing_min 0.70` (fires when `1 − Emotion Explicit ≥` it, so implicit counts as missing), `emotion_sensual_from 0.60`, `kiss_min 0.80`, `act_min 0.80`, `skip_act_on_conflict true`, `enabled true`.
- Kissing is added alongside an act. Nothing new is stored per image (no DB change).
- Snippet lists live in `data/t2i_captions/directions/` — a subdirectory, so the top-level wildcard loader (`glob("*.txt")`, non-recursive) never sees them.
- Frontend: Vue 3 `<script setup>`, strict TS, `npm run build` must pass. Never write the form from a watcher (T2I autosave-drift rule).

**Deviations from the spec, decided while planning:**
- Classification values are parsed per row in `_read_row` (like the existing score cells), not kept as numpy arrays at index time: nothing filters on them yet (filters are a non-goal), and it keeps the index unchanged except for the `find` hash map.
- `find` is backed by a map built during the normal index scan (first 8 bytes of each caption's SHA-1 → row id; the row text is compared on a hit), not a lazily-built index. It costs one SHA-1 per caption during the scan (~0.1 s for 80 MB) and ~10 MB. A row whose `Caption SHA1` cell disagrees with its caption text gets `classification = None`, so an edited caption can never borrow a stale classification.
- The "Caption directions" checkbox is remembered per browser in `localStorage` (`metascan.t2i.directions.v1`) instead of being added to the autosaved per-image `form_state`, because nothing is stored per image (decided) and `form_state` is a server-validated schema.
- An empty or missing snippet file for a part that passed its threshold produces a warning on that step (spec §9); a CSV with no classification columns produces none.
- Out-of-range thresholds are clamped silently, without the spec's server-log warning: `get_t2i_config` runs on every request and batch, so a log line there would repeat endlessly.

## Review Focus

1. **A caption edited in the CSV after the merge** — its `Caption SHA1` no longer matches; it must get no direction (never the old caption's). Test in Task 2.
2. **Two identical captions in the CSV** — `find` must return a row whose text equals the query (the first), never crash or return a row with different text on a hash-prefix collision. Test in Task 2.
3. **A snippet file containing a rejected line or parentheses** — the line is dropped with a warning; the other lines still work; nothing raises into a batch. Test in Task 3.
4. **`content_mode` sfw with a confident act** — no act or kissing text reaches the VLM, and the sensual emotion list is not used. Test in Task 3.
5. **`find` raising (unreadable CSV mid-run)** — the step proceeds with no direction and a warning; the batch does not die. Test in Task 6.

---

## File Structure

| Path | Responsibility |
|---|---|
| `scripts/caption_classifier/merge.py` (create) | Append classification columns to the captions CSV; `--replace` swaps it in with a backup |
| `metascan/core/t2i_captions.py` (modify) | `Classification` dataclass, `CaptionRow.classification`, per-row parsing, `CaptionStore.find` |
| `metascan/core/t2i_directions.py` (create) | `DirectionSettings`, `SnippetCache`, `Direction`, `build_direction` |
| `backend/config.py` (modify) | `t2i.directions` section in `get_t2i_config` |
| `metascan/core/t2i_prompt.py` (modify) | `direction` argument on `compose_t2i_prompts` and `fallback_prompt` |
| `data/meta_prompt.yml` (modify) | One rule in `T2I_CAPTION_PREAMBLE` |
| `metascan/core/t2i_runner.py` (modify) | Directions for Manual prompts and Random steps; `directions` request flag; `direction` / `direction_parts` on results and steps |
| `backend/api/t2i.py`, `backend/main.py` (modify) | Request flag, response fields, `SnippetCache` wiring |
| `frontend/src/types/t2i.ts`, `api/t2i.ts`, `stores/t2i.ts`, `components/dialogs/T2IDialog.vue` (modify) | Toggle, request flag, Direction line |
| `data/t2i_captions/directions/*.txt` (create) | Starter snippet lists |
| `.claude/rules/t2i.md`, `docs/t2i.md`, `scripts/caption_classifier/README.md` (modify) | Rules and user docs |
| Tests (create) | `tests/test_caption_classifier_merge.py`, `tests/test_t2i_caption_classification.py`, `tests/test_t2i_directions.py`, `tests/test_t2i_runner_directions.py` |
| Tests (modify) | `tests/test_t2i_config.py`, `tests/test_t2i_prompt.py`, `tests/test_t2i_api.py` |

---

### Task 1: `merge.py` — classification columns into the captions CSV

**Files:**
- Create: `scripts/caption_classifier/merge.py`
- Test: `tests/test_caption_classifier_merge.py`

**Interfaces:**
- Consumes: `results.load_records`, `results.newest_results`, `results.caption_sha1`, `rubric.PARTNER`, `rubric.EMOTION`, `rubric.ACT_BY_LETTER`, `classify.DEFAULT_CSV`, `classify.DEFAULT_OUT`.
- Produces: `merge.CLASS_COLUMNS: List[str]` = `["Caption SHA1", "Emotion", "Emotion Explicit", "Kiss", "Partner", "Act", "Act P", "Act Conflict", "Issues"]`; `merge.classification_cells(rec: Dict[str, Any]) -> Dict[str, str]`; `merge.MergeStats` dataclass (`filled, stale, unclassified, errors, blank: int`); `merge.merge_csv(csv_path: Path, records: Dict[int, Dict[str, Any]], dest: Path) -> MergeStats`; `merge.main(argv) -> int`. Cell formats: probabilities `f"{p:.4f}"`, labels by name, `Act Conflict` `true`/`false`, `Issues` `;`-joined types.

- [ ] **Step 1: Write the failing tests**

`tests/test_caption_classifier_merge.py`:

```python
"""Tests for merging classifier results into the captions CSV."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.caption_classifier import merge
from scripts.caption_classifier.results import caption_sha1

HEADER = ["Caption", "Aspect Ratio", "Erotic Score"]
CAPTIONS = ["__ALICE__ smiles.", "__BELLA__ kneels.", "__ALICE__ reads."]


def _write_csv(path: Path, rows, header=HEADER):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def _rec(row_id, caption, act="L", status="ok"):
    act_dist = {letter: 0.0 for letter in "ABCDEFGHIJKLMNOPQRS"}
    act_dist[act] = 0.9
    act_dist["A"] = round(act_dist["A"] + 0.1, 4)
    return {
        "row_id": row_id, "caption_sha1": caption_sha1(caption), "status": status,
        "partner": {"A": 0.2, "B": 0.8, "C": 0.0, "D": 0.0},
        "kiss": {"Y": 0.85, "N": 0.15},
        "emotion": {"A": 0.6, "B": 0.3, "C": 0.1},
        "act_raw": act_dist, "act_gated": act_dist, "act_gate_conflict": False,
        "issues": [{"type": "extra_limb"}, {"type": "gaze_conflict"}],
    }


def _read(path: Path):
    return list(csv.DictReader(path.open(encoding="utf-8")))


def test_cells_use_names_and_four_decimals():
    cells = merge.classification_cells(_rec(0, CAPTIONS[0]))
    assert cells == {
        "Caption SHA1": caption_sha1(CAPTIONS[0]),
        "Emotion": "none",
        "Emotion Explicit": "0.1000",
        "Kiss": "0.8500",
        "Partner": "male",
        "Act": "doggy",
        "Act P": "0.9000",
        "Act Conflict": "false",
        "Issues": "extra_limb;gaze_conflict",
    }


def test_columns_are_filled_only_where_the_caption_still_matches(tmp_path):
    src = tmp_path / "c.csv"
    _write_csv(src, [[c, "1:1", "0.5"] for c in CAPTIONS])
    records = {
        0: _rec(0, CAPTIONS[0]),
        1: _rec(1, "an older caption text"),  # edited since classification
        2: {**_rec(2, CAPTIONS[2]), "status": "error", "error": "HTTP 400"},
    }
    dest = tmp_path / "m.csv"
    stats = merge.merge_csv(src, records, dest)
    rows = _read(dest)
    assert list(rows[0])[: len(HEADER)] == HEADER
    assert list(rows[0])[len(HEADER):] == merge.CLASS_COLUMNS
    assert [r["Caption"] for r in rows] == CAPTIONS
    assert rows[0]["Act"] == "doggy"
    assert rows[1]["Act"] == "" and rows[1]["Caption SHA1"] == ""
    assert rows[2]["Act"] == ""
    assert (stats.filled, stats.stale, stats.errors, stats.unclassified) == (1, 1, 1, 0)


def test_row_ids_skip_blank_captions_like_the_caption_store(tmp_path):
    src = tmp_path / "c.csv"
    _write_csv(src, [[CAPTIONS[0], "1:1", "0.5"], ["   ", "1:1", "0.5"],
                     [CAPTIONS[1], "1:1", "0.5"]])
    records = {0: _rec(0, CAPTIONS[0]), 1: _rec(1, CAPTIONS[1])}
    dest = tmp_path / "m.csv"
    stats = merge.merge_csv(src, records, dest)
    rows = _read(dest)
    assert [r["Act"] for r in rows] == ["doggy", "", "doggy"]
    assert stats.blank == 1 and stats.filled == 2


def test_merging_again_replaces_the_columns(tmp_path):
    src = tmp_path / "c.csv"
    _write_csv(src, [[c, "1:1", "0.5"] for c in CAPTIONS])
    first = tmp_path / "m1.csv"
    merge.merge_csv(src, {0: _rec(0, CAPTIONS[0])}, first)
    second = tmp_path / "m2.csv"
    merge.merge_csv(first, {0: _rec(0, CAPTIONS[0], act="M")}, second)
    with second.open(encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    assert header == HEADER + merge.CLASS_COLUMNS
    assert _read(second)[0]["Act"] == "cowgirl"


def test_replace_keeps_a_backup_and_swaps_the_file(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "results-aaaaaaaaaaaa.jsonl").write_text(
        json.dumps(_rec(0, CAPTIONS[0])) + "\n"
    )
    src = tmp_path / "c.csv"
    _write_csv(src, [[c, "1:1", "0.5"] for c in CAPTIONS])
    original = src.read_bytes()
    assert merge.main(["--csv", str(src), "--out", str(out)]) == 0
    assert src.read_bytes() == original  # without --replace nothing is swapped
    assert (tmp_path / "c.merged.csv").exists()
    assert merge.main(["--csv", str(src), "--out", str(out), "--replace"]) == 0
    backups = list(tmp_path.glob("c.csv.bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert _read(src)[0]["Act"] == "doggy"
    assert not (tmp_path / "c.merged.csv").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/pytest tests/test_caption_classifier_merge.py -q`
Expected: FAIL with `ImportError: cannot import name 'merge'`

- [ ] **Step 3: Write the implementation**

`scripts/caption_classifier/merge.py`:

```python
"""Append the classifier's results to the captions CSV as extra columns.

Usage:
    python -m scripts.caption_classifier.merge [--csv PATH] [--out DIR]
        [--results PATH] [--replace]

Writes <csv stem>.merged.csv next to the captions CSV. With --replace the
original is kept as <csv>.bak-<timestamp> and the merged file takes its
place (an atomic rename). A row gets classification cells only when its
caption's SHA-1 still equals the one recorded at classification time; an
edited, failed or unclassified row gets blank cells, which means "no
direction". Row ids count non-blank captions, exactly like CaptionStore.
Running it again replaces the classification columns, never duplicates them.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .classify import DEFAULT_CSV, DEFAULT_OUT
from .results import caption_sha1, load_records, newest_results
from .rubric import ACT_BY_LETTER, EMOTION, PARTNER

CLASS_COLUMNS: List[str] = [
    "Caption SHA1",
    "Emotion",
    "Emotion Explicit",
    "Kiss",
    "Partner",
    "Act",
    "Act P",
    "Act Conflict",
    "Issues",
]


def _norm(name: str) -> str:
    return " ".join(name.replace("﻿", "").split()).lower()


_CLASS_KEYS = {_norm(c) for c in CLASS_COLUMNS}


@dataclass
class MergeStats:
    filled: int = 0
    stale: int = 0  # caption changed since classification
    unclassified: int = 0  # no record for the row
    errors: int = 0  # the record is an error row
    blank: int = 0  # blank caption: not a CaptionStore row


def _top(dist: Dict[str, float]) -> str:
    return max(dist, key=lambda k: dist[k])


def classification_cells(rec: Dict[str, Any]) -> Dict[str, str]:
    act = _top(rec["act_gated"])
    return {
        "Caption SHA1": str(rec["caption_sha1"]),
        "Emotion": EMOTION[_top(rec["emotion"])],
        "Emotion Explicit": f"{rec['emotion']['C']:.4f}",
        "Kiss": f"{rec['kiss']['Y']:.4f}",
        "Partner": PARTNER[_top(rec["partner"])],
        "Act": ACT_BY_LETTER[act].name,
        "Act P": f"{rec['act_gated'][act]:.4f}",
        "Act Conflict": "true" if rec["act_gate_conflict"] else "false",
        "Issues": ";".join(i["type"] for i in rec["issues"]),
    }


def merge_csv(
    csv_path: Path, records: Dict[int, Dict[str, Any]], dest: Path
) -> MergeStats:
    stats = MergeStats()
    with csv_path.open("r", newline="", encoding="utf-8", errors="replace") as src:
        reader = csv.reader(src)
        header = next(reader)
        keep = [i for i, name in enumerate(header) if _norm(name) not in _CLASS_KEYS]
        norm = [_norm(h) for h in header]
        caption_at = norm.index("caption")
        row_id = 0
        with dest.open("w", newline="", encoding="utf-8") as out:
            writer = csv.writer(out, lineterminator="\n")
            writer.writerow([header[i] for i in keep] + CLASS_COLUMNS)
            for row in reader:
                if not row:
                    continue
                base = [row[i] if i < len(row) else "" for i in keep]
                caption = row[caption_at] if caption_at < len(row) else ""
                cells = [""] * len(CLASS_COLUMNS)
                if not caption.strip():
                    stats.blank += 1
                else:
                    rec = records.get(row_id)
                    row_id += 1
                    if rec is None:
                        stats.unclassified += 1
                    elif rec.get("status") != "ok":
                        stats.errors += 1
                    elif rec["caption_sha1"] != caption_sha1(caption):
                        stats.stale += 1
                    else:
                        filled = classification_cells(rec)
                        cells = [filled[c] for c in CLASS_COLUMNS]
                        stats.filled += 1
                writer.writerow(base + cells)
    return stats


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Merge classifier results into the captions CSV.")
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--results", type=Path)
    ap.add_argument("--replace", action="store_true")
    args = ap.parse_args(argv)
    try:
        results = args.results or newest_results(args.out)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not args.csv.exists():
        print(f"no captions CSV at {args.csv}", file=sys.stderr)
        return 2
    merged = args.csv.with_name(f"{args.csv.stem}.merged.csv")
    stats = merge_csv(args.csv, load_records(results), merged)
    print(
        f"{stats.filled} filled · {stats.stale} caption changed · "
        f"{stats.errors} error rows · {stats.unclassified} unclassified · "
        f"{stats.blank} blank captions → {merged}"
    )
    if args.replace:
        backup = args.csv.with_name(
            f"{args.csv.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy2(args.csv, backup)
        os.replace(merged, args.csv)
        print(f"replaced {args.csv} (backup: {backup.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/pytest tests/test_caption_classifier_merge.py -q`
Expected: 5 passed

- [ ] **Step 5: Format and commit**

```bash
venv/bin/black scripts/caption_classifier/merge.py tests/test_caption_classifier_merge.py
git add scripts/caption_classifier/merge.py tests/test_caption_classifier_merge.py
git commit -m "feat(caption-classifier): merge classification columns into the captions CSV"
```

---

### Task 2: `CaptionStore` — classification per row and `find`

**Files:**
- Modify: `metascan/core/t2i_captions.py` (dataclasses near line 95, `_Index` ~183, `_Accumulator.__init__`/`add`/`finish` ~200–315, `_read_row` ~377, `CaptionStore` ~660)
- Test: `tests/test_t2i_caption_classification.py`

**Interfaces:**
- Produces: `t2i_captions.Classification` (frozen: `emotion: str, emotion_explicit: float, kiss: float, partner: str, act: str, act_p: float, act_conflict: bool, issues: Tuple[str, ...]`); `CaptionRow.classification: Optional[Classification] = None` (last field, default None; `to_dict` unchanged); `CaptionStore.find(caption: str) -> Optional[CaptionRow]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_t2i_caption_classification.py`:

```python
"""Tests for the classification columns of the caption CSV and CaptionStore.find.

Every CSV is written here from hand-written rows -- never the real file.
"""

from __future__ import annotations

import csv
import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import List
from unittest import mock

from metascan.core.t2i_captions import CaptionStore, Classification

BASE = ["Caption", "Aspect Ratio", "Erotic Score"]
CLASS = ["Caption SHA1", "Emotion", "Emotion Explicit", "Kiss", "Partner",
         "Act", "Act P", "Act Conflict", "Issues"]


def sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def classified(caption: str, act: str = "doggy", digest: str = "") -> List[str]:
    return [caption, "1:1", "0.7", digest or sha(caption), "none", "0.1000",
            "0.8500", "male", act, "0.9000", "false", "extra_limb;gaze_conflict"]


class Case(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "c.csv"

    def write(self, header: List[str], rows: List[List[str]]) -> CaptionStore:
        with self.path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
        return CaptionStore(self.path)


class ClassificationTests(Case):
    def test_a_classified_row_carries_its_values(self) -> None:
        store = self.write(BASE + CLASS, [classified("__ALICE__ kneels.")])
        c = store.get(0).classification
        self.assertEqual(
            c,
            Classification(
                emotion="none", emotion_explicit=0.1, kiss=0.85, partner="male",
                act="doggy", act_p=0.9, act_conflict=False,
                issues=("extra_limb", "gaze_conflict"),
            ),
        )

    def test_blank_cells_mean_no_classification(self) -> None:
        store = self.write(BASE + CLASS, [["__ALICE__ sits.", "1:1", "0.2"] + [""] * 9])
        self.assertIsNone(store.get(0).classification)

    def test_a_caption_edited_after_the_merge_loses_its_classification(self) -> None:
        row = classified("__ALICE__ kneels.", digest=sha("__ALICE__ kneeled."))
        store = self.write(BASE + CLASS, [row])
        self.assertIsNone(store.get(0).classification)

    def test_an_unusable_number_means_no_classification(self) -> None:
        row = classified("__ALICE__ kneels.")
        row[len(BASE) + 2] = "lots"  # Emotion Explicit
        store = self.write(BASE + CLASS, [row])
        self.assertIsNone(store.get(0).classification)

    def test_a_csv_without_the_columns_works_as_before(self) -> None:
        store = self.write(BASE, [["__ALICE__ sits.", "1:1", "0.2"]])
        row = store.get(0)
        self.assertIsNone(row.classification)
        self.assertEqual(row.erotic_score, 0.2)
        self.assertNotIn("classification", row.to_dict())


class FindTests(Case):
    def test_find_returns_the_row_with_exactly_that_text(self) -> None:
        store = self.write(BASE + CLASS, [classified("A."), classified("B.", act="cowgirl")])
        row = store.find("B.")
        assert row is not None
        self.assertEqual((row.id, row.classification.act), (1, "cowgirl"))
        self.assertIsNone(store.find("B. "))  # exact text only
        self.assertIsNone(store.find("C."))

    def test_duplicate_captions_find_the_first(self) -> None:
        store = self.write(BASE, [["Same.", "1:1", "0.1"], ["Other.", "1:1", "0.1"],
                                  ["Same.", "1:1", "0.1"]])
        row = store.find("Same.")
        assert row is not None
        self.assertEqual(row.id, 0)

    def test_a_hash_prefix_collision_never_returns_other_text(self) -> None:
        store = self.write(BASE, [["Same.", "1:1", "0.1"]])
        store.find("Same.")  # build the index
        with mock.patch(
            "metascan.core.t2i_captions._caption_key", return_value=b"\x00" * 8
        ):
            index = store._current()
            assert index is not None
            index.by_hash[b"\x00" * 8] = 0
            self.assertIsNone(store.find("Different."))

    def test_find_follows_a_rewritten_file(self) -> None:
        store = self.write(BASE, [["Old.", "1:1", "0.1"]])
        self.assertIsNotNone(store.find("Old."))
        self.write(BASE, [["Newer caption.", "1:1", "0.1"]])
        self.assertIsNone(store.find("Old."))
        self.assertIsNotNone(store.find("Newer caption."))

    def test_find_on_a_missing_file_is_none(self) -> None:
        self.assertIsNone(CaptionStore(self.path).find("x"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/pytest tests/test_t2i_caption_classification.py -q`
Expected: FAIL with `ImportError: cannot import name 'Classification'`

- [ ] **Step 3: Write the implementation**

In `metascan/core/t2i_captions.py`:

1. Add `import hashlib` to the imports.
2. Above `CaptionRow`, add:

```python
@dataclass(frozen=True)
class Classification:
    """A row's caption-classifier result (merged in by
    ``scripts/caption_classifier/merge.py``); drives t2i directions."""

    emotion: str
    emotion_explicit: float
    kiss: float
    partner: str
    act: str
    act_p: float
    act_conflict: bool
    issues: Tuple[str, ...]
```

3. Add a last field to `CaptionRow` (after `clothing`), leaving `to_dict` unchanged:

```python
    classification: Optional["Classification"] = None
```

4. After `_as_array`, add:

```python
def _caption_key(caption: str) -> bytes:
    """Hash-map key for ``CaptionStore.find``: SHA-1 prefix of the exact text."""
    return hashlib.sha1(caption.encode("utf-8", "surrogatepass")).digest()[:8]


def _sha1_hex(caption: str) -> str:
    return hashlib.sha1(caption.encode("utf-8", "surrogatepass")).hexdigest()
```

5. In `_Index`, add a last field `by_hash: Dict[bytes, int]` (caption key → first row id).
6. In `_Accumulator.__init__`, add `self.by_hash: Dict[bytes, int] = {}`; in `add`, right after `self.total += 1`, add:

```python
        self.by_hash.setdefault(_caption_key(_cell(row, self.caption_at)), row_id)
```

   and in `finish`, pass `by_hash=self.by_hash`.
7. Before `_read_row`, add:

```python
def _classification(cell: Callable[[str], str], caption: str) -> Optional[Classification]:
    """The row's classification, or None when the cells are blank, unusable,
    or were computed for a different caption text."""
    act = cell("act").strip()
    if not act:
        return None
    digest = cell("caption sha1").strip().lower()
    if digest and digest != _sha1_hex(caption):
        return None
    numbers = [_parse_score(cell(name)) for name in ("emotion explicit", "kiss", "act p")]
    if any(math.isnan(n) for n in numbers):
        return None
    emotion_explicit, kiss, act_p = numbers
    issues = tuple(t for t in (p.strip() for p in cell("issues").split(";")) if t)
    return Classification(
        emotion=cell("emotion").strip().lower(),
        emotion_explicit=emotion_explicit,
        kiss=kiss,
        partner=cell("partner").strip().lower() or "none",
        act=act,
        act_p=act_p,
        act_conflict=cell("act conflict").strip().lower() == "true",
        issues=issues,
    )
```

   (add `Callable` to the `typing` import).
8. In `_read_row`, compute `caption = cell("caption")` once, use it for `caption=`, and add `classification=_classification(cell, caption)` to the `CaptionRow(...)` call.
9. In `CaptionStore`, after `get`, add:

```python
    def find(self, caption: str) -> Optional[CaptionRow]:
        """The first row whose caption is exactly ``caption``, or None."""
        index = self._current()
        if index is None:
            return None
        row_id = index.by_hash.get(_caption_key(caption))
        if row_id is None:
            return None
        try:
            row = _read_row(index, row_id)
        except KeyError:
            return None
        return row if row.caption == caption else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/pytest tests/test_t2i_caption_classification.py tests/test_t2i_captions.py -q`
Expected: all passed (10 new, the existing caption-store tests unchanged)

- [ ] **Step 5: Type-check, format and commit**

```bash
venv/bin/mypy --check-untyped-defs metascan/core/t2i_captions.py
venv/bin/black metascan/core/t2i_captions.py tests/test_t2i_caption_classification.py
git add metascan/core/t2i_captions.py tests/test_t2i_caption_classification.py
git commit -m "feat(t2i): caption store reads classification columns and finds rows by text"
```

---

### Task 3: `t2i_directions` — snippets, settings and `build_direction`

**Files:**
- Create: `metascan/core/t2i_directions.py`
- Test: `tests/test_t2i_directions.py`

**Interfaces:**
- Consumes: `CaptionRow`, `Classification` (Task 2); `t2i_wildcards._read_list(path, slot, warnings) -> Optional[Tuple[str, ...]]`; `t2i_characters._draw(key, name, slot, salt, size) -> int`.
- Produces:
  - `DIRECTIONS_DIRNAME = "directions"`; `NO_DIRECTION_ACTS = frozenset({"none-artistic", "unclear"})`
  - `DirectionSettings` frozen dataclass: `enabled: bool = True, emotion_missing_min: float = 0.70, emotion_sensual_from: float = 0.60, kiss_min: float = 0.80, act_min: float = 0.80, skip_act_on_conflict: bool = True`; `DirectionSettings.from_config(section: Mapping[str, Any]) -> DirectionSettings`
  - `Snippets = Mapping[str, Tuple[str, ...]]` (file key such as `"act.doggy.pov"` → lines)
  - `load_snippets(directory: Path) -> Tuple[Dict[str, Tuple[str, ...]], List[str]]`
  - `SnippetCache(directory: Path)` with `get() -> Tuple[Dict[str, Tuple[str, ...]], List[str]]` (never raises; reloads on change)
  - `Direction` frozen dataclass: `text: str, parts: Tuple[str, ...], warnings: Tuple[str, ...]`
  - `build_direction(row: Optional[CaptionRow], seed: int, content_mode: str, settings: DirectionSettings, snippets: Snippets) -> Optional[Direction]` — None when disabled or the row has no classification; otherwise a Direction (text may be empty when nothing passed or only warnings).

- [ ] **Step 1: Write the failing tests**

`tests/test_t2i_directions.py`:

```python
"""Tests for t2i caption directions: snippet loading and build_direction."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Dict, Optional, Tuple

from metascan.core.t2i_captions import CaptionRow, Classification
from metascan.core.t2i_directions import (
    Direction,
    DirectionSettings,
    SnippetCache,
    build_direction,
    load_snippets,
)

SNIPPETS: Dict[str, Tuple[str, ...]] = {
    "act.doggy": ("she is on all fours and he takes her from behind",),
    "act.doggy.pov": ("seen from his point of view, she is on all fours before him",),
    "kissing": ("they kiss deeply",),
    "emotion": ("a soft unguarded smile", "a calm, open expression"),
    "emotion.sensual": ("a flushed, heavy-lidded look of pleasure",),
}
S = DirectionSettings()


def row(
    *,
    act: str = "none-artistic",
    act_p: float = 0.0,
    conflict: bool = False,
    kiss: float = 0.0,
    explicit: float = 1.0,
    partner: str = "none",
    erotic: Optional[float] = 0.1,
    classified: bool = True,
) -> CaptionRow:
    c = Classification(
        emotion="explicit", emotion_explicit=explicit, kiss=kiss, partner=partner,
        act=act, act_p=act_p, act_conflict=conflict, issues=(),
    )
    return CaptionRow(
        id=0, caption="x", aspect_ratio="1:1", nudity=None, artistic_quality=None,
        erotic_score=erotic, pornographic_score=None, males=0, females=1,
        clothing=(), classification=c if classified else None,
    )


def build(r: CaptionRow, seed: int = 1, mode: str = "uncensored",
          settings: DirectionSettings = S, snippets=SNIPPETS) -> Optional[Direction]:
    return build_direction(r, seed, mode, settings, snippets)


class BuildTests(unittest.TestCase):
    def test_nothing_without_a_row_a_classification_or_when_disabled(self) -> None:
        self.assertIsNone(build_direction(None, 1, "uncensored", S, SNIPPETS))
        self.assertIsNone(build(row(classified=False)))
        self.assertIsNone(build(row(explicit=0.0), settings=DirectionSettings(enabled=False)))

    def test_below_every_threshold_gives_an_empty_direction(self) -> None:
        d = build(row(act="doggy", act_p=0.79, kiss=0.79, explicit=0.31))
        assert d is not None
        self.assertEqual((d.text, d.parts, d.warnings), ("", (), ()))

    def test_each_threshold_fires_at_its_boundary(self) -> None:
        d = build(row(act="doggy", act_p=0.80, kiss=0.80, explicit=0.30))
        assert d is not None
        self.assertEqual(d.parts, ("act:doggy", "kissing", "emotion"))

    def test_implicit_emotion_counts_as_missing(self) -> None:
        # emotion "implicit" has a low explicit probability: the snippet fires.
        d = build(row(explicit=0.05))
        assert d is not None
        self.assertEqual(d.parts, ("emotion",))

    def test_parts_become_capitalised_sentences_in_order(self) -> None:
        d = build(row(act="doggy", act_p=0.9, kiss=0.9, explicit=0.0, erotic=0.1), seed=3)
        assert d is not None
        sentences = d.text.split(". ")
        self.assertEqual(sentences[0], "She is on all fours and he takes her from behind")
        self.assertEqual(sentences[1], "They kiss deeply")
        self.assertTrue(d.text.endswith("."))

    def test_a_conflicting_act_is_skipped_unless_configured(self) -> None:
        r = row(act="doggy", act_p=0.9, conflict=True)
        self.assertEqual(build(r).parts, ())
        keep = DirectionSettings(skip_act_on_conflict=False)
        self.assertEqual(build(r, settings=keep).parts, ("act:doggy",))

    def test_none_artistic_and_unclear_never_direct(self) -> None:
        for act in ("none-artistic", "unclear"):
            self.assertEqual(build(row(act=act, act_p=1.0)).parts, ())

    def test_an_uncounted_partner_prefers_the_pov_list(self) -> None:
        d = build(row(act="doggy", act_p=0.9, partner="male"))
        assert d is not None
        self.assertEqual(d.parts, ("act:doggy:pov",))
        self.assertIn("point of view", d.text)
        no_pov = {k: v for k, v in SNIPPETS.items() if k != "act.doggy.pov"}
        d = build(row(act="doggy", act_p=0.9, partner="male"), snippets=no_pov)
        assert d is not None
        self.assertEqual(d.parts, ("act:doggy",))

    def test_high_erotic_score_uses_the_sensual_list_and_falls_back(self) -> None:
        d = build(row(explicit=0.0, erotic=0.6))
        assert d is not None
        self.assertEqual(d.parts, ("emotion:sensual",))
        no_sensual = {k: v for k, v in SNIPPETS.items() if k != "emotion.sensual"}
        d = build(row(explicit=0.0, erotic=0.6), snippets=no_sensual)
        assert d is not None
        self.assertEqual(d.parts, ("emotion",))

    def test_sfw_drops_act_and_kissing_and_the_sensual_list(self) -> None:
        d = build(row(act="doggy", act_p=0.99, kiss=0.99, explicit=0.0, erotic=0.9),
                  mode="sfw")
        assert d is not None
        self.assertEqual(d.parts, ("emotion",))
        self.assertNotIn("behind", d.text)

    def test_the_snippet_follows_the_seed(self) -> None:
        texts = {build(row(explicit=0.0), seed=s).text for s in range(40)}
        self.assertEqual(len(texts), 2)  # both emotion lines get used
        self.assertEqual(build(row(explicit=0.0), seed=7).text,
                         build(row(explicit=0.0), seed=7).text)

    def test_a_missing_list_is_a_warning_not_an_error(self) -> None:
        d = build(row(act="cowgirl", act_p=0.9, explicit=0.0))
        assert d is not None
        self.assertEqual(d.parts, ("emotion",))
        self.assertEqual(len(d.warnings), 1)
        self.assertIn("act.cowgirl.txt", d.warnings[0])


class LoadTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_lines_are_screened_and_parentheses_rejected(self) -> None:
        (self.dir / "emotion.txt").write_text(
            "# comment\n\na warm smile\na schoolgirl grin\na grin (wide)\n",
            encoding="utf-8",
        )
        snippets, warnings = load_snippets(self.dir)
        self.assertEqual(snippets["emotion"], ("a warm smile",))
        self.assertEqual(len(warnings), 2)

    def test_the_cache_reloads_on_change_and_never_raises(self) -> None:
        path = self.dir / "kissing.txt"
        path.write_text("they kiss\n", encoding="utf-8")
        cache = SnippetCache(self.dir)
        self.assertEqual(cache.get()[0]["kissing"], ("they kiss",))
        path.write_text("they kiss softly\n", encoding="utf-8")
        later = time.time() + 5
        os.utime(path, (later, later))
        self.assertEqual(cache.get()[0]["kissing"], ("they kiss softly",))
        missing = SnippetCache(self.dir / "nope")
        self.assertEqual(missing.get(), ({}, []))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/pytest tests/test_t2i_directions.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'metascan.core.t2i_directions'`

- [ ] **Step 3: Write the implementation**

`metascan/core/t2i_directions.py`:

```python
"""Caption directions for t2i prompt writing (spec 2026-10-02).

When a caption's classification passes a threshold, a short snippet is
picked from a list in ``data/t2i_captions/directions/`` and handed to the
prompt-writing model as a DIRECTION next to the description. Pure apart
from the cached file reads:

* lists are ``<key>.txt``: ``act.<act-name>[.pov]``, ``kissing``,
  ``emotion`` and ``emotion.sensual``; one snippet per line, ``#`` comments;
  every line passes the adult-only screen (``t2i_wildcards._read_list``);
* the line is picked from the seed exactly like a plain wildcard:
  ``sha256(f"{seed}|direction|{key}|0")`` modulo the list length;
* parts come in a fixed order -- act, kissing, emotion -- one sentence each;
* ``sfw`` content mode never gets an act or kissing part, and its emotion
  part always comes from ``emotion.txt``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from metascan.core.t2i_captions import CaptionRow
from metascan.core.t2i_characters import _draw
from metascan.core.t2i_wildcards import _read_list

logger = logging.getLogger(__name__)

DIRECTIONS_DIRNAME = "directions"
NO_DIRECTION_ACTS = frozenset({"none-artistic", "unclear"})

Snippets = Mapping[str, Tuple[str, ...]]


@dataclass(frozen=True)
class DirectionSettings:
    enabled: bool = True
    emotion_missing_min: float = 0.70
    emotion_sensual_from: float = 0.60
    kiss_min: float = 0.80
    act_min: float = 0.80
    skip_act_on_conflict: bool = True

    @classmethod
    def from_config(cls, section: Mapping[str, Any]) -> "DirectionSettings":
        """From an already-sanitised ``t2i.directions`` section."""
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in section.items() if k in names})


@dataclass(frozen=True)
class Direction:
    text: str
    parts: Tuple[str, ...]
    warnings: Tuple[str, ...]


def load_snippets(directory: Path) -> Tuple[Dict[str, Tuple[str, ...]], List[str]]:
    warnings: List[str] = []
    snippets: Dict[str, Tuple[str, ...]] = {}
    if not directory.is_dir():
        return snippets, warnings
    for path in sorted(directory.glob("*.txt")):
        values = _read_list(path, "direction", warnings)
        if values:
            snippets[path.stem] = values
    return snippets, warnings


class SnippetCache:
    """Serves the snippet lists, reloading when a file changes. Never raises."""

    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)
        self._lock = threading.Lock()
        self._signature: Optional[Tuple[Any, ...]] = None
        self._snippets: Dict[str, Tuple[str, ...]] = {}
        self._warnings: List[str] = []

    def _signature_now(self) -> Tuple[Any, ...]:
        if not self._directory.is_dir():
            return ("missing",)
        signature: List[Tuple[str, int, int]] = []
        for path in sorted(self._directory.glob("*.txt")):
            try:
                stat = path.stat()
            except OSError:
                continue
            signature.append((path.name, stat.st_mtime_ns, stat.st_size))
        return tuple(signature)

    def get(self) -> Tuple[Dict[str, Tuple[str, ...]], List[str]]:
        with self._lock:
            try:
                signature = self._signature_now()
                if signature != self._signature:
                    self._snippets, self._warnings = load_snippets(self._directory)
                    self._signature = signature
            except Exception as exc:  # never raise into a request or a batch
                logger.exception("t2i direction lists reload failed")
                return dict(self._snippets), self._warnings + [
                    f"could not reload the direction lists: {exc}"
                ]
            return dict(self._snippets), list(self._warnings)


def _sentence(text: str) -> str:
    text = text.strip().rstrip(".")
    return (text[:1].upper() + text[1:] + ".") if text else ""


def build_direction(
    row: Optional[CaptionRow],
    seed: int,
    content_mode: str,
    settings: DirectionSettings,
    snippets: Snippets,
) -> Optional[Direction]:
    if not settings.enabled or row is None or row.classification is None:
        return None
    c = row.classification
    sfw = content_mode == "sfw"
    texts: List[str] = []
    parts: List[str] = []
    warnings: List[str] = []

    def pick(keys: Sequence[str], labels: Sequence[str]) -> None:
        for key, label in zip(keys, labels):
            values = snippets.get(key)
            if values:
                texts.append(_sentence(values[_draw(seed, "direction", key, 0, len(values))]))
                parts.append(label)
                return
        warnings.append(
            f"no direction snippets for {keys[-1]}: "
            f"{DIRECTIONS_DIRNAME}/{keys[-1]}.txt is missing or empty"
        )

    act_ok = (
        not sfw
        and c.act not in NO_DIRECTION_ACTS
        and c.act_p >= settings.act_min
        and not (settings.skip_act_on_conflict and c.act_conflict)
    )
    if act_ok:
        if c.partner != "none":
            pick([f"act.{c.act}.pov", f"act.{c.act}"], [f"act:{c.act}:pov", f"act:{c.act}"])
        else:
            pick([f"act.{c.act}"], [f"act:{c.act}"])
    if not sfw and c.kiss >= settings.kiss_min:
        pick(["kissing"], ["kissing"])
    if round(1.0 - c.emotion_explicit, 6) >= settings.emotion_missing_min:
        erotic = row.erotic_score
        if not sfw and erotic is not None and erotic >= settings.emotion_sensual_from:
            pick(["emotion.sensual", "emotion"], ["emotion:sensual", "emotion"])
        else:
            pick(["emotion"], ["emotion"])
    return Direction(text=" ".join(texts), parts=tuple(parts), warnings=tuple(warnings))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/pytest tests/test_t2i_directions.py -q`
Expected: 14 passed.

- [ ] **Step 5: Type-check, format and commit**

```bash
venv/bin/mypy --check-untyped-defs metascan/core/t2i_directions.py
venv/bin/black metascan/core/t2i_directions.py tests/test_t2i_directions.py
git add metascan/core/t2i_directions.py tests/test_t2i_directions.py
git commit -m "feat(t2i): build caption directions from classification and snippet lists"
```

---

### Task 4: `t2i.directions` config section

**Files:**
- Modify: `backend/config.py` (`get_t2i_config`, ~225–347)
- Test: `tests/test_t2i_config.py` (update `DEFAULTS`, add a test class)

**Interfaces:**
- Consumes: `DirectionSettings` defaults (Task 3).
- Produces: `get_t2i_config(cfg)["directions"]` → `{"enabled": bool, "emotion_missing_min": float, "emotion_sensual_from": float, "kiss_min": float, "act_min": float, "skip_act_on_conflict": bool}`; a non-bool flag or a non-number threshold falls back to its default; a number outside 0–1 is clamped.

- [ ] **Step 1: Write the failing tests**

In `tests/test_t2i_config.py`, add to the `DEFAULTS` dict:

```python
    "directions": {
        "enabled": True,
        "emotion_missing_min": 0.70,
        "emotion_sensual_from": 0.60,
        "kiss_min": 0.80,
        "act_min": 0.80,
        "skip_act_on_conflict": True,
    },
```

and append:

```python
class TestDirections(unittest.TestCase):
    def test_valid_values_pass_through(self):
        section = {
            "enabled": False, "emotion_missing_min": 0.5, "emotion_sensual_from": 0.4,
            "kiss_min": 0.9, "act_min": 0.95, "skip_act_on_conflict": False,
        }
        self.assertEqual(cfg(directions=section)["directions"], section)

    def test_out_of_range_numbers_are_clamped(self):
        got = cfg(directions={"act_min": 1.7, "kiss_min": -0.2})["directions"]
        self.assertEqual((got["act_min"], got["kiss_min"]), (1.0, 0.0))

    def test_junk_falls_back_to_the_defaults(self):
        for junk in JUNK:
            with self.subTest(junk=junk):
                got = cfg(directions=junk)["directions"]
                self.assertEqual(got, DEFAULTS["directions"])
        got = cfg(directions={"enabled": "yes", "act_min": "0.9"})["directions"]
        self.assertEqual(got, DEFAULTS["directions"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/pytest tests/test_t2i_config.py -q`
Expected: FAIL — `test_an_empty_config_gives_exactly_the_spec_defaults` (no `directions` key) and the new class (`KeyError: 'directions'`).

- [ ] **Step 3: Write the implementation**

In `backend/config.py`: import `DirectionSettings` from `metascan.core.t2i_directions`; add `"directions": {...}` to the docstring shape; before the `return` in `get_t2i_config` add:

```python
    raw_directions = raw.get("directions")
    if not isinstance(raw_directions, dict):
        raw_directions = {}
    base = DirectionSettings()
    directions: Dict[str, Any] = {}
    for name in ("enabled", "skip_act_on_conflict"):
        value = raw_directions.get(name)
        directions[name] = value if isinstance(value, bool) else getattr(base, name)
    for name in ("emotion_missing_min", "emotion_sensual_from", "kiss_min", "act_min"):
        number = _number(raw_directions.get(name))
        directions[name] = (
            getattr(base, name) if number is None else min(1.0, max(0.0, number))
        )
```

and add `"directions": directions,` to the returned dict. (`_number` already rejects bools, strings and non-finite values; check its signature in this file and use it as the other fields do. Import `Any`/`Dict` if not already imported.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/pytest tests/test_t2i_config.py -q`
Expected: all passed (including the fixed-point and JSON-serialisable tests).

- [ ] **Step 5: Format and commit**

```bash
venv/bin/black backend/config.py tests/test_t2i_config.py
git add backend/config.py tests/test_t2i_config.py
git commit -m "feat(t2i): t2i.directions thresholds in the config"
```

---

### Task 5: Prompt composition with a direction

**Files:**
- Modify: `metascan/core/t2i_prompt.py` (`compose_t2i_prompts`, `fallback_prompt`)
- Modify: `data/meta_prompt.yml` (`T2I_CAPTION_PREAMBLE`)
- Test: `tests/test_t2i_prompt.py` (append)

**Interfaces:**
- Produces: `compose_t2i_prompts(profile, resolved_caption, content_mode, direction: Optional[str] = None) -> Tuple[str, str]`; `fallback_prompt(profile, resolved_caption, direction: Optional[str] = None) -> Tuple[str, Optional[str]]`. A blank or None direction leaves both outputs byte-identical to today.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_t2i_prompt.py` (it already imports `compose_t2i_prompts`, `fallback_prompt`, `MODEL_PROFILES` or add the imports):

```python
class DirectionTests(unittest.TestCase):
    def test_no_direction_leaves_both_prompts_unchanged(self) -> None:
        profile = MODEL_PROFILES["krea2"]
        plain = compose_t2i_prompts(profile, "A woman reads.", "uncensored")
        for blank in (None, "", "   "):
            self.assertEqual(
                compose_t2i_prompts(profile, "A woman reads.", "uncensored", blank), plain
            )
        self.assertEqual(plain[1], "DESCRIPTION:\nA woman reads.\n\nWrite the prompt now.")

    def test_a_direction_is_its_own_block_after_the_description(self) -> None:
        profile = MODEL_PROFILES["krea2"]
        system, user = compose_t2i_prompts(
            profile, "A woman reads.", "uncensored", "Give her a soft smile."
        )
        self.assertEqual(
            user,
            "DESCRIPTION:\nA woman reads.\n\nDIRECTION:\nGive her a soft smile."
            "\n\nWrite the prompt now.",
        )
        self.assertIn("DIRECTION", system)  # the preamble explains the block

    def test_the_fallback_appends_the_direction(self) -> None:
        sd = MODEL_PROFILES["sd"]
        plain, _ = fallback_prompt(sd, "A woman reads.")
        with_direction, _ = fallback_prompt(sd, "A woman reads.", "She smiles.")
        self.assertEqual(with_direction, plain + " She smiles.")
        self.assertEqual(fallback_prompt(sd, "A woman reads.", "  "), fallback_prompt(sd, "A woman reads."))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/pytest tests/test_t2i_prompt.py -q -k Direction`
Expected: FAIL with `TypeError: compose_t2i_prompts() takes 3 positional arguments but 4 were given`

- [ ] **Step 3: Write the implementation**

In `metascan/core/t2i_prompt.py`:

```python
_USER_TEMPLATE = "DESCRIPTION:\n{description}\n\nWrite the prompt now."
_USER_TEMPLATE_DIRECTED = (
    "DESCRIPTION:\n{description}\n\nDIRECTION:\n{direction}\n\nWrite the prompt now."
)
```

Change `compose_t2i_prompts` to take `direction: Optional[str] = None` and end with:

```python
    if direction is not None and direction.strip():
        return system, _USER_TEMPLATE_DIRECTED.format(
            description=resolved_caption, direction=direction.strip()
        )
    return system, _USER_TEMPLATE.format(description=resolved_caption)
```

Change `fallback_prompt` to take `direction: Optional[str] = None`, and replace its first prompt line with:

```python
    text = resolved_caption.strip()
    if direction is not None and direction.strip():
        text = f"{text} {direction.strip()}" if text else direction.strip()
    prompt = strip_parentheses(text)
```

Update both docstrings (one sentence each: a non-blank direction adds a DIRECTION block / is appended). In `data/meta_prompt.yml`, add one bullet at the end of `T2I_CAPTION_PREAMBLE` (after the parentheses bullet, same indentation, no parentheses in the text):

```yaml
  - If a DIRECTION block follows the description, carry it out: work each instruction into the prompt naturally, in the guideline's style. A direction adds detail but never overrides the description; where they disagree, the description wins.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/pytest tests/test_t2i_prompt.py tests/test_t2i_runner.py -q`
Expected: all passed — the runner's existing `user_prompt` equality assertions prove the undirected prompt is unchanged.

- [ ] **Step 5: Format and commit**

```bash
venv/bin/black metascan/core/t2i_prompt.py tests/test_t2i_prompt.py
git add metascan/core/t2i_prompt.py data/meta_prompt.yml tests/test_t2i_prompt.py
git commit -m "feat(t2i): a DIRECTION block in the caption prompt and the fallback"
```

---

### Task 6: Runner and API wiring

**Files:**
- Modify: `metascan/core/t2i_runner.py` (`BatchRequest`, `PromptResult`, `_Batch`, `_Step`, `T2iRunner.__init__`, `generate_prompt`, `_plan` where `_Batch(...)` is built, `_write_prompt`, `_random_step`, `_announce_step`)
- Modify: `backend/api/t2i.py` (`CaptionRequest`, `BatchBody`, `/prompt`, `/batches`), `backend/main.py` (runner construction)
- Test: `tests/test_t2i_runner_directions.py` (create); `tests/test_t2i_api.py` (update `TestPrompt`)

**Interfaces:**
- Consumes: `CaptionStore.find` (Task 2); `SnippetCache`, `DirectionSettings`, `build_direction`, `Direction`, `DIRECTIONS_DIRNAME` (Task 3); `cfg["directions"]` (Task 4); `compose_t2i_prompts(..., direction)`, `fallback_prompt(..., direction)` (Task 5).
- Produces:
  - `BatchRequest.directions: Optional[bool] = None` (None = config default)
  - `PromptResult.direction: Optional[str] = None`, `PromptResult.direction_parts: List[str] = field(default_factory=list)`
  - `T2iRunner(..., directions: Optional[SnippetCache] = None)`
  - `generate_prompt(*, caption, seed, model, directions: Optional[bool] = None) -> PromptResult`
  - `batch_step` frames and `_Batch.steps` entries gain `"direction": Optional[str]` and `"direction_parts": List[str]`
  - `POST /api/t2i/prompt` body accepts `directions: bool | null`; response adds `direction`, `direction_parts`. `POST /api/t2i/batches` body accepts `directions`.

- [ ] **Step 1: Write the failing tests**

`tests/test_t2i_runner_directions.py`:

```python
"""Caption directions through T2iRunner: Manual prompts and Random steps.

Reuses the runner fixtures; the captions CSV here carries hand-written
classification columns, and the snippet lists are written by the test.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from unittest import mock

from metascan.core.t2i_directions import SnippetCache
from tests.test_t2i_runner import BatchCase

COLUMNS = ["Caption", "Aspect Ratio", "Nudity", "Artistic Quality", "Erotic Score",
           "Pornographic Score", "Males", "Females", "Clothing", "Caption SHA1",
           "Emotion", "Emotion Explicit", "Kiss", "Partner", "Act", "Act P",
           "Act Conflict", "Issues"]
ACT_CAPTION = "__ALICE__ is on all fours on the bed."
PLAIN_CAPTION = "__ALICE__ laughs at the window."


def _row(caption: str, act: str, act_p: str, explicit: str) -> list:
    digest = hashlib.sha1(caption.encode("utf-8")).hexdigest()
    return [caption, "1:1", "full", "0.5", "0.2", "0.9", "1", "1", "[]", digest,
            "none", explicit, "0.0", "none", act, act_p, "false", ""]


class DirectionCase(BatchCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        with open(self.csv_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(COLUMNS)
            w.writerow(_row(ACT_CAPTION, "doggy", "0.9500", "0.0500"))
            w.writerow(_row(PLAIN_CAPTION, "none-artistic", "0.9900", "0.9900"))
        snippet_dir = Path(self.root) / "directions"
        snippet_dir.mkdir()
        (snippet_dir / "act.doggy.txt").write_text("he takes her from behind\n", encoding="utf-8")
        (snippet_dir / "emotion.txt").write_text("a soft unguarded smile\n", encoding="utf-8")
        self.runner.directions = SnippetCache(snippet_dir)

    def user_turns(self) -> list:
        assert self.vlm is not None
        return [call["user_prompt"] for call in self.vlm.calls]


class TestManualPromptDirections(DirectionCase):
    async def test_a_classified_caption_gets_a_direction_block(self) -> None:
        result = await self.runner.generate_prompt(caption=ACT_CAPTION, seed=5, model="krea2")
        (user,) = self.user_turns()
        self.assertIn(
            "\n\nDIRECTION:\nHe takes her from behind. A soft unguarded smile.\n\n", user
        )
        self.assertEqual(result.direction, "He takes her from behind. A soft unguarded smile.")
        self.assertEqual(result.direction_parts, ["act:doggy", "emotion"])

    async def test_below_threshold_or_turned_off_the_prompt_is_unchanged(self) -> None:
        for caption, flag in ((PLAIN_CAPTION, None), (ACT_CAPTION, False)):
            with self.subTest(caption=caption, flag=flag):
                assert self.vlm is not None
                self.vlm.calls.clear()
                result = await self.runner.generate_prompt(
                    caption=caption, seed=5, model="krea2", directions=flag
                )
                (user,) = self.user_turns()
                self.assertNotIn("DIRECTION", user)
                self.assertIsNone(result.direction)

    async def test_the_config_switch_is_the_default(self) -> None:
        self.cfg["directions"] = {**self.cfg["directions"], "enabled": False}
        await self.runner.generate_prompt(caption=ACT_CAPTION, seed=5, model="krea2")
        self.assertNotIn("DIRECTION", self.user_turns()[0])

    async def test_a_failing_lookup_gives_no_direction_and_a_warning(self) -> None:
        with mock.patch.object(self.captions, "find", side_effect=OSError("gone")):
            result = await self.runner.generate_prompt(caption=ACT_CAPTION, seed=5, model="krea2")
        self.assertNotIn("DIRECTION", self.user_turns()[0])
        self.assertTrue(any("direction" in w for w in result.warnings))

    async def test_without_a_vlm_the_fallback_carries_the_direction(self) -> None:
        self.vlm = None
        result = await self.runner.generate_prompt(caption=ACT_CAPTION, seed=5, model="krea2")
        self.assertTrue(result.prompt.endswith("He takes her from behind. A soft unguarded smile."))


class TestRandomStepDirections(DirectionCase):
    async def test_a_drawn_step_carries_and_reports_its_direction(self) -> None:
        self.roomy()
        batch_id = await self.run_to_end(
            self.random_mode(batch_size=2, count_per_batch=1)
        )
        steps = {s["caption"]: s for s in self.written(batch_id)}
        self.assertEqual(steps[ACT_CAPTION]["direction_parts"], ["act:doggy", "emotion"])
        self.assertIsNone(steps[PLAIN_CAPTION]["direction"])
        directed = [u for u in self.user_turns() if "DIRECTION" in u]
        self.assertEqual(len(directed), 1)
        frames = self.frames("batch_step")
        self.assertTrue(all("direction" in f for f in frames))

    async def test_directions_false_turns_them_off_for_the_batch(self) -> None:
        self.roomy()
        await self.run_to_end(
            self.random_mode(batch_size=2, count_per_batch=1, directions=False)
        )
        self.assertFalse(any("DIRECTION" in u for u in self.user_turns()))
```

In `tests/test_t2i_api.py` `TestPrompt`: change the happy-path expected JSON to add `"direction": None, "direction_parts": []`, and the call expectation to `[("generate_prompt", {**self.BODY, "directions": None})]`; add:

```python
    def test_the_directions_flag_reaches_the_runner(self) -> None:
        resp = self.client.post("/api/t2i/prompt", json={**self.BODY, "directions": False})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.runner.calls, [("generate_prompt", {**self.BODY, "directions": False})])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/pytest tests/test_t2i_runner_directions.py tests/test_t2i_api.py -q -x`
Expected: FAIL — `generate_prompt() got an unexpected keyword argument 'directions'` / missing `direction` attribute.

- [ ] **Step 3: Write the implementation**

`metascan/core/t2i_runner.py`:

1. Imports: `from metascan.core.t2i_captions import CaptionPicker, CaptionRow, CaptionStore`; `from metascan.core.t2i_directions import Direction, DirectionSettings, SnippetCache, build_direction`.
2. `BatchRequest`: add last field `directions: Optional[bool] = None  # None: the config's t2i.directions.enabled`.
3. `PromptResult`: add `direction: Optional[str] = None` and `direction_parts: List[str] = field(default_factory=list)`.
4. `_Batch`: add fields (before the defaulted ones) `directions: bool` and `direction_settings: DirectionSettings`; in the `_Batch(...)` construction add:

```python
            directions=(
                cfg["directions"]["enabled"] if req.directions is None else bool(req.directions)
            ),
            direction_settings=DirectionSettings.from_config(cfg["directions"]),
```

5. `_Step`: add `direction: Optional[str] = None` and `direction_parts: List[str] = field(default_factory=list)` as the last fields; `_announce_step` adds `"direction": step.direction, "direction_parts": list(step.direction_parts)` to the dict.
6. `T2iRunner.__init__`: add parameter `directions: Optional[SnippetCache] = None` and `self.directions = directions`.
7. Add helpers to `T2iRunner`:

```python
    async def _find_row(self, caption: str, warnings: List[str]) -> Optional[CaptionRow]:
        """The CSV row with exactly this caption; a failing lookup is a warning."""
        try:
            return await asyncio.to_thread(self.captions.find, caption)
        except Exception as exc:  # never fail a prompt over a direction
            logger.warning("t2i direction lookup failed: %s", exc)
            warnings.append(f"caption direction skipped: lookup failed ({self._reason(exc)})")
            return None

    async def _direction(
        self,
        row: Optional[CaptionRow],
        seed: int,
        content_mode: str,
        settings: DirectionSettings,
        enabled: bool,
        warnings: List[str],
    ) -> Optional[Direction]:
        """The direction for this caption, or None. Warnings go to ``warnings``."""
        if not enabled or self.directions is None or row is None:
            return None
        snippets, _ = await asyncio.to_thread(self.directions.get)
        direction = build_direction(row, seed, content_mode, settings, snippets)
        if direction is None:
            return None
        warnings.extend(direction.warnings)
        if not direction.text:
            return None
        logger.info("t2i direction for row %d: %s", row.id, ", ".join(direction.parts))
        return direction
```

   (`_reason` is the existing static helper that formats an exception; if it is an instance method, call it the same way the file already does.)
8. `generate_prompt`: add `directions: Optional[bool] = None` to the signature. After `warnings = list(resolved.warnings)`:

```python
        enabled = cfg["directions"]["enabled"] if directions is None else bool(directions)
        row = await self._find_row(caption, warnings) if enabled and self.directions else None
        direction = await self._direction(
            row, seed, cfg["content_mode"],
            DirectionSettings.from_config(cfg["directions"]), enabled, warnings,
        )
        text = direction.text if direction else None
        parts = list(direction.parts) if direction else []
```

   pass `text` as the `direction` argument to `fallback_prompt(...)` and `compose_t2i_prompts(...)`, and build both `PromptResult(...)` returns with `direction=text, direction_parts=parts`.
9. `_write_prompt(self, batch, resolved_text, direction: Optional[str] = None)`: pass `direction` to `fallback_prompt(profile, resolved_text, direction)` inside `fall_back` and to `compose_t2i_prompts(profile, resolved_text, batch.content_mode, direction)`.
10. `_random_step`: keep the drawn row (`row: Optional[CaptionRow] = None`; set `row = await asyncio.to_thread(batch.picker.next)` in the draw branch; in the named-caption branch `row = await self._find_row(caption, warnings)` when `batch.directions and self.directions`). Move `warnings: List[str] = []` above the branch. After resolving:

```python
        direction = await self._direction(
            row, seeds[0], batch.content_mode, batch.direction_settings,
            batch.directions, warnings,
        )
        prompt, negative, notes = await self._write_prompt(
            batch, resolved.text, direction.text if direction else None
        )
```

   and add `direction=direction.text if direction else None, direction_parts=list(direction.parts) if direction else []` to the returned `_Step`.

`backend/api/t2i.py`: `CaptionRequest` add `directions: Optional[bool] = None`; `generate_prompt` route passes `directions=body.directions` and returns `"direction": result.direction, "direction_parts": result.direction_parts`; `BatchBody` add `directions: Optional[bool] = None`; `start_batch` passes `directions=body.directions` into `BatchRequest(...)`.

`backend/main.py`: import `DIRECTIONS_DIRNAME, SnippetCache` from `metascan.core.t2i_directions` and pass `directions=SnippetCache(t2i_dir / DIRECTIONS_DIRNAME)` to `T2iRunner(...)`.

Check `BatchCase.random_mode` in `tests/test_t2i_runner.py` forwards `**over` into `BatchRequest`; if it builds fields explicitly, `directions=False` must reach the request (it does when the helper does `dict(...); fields.update(over)`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/pytest tests/test_t2i_runner_directions.py tests/test_t2i_runner.py tests/test_t2i_api.py tests/test_t2i_service.py -q`
Expected: all passed.

- [ ] **Step 5: Type-check, format and commit**

```bash
venv/bin/mypy --check-untyped-defs metascan/core/t2i_runner.py
venv/bin/black metascan/core/t2i_runner.py backend/api/t2i.py backend/main.py tests/test_t2i_runner_directions.py tests/test_t2i_api.py
git add metascan/core/t2i_runner.py backend/api/t2i.py backend/main.py tests/test_t2i_runner_directions.py tests/test_t2i_api.py
git commit -m "feat(t2i): caption directions in Manual prompts and Random steps"
```

---

### Task 7: Dialog — toggle and Direction line

**Files:**
- Modify: `frontend/src/types/t2i.ts` (`T2iConfig`, `T2iPromptResult`, `T2iBatchRequest`, `T2iBatchStep`)
- Modify: `frontend/src/api/t2i.ts` (`generateT2iPrompt` body type)
- Modify: `frontend/src/stores/t2i.ts` (`generatePrompt` body type, line ~259)
- Modify: `frontend/src/components/dialogs/T2IDialog.vue`

**Interfaces:**
- Consumes: the API fields from Task 6.
- Produces: `T2iConfig.directions: T2iDirectionsConfig` (`enabled, emotion_missing_min, emotion_sensual_from, kiss_min, act_min, skip_act_on_conflict`); `direction: string | null` and `direction_parts: string[]` on `T2iPromptResult` and `T2iBatchStep`; `directions?: boolean | null` on `T2iBatchRequest` and the prompt body.

There is no frontend test runner; verification is `npm run build` (vue-tsc strict) plus the manual check in Task 8.

- [ ] **Step 1: Types and API**

In `types/t2i.ts` add:

```ts
/** GET /api/t2i/config `directions`: when caption directions apply (config.json t2i.directions). */
export interface T2iDirectionsConfig {
  enabled: boolean
  emotion_missing_min: number
  emotion_sensual_from: number
  kiss_min: number
  act_min: number
  skip_act_on_conflict: boolean
}
```

add `directions: T2iDirectionsConfig` to `T2iConfig`; add `direction: string | null` and `direction_parts: string[]` to `T2iPromptResult` and `T2iBatchStep`; add `directions?: boolean | null` to `T2iBatchRequest`. In `api/t2i.ts` and the store's `generatePrompt`, extend the body type with `directions?: boolean`.

- [ ] **Step 2: Toggle state (per browser, never per image)**

In `T2IDialog.vue` `<script setup>`, next to `promptWarnings`:

```ts
// Caption directions: remembered per browser, not per image (nothing about
// them is stored with an image). Defaults to the config's switch.
const DIRECTIONS_KEY = 'metascan.t2i.directions.v1'
function readDirectionsPref(): boolean | null {
  try {
    const v = localStorage.getItem(DIRECTIONS_KEY)
    return v === null ? null : v === '1'
  } catch {
    return null
  }
}
const directionsPref = ref<boolean | null>(readDirectionsPref())
const useDirections = computed(() => directionsPref.value ?? store.config?.directions?.enabled ?? true)
function setDirections(on: boolean) {
  directionsPref.value = on
  try {
    localStorage.setItem(DIRECTIONS_KEY, on ? '1' : '0')
  } catch {
    /* private mode: the choice lasts for this session */
  }
}
const promptDirection = ref<{ text: string; parts: string[] } | null>(null)
const shownDirection = computed(() => {
  if (liveBoxes.value) {
    const s = step.value
    return s?.direction ? { text: s.direction, parts: s.direction_parts } : null
  }
  return promptDirection.value
})
```

- [ ] **Step 3: Send the flag and keep the line in step**

- `onGeneratePrompt`: body becomes `{ caption: form.caption, seed: form.seed, model: form.model, directions: useDirections.value }`; after a successful result set `promptDirection.value = r.direction ? { text: r.direction, parts: r.direction_parts } : null`.
- `onRoll`: set `promptDirection.value = null` next to `promptWarnings.value = []`.
- `onGenerate`: after `buildBatchRequest(...)`, set `req.directions = useDirections.value` (declare `const req` with the `T2iBatchRequest` type if needed); where `promptWarnings.value = []` is reset after a start, also set `promptDirection.value = null`.
- The resolved-caption key for the prompt body stays `[caption, seed, model]` (directions do not change the resolved caption).

- [ ] **Step 4: Markup**

In the Prompt section, change the title row and add the line after the prompt warnings list:

```vue
<div class="sec-head">
  <label class="sec-title" for="t2i-prompt">Prompt</label>
  <label class="check" title="Use the caption's classification to add a direction when the prompt is written">
    <input
      type="checkbox"
      :checked="useDirections"
      :disabled="randomLocked"
      @change="setDirections(($event.target as HTMLInputElement).checked)"
    />
    Caption directions
  </label>
</div>
```

```vue
<p v-if="shownDirection" class="direction" :title="shownDirection.text">
  Direction · {{ shownDirection.parts.join(' · ') }}: {{ shownDirection.text }}
</p>
```

Add scoped styles using existing tokens in the file (match the `.lint` list's font size and muted colour; `.sec-head { display: flex; justify-content: space-between; align-items: baseline; }`, `.check { font-size: 0.85em; display: inline-flex; gap: 0.35em; align-items: center; }`, `.direction { margin: 0.35em 0 0; font-size: 0.85em; opacity: 0.8; }`). If `.sec-head` already exists in the file, reuse it.

- [ ] **Step 5: Build and commit**

Run: `cd frontend && npm run build`
Expected: type-check and build succeed with no errors.

```bash
git add frontend/src/types/t2i.ts frontend/src/api/t2i.ts frontend/src/stores/t2i.ts frontend/src/components/dialogs/T2IDialog.vue
git commit -m "feat(t2i): caption directions toggle and Direction line in the dialog"
```

---

### Task 8: Starter snippets, docs and full verification

**Files:**
- Create: `data/t2i_captions/directions/` lists (below)
- Modify: `.claude/rules/t2i.md`, `docs/t2i.md`, `scripts/caption_classifier/README.md`

- [ ] **Step 1: Starter snippet lists**

Create one file per key with a header comment and the starter lines below. These are minimal placeholders the user will expand; each line must pass the adult-only screen (run Step 2 to prove it).

- `emotion.txt`: `# One expression per line; the seed picks one. Used when a caption names no explicit emotion.` then: `give each person a facial expression that suits the moment, such as a soft unguarded smile` · `give each person a natural, readable expression, such as calm attentiveness` · `give each person an expression that fits the scene, such as quiet amusement`
- `emotion.sensual.txt`: `# Used instead of emotion.txt when the caption's Erotic Score is high.` then: `give her a flushed, heavy-lidded look of pleasure` · `give her parted lips and a dreamy, aroused expression` · `give her a sultry, inviting gaze`
- `kissing.txt`: `they are kissing deeply, eyes closed` · `their lips meet in a slow, intimate kiss`
- One `act.<name>.txt` for each of: `breast-fondling`, `female-masturbation`, `female-toy-masturbation`, `partner-manual-female`, `object-insertion`, `male-masturbation`, `handjob`, `fellatio`, `cunnilingus`, `missionary`, `doggy`, `cowgirl`, `reverse-cowgirl`, `spooning`, `standing-sex`, `paizuri`, `ff-tribbing`. Each holds a header comment `# Direction for the <name> act; the seed picks one line.` and one line restating that act's "look for" text from `scripts/caption_classifier/rubric.py` as an instruction, prefixed with "make it explicit that " — e.g. `act.doggy.txt`: `make it explicit that she is on her hands and knees with him behind her, his hips against hers`.
- `act.fellatio.pov.txt`, `act.doggy.pov.txt`, `act.cowgirl.pov.txt`, `act.handjob.pov.txt`: one line each written from the viewer's point of view, e.g. `act.cowgirl.pov.txt`: `make it explicit that she straddles the viewer's hips, seen from below in first person`.

- [ ] **Step 2: Prove the starter lists load cleanly**

Run:
```bash
venv/bin/python -c "
from pathlib import Path
from metascan.core.t2i_directions import load_snippets
s, w = load_snippets(Path('data/t2i_captions/directions'))
print(len(s), 'lists', sum(map(len, s.values())), 'lines'); print('warnings:', w)
"
```
Expected: `24 lists` (2 emotion + kissing + 17 acts + 4 pov), and `warnings: []`. A rejected line must be rewritten, never the screen loosened.

- [ ] **Step 3: Docs**

- `.claude/rules/t2i.md`: add one bullet "**Caption directions.**" summarising: classification columns merged by `scripts/caption_classifier/merge.py`; `CaptionRow.classification` is None unless the row's `Caption SHA1` matches its text; `CaptionStore.find` is exact-text; snippet lists in `data/t2i_captions/directions/` (subdirectory so the wildcard loader never sees them) pass `_reject_reason`; snippet choice `sha256(f"{seed}|direction|{key}|0")`; order act → kissing → emotion; sfw drops act/kissing and the sensual list; no direction ⇒ prompts byte-identical; nothing stored per image; the dialog toggle is localStorage-only.
- `docs/t2i.md`: a short "Caption directions" section for users (what it does, the checkbox, the Direction line, the config keys with defaults, editing the snippet lists).
- `scripts/caption_classifier/README.md`: add step 5 under Run — `venv/bin/python -m scripts.caption_classifier.merge` then `--replace`, and what the backup is.

- [ ] **Step 4: Full verification**

Run:
```bash
venv/bin/pytest -q
venv/bin/black --check metascan/ backend/ tests/ scripts/caption_classifier/
venv/bin/flake8 metascan/ backend/ tests/ --count --select=E9,F63,F7,F82 --show-source --statistics
venv/bin/mypy --check-untyped-defs metascan/
cd frontend && npm run build
```
Expected: pytest all green except the known flake `test_file_watcher_triggers_reload`; black clean; flake8 `0`; mypy clean; build succeeds.

- [ ] **Step 5: Merge the real results and smoke-test (manual, with the user)**

```bash
venv/bin/python -m scripts.caption_classifier.merge            # writes t2i_captions.merged.csv
venv/bin/python -m scripts.caption_classifier.merge --replace  # swaps it in, keeps a backup
```
Expected: about 82,880 filled, 0 caption changed, 0 errors. Then, with the user's app running, open T2I, roll a caption the summary lists as `doggy` and one with `Emotion none`, press Generate Prompt with Caption directions on and off, and confirm the Direction line and the prompt text change accordingly. Per the evaluation rule, the A/B prompt comparisons for tuning use hand-written captions, not library ones.

- [ ] **Step 6: Commit**

```bash
git add data/t2i_captions/directions .claude/rules/t2i.md docs/t2i.md scripts/caption_classifier/README.md
git commit -m "docs(t2i): caption directions rules, user guide and starter snippet lists"
```
