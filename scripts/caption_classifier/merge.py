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
    ap = argparse.ArgumentParser(
        description="Merge classifier results into the captions CSV."
    )
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
