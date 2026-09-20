#!/usr/bin/env python3
"""Validate an exported ComfyUI workflow against metascan's i2v contract.

Usage:  python scripts/validate_i2v_workflow.py <workflow.json>

Exits 0 if the graph would register as a (minimax, i2va) ref2v preset,
1 otherwise. Warnings are printed but do not fail the check.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from metascan.core.comfy_bindings import resolve_bindings  # noqa: E402
from metascan.core.workflow_validation import validate_workflow  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip())
        return 2
    path = Path(argv[1])
    try:
        workflow = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        print(f"FAIL  cannot read {path}: {exc}")
        return 1

    if "nodes" in workflow and "links" in workflow:
        print("FAIL  this is the UI workflow format, not API format.")
        print("      In ComfyUI: enable Dev Mode, then Workflow -> Export (API).")
        return 1

    report = validate_workflow(workflow, "ref2v", "minimax", "i2va")
    errors = [f for f in report.findings if f.level == "error"]
    warnings = [f for f in report.findings if f.level == "warning"]

    for f in errors:
        print(f"ERROR   [{f.code}] {f.message}")
    for f in warnings:
        print(f"warning [{f.code}] {f.message}")

    if errors:
        print(f"\nFAIL  {len(errors)} error(s); this workflow will not register.")
        return 1

    b = resolve_bindings(workflow, "ref2v")
    print("\nResolved bindings:")
    for name in (
        "positive",
        "seed",
        "save",
        "first_frame",
        "resolution",
        "duration",
        "lora_stack",
    ):
        node_id = getattr(b, name)
        cls = workflow.get(node_id, {}).get("class_type", "-") if node_id else "-"
        mark = "ok " if node_id else "   "
        print(f"  {mark} MS_{name.upper():<13} -> {str(node_id):<8} {cls}")

    missing = [n for n in ("resolution", "duration", "lora_stack") if not getattr(b, n)]
    print(f"\nPASS  registers as (minimax, i2va).", end="")
    print(f"  Optional slots unbound: {', '.join(missing)}" if missing else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
