"""Validate a ComfyUI workflow against the MS_* contract for a preset's
declared video target and generation mode.

Pure module (no I/O) layered on top of comfy_bindings: where
``resolve_bindings`` raises on the first hard failure, this module walks
the whole workflow and returns a ``ValidationReport`` -- every error and
warning at once, plus machine-applicable ``TitleFix`` suggestions (the
one fix that is safe to compute without knowing anything about a
target's node classes: renaming a misspelled ``MS_*`` title to the
closest known one).

The target/mode axis is the extension point: validators are registered
per ``(video_target, video_mode)`` pair in ``_VALIDATORS``. Only
``("minimax", "ref2va")`` ships today; a pair without a validator gets a
single "no target-specific validation" warning on top of the generic
contract checks, so registering a preset for a future target/mode never
hard-fails just because its validator hasn't been written yet.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from metascan.core.comfy_bindings import _REQUIRED_TITLES, _REQUIRED_WIDGETS

# Canonical axes for the association stored on workflow_presets
# (video_target / video_mode, both nullable). Extend these tuples as new
# dialects land -- the storyboard routes and the registration dialog both
# validate against them.
VIDEO_TARGETS: Tuple[str, ...] = ("minimax",)
VIDEO_MODES: Tuple[str, ...] = ("t2va", "i2va", "fl2va", "ref2va")

# Every title the MS_* contract knows, whether or not it carries required
# widgets (MS_SEED / MS_SAVE / MS_LORA_STACK have none).
KNOWN_TITLES: Tuple[str, ...] = tuple(
    sorted(set(_REQUIRED_WIDGETS) | {"MS_SEED", "MS_SAVE", "MS_LORA_STACK"})
)


@dataclass(frozen=True)
class Finding:
    """One validation result. ``level`` is 'error' (registration should be
    refused) or 'warning' (usable, but a capability will be missing)."""

    level: str
    code: str
    message: str
    node_id: Optional[str] = None


@dataclass(frozen=True)
class TitleFix:
    """A machine-applicable fix: set ``_meta.title`` on one node."""

    node_id: str
    old_title: Optional[str]
    title: str
    reason: str


@dataclass
class ValidationReport:
    findings: List[Finding] = field(default_factory=list)
    fixes: List[TitleFix] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.level == "error" for f in self.findings)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "findings": [asdict(f) for f in self.findings],
            "fixes": [asdict(f) for f in self.fixes],
        }


def _node_titles(workflow: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Every (node_id, MS_*-prefixed title) pair, in workflow order."""
    out: List[Tuple[str, str]] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        title = (node.get("_meta") or {}).get("title")
        if isinstance(title, str) and title.startswith("MS_"):
            out.append((str(node_id), title))
    return out


def _inputs(workflow: Dict[str, Any], node_id: str) -> Dict[str, Any]:
    node = workflow.get(node_id) or {}
    inputs = node.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def _generic_findings(
    workflow: Dict[str, Any], kind: str
) -> Tuple[List[Finding], List[TitleFix], Dict[str, str]]:
    """Kind-level MS_* contract checks, mirroring resolve_bindings but as
    a full report. Returns (findings, fixes, title -> node_id map)."""
    findings: List[Finding] = []
    fixes: List[TitleFix] = []

    pairs = _node_titles(workflow)
    found: Dict[str, str] = {}
    for node_id, title in pairs:
        if title in KNOWN_TITLES and title not in found:
            found[title] = node_id

    # Duplicates of known titles.
    seen: Dict[str, int] = {}
    for _, title in pairs:
        if title in KNOWN_TITLES:
            seen[title] = seen.get(title, 0) + 1
    for title, count in sorted(seen.items()):
        if count > 1:
            findings.append(
                Finding(
                    "error",
                    "duplicate_title",
                    f"{title} appears on {count} nodes; each MS_* title may "
                    "appear on at most one node.",
                )
            )

    # Unknown MS_* titles: warn, and propose a rename when there is exactly
    # one close known match that isn't already taken.
    for node_id, title in pairs:
        if title in KNOWN_TITLES:
            continue
        candidates = [t for t in KNOWN_TITLES if t not in found]
        match = difflib.get_close_matches(title, candidates, n=1, cutoff=0.6)
        if match:
            fixes.append(
                TitleFix(
                    node_id=node_id,
                    old_title=title,
                    title=match[0],
                    reason=f"{title} looks like a misspelling of {match[0]}",
                )
            )
            findings.append(
                Finding(
                    "warning",
                    "unknown_title",
                    f"Node {node_id} has unknown title {title}; did you mean "
                    f"{match[0]}? A fix is available.",
                    node_id=node_id,
                )
            )
        else:
            findings.append(
                Finding(
                    "warning",
                    "unknown_title",
                    f"Node {node_id} has unknown MS_* title {title}; metascan "
                    "will ignore it.",
                    node_id=node_id,
                )
            )

    # Missing required titles for the kind. A pending rename fix that would
    # supply the title keeps this an error (the fix hasn't been applied),
    # but the message points at it.
    fixable = {f.title for f in fixes}
    for title in _REQUIRED_TITLES.get(kind, ()):
        if title in found:
            continue
        suffix = " A title fix above would supply it." if title in fixable else ""
        findings.append(
            Finding(
                "error",
                "missing_required",
                f"Missing required node title {title} for kind {kind!r}." + suffix,
            )
        )

    # Widget checks on bound nodes.
    for title, node_id in sorted(found.items()):
        required = _REQUIRED_WIDGETS.get(title)
        if required:
            inputs = _inputs(workflow, node_id)
            missing = [w for w in required if w not in inputs]
            if missing:
                findings.append(
                    Finding(
                        "error",
                        "missing_widget",
                        f"Node {node_id} titled {title} is missing required "
                        "widget(s): " + ", ".join(missing),
                        node_id=node_id,
                    )
                )
    if "MS_SEED" in found:
        inputs = _inputs(workflow, found["MS_SEED"])
        if "seed" not in inputs and "noise_seed" not in inputs:
            findings.append(
                Finding(
                    "error",
                    "missing_widget",
                    f"Node {found['MS_SEED']} titled MS_SEED exposes neither a "
                    "'seed' nor a 'noise_seed' widget.",
                    node_id=found["MS_SEED"],
                )
            )

    return findings, fixes, found


# -- Per-(target, mode) validators ----------------------------------------

Validator = Callable[[Dict[str, Any], Dict[str, str]], List[Finding]]


def _validate_minimax_ref2va(
    workflow: Dict[str, Any], found: Dict[str, str]
) -> List[Finding]:
    """MiniMax H3 ref2va: reference pictures, voice audio, and a driven
    duration are how the storyboard pipeline feeds the workflow -- their
    absence doesn't break generation, it silently drops a capability."""
    findings: List[Finding] = []
    if "MS_REF_IMAGE" not in found:
        findings.append(
            Finding(
                "warning",
                "no_ref_image_slot",
                "No MS_REF_IMAGE slot: subject/scene reference pictures "
                "cannot be attached (ref2va is reference-driven).",
            )
        )
    if "MS_AUDIO" not in found:
        findings.append(
            Finding(
                "warning",
                "no_audio_slot",
                "No MS_AUDIO slot: subject voice references cannot be " "attached.",
            )
        )
    if "MS_DURATION" not in found:
        findings.append(
            Finding(
                "warning",
                "no_duration",
                "No MS_DURATION node: clip length will not follow the "
                "shot's duration.",
            )
        )
    for title in ("MS_FIRST_FRAME", "MS_LAST_FRAME"):
        if title in found:
            findings.append(
                Finding(
                    "warning",
                    "keyframe_slot_unused",
                    f"{title} is bound but ref2va never writes keyframes; "
                    "the baked-in image will be used verbatim.",
                    node_id=found[title],
                )
            )
    return findings


_VALIDATORS: Dict[Tuple[str, str], Validator] = {
    ("minimax", "ref2va"): _validate_minimax_ref2va,
}


def validate_workflow(
    workflow: Dict[str, Any],
    kind: str,
    video_target: Optional[str] = None,
    video_mode: Optional[str] = None,
) -> ValidationReport:
    """Full-workflow validation: the generic MS_* contract for ``kind``,
    plus the (target, mode) validator when one is registered."""
    findings, fixes, found = _generic_findings(workflow, kind)
    if video_target and video_mode:
        validator = _VALIDATORS.get((video_target, video_mode))
        if validator is not None:
            findings.extend(validator(workflow, found))
        else:
            findings.append(
                Finding(
                    "warning",
                    "no_validator",
                    f"No target-specific validation exists yet for "
                    f"({video_target}, {video_mode}); only the generic MS_* "
                    "contract was checked.",
                )
            )
    return ValidationReport(findings=findings, fixes=fixes)


def apply_fixes(workflow: Dict[str, Any], fixes: List[TitleFix]) -> Dict[str, Any]:
    """Return a deep copy of ``workflow`` with each fix's title applied.
    A fix whose node no longer exists is skipped silently -- fixes are
    computed against the same graph they are applied to in practice."""
    graph: Dict[str, Any] = json.loads(json.dumps(workflow))
    for fix in fixes:
        node = graph.get(fix.node_id)
        if isinstance(node, dict):
            meta = node.setdefault("_meta", {})
            if isinstance(meta, dict):
                meta["title"] = fix.title
    return graph


__all__ = [
    "Finding",
    "TitleFix",
    "ValidationReport",
    "VIDEO_MODES",
    "VIDEO_TARGETS",
    "apply_fixes",
    "validate_workflow",
]
