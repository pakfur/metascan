"""MiniMax H3 I2VA prompt compiler for the image-to-video flow.

Pure module (no I/O). The format authority is
data/prompt_guides/minimax-h3/VIDEO_PROMPT_WRITING_GUIDE_base_en.md
(sections 2, 3.1, 4): the I2VA final prompt is the single-picture
alignment instruction, one blank line, then the three core fields
(integrated_multimodal_description / overall_soundscape /
non_diegetic_music). The VLM never produces document structure -- it
fills beat prose that assemble_i2v_prompt() slots into a code-owned
template, and the beat count is baked into the GBNF grammar so a wrong
count is structurally impossible (the shot-template fill_grammar
pattern). lint_i2v_prompt() is the advisory safety net, run over both
generated and hand-edited prompts.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import List, Tuple

from metascan.core.h3_compiler import _CAMERA_PHRASES
from metascan.core.i2v_fixes import camera_findings, speech_findings


class I2vError(RuntimeError):
    """Unrecoverable prompt-expansion failure (bad VLM output)."""


# Fixed duration -> beat count table (spec: duration drives beat count;
# off-table durations snap to the nearest entry, ties to the shorter).
_BEAT_COUNTS: Tuple[Tuple[float, int], ...] = (
    (6.0, 3),
    (10.0, 4),
    (15.0, 5),
    (20.0, 6),
)


def beat_count(duration_s: float) -> int:
    best = min(_BEAT_COUNTS, key=lambda e: (abs(e[0] - float(duration_s)), e[0]))
    return best[1]


# The base guide's section-4 motion table has no POV entry; pov is a
# storyboard-only concept, so it is excluded from the i2v camera enum.
I2V_CAMERA_VALUES: Tuple[str, ...] = tuple(v for v in _CAMERA_PHRASES if v != "pov")

_COMMON = r"""string ::= "\"" char* "\""
char ::= [^"\\\x7F\x00-\x1F] | "\\" (["\\bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])
ws ::= [ \t\n]*
"""


def i2v_grammar(duration_s: float) -> str:
    """GBNF for the beats+sound JSON. Beat count baked into the root
    rule; camera constrained to I2V_CAMERA_VALUES by alternation."""
    n = beat_count(duration_s)
    camera_alts = " | ".join('"\\"%s\\""' % v for v in I2V_CAMERA_VALUES)
    seq = ' ws "," ws '.join(["beat"] * n)
    return (
        'root ::= "{" ws "\\"beats\\"" ws ":" ws "[" ws ' + seq + ' ws "]" ws "," ws '
        '"\\"overall_soundscape\\"" ws ":" ws string ws "," ws '
        '"\\"non_diegetic_music\\"" ws ":" ws string ws "}"\n'
        'beat ::= "{" ws "\\"action\\"" ws ":" ws string ws "," ws '
        '"\\"camera\\"" ws ":" ws camera ws "}"\n'
        "camera ::= " + camera_alts + "\n" + _COMMON
    )


def i2v_max_tokens(duration_s: float) -> int:
    """Scale with beat count so long durations are never tail-truncated
    (the stage_max_tokens pattern)."""
    return 260 + 90 * beat_count(duration_s)


_ARC_HINT = (
    "Beat 1 must begin from exactly what the image depicts (it is the "
    "video's first frame) and set the action in motion; middle beats "
    "develop it continuously; the final beat lands a clear result or "
    "reaction."
)


def build_i2v_user_prompt(idea: str, duration_s: float) -> str:
    n = beat_count(duration_s)
    idea_line = idea.strip() or "(none -- infer a natural continuation)"
    return (
        f"The attached image is the exact first frame of a "
        f"{duration_s:.0f}-second video.\n"
        f"User's idea for the video: {idea_line}\n\n"
        f"Write exactly {n} beats. {_ARC_HINT}\n"
        "Each beat's 'action' is 1-2 present-tense sentences of concrete "
        "visible action and sound-producing events; 'camera' is one motion "
        "from the allowed list (use 'static' unless movement adds "
        "something). Then summarize ambient and action sound in "
        "'overall_soundscape' and the background score in "
        "'non_diegetic_music'."
    )


@dataclass
class I2vBeat:
    action: str
    camera: str


@dataclass
class I2vResult:
    beats: List[I2vBeat]
    overall_soundscape: str
    non_diegetic_music: str


_DEFAULT_SOUNDSCAPE = "Natural ambient sound consistent with the scene."
_DEFAULT_MUSIC = "No non-diegetic music."


def validate_i2v_beats(raw: str) -> I2vResult:
    """Strict parse + seatbelt normalization. The grammar is the real
    enforcement; this drops to safe defaults instead of raising wherever
    a default exists (storyboard_story validator philosophy)."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise I2vError(f"VLM response is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise I2vError("VLM response is not a JSON object")
    beats: List[I2vBeat] = []
    raw_beats = data.get("beats")
    for b in raw_beats if isinstance(raw_beats, list) else []:
        if not isinstance(b, dict):
            continue
        action = " ".join(str(b.get("action") or "").split())
        if not action:
            continue
        camera = str(b.get("camera") or "").strip()
        if camera not in I2V_CAMERA_VALUES:
            camera = "static"
        beats.append(I2vBeat(action=action, camera=camera))
    if not beats:
        raise I2vError("VLM returned no usable beats")

    def _text(key: str, default: str) -> str:
        v = " ".join(str(data.get(key) or "").split())
        return v or default

    return I2vResult(
        beats=beats,
        overall_soundscape=_text("overall_soundscape", _DEFAULT_SOUNDSCAPE),
        non_diegetic_music=_text("non_diegetic_music", _DEFAULT_MUSIC),
    )


# Base guide section 2.1, I2VA instruction -- verbatim, single picture.
ALIGNMENT_LINE = (
    "For the target video, at 0.00 seconds into the target video, "
    "<Picture 1> (from [Shot 1]) is fully referenced."
)

_OPENING = (
    "[Shot 1] The subjects, composition, and setting shown in "
    "<Picture 1> are established at 0.00 seconds and keep their "
    "appearance, clothing, colors, and spatial relationships."
)


def _sentence(text: str) -> str:
    t = text.strip()
    if t and t[-1] not in ".!?":
        t += "."
    return t


def assemble_i2v_prompt(result: I2vResult) -> str:
    """Deterministic assembly: code owns 100% of document structure."""
    parts = [_OPENING]
    any_motion = False
    for beat in result.beats:
        parts.append(_sentence(beat.action))
        if beat.camera != "static":
            any_motion = True
            parts.append(f"The camera {_CAMERA_PHRASES[beat.camera]}.")
    if not any_motion:
        parts.insert(1, "The camera holds a static shot.")
    description = " ".join(parts)
    return (
        f"{ALIGNMENT_LINE}\n\n"
        f"integrated_multimodal_description: {description}\n\n"
        f"overall_soundscape: {_sentence(result.overall_soundscape)}\n\n"
        f"non_diegetic_music: {_sentence(result.non_diegetic_music)}"
    )


_LABEL_RE = re.compile(r"<([^<>\n]+)>")
_ALLOWED_LABELS = {"Picture 1", "d", "/d"}
_REQUIRED_FIELDS = (
    "integrated_multimodal_description:",
    "overall_soundscape:",
    "non_diegetic_music:",
)
_REF_GUIDE_MARKERS = (
    "subject_definitions:",
    "retention_analysis:",
    "summary:",
)
_DESCRIPTION_RE = re.compile(
    r"integrated_multimodal_description:(.*?)(?:\n\n|\Z)", re.S
)
_MIN_WORDS_PER_BEAT = 12


def lint_i2v_prompt(text: str, duration_s: float) -> List[str]:
    """Expectation-driven advisory lint over the final document text.
    Warnings only -- runs on generated AND hand-edited prompts."""
    issues: List[str] = []
    lines = text.splitlines()
    if not lines or lines[0].strip() != ALIGNMENT_LINE:
        issues.append(
            "first line is not the exact I2VA alignment instruction "
            "(base guide section 2.1)"
        )
    elif len(lines) < 2 or lines[1].strip():
        issues.append("the alignment line must be followed by one blank line")
    for fld in _REQUIRED_FIELDS:
        n = text.count(fld)
        if n != 1:
            issues.append(f"field '{fld}' must appear exactly once (found {n})")
    for marker in _REF_GUIDE_MARKERS:
        if re.search(r"(?:^|\n)" + re.escape(marker), text):
            issues.append(
                f"'{marker}' is a ref-guide (six-section) field; the i2v "
                "flow uses the base-guide I2VA format"
            )
    for label in _LABEL_RE.findall(text):
        if label not in _ALLOWED_LABELS:
            issues.append(
                f"unknown reference label <{label}>; only <Picture 1> "
                "exists in this flow"
            )
    if "[Shot 1]" not in text:
        issues.append("the description does not open with [Shot 1]")
    m = _DESCRIPTION_RE.search(text)
    if m:
        words = len(m.group(1).split())
        floor = _MIN_WORDS_PER_BEAT * beat_count(duration_s)
        if words < floor:
            issues.append(
                f"description is short for {duration_s:.0f}s "
                f"({words} words < {floor}); consider more beat detail"
            )
    # Camera phrasing and unwrapped speech live in i2v_fixes, which also
    # knows how to rewrite most of them (the dialog's "Apply fixes").
    issues.extend(f.message for f in camera_findings(text))
    issues.extend(f.message for f in speech_findings(text))
    return issues


# ---- output resolution ------------------------------------------------
#
# The source image IS the first frame, so the output aspect ratio must
# track the source: any other AR letterboxes or crops that frame.
# Orientation therefore needs no control of its own -- it falls out of
# preserving the ratio. The user picks only a pixel budget.

I2V_DIM_MULTIPLE: int = 32


def i2v_dims(
    src_w: int, src_h: int, megapixels: float, multiple: int = I2V_DIM_MULTIPLE
) -> Tuple[int, int]:
    """Output dimensions at a megapixel budget, preserving source aspect.

    Both edges land on the ``multiple`` grid -- MiniMax H3's width/height
    widgets declare step=32, so an off-grid edge is not a valid size.
    Rounding each edge independently compounds the aspect error at that
    coarseness (a 16:9 source at 0.5 MP drifts ~4%), so candidate widths
    around the ideal are scored on aspect fidelity first, pixel budget
    second, and the best pair wins. The floor of one multiple keeps an
    extreme panorama's short edge usable rather than zero.
    """
    if src_w <= 0 or src_h <= 0:
        raise I2vError(f"Source dimensions must be positive, got {src_w}x{src_h}")
    if megapixels <= 0:
        raise I2vError(f"Megapixel budget must be positive, got {megapixels}")

    aspect = src_w / src_h
    budget = megapixels * 1_000_000
    ideal_w = math.sqrt(budget * aspect)

    def _snap(value: float) -> int:
        return max(multiple, round(value / multiple) * multiple)

    best: Tuple[float, int, int] = (float("inf"), 0, 0)
    centre = round(ideal_w / multiple)
    for step in range(-2, 3):
        width = max(1, centre + step) * multiple
        height = _snap(width / aspect)
        # Aspect fidelity dominates: the source image is the first frame,
        # so drift there is visible as crop or stretch. Budget is a
        # preference, weighted an order of magnitude lower.
        score = 10.0 * abs(math.log((width / height) / aspect)) + abs(
            math.log((width * height) / budget)
        )
        if score < best[0]:
            best = (score, width, height)
    return (int(best[1]), int(best[2]))


__all__ = [
    "ALIGNMENT_LINE",
    "I2V_CAMERA_VALUES",
    "I2V_DIM_MULTIPLE",
    "I2vBeat",
    "I2vError",
    "I2vResult",
    "assemble_i2v_prompt",
    "beat_count",
    "build_i2v_user_prompt",
    "i2v_dims",
    "i2v_grammar",
    "i2v_max_tokens",
    "lint_i2v_prompt",
    "validate_i2v_beats",
]
