"""Flatten a results file into classifications.csv, one row per caption.

Usage:
    python -m scripts.caption_classifier.summarize [--out DIR] [--results PATH]
        [--output PATH] [--csv PATH]

Without --results the newest results-*.jsonl in --out is used; the CSV is
written next to it unless --output says otherwise. The first column,
``prompt``, is a short excerpt of each caption read from --csv (read-only)
so rows can be matched to captions by eye; it is blank for a row the CSV
no longer has.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from metascan.core.t2i_captions import CaptionStore

from .classify import DEFAULT_CSV, DEFAULT_OUT
from .results import load_records, newest_results
from .rubric import ACT_BY_LETTER, EMOTION, PARTNER

COLUMNS: List[str] = [
    "prompt",
    "row_id",
    "caption_sha1",
    "status",
    "partner",
    "partner_p",
    "kiss_p",
    "emotion",
    "p_emotion_none",
    "p_emotion_implicit",
    "p_emotion_explicit",
    "act_1",
    "act_1_p",
    "act_2",
    "act_2_p",
    "act_3",
    "act_3_p",
    "act_raw_top",
    "act_raw_top_p",
    "act_gate_conflict",
    "issue_types",
    "issues",
    "error",
]


def _ranked(dist: Dict[str, float]) -> List[Tuple[str, float]]:
    return sorted(dist.items(), key=lambda kv: (-kv[1], kv[0]))


def _p(value: float) -> str:
    return f"{value:.4g}"


def prompt_excerpt(caption: str, edge: int = 10) -> str:
    """The first and last ``edge`` characters of a caption, on one line."""
    text = " ".join(caption.split())
    if not text:
        return ""
    return f"{text[:edge]}...{text[-edge:]}"


def summarize_record(rec: Dict[str, Any], caption: str = "") -> Dict[str, str]:
    row = {col: "" for col in COLUMNS}
    row.update(
        prompt=prompt_excerpt(caption),
        row_id=str(rec["row_id"]),
        caption_sha1=str(rec["caption_sha1"]),
        status=str(rec["status"]),
    )
    if rec["status"] != "ok":
        row["error"] = str(rec.get("error", ""))
        return row
    partner, partner_p = _ranked(rec["partner"])[0]
    emotion, _ = _ranked(rec["emotion"])[0]
    row.update(
        partner=PARTNER[partner],
        partner_p=_p(partner_p),
        kiss_p=_p(rec["kiss"]["Y"]),
        emotion=EMOTION[emotion],
        p_emotion_none=_p(rec["emotion"]["A"]),
        p_emotion_implicit=_p(rec["emotion"]["B"]),
        p_emotion_explicit=_p(rec["emotion"]["C"]),
    )
    for i, (letter, p) in enumerate(_ranked(rec["act_gated"])[:3], start=1):
        if p > 0:
            row[f"act_{i}"] = ACT_BY_LETTER[letter].name
            row[f"act_{i}_p"] = _p(p)
    raw_letter, raw_p = _ranked(rec["act_raw"])[0]
    row.update(
        act_raw_top=ACT_BY_LETTER[raw_letter].name,
        act_raw_top_p=_p(raw_p),
        act_gate_conflict="true" if rec["act_gate_conflict"] else "false",
        issue_types=";".join(i["type"] for i in rec["issues"]),
        issues=json.dumps(rec["issues"], ensure_ascii=False) if rec["issues"] else "",
    )
    return row


def write_csv(
    records: Dict[int, Dict[str, Any]], output: Path, captions: Mapping[int, str]
) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for row_id in sorted(records):
            writer.writerow(summarize_record(records[row_id], captions.get(row_id, "")))
    return len(records)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Write classifications.csv from a results file."
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--results", type=Path)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = ap.parse_args(argv)
    try:
        results = args.results or newest_results(args.out)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    output = args.output or results.parent / "classifications.csv"
    records = load_records(results)
    store = CaptionStore(args.csv)
    if not store.available():
        print(f"cannot read captions from {args.csv}: {store.error()}", file=sys.stderr)
        return 2
    total = store.total()
    captions = {
        row_id: store.get(row_id).caption for row_id in records if row_id < total
    }
    count = write_csv(records, output, captions)
    print(f"{count} rows from {results.name} → {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
