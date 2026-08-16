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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from metascan.core.storyboard_story import CAMERA_MOTION_VALUES

# -- Dataclasses -------------------------------------------------------


@dataclass(frozen=True)
class RefPlan:
    subject_labels: Dict[int, str]  # subject_id -> "Subject 1"
    environment_label: str  # "Subject K+1" (scene environment)
    picture_labels: List[Tuple[str, str]]  # [(posix_path, "Picture 1"), ...]
    keyframe_picture_label: str  # next free "Picture N" (i2va/fl2va anchors)


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
    """Number subjects, the scene environment, and every reference picture.

    ref-guide §2.1 makes environments Subjects: the scene always becomes
    the final ``<Subject K+1>``. Pictures are numbered in upload order:
    each subject's ``reference_path`` then ``reference_path_2`` (subject
    order), then the scene's ``reference_path``.
    """
    ordered = sorted(subjects, key=lambda s: s.get("sort_order", 0))
    subject_labels: Dict[int, str] = {}
    picture_labels: List[Tuple[str, str]] = []
    next_picture = 1

    for subject in ordered:
        subject_labels[subject["id"]] = f"Subject {len(subject_labels) + 1}"
        for key in ("reference_path", "reference_path_2"):
            path = subject.get(key)
            if path:
                picture_labels.append((path, f"Picture {next_picture}"))
                next_picture += 1

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
                f"<{refplan.subject_labels[subject_id]}>"
                if subject_id is not None
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
    """Rescale beat durations to sum exactly to ``duration_s`` and group
    beats into internal shots, cutting at each beat with ``is_cut == 1``
    (beat 0 always starts shot 1)."""
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
    current_indices: List[int] = []
    current_start = 0.0
    cum = 0.0
    shot_number = 1

    for i, beat in enumerate(beats):
        if i > 0 and beat.get("is_cut"):
            shots.append(
                TimelineShot(
                    number=shot_number,
                    start_s=current_start,
                    beat_indices=current_indices,
                )
            )
            shot_number += 1
            current_indices = []
            current_start = cum
        current_indices.append(i)
        cum += rescaled[i]

    if current_indices:
        shots.append(
            TimelineShot(
                number=shot_number,
                start_s=current_start,
                beat_indices=current_indices,
            )
        )

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
    action = panel.get("action", "")
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
