"""The append-only results file: which rows to run, which are done, writing.

One file per prompt version, ``results-<version>.jsonl``, one JSON object per
line. A run killed mid-write can leave a half line at the end; readers skip
it and the writer starts the next record on a fresh line.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, TextIO, Tuple


class VersionConflict(RuntimeError):
    """Results from another prompt version exist and --new-run was not given."""


def caption_sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def select_rows(
    total: int, count: Optional[int], sample: Optional[int], seed: int
) -> List[int]:
    if count is not None:
        return list(range(min(count, total)))
    if sample is not None:
        picked = random.Random(seed).sample(range(total), min(sample, total))
        return sorted(picked)
    return list(range(total))


def results_path(out_dir: Path, version: str) -> Path:
    return out_dir / f"results-{version}.jsonl"


def open_run(out_dir: Path, version: str, new_run: bool) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    mine = results_path(out_dir, version)
    others = sorted(p.name for p in out_dir.glob("results-*.jsonl") if p != mine)
    if others and not mine.exists() and not new_run:
        raise VersionConflict(
            f"{out_dir} holds results for another prompt version ({', '.join(others)}); "
            f"the current version is {version}. Pass --new-run to start "
            f"{mine.name} alongside them."
        )
    return mine


def _records(path: Path) -> Iterator[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # a half-written line from a killed run


def load_done(path: Path) -> Set[Tuple[int, str]]:
    return {
        (int(r["row_id"]), str(r["caption_sha1"]))
        for r in _records(path)
        if r.get("status") == "ok"
    }


def load_records(path: Path) -> Dict[int, Dict[str, Any]]:
    """The record to report per row: the last ``ok`` one, else the last one."""
    best: Dict[int, Dict[str, Any]] = {}
    for rec in _records(path):
        row_id = int(rec["row_id"])
        current = best.get(row_id)
        if (
            rec.get("status") == "ok"
            or current is None
            or current.get("status") != "ok"
        ):
            best[row_id] = rec
    return best


def newest_results(out_dir: Path) -> Path:
    files = sorted(out_dir.glob("results-*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f"no results-*.jsonl in {out_dir}")
    return files[-1]


class ResultsWriter:
    def __init__(self, path: Path) -> None:
        needs_newline = False
        if path.exists() and path.stat().st_size > 0:
            with path.open("rb") as fh:
                fh.seek(-1, 2)
                needs_newline = fh.read(1) != b"\n"
        self._fh: TextIO = path.open("a", encoding="utf-8")
        if needs_newline:
            self._fh.write("\n")

    def write(self, record: Dict[str, Any]) -> None:
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()
