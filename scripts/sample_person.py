#!/usr/bin/env python3
"""Sample people from person_appearance.yaml, or validate it.

The sampler is ``metascan.core.t2i_appearance`` (the one the T2I caption
engine draws characters with); this is its command line.

  python scripts/sample_person.py -n 10 --template portrait
  python scripts/sample_person.py -n 5 --set ancestry=celtic --set gender=woman
  python scripts/sample_person.py -n 3 -a          # also list attribute, value
  python scripts/sample_person.py --engine -n 3    # the caption engine's slots
  python scripts/sample_person.py --validate
  python scripts/sample_person.py -n 1000 --jsonl > people.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from metascan.core.t2i_appearance import AppearanceError, Sampler, parse  # noqa: E402
from metascan.core.t2i_wildcards import _reject_reason  # noqa: E402

DEFAULT = (
    Path(__file__).resolve().parent.parent / "data/t2i_captions/person_appearance.yaml"
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config", nargs="?", default=str(DEFAULT))
    ap.add_argument("-n", type=int, default=5)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--template", action="append")
    ap.add_argument(
        "--set",
        action="append",
        default=[],
        help="attr=value_id (comma-separated for multi)",
    )
    ap.add_argument("--jsonl", action="store_true")
    ap.add_argument(
        "-a",
        "--attributes",
        action="store_true",
        help="print 'attribute, value' lines for each sample before its prompts",
    )
    ap.add_argument("--engine", action="store_true", help="print the engine slots too")
    ap.add_argument("--validate", action="store_true")
    a = ap.parse_args()
    data = yaml.safe_load(open(a.config, encoding="utf-8"))
    try:
        appearance, warnings = parse(data, _reject_reason)
    except AppearanceError as exc:
        print(f"config errors: {exc}", file=sys.stderr)
        sys.exit(1)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    if a.validate:
        print("config OK", file=sys.stderr)
        return
    cfg = appearance.cfg
    sampler = Sampler(cfg, random.Random(a.seed))
    fixed = dict(x.split("=", 1) for x in a.set)
    templates = cfg.get("templates") or {}
    for _ in range(a.n):
        state = sampler.sample(fixed)
        composed = sampler.compose(state)
        prompts = {
            name: sampler.render(state, text, composed)
            for name, text in templates.items()
            if not a.template or name in a.template
        }
        slots = {
            name: sampler.render(state, text, composed)
            for name, text in appearance.engine.slots.items()
        }
        summary = sampler.summary(state)
        if a.jsonl:
            row = {"attributes": summary, "prompts": prompts, "slots": slots}
            print(json.dumps(row, ensure_ascii=False))
            continue
        if a.attributes:
            for attr, val in summary.items():
                print(f"{attr}, {val}")
        for name, text in prompts.items():
            print(f"[{name}] {text}")
        if a.engine:
            for name, text in slots.items():
                print(f"  {name}: {text}")
        print()


if __name__ == "__main__":
    main()
