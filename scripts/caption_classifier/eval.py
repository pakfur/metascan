"""Measure the classifier on hand-written captions with known answers.

Usage:
    python -m scripts.caption_classifier.eval [--server-url URL]
        [--model qwen3vl-30b-a3b] [--parallel 4] [--fixtures PATH]

Prints per-field accuracy, an act confusion list, plausibility recall and
false alarms, and calibration (top-letter probability against how often that
letter was right). Run it before the full run and after any rubric change.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from metascan.core.t2i_captions import CaptionRow

from .classify import DEFAULT_LOG, ExternalServer, setup_logging
from .prompt import PROMPT_VERSION
from .rubric import ACT_BY_LETTER, EMOTION, ISSUE_TYPES, KISS, PARTNER, allowed_acts
from .runner import FatalServerError, Runner
from .server import LlamaServer, ServerError, build_command

FIXTURES = Path(__file__).with_name("fixtures") / "fixtures.json"
FIELDS = (
    ("partner", "partner"),
    ("kiss", "kiss"),
    ("emotion", "emotion"),
    ("act", "act_gated"),
)
BUCKETS = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01))


def load_fixtures(path: Path) -> List[Dict[str, Any]]:
    fixtures: List[Dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    for fx in fixtures:
        e = fx["expect"]
        ok = (
            e["partner"] in PARTNER
            and e["kiss"] in KISS
            and e["emotion"] in EMOTION
            and e["act"] in ACT_BY_LETTER
            and e["act"] in allowed_acts(fx["males"], fx["females"], e["partner"])
            and all(t in ISSUE_TYPES for t in e["issues"])
        )
        if not ok:
            raise ValueError(f"fixture {fx['id']!r} has an impossible expected answer")
    return fixtures


def fixture_row(index: int, fx: Dict[str, Any]) -> CaptionRow:
    return CaptionRow(
        id=index,
        caption=fx["caption"],
        aspect_ratio="1:1",
        nudity=fx["nudity"],
        artistic_quality=None,
        erotic_score=fx["erotic"],
        pornographic_score=fx["porn"],
        males=fx["males"],
        females=fx["females"],
        clothing=(),
    )


def _top(dist: Dict[str, float]) -> Tuple[str, float]:
    return max(dist.items(), key=lambda kv: kv[1])


def score(
    fixtures: List[Dict[str, Any]], records: Dict[int, Dict[str, Any]]
) -> Dict[str, Any]:
    accuracy = {name: [0, 0] for name, _ in FIELDS}
    confusion: Counter = Counter()
    recall = [0, 0]
    false_alarms = [0, 0]
    buckets = [[0, 0] for _ in BUCKETS]
    errors: List[str] = []
    for index, fx in enumerate(fixtures):
        rec = records.get(index)
        if rec is None or rec.get("status") != "ok":
            errors.append(f"{fx['id']}: {(rec or {}).get('error', 'no result')}")
            continue
        expect = fx["expect"]
        for name, key in FIELDS:
            letter, p = _top(rec[key])
            right = letter == expect[name]
            accuracy[name][0] += int(right)
            accuracy[name][1] += 1
            for b, (lo, hi) in enumerate(BUCKETS):
                if lo <= p < hi:
                    buckets[b][0] += 1
                    buckets[b][1] += int(right)
            if name == "act" and not right:
                confusion[
                    (ACT_BY_LETTER[expect["act"]].name, ACT_BY_LETTER[letter].name)
                ] += 1
        found = {i["type"] for i in rec["issues"]}
        if expect["issues"]:
            recall[0] += sum(1 for t in expect["issues"] if t in found)
            recall[1] += len(expect["issues"])
        else:
            false_alarms[0] += int(bool(found))
            false_alarms[1] += 1
    return {
        "accuracy": {k: (v[0], v[1]) for k, v in accuracy.items()},
        "confusion": dict(confusion),
        "issue_recall": (recall[0], recall[1]),
        "issue_false_alarms": (false_alarms[0], false_alarms[1]),
        "calibration": [
            (f"{lo:.1f}-{min(hi, 1.0):.1f}", n, right)
            for (lo, hi), (n, right) in zip(BUCKETS, buckets)
        ],
        "errors": errors,
    }


def _ratio(pair: Tuple[int, int]) -> str:
    right, total = pair
    return f"{right}/{total}" + (f" ({right / total:.0%})" if total else "")


def format_report(report: Dict[str, Any]) -> str:
    lines = [f"prompt version {PROMPT_VERSION}", "", "accuracy:"]
    lines += [f"  {name:8} {_ratio(pair)}" for name, pair in report["accuracy"].items()]
    lines += ["", "act confusions (expected → got):"]
    lines += [
        f"  {exp} → {got}: {n}" for (exp, got), n in sorted(report["confusion"].items())
    ]
    lines += [
        "",
        f"issue recall:        {_ratio(report['issue_recall'])}",
        f"issue false alarms:  {_ratio(report['issue_false_alarms'])} clean captions flagged",
        "",
        "calibration (top probability → answers, right):",
    ]
    lines += [
        f"  {label}: {n} answers, {right} right"
        for label, n, right in report["calibration"]
    ]
    if report["errors"]:
        lines += ["", "errors:"] + [f"  {e}" for e in report["errors"]]
    return "\n".join(lines)


class _Rows:
    def __init__(self, rows: List[CaptionRow]) -> None:
        self._rows = rows

    def get(self, row_id: int) -> CaptionRow:
        return self._rows[row_id]


class _Collect:
    def __init__(self) -> None:
        self.records: Dict[int, Dict[str, Any]] = {}

    def write(self, record: Dict[str, Any]) -> None:
        self.records[int(record["row_id"])] = record


async def _classify(
    args: argparse.Namespace, rows: List[CaptionRow]
) -> Dict[int, Dict[str, Any]]:
    server: Union[ExternalServer, LlamaServer]
    if args.server_url:
        server = ExternalServer(args.server_url)
    else:
        server = LlamaServer(
            lambda port: build_command(args.model, port, args.parallel, 6144)
        )
    sink = _Collect()
    runner = Runner(
        rows=_Rows(rows),
        server=server,
        writer=sink,
        model_id=args.model,
        prompt_version=PROMPT_VERSION,
        workers=args.parallel,
    )
    await server.start()
    try:
        await runner.run([r.id for r in rows])
    finally:
        await server.stop()
    return sink.records


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Evaluate the classifier on fixture captions."
    )
    ap.add_argument("--fixtures", type=Path, default=FIXTURES)
    ap.add_argument("--model", default="qwen3vl-30b-a3b")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--server-url", default="")
    ap.add_argument("--log-file", type=Path, default=DEFAULT_LOG)
    args = ap.parse_args(argv)
    setup_logging(args.log_file)
    fixtures = load_fixtures(args.fixtures)
    rows = [fixture_row(i, fx) for i, fx in enumerate(fixtures)]
    try:
        records = asyncio.run(_classify(args, rows))
    except (ServerError, FatalServerError) as exc:
        print(exc, file=sys.stderr)
        return 3
    print(format_report(score(fixtures, records)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
