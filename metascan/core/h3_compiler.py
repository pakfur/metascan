"""H3 (MiniMax) video-prompt compiler — pure core: plans, timeline math,
deterministic section renderers, LLM scaffold, document assembly.

Format authority — every rendered string in this module follows these
two vendored guides verbatim (read fully before touching a renderer):

- ``data/prompt_guides/minimax-h3/VIDEO_PROMPT_WRITING_GUIDE_base_en.md``
  (T2VA/I2VA/FL2VA instruction lines §2.1, shots/cuts §4.2, camera
  vocabulary §4.3).
- ``data/prompt_guides/minimax-h3/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md``
  (full-reference labels §2, ``summary`` task-type prefixes §3,
  ``retention_analysis`` relationship markers §4, speaker/dialog rules
  §5.4, six-section document order §1/§7).

Pure module: no I/O, no VLM calls (those live in ``storyboard_runner``
and are added in Task 4 alongside the lint layer from Task 3). Imports
are limited to ``dataclasses``/``typing``/``json`` plus the shared
``CAMERA_MOTION_VALUES`` enum tuple.

``<Audio N>`` voice-timbre references (ref-guide §2.4/§4.2, ``RefPlan.
audio_labels``, ``render_audio_definition_lines``/``render_audio_
retention_lines``/``active_audio_refs`` below) are deliberately NOT
covered by ``_lint_unknown_labels`` -- its ``_LABEL_RE`` regex only
matches ``Subject``/``Picture``, so an ``<Audio N>`` span in a compiled
document is invisible to lint by design. Do not extend that regex to
"fix" this; audio spans are static (subject/voice-ref-driven, never
VLM-authored) so there's nothing to validate against drift.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

from metascan.core.storyboard_story import CAMERA_MOTION_VALUES

_PROMPT_KEYS = frozenset({"H3_BODY_SYSTEM", "H3_SOUND_SYSTEM"})


def __getattr__(name: str) -> str:
    """Resolve ``H3_BODY_SYSTEM`` / ``H3_SOUND_SYSTEM`` against the live
    YAML prompt store on every access (module-attribute hot reload).

    NOTE: access these as module attributes at call time (``h3.H3_BODY_SYSTEM``).
    A module-scope ``from ... import H3_BODY_SYSTEM`` freezes a snapshot at
    import time and defeats the hot reload -- mirrors ``ref_describe.py``
    and ``storyboard_story.py``'s identical caveat for their own prompts.
    """
    if name in _PROMPT_KEYS:
        from metascan.core.prompt_store import get_prompt_store

        return get_prompt_store().get(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class H3Error(ValueError):
    """An H3 LLM-stage response could not be validated."""


# -- Dataclasses -------------------------------------------------------


@dataclass(frozen=True)
class RefPlan:
    subject_labels: Dict[int, str]  # subject_id -> "Subject 1"
    environment_label: str  # "Subject K+1" (scene environment)
    picture_labels: List[Tuple[str, str]]  # [(posix_path, "Picture 1"), ...]
    keyframe_picture_label: str  # next free "Picture N" (i2va/fl2va anchors)
    # (subject_id, "Audio N") -- numbered 1.. in subject sort_order, assigned
    # only for subjects with a truthy voice_ref_path (ref-guide §2.4). Default
    # keeps every pre-Task-4 constructor call/test passing unchanged.
    audio_labels: Tuple[Tuple[int, str], ...] = ()


@dataclass(frozen=True)
class SpeakerLine:
    beat_index: int
    line_index: int
    speaker_id: str  # "S1"
    subject_label: Optional[str]  # "<Subject 2>" or None for unnamed voices
    voice: Optional[str]
    delivery: Optional[str]
    language: str
    text: str


@dataclass(frozen=True)
class SpeakerPlan:
    lines: List[SpeakerLine]
    voice_by_id: Dict[str, str]  # "S1" -> establishing voice description


@dataclass(frozen=True)
class TimelineShot:
    number: int  # 1-based internal [Shot N]
    start_s: float  # 0.0 for shot 1
    beat_indices: List[int]


@dataclass(frozen=True)
class Timeline:
    shots: List[TimelineShot]
    duration_s: float
    alignment_line: Optional[str]  # None for t2va/ref2va


@dataclass(frozen=True)
class LintError:
    code: str  # e.g. "missing_section", "timestamp_order", "dialog_missing"
    severity: str  # "error" | "warning"
    message: str


@dataclass(frozen=True)
class LintExpectations:
    subject_labels: FrozenSet[str]  # {"Subject 1", ...} incl. environment
    picture_labels: FrozenSet[str]  # incl. keyframe label(s) when mode uses them
    dialog_lines: Tuple[SpeakerLine, ...]
    timeline: Timeline
    duration_s: float
    # Per internal shot (same order as ``timeline.shots``), the canonical
    # phrases (``_CAMERA_PHRASES`` values) of that shot's beats' explicitly
    # set ``camera_motion``s. Empty by default -- only populated when
    # ``build_expectations`` is given ``beats`` (Task 4's controller
    # ruling); the camera_vocab lint check only cross-checks a shot's text
    # against this when it's non-empty, keeping it backward compatible.
    shot_camera_phrases: Tuple[Tuple[str, ...], ...] = ()


# -- Camera vocabulary (base guide §4.3) --------------------------------

_CAMERA_PHRASES: Dict[str, str] = {
    "zoom_in": "zooms in",
    "zoom_out": "zooms out",
    "push_in": "pushes in",
    "pull_out": "pulls out",
    "pan_left": "pans left",
    "pan_right": "pans right",
    "truck_left": "trucks left",
    "truck_right": "trucks right",
    "tilt_up": "tilts up",
    "tilt_down": "tilts down",
    "pedestal_up": "pedestals up",
    "pedestal_down": "pedestals down",
    "arc": "arcs around the subject",
    "tracking": "tracks the subject",
    "static": "holds a static shot",
    "shake_slight": "shakes slightly",
    "shake_strong": "shakes strongly",
    "pov": "adopts the subject's point of view",
    "roll_cw": "rolls clockwise",
    "roll_ccw": "rolls counterclockwise",
}

assert set(_CAMERA_PHRASES.keys()) == set(CAMERA_MOTION_VALUES), (
    "h3_compiler._CAMERA_PHRASES has drifted from "
    "storyboard_story.CAMERA_MOTION_VALUES"
)

_KEYFRAME_MODES = ("i2va", "fl2va")


# -- Plans ---------------------------------------------------------------


def assign_reference_labels(
    subjects: Sequence[Mapping[str, Any]], scene: Mapping[str, Any]
) -> RefPlan:
    """Number subjects, the scene environment, every reference picture, and
    every voice-timbre audio reference.

    ref-guide §2.1 makes environments Subjects: the scene always becomes
    the final ``<Subject K+1>``. Pictures are numbered in upload order:
    each subject's ``reference_path`` then ``reference_path_2`` (subject
    order), then the scene's ``reference_path``. ``<Audio N>`` labels
    (ref-guide §2.4) are numbered separately, in subject ``sort_order``,
    for every subject with a truthy ``voice_ref_path``.
    """
    ordered = sorted(subjects, key=lambda s: s.get("sort_order", 0))
    subject_labels: Dict[int, str] = {}
    picture_labels: List[Tuple[str, str]] = []
    audio_labels: List[Tuple[int, str]] = []
    next_picture = 1
    next_audio = 1

    for subject in ordered:
        subject_labels[subject["id"]] = f"Subject {len(subject_labels) + 1}"
        for key in ("reference_path", "reference_path_2"):
            path = subject.get(key)
            if path:
                picture_labels.append((path, f"Picture {next_picture}"))
                next_picture += 1
        if subject.get("voice_ref_path"):
            audio_labels.append((subject["id"], f"Audio {next_audio}"))
            next_audio += 1

    scene_ref = scene.get("reference_path")
    if scene_ref:
        picture_labels.append((scene_ref, f"Picture {next_picture}"))
        next_picture += 1

    environment_label = f"Subject {len(subject_labels) + 1}"
    keyframe_picture_label = f"Picture {next_picture}"
    return RefPlan(
        subject_labels=subject_labels,
        environment_label=environment_label,
        picture_labels=picture_labels,
        keyframe_picture_label=keyframe_picture_label,
        audio_labels=tuple(audio_labels),
    )


def _normalize_voice(voice: Optional[str]) -> str:
    return (voice or "").strip().lower()


def assign_speakers(
    beats: Sequence[Mapping[str, Any]],
    subjects: Sequence[Mapping[str, Any]],
    refplan: RefPlan,
) -> SpeakerPlan:
    """Assign stable ``(Sx)`` ids in first-vocal-event order (ref-guide §5.4).

    The key is ``subject_id`` when the dialog line names one, else the
    normalized voice string — so the same key always yields the same id.
    """
    subject_by_id = {s["id"]: s for s in subjects}
    key_to_speaker: Dict[Tuple[str, Any], str] = {}
    voice_by_id: Dict[str, str] = {}
    lines: List[SpeakerLine] = []
    next_n = 1

    for beat_index, beat in enumerate(beats):
        dialog = beat.get("dialog") or []
        for line_index, d in enumerate(dialog):
            subject_id = d.get("subject_id")
            if subject_id is not None:
                key: Tuple[str, Any] = ("subj", subject_id)
            else:
                key = ("voice", _normalize_voice(d.get("voice")))

            speaker_id = key_to_speaker.get(key)
            if speaker_id is None:
                speaker_id = f"S{next_n}"
                key_to_speaker[key] = speaker_id
                next_n += 1
                if subject_id is not None:
                    subject = subject_by_id.get(subject_id) or {}
                    voice_by_id[speaker_id] = (
                        subject.get("voice") or d.get("voice") or "a voice"
                    )
                else:
                    voice_by_id[speaker_id] = d.get("voice") or "a voice"

            subject_label = (
                f"<{refplan.subject_labels.get(subject_id)}>"
                if subject_id is not None and refplan.subject_labels.get(subject_id)
                else None
            )
            lines.append(
                SpeakerLine(
                    beat_index=beat_index,
                    line_index=line_index,
                    speaker_id=speaker_id,
                    subject_label=subject_label,
                    voice=d.get("voice"),
                    delivery=d.get("delivery"),
                    language=d.get("language") or "English",
                    text=d.get("text", ""),
                )
            )

    return SpeakerPlan(lines=lines, voice_by_id=voice_by_id)


# -- Timeline --------------------------------------------------------------


def format_timecode(seconds: float) -> str:
    """Render seconds as ``MM:SS.mmm``, e.g. ``"00:03.500"``."""
    minutes = int(seconds // 60)
    secs = seconds - minutes * 60
    return f"{minutes:02d}:{secs:06.3f}"


def _increment_picture_label(label: str) -> str:
    prefix, _, num = label.rpartition(" ")
    return f"{prefix} {int(num) + 1}"


def _build_alignment_line(
    mode: str,
    refplan: RefPlan,
    duration_s: float,
    shots: Sequence[TimelineShot],
) -> Optional[str]:
    if mode == "i2va":
        kf = refplan.keyframe_picture_label
        return (
            "For the target video, at 0.00 seconds into the target video, "
            f"<{kf}> (from [Shot 1]) is fully referenced."
        )
    if mode == "fl2va":
        kf = refplan.keyframe_picture_label
        kf2 = _increment_picture_label(kf)
        last = shots[-1].number if shots else 1
        return (
            "How the reference pictures align with the target video — "
            f"{kf} (from Shot 1) aligns with the 0.00-second mark of the "
            f"target video; {kf2} (from Shot {last}) aligns with the "
            f"{duration_s:.2f}-second mark of the target video."
        )
    return None


def compute_timeline(
    beats: Sequence[Mapping[str, Any]],
    duration_s: float,
    mode: str,
    refplan: RefPlan,
) -> Timeline:
    """Rescale beat durations to sum exactly to ``duration_s`` and map
    every beat to its own ``[Shot n]``.

    Beat == shot is deliberate: the storyboard's beat breakdown IS the
    shot script, and the compiled ``detailed_description`` must follow it
    exactly — one ``[Shot n]`` per beat, each carrying its start
    timestamp (the end time of the previous beat). ``is_cut`` no longer
    changes the grouping; it only tunes the deterministic phrasing in
    ``render_detailed_description`` ("the shot cuts" vs a continuous
    transition)."""
    n = len(beats)
    orig_durations = [float(b.get("duration_s") or 0.0) for b in beats]
    total = sum(orig_durations)
    if total > 0:
        scale = duration_s / total
        rescaled = [d * scale for d in orig_durations]
    elif n > 0:
        rescaled = [duration_s / n] * n
    else:
        rescaled = []

    shots: List[TimelineShot] = []
    cum = 0.0
    for i in range(n):
        shots.append(TimelineShot(number=i + 1, start_s=cum, beat_indices=[i]))
        cum += rescaled[i]

    alignment_line = _build_alignment_line(mode, refplan, duration_s, shots)
    return Timeline(shots=shots, duration_s=duration_s, alignment_line=alignment_line)


# -- Camera rendering --------------------------------------------------------


def render_camera(
    motion: Optional[str], amplitude: Optional[str], speed: Optional[str]
) -> Optional[str]:
    """``"{motion phrase}, {amplitude} amplitude, {speed} speed"``, dropping
    absent parts. ``None`` when nothing is set."""
    parts: List[str] = []
    if motion:
        parts.append(_CAMERA_PHRASES[motion])
    if amplitude:
        parts.append(f"{amplitude} amplitude")
    if speed:
        parts.append(f"{speed} speed")
    if not parts:
        return None
    return ", ".join(parts)


# -- Deterministic section renderers -----------------------------------


def _fallback_setting(scene: Mapping[str, Any]) -> str:
    return str(scene.get("setting") or scene.get("location") or scene.get("name") or "")


def render_subject_definitions(
    refplan: RefPlan,
    subjects: Sequence[Mapping[str, Any]],
    scene: Mapping[str, Any],
) -> str:
    ordered = sorted(subjects, key=lambda s: s.get("sort_order", 0))
    pic_by_path = dict(refplan.picture_labels)
    lines: List[str] = []

    for subject in ordered:
        label = refplan.subject_labels[subject["id"]]
        name = subject.get("name", "")
        description = subject.get("description", "")
        pics = [
            pic_by_path[p]
            for p in (subject.get("reference_path"), subject.get("reference_path_2"))
            if p and p in pic_by_path
        ]
        if pics:
            pic_str = " and ".join(f"<{p}>" for p in pics)
            lines.append(f"<{label}> is the {name} in {pic_str}, {description}.")
        else:
            lines.append(f"<{label}> is the {name}: {description}.")

    scene_name = scene.get("name", "scene")
    setting = _fallback_setting(scene)
    scene_ref = scene.get("reference_path")
    env_pic = pic_by_path.get(scene_ref) if scene_ref else None
    if env_pic:
        lines.append(
            f"<{refplan.environment_label}> is the {scene_name} environment "
            f"in <{env_pic}>, {setting}."
        )
    else:
        lines.append(
            f"<{refplan.environment_label}> is the {scene_name} environment: {setting}."
        )
    return "\n".join(lines)


def render_summary(
    refplan: RefPlan,
    panel: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
    mode: str,
) -> str:
    ordered = sorted(subjects, key=lambda s: s.get("sort_order", 0))
    labels = [f"<{refplan.subject_labels[s['id']]}>" for s in ordered]
    if not labels:
        joined = ""
    elif len(labels) == 1:
        joined = labels[0]
    else:
        joined = ", ".join(labels[:-1]) + " and " + labels[-1]

    prefix = (
        "[reference generation + keyframe completion]"
        if mode in _KEYFRAME_MODES
        else "[reference generation]"
    )
    action = _substitute_subject_labels(str(panel.get("action", "")), subjects, refplan)
    return (
        f"{prefix} The target video shows {joined} in "
        f"<{refplan.environment_label}>: {action}."
    )


def _first_words(text: str, n: int = 12) -> str:
    return " ".join(text.split()[:n])


def render_retention_analysis(
    refplan: RefPlan,
    subjects: Sequence[Mapping[str, Any]],
    scene: Mapping[str, Any],
    timeline: Timeline,
) -> str:
    ordered = sorted(subjects, key=lambda s: s.get("sort_order", 0))
    shot_list = ", ".join(f"[Shot {shot.number}]" for shot in timeline.shots)
    lines: List[str] = []

    for subject in ordered:
        label = refplan.subject_labels[subject["id"]]
        descriptor = _first_words(subject.get("description", ""))
        lines.append(
            f"<{label}> (appears in {shot_list}): fully_preserved - {descriptor}."
        )

    env_descriptor = _first_words(_fallback_setting(scene))
    lines.append(
        f"<{refplan.environment_label}> (appears in {shot_list}): "
        f"fully_preserved - {env_descriptor}."
    )

    if timeline.alignment_line is not None:
        kf = refplan.keyframe_picture_label
        if "is fully referenced" in timeline.alignment_line:
            lines.append(
                f"<{kf}> ([Shot 1] first frame): fully_preserved - "
                "the shot begins from this frame."
            )
        else:
            kf2 = _increment_picture_label(kf)
            last = timeline.shots[-1].number if timeline.shots else 1
            lines.append(
                f"<{kf2}> ([Shot {last}] last frame): fully_preserved - "
                "the shot ends on this frame."
            )

    return "\n".join(lines)


def _dialog_clause(sl: SpeakerLine) -> str:
    # The beats stage sometimes parrots the line text into ``voice`` (it
    # should be a timbre like "soft female voice") — treat that as unset
    # rather than rendering "says in a Hello? voice". Containment either
    # way, not equality: observed values include the text with a filler
    # prefix ("Mm... you're here." for the line "You're here.").
    voice = (sl.voice or "").strip()
    text_norm = (sl.text or "").strip().lower()
    voice_norm = voice.lower()
    if text_norm and (text_norm in voice_norm or voice_norm in text_norm):
        voice = ""
    speaker = (
        f"{sl.subject_label} ({sl.speaker_id})"
        if sl.subject_label
        else f"the {voice or 'voice'} ({sl.speaker_id})"
    )
    clause = "says"
    if voice:
        suffix = "" if "voice" in voice.lower() else " voice"
        clause += f" in a {voice}{suffix}"
    if sl.delivery:
        clause += f", {sl.delivery.strip()},"
    return f"{speaker} {clause} <d>[{sl.language}] {sl.text}</d>"


def _substitute_subject_labels(
    text: str,
    subjects: Sequence[Mapping[str, Any]],
    refplan: RefPlan,
) -> str:
    """Replace roster-name mentions with their ``<Subject N>`` labels.

    The ref guide requires every subject reference in the prose to carry
    its label, but the beats stage writes plain roster names ("the Young
    Escort"). Longest name first so overlapping names can't partially
    match; an optional leading article folds into the replacement, so
    "the Business Woman's" becomes "<Subject 1>'s"."""
    out = text
    ordered = sorted(
        subjects, key=lambda s: len(str(s.get("name") or "")), reverse=True
    )
    for subject in ordered:
        name = str(subject.get("name") or "").strip()
        label = refplan.subject_labels.get(subject["id"])
        if not name or not label:
            continue
        pattern = re.compile(r"\b(?:the\s+)?" + re.escape(name) + r"\b", re.IGNORECASE)
        out = pattern.sub(f"<{label}>", out)
    return out


def _sentence(text: str) -> str:
    s = text.strip()
    if not s:
        return s
    s = s[0].upper() + s[1:]
    return s if s[-1] in ".!?" else s + "."


def render_detailed_description(
    style: str,
    beats: Sequence[Mapping[str, Any]],
    timeline: Timeline,
    speakers: SpeakerPlan,
    subjects: Sequence[Mapping[str, Any]] = (),
    refplan: Optional[RefPlan] = None,
) -> str:
    """Deterministic ``detailed_description``: the beat breakdown IS the
    shot script, rendered verbatim rather than paraphrased by the VLM.

    One ``[Shot n]`` per beat (beat == shot, see ``compute_timeline``);
    every shot after the first carries its ``At MM:SS.mmm`` start
    timestamp per the ref-guide §5 cut-time convention. Each shot block
    is the beat's action prose (roster-name mentions swapped for their
    ``<Subject N>`` labels), its canonical camera phrase, its dialog as
    ``(Sx)``-tagged ``<d>`` spans, and its sound event."""

    def _labeled(text: str) -> str:
        if refplan is None or not subjects:
            return text
        return _substitute_subject_labels(text, subjects, refplan)

    lines_by_beat: Dict[int, List[SpeakerLine]] = {}
    for sl in speakers.lines:
        lines_by_beat.setdefault(sl.beat_index, []).append(sl)

    parts = [f"The target video is in a {style} style."]
    for shot in timeline.shots:
        bi = shot.beat_indices[0]
        beat = beats[bi] if bi < len(beats) else {}
        if shot.number == 1:
            header = "[Shot 1]"
        elif beat.get("is_cut"):
            header = f"[Shot {shot.number}] At {format_timecode(shot.start_s)}, the shot cuts."
        else:
            header = (
                f"[Shot {shot.number}] At {format_timecode(shot.start_s)}, "
                "continuing without a cut."
            )

        sentences: List[str] = []
        action = _sentence(_labeled(str(beat.get("action") or "")))
        if action:
            sentences.append(action)
        camera = render_camera(
            beat.get("camera_motion"),
            beat.get("camera_amplitude"),
            beat.get("camera_speed"),
        )
        if camera:
            sentences.append(f"The camera {camera}.")
        for sl in sorted(lines_by_beat.get(bi, []), key=lambda x: x.line_index):
            sentences.append(_dialog_clause(sl))
        sound = beat.get("sound")
        if sound:
            sentences.append(_sentence(_labeled(str(sound))))

        parts.append(f"{header} {' '.join(sentences)}".strip())
    return "\n".join(parts)


# -- Audio references (ref-guide §2.4/§4.2) --------------------------------


def _active_audio_entries(
    refplan: RefPlan, speakers: SpeakerPlan, subjects: Sequence[Mapping[str, Any]]
) -> List[Tuple[int, str, str, str]]:
    """``(subject_id, audio_label, speaker_id, voice_ref_path)`` for every
    ``RefPlan`` audio label whose subject actually SPEAKS in this panel --
    i.e. its bracketed subject label (``"<Subject N>"``) appears among
    ``speakers.lines``. Non-speaking subjects' audio labels are silently
    dropped here, which is what keeps ``render_audio_definition_lines``,
    ``render_audio_retention_lines``, and ``active_audio_refs`` (the
    runner's upload order) in lockstep with each other."""
    subject_by_id = {s["id"]: s for s in subjects}
    speaking_labels = {sl.subject_label for sl in speakers.lines if sl.subject_label}
    out: List[Tuple[int, str, str, str]] = []
    for subject_id, audio_label in refplan.audio_labels:
        subject_label = refplan.subject_labels.get(subject_id)
        bracketed = f"<{subject_label}>" if subject_label else None
        if bracketed is None or bracketed not in speaking_labels:
            continue
        subject = subject_by_id.get(subject_id)
        voice_ref_path = subject.get("voice_ref_path") if subject else None
        if not voice_ref_path:
            continue
        speaker_id = next(
            (sl.speaker_id for sl in speakers.lines if sl.subject_label == bracketed),
            None,
        )
        if speaker_id is None:
            continue
        out.append((subject_id, audio_label, speaker_id, voice_ref_path))
    return out


def active_audio_refs(
    refplan: RefPlan, speakers: SpeakerPlan, subjects: Sequence[Mapping[str, Any]]
) -> List[Tuple[str, str]]:
    """``[(voice_ref_path, "Audio N"), ...]`` in label order -- the set the
    runner actually uploads as H3 audio references. Only subjects with a
    defined ``<Audio N>`` label (truthy ``voice_ref_path``) who also
    actually speak in this panel are included."""
    return [
        (voice_ref_path, audio_label)
        for _, audio_label, _, voice_ref_path in _active_audio_entries(
            refplan, speakers, subjects
        )
    ]


def render_audio_definition_lines(
    refplan: RefPlan, speakers: SpeakerPlan, subjects: Sequence[Mapping[str, Any]]
) -> List[str]:
    """ref-guide §2.4: one ``subject_definitions`` line per active audio
    label -- ``"<Audio 1> is the voice-timbre reference for <Subject 2>
    (S1)."`` -- reusing the speaker's already-assigned ``(Sx)`` id rather
    than assigning a new one (never independently numbered)."""
    lines: List[str] = []
    for subject_id, audio_label, speaker_id, _ in _active_audio_entries(
        refplan, speakers, subjects
    ):
        subject_label = refplan.subject_labels[subject_id]
        lines.append(
            f"<{audio_label}> is the voice-timbre reference for "
            f"<{subject_label}> ({speaker_id})."
        )
    return lines


def render_audio_retention_lines(
    refplan: RefPlan, speakers: SpeakerPlan, subjects: Sequence[Mapping[str, Any]]
) -> List[str]:
    """ref-guide §4.2 ``reference`` relationship marker for the same active
    set as ``render_audio_definition_lines``: ``"<Audio 1>: reference -
    the target speaker follows <Audio 1>'s voice timbre and delivery
    without copying the original signal."``"""
    return [
        f"<{audio_label}>: reference - the target speaker follows "
        f"<{audio_label}>'s voice timbre and delivery without copying "
        "the original signal."
        for _, audio_label, _, _ in _active_audio_entries(refplan, speakers, subjects)
    ]


# -- LLM scaffold ------------------------------------------------------------


def build_scaffold(
    panel: Mapping[str, Any],
    scene: Mapping[str, Any],
    storyboard: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
    refplan: RefPlan,
    speakers: SpeakerPlan,
    timeline: Timeline,
) -> str:
    """Machine-readable brief for the H3 body/sound LLM stages (Task 4)."""
    beats = panel.get("beats") or []
    style = storyboard.get("style_block") or "cinematic, live-action"

    subject_by_id = {s["id"]: s for s in subjects}
    subject_ids = panel.get("subject_ids") or list(subject_by_id.keys())
    present_parts: List[str] = []
    for sid in subject_ids:
        subject = subject_by_id.get(sid)
        if subject is None:
            continue
        label = refplan.subject_labels.get(sid, "")
        present_parts.append(
            f"<{label}> {subject.get('name', '')} — {subject.get('description', '')}"
        )
    subjects_present = "; ".join(present_parts)

    lines_by_beat: Dict[int, List[SpeakerLine]] = {}
    for sl in speakers.lines:
        lines_by_beat.setdefault(sl.beat_index, []).append(sl)

    out: List[str] = [
        f"STYLE: {style}",
        f"SUBJECTS PRESENT: {subjects_present}",
        f"DURATION: {timeline.duration_s:.1f}s",
    ]

    for shot in timeline.shots:
        if shot.number == 1:
            out.append(f"[Shot 1] starts {format_timecode(shot.start_s)}")
        else:
            out.append(f"[Shot {shot.number}] At {format_timecode(shot.start_s)}, cut.")

        for bi in shot.beat_indices:
            beat = beats[bi] if bi < len(beats) else {}
            out.append(f"- action: {beat.get('action', '')}")

            camera = render_camera(
                beat.get("camera_motion"),
                beat.get("camera_amplitude"),
                beat.get("camera_speed"),
            )
            out.append(f"- camera: {camera or 'unspecified'}")

            for sl in sorted(lines_by_beat.get(bi, []), key=lambda x: x.line_index):
                if sl.subject_label:
                    speaker_part = f"{sl.subject_label} ({sl.speaker_id})"
                else:
                    speaker_part = f"the {sl.voice or 'voice'} ({sl.speaker_id})"
                delivery_part = f" [{sl.delivery}]" if sl.delivery else ""
                out.append(
                    f"- dialog: {speaker_part}{delivery_part} "
                    f'({sl.language}): "{sl.text}"'
                )

            sound = beat.get("sound")
            if sound:
                out.append(f"- sound: {sound}")

    return "\n".join(out)


# -- LLM stage plumbing (Task 4): grammar, validator, prompt builders ------
#
# The VLM calls themselves live in storyboard_runner.py -- this module
# stays pure (no I/O). Grammar built with the same _COMMON-rules .format()
# idiom as ref_describe.py; "N/A" for non_diegetic_music is a plain string
# value, not null, so both fields are the unadorned ``string`` rule.

_COMMON = r"""nullable ::= string | "null"
string ::= "\"" char* "\""
char ::= [^"\\\x7F\x00-\x1F] | "\\" (["\\bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])
ws ::= [ \t\n]*
"""

SOUND_GRAMMAR = (
    r"""root ::= "{{" ws "\"overall_soundscape\"" ws ":" ws string ws "," ws "\"non_diegetic_music\"" ws ":" ws string ws "}}"
"""
    + _COMMON
).format()


def _loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as e:
        raise H3Error(f"response is not valid JSON: {e}") from e


def _clean(v: Any) -> Optional[str]:
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return None


def validate_sound_response(raw: str) -> Dict[str, str]:
    """Validate the sound-stage JSON: ``{"overall_soundscape", "non_diegetic_music"}``.

    An empty/absent ``non_diegetic_music`` becomes ``"N/A"``; an empty or
    absent ``overall_soundscape`` raises (every shot has *some* ambience).
    """
    data = _loads(raw)
    if not isinstance(data, dict):
        raise H3Error("sound response is not a JSON object")
    soundscape = _clean(data.get("overall_soundscape"))
    if not soundscape:
        raise H3Error("overall_soundscape is empty")
    music = _clean(data.get("non_diegetic_music")) or "N/A"
    return {"overall_soundscape": soundscape, "non_diegetic_music": music}


def build_body_user_prompt(scaffold: str) -> str:
    return (
        f"Shot scaffold:\n{scaffold}\n\n"
        "Write the detailed_description body for this scaffold."
    )


def build_retry_user_prompt(scaffold: str, errors: List[LintError]) -> str:
    error_lines = (
        "\n".join(f"- {e.message}" for e in errors if e.severity == "error")
        or "(no specific errors reported)"
    )
    return (
        f"Shot scaffold:\n{scaffold}\n\n"
        "Your previous detailed_description body had these problems:\n"
        f"{error_lines}\n\n"
        "Write a corrected detailed_description body that fixes every "
        "problem above while still following every rule in the system "
        "prompt."
    )


def build_sound_user_prompt(
    beats: Sequence[Mapping[str, Any]], tone: Optional[str]
) -> str:
    events = [str(b["sound"]) for b in beats if b.get("sound")]
    event_lines = "\n".join(f"- {e}" for e in events) or "(no sound events listed)"
    tone_line = f"Tone/mood: {tone}\n" if tone else ""
    return (
        f"{tone_line}Sound events across this shot's beats:\n{event_lines}\n\n"
        "Write the sound JSON for this shot."
    )


# -- Document assembly -------------------------------------------------------


def assemble(
    alignment_line: Optional[str],
    subject_definitions: str,
    summary: str,
    retention_analysis: str,
    detailed_description: str,
    overall_soundscape: str,
    non_diegetic_music: str,
) -> str:
    """Join the six-section document (ref-guide §1) in guide order, with the
    alignment instruction (when present) first, each block separated by a
    single blank line."""
    sections = (
        ("subject_definitions", subject_definitions),
        ("summary", summary),
        ("retention_analysis", retention_analysis),
        ("detailed_description", detailed_description),
        ("overall_soundscape", overall_soundscape),
        ("non_diegetic_music", non_diegetic_music),
    )
    blocks: List[str] = []
    if alignment_line is not None:
        blocks.append(alignment_line)
    blocks.extend(f"{header}:\n{body}" for header, body in sections)
    return "\n\n".join(blocks)


# -- Lint --------------------------------------------------------------

_SECTION_ORDER: Tuple[str, ...] = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)

_HEADER_LINE_RE = re.compile(r"^(\w+):$", re.MULTILINE)
_LABEL_RE = re.compile(r"<(Subject|Picture)\s+(\d+)>")
_SHOT_MARK_RE = re.compile(r"\[Shot (\d+)\](?:\s+At\s+(\d{2}:\d{2}\.\d{3}))?")
_DIALOG_SPAN_RE = re.compile(r"<d>\[([^\]]+)\]\s*(.*?)\s*</d>")
_SPEAKER_PAREN_RE = re.compile(r"\(([A-Za-z0-9, ]+)\)")
_RETENTION_LINE_RE = re.compile(r":\s*([A-Za-z_]+)\s*-")

_RETENTION_MARKERS: FrozenSet[str] = frozenset(
    {"fully_preserved", "partially_preserved", "attribute_transfer", "weak_reference"}
)
# ref-guide §4.2: <Audio N> retention lines use their own marker vocabulary,
# distinct from the §4.1 set above -- a single merged set would wrongly
# allow e.g. "fully_copy" on a <Subject N> line. Kept separate and applied
# per-line (see _lint_retention_and_sound) based on whether the line starts
# with "<Audio".
_AUDIO_RETENTION_MARKERS: FrozenSet[str] = frozenset(
    {"fully_copy", "partially_copy", "reference", "weak_reference"}
)

# Opposite-direction camera phrase pairs (canonical phrasing from
# ``_CAMERA_PHRASES``). Two members of the same pair inside one shot's text
# describe contradictory motion and are conservatively flagged.
_OPPOSITE_CAMERA_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("zooms in", "zooms out"),
    ("pushes in", "pulls out"),
    ("pans left", "pans right"),
    ("trucks left", "trucks right"),
    ("tilts up", "tilts down"),
    ("pedestals up", "pedestals down"),
    ("rolls clockwise", "rolls counterclockwise"),
)

_TIMESTAMP_TOLERANCE_S = 0.5
_FLOAT_EPS = 1e-6
_WORD_COUNT_FLOOR = 150
_WORD_COUNT_MIN = 350
_WORD_COUNT_MAX = 500


def build_expectations(
    refplan: RefPlan,
    speakers: SpeakerPlan,
    timeline: Timeline,
    mode: str,
    beats: Optional[Sequence[Mapping[str, Any]]] = None,
) -> LintExpectations:
    """Derive the set of labels/dialog/timing a compiled H3 document must
    honor, given the same plans used to render it.

    ``beats`` (the panel's flat beat list, indexed the same way
    ``timeline``'s ``TimelineShot.beat_indices`` are) is optional and
    backward compatible: when omitted, ``shot_camera_phrases`` stays empty
    and the camera_vocab lint check behaves exactly as before (Task 3).
    When provided, it populates ``shot_camera_phrases`` so camera_vocab can
    also cross-check a shot's rendered text against what its beats'
    ``camera_motion`` fields actually called for.
    """
    subject_labels = set(refplan.subject_labels.values())
    subject_labels.add(refplan.environment_label)

    picture_labels = {label for _, label in refplan.picture_labels}
    if mode in _KEYFRAME_MODES:
        kf = refplan.keyframe_picture_label
        picture_labels.add(kf)
        if mode == "fl2va":
            picture_labels.add(_increment_picture_label(kf))

    shot_camera_phrases: Tuple[Tuple[str, ...], ...] = ()
    if beats is not None:
        per_shot: List[Tuple[str, ...]] = []
        for shot in timeline.shots:
            phrases: List[str] = []
            for bi in shot.beat_indices:
                beat = beats[bi] if bi < len(beats) else {}
                motion = beat.get("camera_motion")
                phrase = _CAMERA_PHRASES.get(motion) if motion else None
                if phrase:
                    phrases.append(phrase)
            per_shot.append(tuple(phrases))
        shot_camera_phrases = tuple(per_shot)

    return LintExpectations(
        subject_labels=frozenset(subject_labels),
        picture_labels=frozenset(picture_labels),
        dialog_lines=tuple(speakers.lines),
        timeline=timeline,
        duration_s=timeline.duration_s,
        shot_camera_phrases=shot_camera_phrases,
    )


def _extract_sections(text: str) -> Dict[str, str]:
    """Split an assembled H3 document into its six named sections, keyed by
    header name, using the ``^header:$`` line convention from ``assemble``.
    Sections that never appear are simply absent from the result."""
    matches = [
        m for m in _HEADER_LINE_RE.finditer(text) if m.group(1) in _SECTION_ORDER
    ]
    sections: Dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.end() + 1
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[m.group(1)] = text[start:end].strip("\n")
    return sections


def _lint_missing_sections(text: str) -> List[LintError]:
    errors: List[LintError] = []
    found = [
        m.group(1)
        for m in _HEADER_LINE_RE.finditer(text)
        if m.group(1) in _SECTION_ORDER
    ]
    seen: List[str] = []
    for name in found:
        if name not in seen:
            seen.append(name)

    missing = [h for h in _SECTION_ORDER if h not in seen]
    for h in missing:
        errors.append(
            LintError("missing_section", "error", f"missing required section '{h}:'")
        )

    if not missing and seen != list(_SECTION_ORDER):
        errors.append(
            LintError(
                "missing_section",
                "error",
                f"sections out of order: expected {list(_SECTION_ORDER)}, found {seen}",
            )
        )
    return errors


def _lint_unknown_labels(text: str, expect: LintExpectations) -> List[LintError]:
    errors: List[LintError] = []
    for m in _LABEL_RE.finditer(text):
        kind, number = m.group(1), m.group(2)
        label = f"{kind} {number}"
        valid = (
            label in expect.subject_labels
            if kind == "Subject"
            else label in expect.picture_labels
        )
        if not valid:
            errors.append(
                LintError(
                    "unknown_label", "error", f"unknown reference label <{label}>"
                )
            )
    return errors


def _parse_timecode(tc: str) -> float:
    minutes, _, secs = tc.partition(":")
    return int(minutes) * 60 + float(secs)


def _lint_timestamps(dd: str, expect: LintExpectations) -> List[LintError]:
    errors: List[LintError] = []
    starts_by_number = {s.number: s.start_s for s in expect.timeline.shots}
    prev_time: Optional[float] = None

    for m in _SHOT_MARK_RE.finditer(dd):
        number = int(m.group(1))
        tc = m.group(2)

        if number == 1:
            if tc is not None:
                errors.append(
                    LintError(
                        "timestamp_order",
                        "error",
                        "[Shot 1] must not carry an At MM:SS.mmm timestamp",
                    )
                )
            continue

        if tc is None:
            errors.append(
                LintError(
                    "timestamp_order",
                    "error",
                    f"[Shot {number}] is missing its At MM:SS.mmm timestamp",
                )
            )
            continue

        t = _parse_timecode(tc)

        if prev_time is not None and t <= prev_time + _FLOAT_EPS:
            errors.append(
                LintError(
                    "timestamp_order",
                    "error",
                    f"[Shot {number}] timestamp {tc} is not strictly increasing",
                )
            )
        if t > expect.duration_s + _TIMESTAMP_TOLERANCE_S + _FLOAT_EPS:
            errors.append(
                LintError(
                    "timestamp_order",
                    "error",
                    f"[Shot {number}] timestamp {tc} exceeds duration_s + 0.5s",
                )
            )

        prescribed = starts_by_number.get(number)
        if (
            prescribed is not None
            and abs(t - prescribed) > _TIMESTAMP_TOLERANCE_S + _FLOAT_EPS
        ):
            errors.append(
                LintError(
                    "timestamp_order",
                    "error",
                    f"[Shot {number}] timestamp {tc} is not within 0.5s of the "
                    f"prescribed start {prescribed:.3f}",
                )
            )

        prev_time = t

    return errors


def _lint_camera_vocab(
    dd: str, expect: Optional[LintExpectations] = None
) -> List[LintError]:
    errors: List[LintError] = []
    marks = list(_SHOT_MARK_RE.finditer(dd))

    expected_by_number: Dict[int, Tuple[str, ...]] = {}
    if expect is not None and expect.shot_camera_phrases:
        expected_by_number = {
            shot.number: phrases
            for shot, phrases in zip(expect.timeline.shots, expect.shot_camera_phrases)
        }
    all_phrases = tuple(_CAMERA_PHRASES.values())

    for i, m in enumerate(marks):
        seg_start = m.end()
        seg_end = marks[i + 1].start() if i + 1 < len(marks) else len(dd)
        segment = dd[seg_start:seg_end].lower()
        shot_no = m.group(1)
        for a, b in _OPPOSITE_CAMERA_PAIRS:
            if a in segment and b in segment:
                errors.append(
                    LintError(
                        "camera_vocab",
                        "warning",
                        f"[Shot {shot_no}] contains contradictory camera motion "
                        f"phrasing: {a!r} and {b!r}",
                    )
                )

        expected = expected_by_number.get(int(shot_no))
        if expected:
            found = [p for p in all_phrases if p in segment]
            unexpected = [p for p in found if p not in expected]
            expected_present = any(p in segment for p in expected)
            if unexpected and not expected_present:
                errors.append(
                    LintError(
                        "camera_vocab",
                        "error",
                        f"[Shot {shot_no}] uses camera motion phrasing "
                        f"{unexpected[0]!r}, which is not among the "
                        f"shot's beats' expected motion(s) {list(expected)!r}",
                    )
                )
    return errors


def _lint_word_count(dd: str) -> List[LintError]:
    count = len(dd.split())
    if count < _WORD_COUNT_FLOOR:
        # Advisory only: the description is rendered deterministically from
        # the beat script, so a terse script legitimately compiles short —
        # flagging it must not mark the panel "failed".
        return [
            LintError(
                "word_count",
                "warning",
                f"detailed_description is {count} words, below the "
                f"{_WORD_COUNT_FLOOR}-word floor",
            )
        ]
    if count < _WORD_COUNT_MIN or count > _WORD_COUNT_MAX:
        return [
            LintError(
                "word_count",
                "warning",
                f"detailed_description is {count} words, outside the "
                f"{_WORD_COUNT_MIN}-{_WORD_COUNT_MAX} target range",
            )
        ]
    return []


def _extract_dialog_spans(dd: str) -> List[Dict[str, Any]]:
    """One entry per ``<d>...</d>`` span, paired with the nearest preceding
    ``(Sx[,Sy])`` marker on the same line (ref-guide §5.4: identity/id sit
    outside the tag, adjacent to it on the same line)."""
    found: List[Dict[str, Any]] = []
    for line in dd.splitlines():
        for dm in _DIALOG_SPAN_RE.finditer(line):
            preceding = list(_SPEAKER_PAREN_RE.finditer(line[: dm.start()]))
            speakers_str = preceding[-1].group(1) if preceding else ""
            speaker_ids = {s.strip() for s in speakers_str.split(",") if s.strip()}
            found.append(
                {"speakers": speaker_ids, "language": dm.group(1), "text": dm.group(2)}
            )
    return found


def _lint_dialog(dd: str, expect: LintExpectations) -> List[LintError]:
    errors: List[LintError] = []
    found = _extract_dialog_spans(dd)
    consumed: Set[int] = set()

    for sl in expect.dialog_lines:
        expected_text = sl.text.strip()

        exact_idx: Optional[int] = None
        for i, f in enumerate(found):
            if (
                i not in consumed
                and sl.speaker_id in f["speakers"]
                and f["text"].strip() == expected_text
            ):
                exact_idx = i
                break
        if exact_idx is not None:
            consumed.add(exact_idx)
            continue

        same_speaker_idx: Optional[int] = None
        for i, f in enumerate(found):
            if i not in consumed and sl.speaker_id in f["speakers"]:
                same_speaker_idx = i
                break
        if same_speaker_idx is not None:
            consumed.add(same_speaker_idx)
            errors.append(
                LintError(
                    "dialog_mutated",
                    "error",
                    f"dialog for ({sl.speaker_id}) does not match the scaffold "
                    f"verbatim: expected {expected_text!r}",
                )
            )
            continue

        errors.append(
            LintError(
                "dialog_missing",
                "error",
                f"missing dialog line for ({sl.speaker_id}): {expected_text!r}",
            )
        )

    for i, f in enumerate(found):
        if i not in consumed:
            errors.append(
                LintError(
                    "dialog_invented",
                    "error",
                    f"unexpected <d> line not present in the scaffold: {f['text']!r}",
                )
            )

    return errors


def _lint_retention_and_sound(sections: Dict[str, str]) -> List[LintError]:
    errors: List[LintError] = []

    retention = sections.get("retention_analysis", "")
    for line in retention.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _RETENTION_LINE_RE.search(line)
        if m is None:
            continue
        marker = m.group(1)
        valid_markers = (
            _AUDIO_RETENTION_MARKERS
            if line.startswith("<Audio")
            else _RETENTION_MARKERS
        )
        if marker not in valid_markers:
            errors.append(
                LintError(
                    "retention_marker",
                    "error",
                    f"invalid retention marker {marker!r} in line: {line!r}",
                )
            )

    if not sections.get("overall_soundscape", "").strip():
        errors.append(
            LintError(
                "soundscape_missing", "error", "overall_soundscape section is empty"
            )
        )

    if not sections.get("non_diegetic_music", "").strip():
        errors.append(
            LintError("music_missing", "error", "non_diegetic_music section is empty")
        )

    return errors


def lint_h3_prompt(text: str, expect: LintExpectations) -> List[LintError]:
    """Expectation-driven lint over an assembled H3 document. Pure/regex
    based — no VLM calls. Returns every violation found; callers decide how
    to act on severities (Task 4 retries once on any ``"error"``)."""
    errors: List[LintError] = []
    sections = _extract_sections(text)

    errors.extend(_lint_missing_sections(text))
    errors.extend(_lint_unknown_labels(text, expect))

    dd = sections.get("detailed_description")
    if dd is not None:
        errors.extend(_lint_timestamps(dd, expect))
        errors.extend(_lint_camera_vocab(dd, expect))
        errors.extend(_lint_word_count(dd))
        errors.extend(_lint_dialog(dd, expect))

    errors.extend(_lint_retention_and_sound(sections))
    return errors
