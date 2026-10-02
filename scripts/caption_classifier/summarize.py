"""Flatten a results file into classifications.csv, one row per caption.

Usage:
    python -m scripts.caption_classifier.summarize [--out DIR] [--results PATH]
        [--output PATH]

Without --results the newest results-*.jsonl in --out is used; the CSV is
written next to it unless --output says otherwise.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .classify import DEFAULT_OUT
from .results import load_records, newest_results
from .rubric import ACT_BY_LETTER, EMOTION, PARTNER

COLUMNS: List[str] = [
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


def summarize_record(rec: Dict[str, Any]) -> Dict[str, str]:
    row = {col: "" for col in COLUMNS}
    row.update(
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


def write_csv(records: Dict[int, Dict[str, Any]], output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for row_id in sorted(records):
            writer.writerow(summarize_record(records[row_id]))
    return len(records)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Write classifications.csv from a results file."
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--results", type=Path)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args(argv)
    try:
        results = args.results or newest_results(args.out)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    output = args.output or results.parent / "classifications.csv"
    count = write_csv(load_records(results), output)
    print(f"{count} rows from {results.name} → {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
