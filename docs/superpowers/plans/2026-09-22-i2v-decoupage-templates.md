# i2v Découpage Templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Image-to-Video dialog pick a "cadence" — a JSON découpage template that fixes the clip's shot structure (timing, shot size, angle, camera, cuts, who is on screen, who speaks) — and have the VLM fill only prose, with code assembling and linting the MiniMax H3 prompt.

**Architecture:** A new pure module `metascan/core/i2v_templates.py` loads and validates `data/i2v_templates/*.json` against the storyboard's camera vocabularies, builds a GBNF grammar with the template baked in, validates the VLM's fill, and assembles the H3 document deterministically. `I2vRunner.generate_prompt` gains a `template_id` branch; `template_id = null` is the existing single-take path, byte-for-byte. The API exposes the library and threads `template_id` through prompt/lint/generate and the clip's `form_state`; the dialog gets a Cadence select and a read-only cadence strip.

**Tech Stack:** Python 3.11 / FastAPI / SQLite (existing), llama.cpp GBNF grammars via `VlmClient.generate_text`, Vue 3 + TypeScript (existing). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-22-i2v-decoupage-templates-design.md`

## Global Constraints

- Python 3.11 only; `make quality test` (flake8 fatal-only, `black` 25.11.0, `mypy` strict on `metascan/core/*`, pytest) must pass before every commit.
- Frontend: `cd frontend && npm run build` (vue-tsc + Vite) must pass; there is no frontend test runner.
- `template_id = null` must leave today's single-take output **byte-for-byte unchanged** (spec §5.1). A golden test pins it.
- Camera vocabularies are imported from `metascan/core/storyboard_parse.py` (`SHOT_SIZE_VALUES`, `ANGLE_VALUES`, `LENS_VALUES`) and `metascan/core/storyboard_story.py` (`CAMERA_MOTION_VALUES`, `CAMERA_AMPLITUDE_VALUES`, `CAMERA_SPEED_VALUES`) — never redeclared. `pov` is excluded from angle and motion.
- Camera phrases render through `metascan.core.h3_compiler._CAMERA_PHRASES` (the same table `i2v_fixes` rewrites toward), amplitude then speed: `The camera pushes in with small amplitude at slow speed.`
- All template-aware lint findings are **warnings**; the prompt box stays free text.
- Nothing is persisted before ingest (existing i2v rule); `template_id` rides `_job_meta["form"]`.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Editing anything under `backend/` reloads the running dev server; check `generation_jobs` has no `queued`/`running` rows first (`sqlite3 -readonly data/metascan.db "SELECT id,state FROM generation_jobs WHERE state IN ('queued','running');"`).

---

## File structure

| File | Responsibility |
|---|---|
| `metascan/core/i2v_templates.py` (new) | Template dataclasses, `parse_i2v_template`, loader/cache, `shots_of`, grammar, user prompt, fill validation, H3 assembly, template-aware lint. Pure: no I/O beyond reading the JSON files. |
| `data/i2v_templates/dialog_ots_15.json`, `melee_12.json`, `intimate_15.json` (new) | The shipped library. |
| `metascan/core/i2v_compiler.py` | `lint_i2v_prompt` gains an optional `template` parameter (delegates to `i2v_templates.lint_against_template`). Nothing else changes. |
| `metascan/core/i2v_form.py` | `template_id` joins `FORM_FIELDS`. |
| `metascan/core/i2v_runner.py` | `generate_prompt(..., template_id)` branch; `generate(..., template_id)` records it in the form snapshot. |
| `data/meta_prompt.yml` | New `I2V_TEMPLATE_SYSTEM` prompt. |
| `backend/services/i2v_service.py` | `list_templates(durations)` with availability stamping. |
| `backend/api/i2v.py` | `GET /templates`; `template_id` on `/prompt`, `/lint`, `/generate`. |
| `frontend/src/types/i2v.ts` | `I2vTemplate`, `I2vFormState.template_id`, `cadenceChips`. |
| `frontend/src/api/i2v.ts` | `listI2vTemplates`; `template_id` on the three POST bodies. |
| `frontend/src/stores/i2v.ts` | `templates` loaded in `open()`. |
| `frontend/src/components/dialogs/I2VDialog.vue` | Cadence select, duration lock, cadence strip, `template_id` in form/signature/load. |
| `docs/i2v-templates.md` (new), `docs/api-reference.md`, `docs/features.md`, `CLAUDE.md` | Documentation. |
| Tests: `tests/test_i2v_templates.py` (new), `tests/test_i2v_compiler.py`, `tests/test_i2v_form.py`, `tests/test_i2v_runner.py`, `tests/test_i2v_api.py` | |

---

### Task 1: Template model, parser and loader

**Files:**
- Create: `metascan/core/i2v_templates.py`
- Test: `tests/test_i2v_templates.py`

**Interfaces:**
- Produces:
  - `class I2vTemplateError(ValueError)`
  - `@dataclass(frozen=True) I2vRole(id: str, note: str = "")`
  - `@dataclass(frozen=True) I2vCamera(shot_size: str, angle: str, camera_motion: str, camera_amplitude: Optional[str] = None, camera_speed: Optional[str] = None, lens: Optional[str] = None)`
  - `@dataclass(frozen=True) I2vBeatSpec(start_s: float, end_s: float, transition: str, cast: Tuple[str, ...], speaker: Optional[str], camera: I2vCamera, note: str = "")`
  - `@dataclass(frozen=True) I2vTemplate(id: str, name: str, description: str, duration_s: float, look: str, soundscape_hint: str, roles: Tuple[I2vRole, ...], beats: Tuple[I2vBeatSpec, ...])`
  - `TRANSITION_VALUES = ("continuous", "cut", "j_cut")`
  - `I2V_ANGLE_VALUES`, `I2V_MOTION_VALUES` (the storyboard tuples minus `"pov"`)
  - `parse_i2v_template(data: Mapping[str, Any], *, where: str = "template") -> I2vTemplate`
  - `load_i2v_templates(directory: Optional[Path] = None) -> Dict[str, I2vTemplate]` (cached per directory)
  - `reload_i2v_templates() -> None`
  - `get_i2v_template(template_id: str, directory: Optional[Path] = None) -> I2vTemplate` (raises `I2vTemplateError` when unknown)
  - `shots_of(t: I2vTemplate) -> List[List[int]]` — beat indices grouped into H3 shots (a `cut`/`j_cut` beat starts a new group)
  - `summarize_i2v_template(t: I2vTemplate) -> Dict[str, Any]` — JSON-safe dict for the API (roles and beats as dicts, `camera` as a dict)
  - `I2V_TEMPLATES_DIR = <repo>/data/i2v_templates`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_i2v_templates.py
"""Tests for metascan/core/i2v_templates.py -- i2v découpage templates.

A template owns the shot structure of an i2v clip; the VLM fills only
prose. Loader tests here, grammar/assembly/lint tests are appended in
later tasks.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from metascan.core.i2v_templates import (
    I2vTemplateError,
    get_i2v_template,
    load_i2v_templates,
    parse_i2v_template,
    reload_i2v_templates,
    shots_of,
    summarize_i2v_template,
)

# The dialog example from the spec, verbatim in structure.
DIALOG: Dict[str, Any] = {
    "id": "dialog_ots_15",
    "name": "Dialog — over-the-shoulder",
    "description": "Two people talking.",
    "duration_s": 15,
    "look": "cinematic, photorealistic skin textures, fine grain",
    "soundscape_hint": "room ambience appropriate to the setting",
    "roles": [
        {"id": "A", "note": "the listener first; speaks second"},
        {"id": "B", "note": "speaks first"},
    ],
    "beats": [
        {
            "start_s": 0, "end_s": 4, "transition": "continuous",
            "cast": ["A", "B"], "speaker": "B",
            "camera": {"shot_size": "MS", "angle": "ots",
                       "camera_motion": "truck_right", "camera_speed": "slow"},
            "note": "over A's shoulder onto B, who is speaking",
        },
        {
            "start_s": 4, "end_s": 5, "transition": "cut",
            "cast": ["B"], "speaker": None,
            "camera": {"shot_size": "CU", "angle": "eye", "camera_motion": "static"},
            "note": "B's face; a reaction, not a line",
        },
        {
            "start_s": 5, "end_s": 10, "transition": "j_cut",
            "cast": ["A", "B"], "speaker": "A",
            "camera": {"shot_size": "MS", "angle": "eye",
                       "camera_motion": "pull_out", "camera_speed": "slow"},
            "note": "A's line starts before we see A; dolly back to a two-shot",
        },
        {
            "start_s": 10, "end_s": 15, "transition": "continuous",
            "cast": ["A", "B"], "speaker": None,
            "camera": {"shot_size": "MS", "angle": "eye",
                       "camera_motion": "arc", "camera_amplitude": "small"},
            "note": "B smiles and nods",
        },
    ],
}


def _with(**changes: Any) -> Dict[str, Any]:
    d = copy.deepcopy(DIALOG)
    d.update(changes)
    return d


def _beat_with(index: int, **changes: Any) -> Dict[str, Any]:
    d = copy.deepcopy(DIALOG)
    d["beats"][index].update(changes)
    return d


@pytest.fixture(autouse=True)
def _fresh_cache():
    reload_i2v_templates()
    yield
    reload_i2v_templates()


# ---- parse -------------------------------------------------------------


def test_parse_the_dialog_example():
    t = parse_i2v_template(DIALOG)
    assert t.id == "dialog_ots_15"
    assert t.duration_s == 15.0
    assert [r.id for r in t.roles] == ["A", "B"]
    assert len(t.beats) == 4
    assert t.beats[0].camera.angle == "ots"
    assert t.beats[0].camera.camera_speed == "slow"
    assert t.beats[0].camera.camera_amplitude is None
    assert t.beats[2].transition == "j_cut"
    assert t.beats[2].speaker == "A"
    assert t.beats[1].speaker is None
    assert t.beats[1].cast == ("B",)


def test_optional_fields_default_to_empty():
    d = _with()
    del d["look"]
    del d["soundscape_hint"]
    del d["beats"][0]["note"]
    t = parse_i2v_template(d)
    assert t.look == ""
    assert t.soundscape_hint == ""
    assert t.beats[0].note == ""


def test_roles_may_be_empty_when_no_beat_casts_anyone():
    d = _with(roles=[])
    for b in d["beats"]:
        b["cast"] = []
        b["speaker"] = None
    t = parse_i2v_template(d)
    assert t.roles == ()


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda d: d["beats"][0]["camera"].update(shot_size="OTS"), "shot_size"),
        (lambda d: d["beats"][0]["camera"].update(angle="EYE"), "angle"),
        (lambda d: d["beats"][0]["camera"].update(angle="pov"), "angle"),
        (lambda d: d["beats"][0]["camera"].update(camera_motion="STATIC"), "camera_motion"),
        (lambda d: d["beats"][0]["camera"].update(camera_motion="pov"), "camera_motion"),
        (lambda d: d["beats"][0]["camera"].update(camera_amplitude="huge"), "camera_amplitude"),
        (lambda d: d["beats"][0]["camera"].update(camera_speed="medium"), "camera_speed"),
        (lambda d: d["beats"][0]["camera"].update(lens="fisheye"), "lens"),
        (lambda d: d["beats"][0]["camera"].pop("shot_size"), "shot_size"),
        (lambda d: d["beats"][1].update(transition="dissolve"), "transition"),
        (lambda d: d["beats"][0].update(transition="cut"), "first beat"),
        (lambda d: d["beats"][0].update(cast=["A", "Z"]), "cast"),
        (lambda d: d["beats"][1].update(speaker="A"), "speaker"),  # A not in cast
        (lambda d: d["beats"][1].update(speaker="Z"), "speaker"),
        (lambda d: d["beats"][1].update(start_s=4.5), "start_s"),  # gap
        (lambda d: d["beats"][3].update(end_s=14), "end_s"),  # short of duration
        (lambda d: d["beats"][0].update(start_s=1), "start_s"),  # not 0
        (lambda d: d["beats"][1].update(end_s=4.2), "0.5"),  # beat too short
        (lambda d: d.update(beats=[]), "beats"),
        (lambda d: d.update(duration_s=0), "duration_s"),
        (lambda d: d.update(roles=[{"id": "A"}, {"id": "A"}]), "duplicate"),
        (lambda d: d.update(roles=[{"id": "bad id"}, {"id": "B"}]), "role id"),
        (lambda d: d.pop("name"), "name"),
    ],
)
def test_parse_rejects_bad_templates_naming_the_field(mutate, fragment):
    d = copy.deepcopy(DIALOG)
    mutate(d)
    with pytest.raises(I2vTemplateError) as excinfo:
        parse_i2v_template(d, where="dialog_ots_15.json")
    assert fragment in str(excinfo.value)
    assert "dialog_ots_15.json" in str(excinfo.value)


# ---- shots -------------------------------------------------------------


def test_cut_and_j_cut_open_new_shots_continuous_does_not():
    t = parse_i2v_template(DIALOG)
    assert shots_of(t) == [[0], [1], [2, 3]]


def test_a_template_with_only_continuous_beats_is_one_shot():
    d = copy.deepcopy(DIALOG)
    for b in d["beats"]:
        b["transition"] = "continuous"
    assert shots_of(parse_i2v_template(d)) == [[0, 1, 2, 3]]


# ---- load ---------------------------------------------------------------


def test_load_reads_every_json_and_requires_id_to_match_the_stem(tmp_path):
    (tmp_path / "dialog_ots_15.json").write_text(json.dumps(DIALOG))
    other = _with(id="other_15", name="Other")
    (tmp_path / "other_15.json").write_text(json.dumps(other))

    loaded = load_i2v_templates(tmp_path)

    assert sorted(loaded) == ["dialog_ots_15", "other_15"]
    assert loaded["other_15"].name == "Other"


def test_load_rejects_an_id_that_does_not_match_the_filename(tmp_path):
    (tmp_path / "renamed.json").write_text(json.dumps(DIALOG))
    with pytest.raises(I2vTemplateError) as excinfo:
        load_i2v_templates(tmp_path)
    assert "renamed.json" in str(excinfo.value)
    assert "dialog_ots_15" in str(excinfo.value)


def test_load_names_the_file_on_invalid_json(tmp_path):
    (tmp_path / "broken.json").write_text("{not json")
    with pytest.raises(I2vTemplateError) as excinfo:
        load_i2v_templates(tmp_path)
    assert "broken.json" in str(excinfo.value)


def test_load_is_cached_until_reload(tmp_path):
    (tmp_path / "dialog_ots_15.json").write_text(json.dumps(DIALOG))
    first = load_i2v_templates(tmp_path)
    (tmp_path / "other_15.json").write_text(json.dumps(_with(id="other_15")))
    assert load_i2v_templates(tmp_path) is first
    reload_i2v_templates()
    assert "other_15" in load_i2v_templates(tmp_path)


def test_get_unknown_template_lists_the_known_ones(tmp_path):
    (tmp_path / "dialog_ots_15.json").write_text(json.dumps(DIALOG))
    with pytest.raises(I2vTemplateError) as excinfo:
        get_i2v_template("nope", tmp_path)
    assert "dialog_ots_15" in str(excinfo.value)


def test_missing_directory_loads_as_empty(tmp_path):
    assert load_i2v_templates(tmp_path / "absent") == {}


# ---- summarize ---------------------------------------------------------


def test_summarize_is_json_safe_and_complete():
    t = parse_i2v_template(DIALOG)
    s = summarize_i2v_template(t)
    json.dumps(s)  # must not raise
    assert s["id"] == "dialog_ots_15"
    assert s["duration_s"] == 15.0
    assert s["roles"] == [
        {"id": "A", "note": "the listener first; speaks second"},
        {"id": "B", "note": "speaks first"},
    ]
    assert s["beats"][2]["transition"] == "j_cut"
    assert s["beats"][2]["camera"] == {
        "shot_size": "MS", "angle": "eye", "camera_motion": "pull_out",
        "camera_amplitude": None, "camera_speed": "slow", "lens": None,
    }
    assert s["beats"][0]["cast"] == ["A", "B"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_i2v_templates.py -q`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'metascan.core.i2v_templates'`

- [ ] **Step 3: Write the module**

```python
# metascan/core/i2v_templates.py
"""i2v découpage templates: the shot structure of an Image-to-Video clip.

A template fixes timing, shot size, angle, camera motion, how each shot
begins (``transition``), who is on screen and who speaks; the VLM fills
only prose. Shots are DERIVED from beats: a ``cut`` or ``j_cut`` beat
opens a new H3 ``[Shot n]``, a ``continuous`` beat stays inside the
current shot with an intra-shot timestamp -- the base guide's "prefer
camera motion when only the distance changes" rule, as data.

Vocabularies are the storyboard's, imported, never redeclared. ``pov`` is
excluded: i2v has no POV mode.

Pure module: reads the JSON files and nothing else. See
docs/superpowers/specs/2026-09-22-i2v-decoupage-templates-design.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from metascan.core.storyboard_parse import (
    ANGLE_VALUES,
    LENS_VALUES,
    SHOT_SIZE_VALUES,
)
from metascan.core.storyboard_story import (
    CAMERA_AMPLITUDE_VALUES,
    CAMERA_MOTION_VALUES,
    CAMERA_SPEED_VALUES,
)

I2V_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "data" / "i2v_templates"

TRANSITION_VALUES: Tuple[str, ...] = ("continuous", "cut", "j_cut")
I2V_ANGLE_VALUES: Tuple[str, ...] = tuple(v for v in ANGLE_VALUES if v != "pov")
I2V_MOTION_VALUES: Tuple[str, ...] = tuple(
    v for v in CAMERA_MOTION_VALUES if v != "pov"
)
MIN_BEAT_S = 0.5
_ROLE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class I2vTemplateError(ValueError):
    """A template file is unusable. The message names the file and field."""


@dataclass(frozen=True)
class I2vRole:
    id: str
    note: str = ""


@dataclass(frozen=True)
class I2vCamera:
    shot_size: str
    angle: str
    camera_motion: str
    camera_amplitude: Optional[str] = None
    camera_speed: Optional[str] = None
    lens: Optional[str] = None


@dataclass(frozen=True)
class I2vBeatSpec:
    start_s: float
    end_s: float
    transition: str
    cast: Tuple[str, ...]
    speaker: Optional[str]
    camera: I2vCamera
    note: str = ""


@dataclass(frozen=True)
class I2vTemplate:
    id: str
    name: str
    description: str
    duration_s: float
    look: str
    soundscape_hint: str
    roles: Tuple[I2vRole, ...]
    beats: Tuple[I2vBeatSpec, ...]


# ---- parse ----------------------------------------------------------


def _enum(
    where: str, value: Any, values: Sequence[str], *, nullable: bool = False
) -> Any:
    if value is None and nullable:
        return None
    if value not in values:
        raise I2vTemplateError(f"{where}: {value!r} is not one of {', '.join(values)}")
    return value


def _text(where: str, value: Any, *, required: bool) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise I2vTemplateError(f"{where}: must be a non-empty string")
    return value


def _num(where: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise I2vTemplateError(f"{where}: {value!r} is not a number")
    return float(value)


def _parse_camera(where: str, data: Any) -> I2vCamera:
    if not isinstance(data, Mapping):
        raise I2vTemplateError(f"{where}: camera must be an object")
    return I2vCamera(
        shot_size=_enum(f"{where}.shot_size", data.get("shot_size"), SHOT_SIZE_VALUES),
        angle=_enum(f"{where}.angle", data.get("angle"), I2V_ANGLE_VALUES),
        camera_motion=_enum(
            f"{where}.camera_motion", data.get("camera_motion"), I2V_MOTION_VALUES
        ),
        camera_amplitude=_enum(
            f"{where}.camera_amplitude",
            data.get("camera_amplitude"),
            CAMERA_AMPLITUDE_VALUES,
            nullable=True,
        ),
        camera_speed=_enum(
            f"{where}.camera_speed",
            data.get("camera_speed"),
            CAMERA_SPEED_VALUES,
            nullable=True,
        ),
        lens=_enum(f"{where}.lens", data.get("lens"), LENS_VALUES, nullable=True),
    )


def _parse_beat(
    where: str, data: Any, role_ids: Sequence[str], index: int
) -> I2vBeatSpec:
    if not isinstance(data, Mapping):
        raise I2vTemplateError(f"{where}: must be an object")
    transition = _enum(f"{where}.transition", data.get("transition"), TRANSITION_VALUES)
    if index == 0 and transition != "continuous":
        raise I2vTemplateError(
            f"{where}.transition: the first beat must be 'continuous' -- "
            "it is anchored to the source picture, there is nothing to cut from"
        )
    raw_cast = data.get("cast", [])
    if not isinstance(raw_cast, list) or any(
        not isinstance(r, str) or r not in role_ids for r in raw_cast
    ):
        raise I2vTemplateError(
            f"{where}.cast: every entry must be a declared role id "
            f"({', '.join(role_ids) or 'none declared'})"
        )
    speaker = data.get("speaker")
    if speaker is not None and speaker not in raw_cast:
        raise I2vTemplateError(
            f"{where}.speaker: {speaker!r} must be one of the beat's cast"
        )
    start = _num(f"{where}.start_s", data.get("start_s"))
    end = _num(f"{where}.end_s", data.get("end_s"))
    if end - start < MIN_BEAT_S:
        raise I2vTemplateError(
            f"{where}.end_s: a beat must be at least {MIN_BEAT_S} s long"
        )
    return I2vBeatSpec(
        start_s=start,
        end_s=end,
        transition=transition,
        cast=tuple(raw_cast),
        speaker=speaker,
        camera=_parse_camera(f"{where}.camera", data.get("camera")),
        note=_text(f"{where}.note", data.get("note"), required=False),
    )


def parse_i2v_template(
    data: Mapping[str, Any], *, where: str = "template"
) -> I2vTemplate:
    """Validate one template document. Every failure names ``where`` (the
    file) and the offending field, so a bad file never loads silently."""
    if not isinstance(data, Mapping):
        raise I2vTemplateError(f"{where}: must be a JSON object")
    duration = _num(f"{where}: duration_s", data.get("duration_s"))
    if duration <= 0:
        raise I2vTemplateError(f"{where}: duration_s must be > 0")

    roles: List[I2vRole] = []
    raw_roles = data.get("roles", [])
    if not isinstance(raw_roles, list):
        raise I2vTemplateError(f"{where}: roles must be a list")
    for i, r in enumerate(raw_roles):
        if not isinstance(r, Mapping) or not isinstance(r.get("id"), str):
            raise I2vTemplateError(f"{where}: roles[{i}] needs a string id")
        rid = r["id"]
        if not _ROLE_ID.match(rid):
            raise I2vTemplateError(
                f"{where}: roles[{i}] role id {rid!r} must be a bare identifier"
            )
        if any(existing.id == rid for existing in roles):
            raise I2vTemplateError(f"{where}: duplicate role id {rid!r}")
        roles.append(I2vRole(id=rid, note=_text(f"{where}: roles[{i}].note", r.get("note"), required=False)))
    role_ids = [r.id for r in roles]

    raw_beats = data.get("beats")
    if not isinstance(raw_beats, list) or not raw_beats:
        raise I2vTemplateError(f"{where}: beats must be a non-empty list")
    beats: List[I2vBeatSpec] = []
    for i, b in enumerate(raw_beats):
        beats.append(_parse_beat(f"{where}: beats[{i}]", b, role_ids, i))
    if beats[0].start_s != 0:
        raise I2vTemplateError(f"{where}: beats[0].start_s must be 0")
    for i in range(1, len(beats)):
        if abs(beats[i].start_s - beats[i - 1].end_s) > 1e-6:
            raise I2vTemplateError(
                f"{where}: beats[{i}].start_s must equal beats[{i - 1}].end_s "
                f"({beats[i - 1].end_s}); beats must tile the clip with no gaps"
            )
    if abs(beats[-1].end_s - duration) > 1e-6:
        raise I2vTemplateError(
            f"{where}: beats[{len(beats) - 1}].end_s must equal duration_s "
            f"({duration})"
        )

    return I2vTemplate(
        id=_text(f"{where}: id", data.get("id"), required=True),
        name=_text(f"{where}: name", data.get("name"), required=True),
        description=_text(f"{where}: description", data.get("description"), required=False),
        duration_s=duration,
        look=_text(f"{where}: look", data.get("look"), required=False),
        soundscape_hint=_text(
            f"{where}: soundscape_hint", data.get("soundscape_hint"), required=False
        ),
        roles=tuple(roles),
        beats=tuple(beats),
    )


# ---- load -----------------------------------------------------------

_cache: Dict[str, Dict[str, I2vTemplate]] = {}


def load_i2v_templates(directory: Optional[Path] = None) -> Dict[str, I2vTemplate]:
    """Load + validate every ``*.json`` under ``directory`` (default
    ``data/i2v_templates/``), cached per directory until
    ``reload_i2v_templates``. A malformed file raises, naming it."""
    d = Path(directory) if directory is not None else I2V_TEMPLATES_DIR
    key = str(d.resolve())
    cached = _cache.get(key)
    if cached is not None:
        return cached
    out: Dict[str, I2vTemplate] = {}
    if d.is_dir():
        for path in sorted(d.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise I2vTemplateError(f"{path.name}: invalid JSON: {exc}") from exc
            t = parse_i2v_template(data, where=path.name)
            if t.id != path.stem:
                raise I2vTemplateError(
                    f"{path.name}: id {t.id!r} must equal the file stem {path.stem!r}"
                )
            out[t.id] = t
    _cache[key] = out
    return out


def reload_i2v_templates() -> None:
    _cache.clear()


def get_i2v_template(template_id: str, directory: Optional[Path] = None) -> I2vTemplate:
    templates = load_i2v_templates(directory)
    if template_id not in templates:
        raise I2vTemplateError(
            f"unknown i2v template {template_id!r}; available: "
            f"{', '.join(sorted(templates)) or '(none)'}"
        )
    return templates[template_id]


# ---- structure ------------------------------------------------------


def shots_of(t: I2vTemplate) -> List[List[int]]:
    """Beat indices grouped into H3 shots. ``cut``/``j_cut`` open a shot."""
    shots: List[List[int]] = []
    for i, b in enumerate(t.beats):
        if i == 0 or b.transition != "continuous":
            shots.append([i])
        else:
            shots[-1].append(i)
    return shots


def summarize_i2v_template(t: I2vTemplate) -> Dict[str, Any]:
    """JSON-safe dict of the whole template, for the API and the dialog's
    cadence strip."""
    d = asdict(t)
    d["roles"] = [asdict(r) for r in t.roles]
    d["beats"] = [
        {**asdict(b), "cast": list(b.cast), "camera": asdict(b.camera)}
        for b in t.beats
    ]
    return d


__all__ = [
    "I2V_ANGLE_VALUES",
    "I2V_MOTION_VALUES",
    "I2V_TEMPLATES_DIR",
    "I2vBeatSpec",
    "I2vCamera",
    "I2vRole",
    "I2vTemplate",
    "I2vTemplateError",
    "MIN_BEAT_S",
    "TRANSITION_VALUES",
    "get_i2v_template",
    "load_i2v_templates",
    "parse_i2v_template",
    "reload_i2v_templates",
    "shots_of",
    "summarize_i2v_template",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_i2v_templates.py -q`
Expected: all pass. If `test_parse_rejects_bad_templates_naming_the_field[...-0.5]` fails, check the beat-length message includes the literal `0.5`.

- [ ] **Step 5: Format, type-check, commit**

```bash
venv/bin/black metascan/core/i2v_templates.py tests/test_i2v_templates.py
venv/bin/mypy metascan/core/i2v_templates.py
git add metascan/core/i2v_templates.py tests/test_i2v_templates.py
git commit -m "feat(i2v): découpage template model, parser and loader

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The shipped template library

**Files:**
- Create: `data/i2v_templates/dialog_ots_15.json`, `data/i2v_templates/melee_12.json`, `data/i2v_templates/intimate_15.json`
- Test: `tests/test_i2v_templates.py` (append)

**Interfaces:**
- Consumes: `load_i2v_templates`, `shots_of` from Task 1.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_i2v_templates.py`:

```python
# ---- shipped library ---------------------------------------------------


def test_the_shipped_library_loads_and_has_the_expected_cadences():
    from metascan.core.i2v_templates import I2V_TEMPLATES_DIR

    lib = load_i2v_templates(I2V_TEMPLATES_DIR)
    assert sorted(lib) == ["dialog_ots_15", "intimate_15", "melee_12"]
    assert shots_of(lib["dialog_ots_15"]) == [[0], [1], [2, 3]]
    assert shots_of(lib["melee_12"]) == [[0, 1], [2]]
    assert shots_of(lib["intimate_15"]) == [[0, 1, 2]]
    assert lib["dialog_ots_15"].duration_s == 15.0
    assert lib["melee_12"].duration_s == 12.0
    assert lib["intimate_15"].duration_s == 15.0
    assert [r.id for r in lib["melee_12"].roles] == ["A", "B"]
    assert [r.id for r in lib["intimate_15"].roles] == ["A"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python -m pytest tests/test_i2v_templates.py -q -k shipped_library`
Expected: FAIL — `assert [] == [...]` (the directory does not exist yet).

- [ ] **Step 3: Write the three files**

`data/i2v_templates/dialog_ots_15.json`:

```json
{
  "id": "dialog_ots_15",
  "name": "Dialog — over-the-shoulder",
  "description": "Two people talking: over-the-shoulder track onto the speaker, a reaction close-up, a J-cut back to a two-shot, then a slow orbit as they settle. Ends near the opening framing, which keeps identity stable across the cuts.",
  "duration_s": 15,
  "look": "cinematic, photorealistic skin textures, fine grain",
  "soundscape_hint": "room ambience appropriate to the setting",
  "roles": [
    {"id": "A", "note": "listens first, speaks second"},
    {"id": "B", "note": "speaks first"}
  ],
  "beats": [
    {
      "start_s": 0, "end_s": 4, "transition": "continuous",
      "cast": ["A", "B"], "speaker": "B",
      "camera": {"shot_size": "MS", "angle": "ots", "camera_motion": "truck_right", "camera_speed": "slow"},
      "note": "over A's shoulder onto B, who is speaking clearly"
    },
    {
      "start_s": 4, "end_s": 5, "transition": "cut",
      "cast": ["B"], "speaker": null,
      "camera": {"shot_size": "CU", "angle": "eye", "camera_motion": "static"},
      "note": "B's face; micro-expressions, a reaction, not a line"
    },
    {
      "start_s": 5, "end_s": 10, "transition": "j_cut",
      "cast": ["A", "B"], "speaker": "A",
      "camera": {"shot_size": "MS", "angle": "eye", "camera_motion": "pull_out", "camera_speed": "slow"},
      "note": "A's line starts before we see A; the camera dollies back into a waist-up two-shot"
    },
    {
      "start_s": 10, "end_s": 15, "transition": "continuous",
      "cast": ["A", "B"], "speaker": null,
      "camera": {"shot_size": "MS", "angle": "eye", "camera_motion": "arc", "camera_amplitude": "small"},
      "note": "a gentle orbit with background parallax as B smiles and nods"
    }
  ]
}
```

`data/i2v_templates/melee_12.json`:

```json
{
  "id": "melee_12",
  "name": "Melee — clash and counter",
  "description": "Two fighters: a low wide shot of the initial clash, a fast tracking push along the line of action as one strikes, then a cut to a handheld side medium for the block and counter. Does not return to the opening framing; identity rests on the description.",
  "duration_s": 12,
  "look": "high contrast, sharp reflections, fast kinetic energy",
  "soundscape_hint": "impacts, cloth and footwork; whatever weather or surface the setting implies",
  "roles": [
    {"id": "A", "note": "attacks first"},
    {"id": "B", "note": "blocks, then counters"}
  ],
  "beats": [
    {
      "start_s": 0, "end_s": 3, "transition": "continuous",
      "cast": ["A", "B"], "speaker": null,
      "camera": {"shot_size": "WS", "angle": "low", "camera_motion": "static"},
      "note": "full-body wide, slightly low, both fighters square up and the first clash lands"
    },
    {
      "start_s": 3, "end_s": 7, "transition": "continuous",
      "cast": ["A", "B"], "speaker": null,
      "camera": {"shot_size": "MS", "angle": "eye", "camera_motion": "tracking", "camera_speed": "fast"},
      "note": "sudden fast push-in tracking parallel to the line of action as A launches a strike"
    },
    {
      "start_s": 7, "end_s": 12, "transition": "cut",
      "cast": ["A", "B"], "speaker": null,
      "camera": {"shot_size": "MS", "angle": "eye", "camera_motion": "shake_slight"},
      "note": "tight side-angle medium: B's block and immediate counter-strike; natural handheld wobble"
    }
  ]
}
```

`data/i2v_templates/intimate_15.json`:

```json
{
  "id": "intimate_15",
  "name": "Intimate — detail to face",
  "description": "One person, one continuous shot: a macro detail on the hands, a slow tilt and push up to a medium close-up, then settling into a close-up on the eyes. No cuts, so identity is the most stable of the library.",
  "duration_s": 15,
  "look": "soft natural light, shallow depth of field with creamy bokeh, volumetric dust",
  "soundscape_hint": "quiet room tone and small close sounds; distant exterior ambience",
  "roles": [
    {"id": "A", "note": "the only person in the shot"}
  ],
  "beats": [
    {
      "start_s": 0, "end_s": 5, "transition": "continuous",
      "cast": ["A"], "speaker": null,
      "camera": {"shot_size": "ECU", "angle": "eye", "camera_motion": "tilt_up", "camera_speed": "slow", "lens": "macro"},
      "note": "macro detail on A's hands and whatever they hold; a slow mechanical tilt upward begins"
    },
    {
      "start_s": 5, "end_s": 10, "transition": "continuous",
      "cast": ["A"], "speaker": null,
      "camera": {"shot_size": "MCU", "angle": "eye", "camera_motion": "push_in", "camera_speed": "slow"},
      "note": "focus settles on A's face, chest-to-head framing, a micro push-in over the whole beat"
    },
    {
      "start_s": 10, "end_s": 15, "transition": "continuous",
      "cast": ["A"], "speaker": null,
      "camera": {"shot_size": "CU", "angle": "eye", "camera_motion": "static"},
      "note": "a tight close-up isolating the emotion in A's eyes; nothing else moves"
    }
  ]
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_i2v_templates.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add data/i2v_templates/ tests/test_i2v_templates.py
git commit -m "feat(i2v): ship dialog, melee and intimate découpage templates

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Grammar, user prompt and fill validation

**Files:**
- Modify: `metascan/core/i2v_templates.py`
- Test: `tests/test_i2v_templates.py` (append)

**Interfaces:**
- Consumes: `I2vTemplate`, `I2vBeatSpec`, `shots_of` (Task 1); `_COMMON` from `metascan.core.i2v_compiler` (the shared `string`/`char`/`ws` GBNF rules; a private cross-import with precedent — `i2v_fixes` imports `_CAMERA_PHRASES` from `h3_compiler`).
- Produces:
  - `i2v_template_grammar(t: I2vTemplate) -> str`
  - `i2v_template_max_tokens(t: I2vTemplate) -> int`
  - `build_i2v_template_user_prompt(t: I2vTemplate, idea: str) -> str`
  - `@dataclass I2vRoleFill(id: str, bound: bool, description: str, tag: str)`
  - `@dataclass I2vBeatFill(action: str, line: Optional[str])`
  - `@dataclass I2vTemplateFill(roles: Dict[str, I2vRoleFill], beats: List[I2vBeatFill], overall_soundscape: str, non_diegetic_music: str)`
  - `validate_i2v_template_fill(raw: str, t: I2vTemplate) -> I2vTemplateFill` (raises `metascan.core.i2v_compiler.I2vError`)
  - `describe_beat(t: I2vTemplate, index: int) -> str` — the one-line brief used in the user prompt and in lint messages

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_i2v_templates.py`:

```python
# ---- grammar / prompt / fill ------------------------------------------

from metascan.core.i2v_compiler import I2vError  # noqa: E402
from metascan.core.i2v_templates import (  # noqa: E402
    build_i2v_template_user_prompt,
    describe_beat,
    i2v_template_grammar,
    i2v_template_max_tokens,
    validate_i2v_template_fill,
)


def _fill(t, **over) -> str:
    """A well-formed VLM response for ``t``."""
    roles = [
        {"id": r.id, "bound": True, "description": f"desc of {r.id}", "tag": f"tag {r.id}"}
        for r in t.roles
    ]
    beats = []
    for b in t.beats:
        beat = {"action": f"action for {b.start_s}"}
        if b.speaker:
            beat["line"] = f"line by {b.speaker}"
        beats.append(beat)
    data = {
        "roles": roles,
        "beats": beats,
        "overall_soundscape": "room tone",
        "non_diegetic_music": "none",
    }
    data.update(over)
    return json.dumps(data)


def test_grammar_bakes_in_role_ids_and_beat_count():
    t = parse_i2v_template(DIALOG)
    g = i2v_template_grammar(t)
    assert 'role0 ::=' in g and 'role1 ::=' in g and 'role2 ::=' not in g
    assert '"\\"A\\""' in g and '"\\"B\\""' in g
    assert 'beat3 ::=' in g and 'beat4 ::=' not in g
    assert 'boolean ::= "true" | "false"' in g


def test_grammar_requires_a_line_exactly_where_a_speaker_is_set():
    t = parse_i2v_template(DIALOG)
    rules = dict(
        line.split(" ::= ", 1) for line in i2v_template_grammar(t).splitlines() if " ::= " in line
    )
    assert '"\\"line\\""' in rules["beat0"]  # B speaks
    assert '"\\"line\\""' not in rules["beat1"]
    assert '"\\"line\\""' in rules["beat2"]  # A speaks
    assert '"\\"line\\""' not in rules["beat3"]


def test_grammar_with_no_roles_is_still_valid():
    d = _with(roles=[])
    for b in d["beats"]:
        b["cast"], b["speaker"] = [], None
    g = i2v_template_grammar(parse_i2v_template(d))
    assert '"\\"roles\\"" ws ":" ws "[" ws  ws "]"' in g


def test_max_tokens_scales_with_beats_and_roles():
    t = parse_i2v_template(DIALOG)
    assert i2v_template_max_tokens(t) > 260 + 90 * 4


def test_describe_beat_reads_like_a_brief():
    t = parse_i2v_template(DIALOG)
    assert describe_beat(t, 0) == (
        "Beat 1 (0.0-4.0 s, medium shot, over the shoulder, camera trucks "
        "right at slow speed; on screen: A, B; B speaks): over A's shoulder "
        "onto B, who is speaking"
    )
    assert describe_beat(t, 1) == (
        "Beat 2 (4.0-5.0 s, CUT to a close-up, eye level, camera holds a static "
        "shot; on screen: B; no line): B's face; a reaction, not a line"
    )
    assert describe_beat(t, 2).startswith("Beat 3 (5.0-10.0 s, J-CUT to a medium shot")
    assert "camera arcs around the subject with small amplitude" in describe_beat(t, 3)


def test_user_prompt_lists_roles_beats_idea_and_soundscape_hint():
    t = parse_i2v_template(DIALOG)
    p = build_i2v_template_user_prompt(t, "two old friends argue about money")
    assert "two old friends argue about money" in p
    assert "15-second video" in p
    assert "Role A: the listener first; speaks second" in p
    assert "Role B: speaks first" in p
    assert describe_beat(t, 0) in p
    assert describe_beat(t, 3) in p
    assert "room ambience appropriate to the setting" in p
    assert "bound" in p  # explains the bound flag
    assert "Beat 1" in p and "exactly what the image depicts" in p


def test_user_prompt_with_empty_idea_says_so():
    t = parse_i2v_template(DIALOG)
    assert "(none -- infer" in build_i2v_template_user_prompt(t, "  ")


def test_validate_fill_round_trips_a_good_response():
    t = parse_i2v_template(DIALOG)
    f = validate_i2v_template_fill(_fill(t), t)
    assert f.roles["A"].bound is True
    assert f.roles["B"].tag == "tag B"
    assert [b.line for b in f.beats] == ["line by B", None, "line by A", None]
    assert f.overall_soundscape == "room tone"


def test_validate_fill_normalises_whitespace_and_defaults_sound():
    t = parse_i2v_template(DIALOG)
    raw = _fill(t, overall_soundscape="  ", non_diegetic_music="")
    f = validate_i2v_template_fill(raw, t)
    assert f.overall_soundscape == "Natural ambient sound consistent with the scene."
    assert f.non_diegetic_music == "No non-diegetic music."


@pytest.mark.parametrize(
    "over, fragment",
    [
        ({"beats": [{"action": "x"}]}, "4 beats"),
        ({"roles": []}, "role"),
    ],
)
def test_validate_fill_rejects_structural_mismatch(over, fragment):
    t = parse_i2v_template(DIALOG)
    with pytest.raises(I2vError) as excinfo:
        validate_i2v_template_fill(_fill(t, **over), t)
    assert fragment in str(excinfo.value)


def test_validate_fill_rejects_a_missing_or_blank_line_where_a_speaker_is_set():
    t = parse_i2v_template(DIALOG)
    data = json.loads(_fill(t))
    data["beats"][0]["line"] = "   "
    with pytest.raises(I2vError) as excinfo:
        validate_i2v_template_fill(json.dumps(data), t)
    assert "beat 1" in str(excinfo.value)


def test_validate_fill_rejects_a_blank_tag_or_description():
    t = parse_i2v_template(DIALOG)
    data = json.loads(_fill(t))
    data["roles"][1]["tag"] = ""
    with pytest.raises(I2vError) as excinfo:
        validate_i2v_template_fill(json.dumps(data), t)
    assert "B" in str(excinfo.value)


def test_validate_fill_rejects_non_json():
    t = parse_i2v_template(DIALOG)
    with pytest.raises(I2vError):
        validate_i2v_template_fill("{not json", t)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_i2v_templates.py -q`
Expected: FAIL at the import of `build_i2v_template_user_prompt` — `ImportError`.

- [ ] **Step 3: Implement**

Append to `metascan/core/i2v_templates.py` (before `__all__`), and add the two imports at the top of the module:

```python
from metascan.core.h3_compiler import _CAMERA_PHRASES
from metascan.core.i2v_compiler import _COMMON, I2vError
```

```python
# ---- rendering vocabulary --------------------------------------------

SHOT_SIZE_PHRASES: Dict[str, str] = {
    "ECU": "an extreme close-up",
    "CU": "a close-up",
    "MCU": "a medium close-up",
    "MS": "a medium shot",
    "MLS": "a medium long shot",
    "WS": "a wide shot",
    "EWS": "an extreme wide shot",
}
ANGLE_PHRASES: Dict[str, str] = {
    "eye": "",
    "low": "from a low angle",
    "high": "from a high angle",
    "overhead": "from directly overhead",
    "dutch": "with a dutch tilt",
    "ots": "over the shoulder",
}
LENS_PHRASES: Dict[str, str] = {
    "wide": "on a wide lens",
    "normal": "",
    "tele": "on a telephoto lens",
    "macro": "on a macro lens",
}


def camera_sentence(cam: I2vCamera, *, capital: bool = True) -> str:
    """``The camera pushes in with small amplitude at slow speed.`` --
    the base guide's motion + amplitude + speed order, the same shape
    ``i2v_fixes`` rewrites toward so Apply fixes never fights it."""
    phrase = _CAMERA_PHRASES[cam.camera_motion]
    if cam.camera_motion == "static":
        text = "the camera holds a static shot"
    else:
        text = f"the camera {phrase}"
        if cam.camera_amplitude:
            text += f" with {cam.camera_amplitude} amplitude"
        if cam.camera_speed:
            text += f" at {cam.camera_speed} speed"
    return (text[0].upper() + text[1:] if capital else text) + "."


def framing_phrase(cam: I2vCamera) -> str:
    """``a medium shot from a low angle on a macro lens`` (no cast)."""
    bits = [SHOT_SIZE_PHRASES[cam.shot_size]]
    if cam.angle != "ots" and ANGLE_PHRASES[cam.angle]:
        bits.append(ANGLE_PHRASES[cam.angle])
    if cam.lens and LENS_PHRASES[cam.lens]:
        bits.append(LENS_PHRASES[cam.lens])
    return " ".join(bits)


def _timestamp(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:06.3f}"


# ---- VLM fill: grammar, prompt, validation ---------------------------


def describe_beat(t: I2vTemplate, index: int) -> str:
    """One-line brief for beat ``index`` -- the VLM's instructions for
    it, and the wording lint messages reuse."""
    b = t.beats[index]
    cam = b.camera
    how = {"continuous": "", "cut": "CUT to ", "j_cut": "J-CUT to "}[b.transition]
    size = SHOT_SIZE_PHRASES[cam.shot_size]
    # "CUT to a close-up" keeps the article; a continuing beat reads
    # "medium shot" bare.
    framing = f"{how}{size}" if how else size.split(" ", 1)[1]
    angle = "over the shoulder" if cam.angle == "ots" else (ANGLE_PHRASES[cam.angle] or "eye level")
    motion = camera_sentence(cam, capital=False).rstrip(".").removeprefix("the ")
    cast = ", ".join(b.cast) if b.cast else "nobody (a detail shot)"
    who = f"{b.speaker} speaks" if b.speaker else "no line"
    return (
        f"Beat {index + 1} ({b.start_s:.1f}-{b.end_s:.1f} s, {framing}, {angle}, "
        f"{motion}; on screen: {cast}; {who}): {b.note or 'no further direction'}"
    )


def i2v_template_grammar(t: I2vTemplate) -> str:
    """GBNF with the template baked in: one rule per role (id fixed as a
    literal) and per beat; a beat with a ``speaker`` gets a required
    ``line`` string, one without has no ``line`` key at all -- so a
    missing or stray line is structurally impossible."""
    role_rules = [
        f'role{i} ::= "{{" ws "\\"id\\"" ws ":" ws "\\"{r.id}\\"" ws "," ws '
        f'"\\"bound\\"" ws ":" ws boolean ws "," ws '
        f'"\\"description\\"" ws ":" ws string ws "," ws '
        f'"\\"tag\\"" ws ":" ws string ws "}}"'
        for i, r in enumerate(t.roles)
    ]
    beat_rules = []
    for i, b in enumerate(t.beats):
        line = ' ws "," ws "\\"line\\"" ws ":" ws string' if b.speaker else ""
        beat_rules.append(
            f'beat{i} ::= "{{" ws "\\"action\\"" ws ":" ws string{line} ws "}}"'
        )
    roles_seq = ' ws "," ws '.join(f"role{i}" for i in range(len(t.roles)))
    beats_seq = ' ws "," ws '.join(f"beat{i}" for i in range(len(t.beats)))
    root = (
        'root ::= "{" ws "\\"roles\\"" ws ":" ws "[" ws ' + roles_seq + ' ws "]" ws "," ws '
        '"\\"beats\\"" ws ":" ws "[" ws ' + beats_seq + ' ws "]" ws "," ws '
        '"\\"overall_soundscape\\"" ws ":" ws string ws "," ws '
        '"\\"non_diegetic_music\\"" ws ":" ws string ws "}"\n'
    )
    return (
        root
        + "\n".join(role_rules + beat_rules)
        + '\nboolean ::= "true" | "false"\n'
        + _COMMON
    )


def i2v_template_max_tokens(t: I2vTemplate) -> int:
    """Roles cost a description each; beats an action (+ a line)."""
    return 260 + 90 * len(t.beats) + 70 * len(t.roles)


def build_i2v_template_user_prompt(t: I2vTemplate, idea: str) -> str:
    idea_line = idea.strip() or "(none -- infer a natural continuation)"
    roles = "\n".join(f"Role {r.id}: {r.note or 'no note'}" for r in t.roles) or (
        "This template casts no one; the beats are detail shots."
    )
    beats = "\n".join(describe_beat(t, i) for i in range(len(t.beats)))
    hint = (
        f"Soundscape guidance: {t.soundscape_hint}\n" if t.soundscape_hint else ""
    )
    return (
        f"The attached image is the exact first frame of a "
        f"{t.duration_s:.0f}-second video with a fixed shot plan.\n"
        f"User's idea for the video: {idea_line}\n\n"
        f"Roles:\n{roles}\n\n"
        "For each role, set 'bound' to true if that person is visible in the "
        "image and describe THAT person; set it false if nobody in the image "
        "fits and invent a fitting person. 'description' is one sentence of "
        "stable identity (age, build, hair, clothing) used on first mention; "
        "'tag' is a 3-6 word handle used afterwards (\"the woman in the red "
        "coat\").\n\n"
        f"Shot plan:\n{beats}\n\n"
        "Write each beat's 'action' as 1-2 present-tense sentences of "
        "concrete visible action matching its brief and framing; do not "
        "restate the camera move. Beat 1 must begin from exactly what the "
        "image depicts. Where a beat lists a speaker, write that person's "
        "spoken words in 'line' (words only, no quotes, no attribution). "
        f"{hint}"
        "Then summarize ambient and action sound in 'overall_soundscape' "
        "and the score in 'non_diegetic_music'."
    )


@dataclass
class I2vRoleFill:
    id: str
    bound: bool
    description: str
    tag: str


@dataclass
class I2vBeatFill:
    action: str
    line: Optional[str]


@dataclass
class I2vTemplateFill:
    roles: Dict[str, I2vRoleFill]
    beats: List[I2vBeatFill]
    overall_soundscape: str
    non_diegetic_music: str


_DEFAULT_SOUNDSCAPE = "Natural ambient sound consistent with the scene."
_DEFAULT_MUSIC = "No non-diegetic music."


def _squash(value: Any) -> str:
    return " ".join(str(value or "").split())


def validate_i2v_template_fill(raw: str, t: I2vTemplate) -> I2vTemplateFill:
    """Strict parse of the VLM's fill against ``t``. The grammar is the
    real enforcement; this catches a model that ignored it."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise I2vError(f"VLM response is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise I2vError("VLM response is not a JSON object")

    roles: Dict[str, I2vRoleFill] = {}
    raw_roles = data.get("roles")
    for r in raw_roles if isinstance(raw_roles, list) else []:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or "")
        desc, tag = _squash(r.get("description")), _squash(r.get("tag"))
        if not desc or not tag:
            raise I2vError(f"VLM gave role {rid or '?'} no description or tag")
        roles[rid] = I2vRoleFill(id=rid, bound=bool(r.get("bound")), description=desc, tag=tag)
    expected_roles = [r.id for r in t.roles]
    if sorted(roles) != sorted(expected_roles):
        raise I2vError(
            f"VLM returned roles {sorted(roles)}; template needs {expected_roles}"
        )

    raw_beats = data.get("beats")
    beats_in = raw_beats if isinstance(raw_beats, list) else []
    if len(beats_in) != len(t.beats):
        raise I2vError(
            f"VLM returned {len(beats_in)} beats; template has {len(t.beats)} beats"
        )
    beats: List[I2vBeatFill] = []
    for i, (spec, b) in enumerate(zip(t.beats, beats_in)):
        action = _squash(b.get("action")) if isinstance(b, dict) else ""
        if not action:
            raise I2vError(f"VLM gave beat {i + 1} no action")
        line = _squash(b.get("line")) if isinstance(b, dict) else ""
        if spec.speaker and not line:
            raise I2vError(f"VLM gave beat {i + 1} no line, but {spec.speaker} speaks")
        beats.append(I2vBeatFill(action=action, line=line if spec.speaker else None))

    return I2vTemplateFill(
        roles=roles,
        beats=beats,
        overall_soundscape=_squash(data.get("overall_soundscape")) or _DEFAULT_SOUNDSCAPE,
        non_diegetic_music=_squash(data.get("non_diegetic_music")) or _DEFAULT_MUSIC,
    )
```

Add to `__all__`: `"I2vBeatFill"`, `"I2vRoleFill"`, `"I2vTemplateFill"`, `"build_i2v_template_user_prompt"`, `"camera_sentence"`, `"describe_beat"`, `"framing_phrase"`, `"i2v_template_grammar"`, `"i2v_template_max_tokens"`, `"validate_i2v_template_fill"`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_i2v_templates.py -q`
Expected: all pass. The `describe_beat` golden strings are exact — adjust the implementation, not the test, if they differ only by wording you introduced.

- [ ] **Step 5: Format, type-check, commit**

```bash
venv/bin/black metascan/core/i2v_templates.py tests/test_i2v_templates.py
venv/bin/mypy metascan/core/i2v_templates.py
git add metascan/core/i2v_templates.py tests/test_i2v_templates.py
git commit -m "feat(i2v): template grammar, brief and fill validation

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: H3 assembly with the off-screen rule

**Files:**
- Modify: `metascan/core/i2v_templates.py`
- Test: `tests/test_i2v_templates.py` (append)

**Interfaces:**
- Consumes: everything above; `ALIGNMENT_LINE`, `_OPENING`, `_sentence` from `metascan.core.i2v_compiler`.
- Produces: `assemble_i2v_template_prompt(t: I2vTemplate, fill: I2vTemplateFill) -> Tuple[str, List[str]]` — the H3 document and the advisory notes assembly itself generated (today only the off-screen rule).

Rendering rules (spec §5.5), fixed here so the golden tests are exact:

- Document = `ALIGNMENT_LINE`, blank line, `integrated_multimodal_description: <shots>`, blank, `overall_soundscape: …`, blank, `non_diegetic_music: …`.
- **Role naming:** first mention anywhere → `description`; after → `tag`. Sentence-initial mentions are capitalised.
- **Speaker ids:** `(S1)`, `(S2)` … assigned in order of first *line*, one per role.
- **Beat 1** (always `continuous`): `[Shot 1] <Look capitalised, ending in a period, if any> <_OPENING> <Framing sentence>. <Camera sentence> <Action> <Line sentence>`
  - Framing sentence, `ots`: `An over-the-shoulder <size> looks past <first cast name> onto <remaining names>.` Non-ots with cast: `<Framing phrase, capitalised> frames <names>.` Empty cast: `<Framing phrase, capitalised> frames the scene.`
  - Names joined "a, b and c".
- **A `cut` beat:** `[Shot n] At MM:SS.mmm, the shot cuts to <framing phrase> of <names>. <Camera sentence> <Action> <Line sentence>` (ots: `… the shot cuts to an over-the-shoulder <size> looking past <first> onto <rest>.`; empty cast: `… the shot cuts to <framing phrase>.`)
- **A `j_cut` beat:** `[Shot n] At MM:SS.mmm, <Tag/desc> (Sk) says: <d>[English] <line></d>, the words carrying over from the previous shot, as the shot cuts to <framing phrase> of <names>. <Camera sentence> <Action>` — the line comes first because the audio leads the picture; no separate line sentence.
- **A `continuous` beat after the first:** `At MM:SS.mmm, <camera sentence, lower-case> <Action> <Line sentence>`. If a role's first mention falls here, prepend `<Description> is now in frame.` before the action.
- **Line sentence:** `<Name> (Sk) says: <d>[English] <line></d>` (rendered with `_sentence` so it ends in a period after `</d>`).
- **Off-screen rule:** for beat index 0, cast members whose fill is `bound=False` are dropped from the framing sentence and a note is returned: `role B is not in the picture; the template casts it in the first shot, so it enters at beat <n> instead` (n = the 1-based index of its next beat, or "never" if none). If that role is beat 0's speaker, the line sentence is rendered `Off-screen, <Description> (S1) says: <d>…</d>`.
- Actions and lines pass through `_sentence`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_i2v_templates.py`:

```python
# ---- assembly ---------------------------------------------------------

from metascan.core.i2v_compiler import ALIGNMENT_LINE  # noqa: E402
from metascan.core.i2v_templates import assemble_i2v_template_prompt  # noqa: E402

DIALOG_FILL = {
    "roles": [
        {"id": "A", "bound": True,
         "description": "a woman in her thirties with dark hair and a red wool coat",
         "tag": "the woman in the red coat"},
        {"id": "B", "bound": True,
         "description": "a man in his forties with grey stubble and a black leather jacket",
         "tag": "the man in the leather jacket"},
    ],
    "beats": [
        {"action": "He leans across the table and speaks under his breath",
         "line": "I told you, we shouldn't be here"},
        {"action": "His jaw tightens and he glances toward the door"},
        {"action": "She sits back and folds her arms",
         "line": "It's already too late"},
        {"action": "He exhales and nods once"},
    ],
    "overall_soundscape": "low jazz piano, glasses, muffled chatter",
    "non_diegetic_music": "none",
}

EXPECTED_DIALOG = (
    ALIGNMENT_LINE
    + "\n\nintegrated_multimodal_description: [Shot 1] Cinematic, photorealistic "
    "skin textures, fine grain. The subjects, composition, and setting shown in "
    "<Picture 1> are established at 0.00 seconds and keep their appearance, "
    "clothing, colors, and spatial relationships. An over-the-shoulder medium "
    "shot looks past a woman in her thirties with dark hair and a red wool coat "
    "onto a man in his forties with grey stubble and a black leather jacket. "
    "The camera trucks right at slow speed. He leans across the table and "
    "speaks under his breath. The man in the leather jacket (S1) says: "
    "<d>[English] I told you, we shouldn't be here.</d> "
    "[Shot 2] At 00:04.000, the shot cuts to a close-up of the man in the "
    "leather jacket. The camera holds a static shot. His jaw tightens and he "
    "glances toward the door. "
    "[Shot 3] At 00:05.000, the woman in the red coat (S2) says: "
    "<d>[English] It's already too late.</d>, the words carrying over from the "
    "previous shot, as the shot cuts to a medium shot of the woman in the red "
    "coat and the man in the leather jacket. The camera pulls out at slow "
    "speed. She sits back and folds her arms. "
    "At 00:10.000, the camera arcs around the subject with small amplitude. "
    "He exhales and nods once."
    "\n\noverall_soundscape: Low jazz piano, glasses, muffled chatter."
    "\n\nnon_diegetic_music: None."
)


def test_dialog_golden():
    t = parse_i2v_template(DIALOG)
    fill = validate_i2v_template_fill(json.dumps(DIALOG_FILL), t)
    text, notes = assemble_i2v_template_prompt(t, fill)
    assert text == EXPECTED_DIALOG
    assert notes == []


def test_speaker_ids_follow_first_line_order():
    t = parse_i2v_template(DIALOG)
    fill = validate_i2v_template_fill(json.dumps(DIALOG_FILL), t)
    text, _ = assemble_i2v_template_prompt(t, fill)
    assert text.index("(S1)") < text.index("(S2)")
    assert "the man in the leather jacket (S1)" in text
    assert "the woman in the red coat (S2)" in text


def test_unbound_role_is_kept_out_of_shot_one_and_enters_later():
    t = parse_i2v_template(DIALOG)
    data = copy.deepcopy(DIALOG_FILL)
    data["roles"][0]["bound"] = False  # A is not in the picture
    fill = validate_i2v_template_fill(json.dumps(data), t)
    text, notes = assemble_i2v_template_prompt(t, fill)

    shot1 = text.split("[Shot 2]")[0]
    assert "red wool coat" not in shot1  # A's description absent from Shot 1
    assert "looks past" not in shot1  # ots collapses when only one is in frame
    assert "A medium shot frames a man in his forties" in shot1
    # A's full description appears at its first rendered mention, in Shot 3.
    shot3 = text.split("[Shot 3]")[1]
    assert "a woman in her thirties with dark hair and a red wool coat (S2) says" in shot3
    assert notes == [
        "role A is not in the picture; the template casts it in the first shot, "
        "so it enters at beat 3 instead"
    ]


def test_unbound_speaker_in_shot_one_speaks_off_screen():
    t = parse_i2v_template(DIALOG)
    data = copy.deepcopy(DIALOG_FILL)
    data["roles"][1]["bound"] = False  # B, who speaks in beat 1, is invented
    fill = validate_i2v_template_fill(json.dumps(data), t)
    text, notes = assemble_i2v_template_prompt(t, fill)
    shot1 = text.split("[Shot 2]")[0]
    assert (
        "Off-screen, a man in his forties with grey stubble and a black leather "
        "jacket (S1) says: <d>[English] I told you, we shouldn't be here.</d>"
    ) in shot1
    assert "A medium shot frames a woman in her thirties" in shot1
    assert notes and "role B" in notes[0]


def test_role_first_seen_in_a_continuous_beat_is_introduced():
    d = copy.deepcopy(DIALOG)
    d["beats"][0]["cast"], d["beats"][0]["speaker"] = ["B"], "B"
    d["beats"][1]["transition"] = "continuous"
    d["beats"][1]["cast"] = ["A", "B"]
    d["beats"][2]["transition"] = "continuous"
    d["beats"][3]["transition"] = "continuous"
    t = parse_i2v_template(d)
    fill = validate_i2v_template_fill(json.dumps(DIALOG_FILL), t)
    text, _ = assemble_i2v_template_prompt(t, fill)
    assert "At 00:04.000, the camera holds a static shot. A woman in her thirties with dark hair and a red wool coat is now in frame." in text
    assert "[Shot 2]" not in text


def test_look_is_optional_and_shot_one_opener_is_the_single_take_one():
    from metascan.core.i2v_compiler import _OPENING

    d = _with(look="")
    t = parse_i2v_template(d)
    fill = validate_i2v_template_fill(json.dumps(DIALOG_FILL), t)
    text, _ = assemble_i2v_template_prompt(t, fill)
    assert "integrated_multimodal_description: " + _OPENING in text


def test_shipped_templates_assemble_without_error():
    from metascan.core.i2v_templates import I2V_TEMPLATES_DIR

    for t in load_i2v_templates(I2V_TEMPLATES_DIR).values():
        fill = validate_i2v_template_fill(_fill(t), t)
        text, notes = assemble_i2v_template_prompt(t, fill)
        assert text.startswith(ALIGNMENT_LINE)
        assert notes == []
        assert text.count("[Shot ") == len(shots_of(t))


def test_melee_and_intimate_shapes():
    from metascan.core.i2v_templates import I2V_TEMPLATES_DIR

    lib = load_i2v_templates(I2V_TEMPLATES_DIR)
    melee = lib["melee_12"]
    text, _ = assemble_i2v_template_prompt(melee, validate_i2v_template_fill(_fill(melee), melee))
    assert "[Shot 1] High contrast, sharp reflections, fast kinetic energy. " in text
    assert "A wide shot from a low angle frames desc of A and desc of B." in text
    assert "At 00:03.000, the camera tracks the subject at fast speed." in text
    assert "[Shot 2] At 00:07.000, the shot cuts to a medium shot of tag A and tag B. The camera shakes slightly." in text

    intimate = lib["intimate_15"]
    text, _ = assemble_i2v_template_prompt(
        intimate, validate_i2v_template_fill(_fill(intimate), intimate)
    )
    assert "An extreme close-up on a macro lens frames desc of A. The camera tilts up at slow speed." in text
    assert "At 00:05.000, the camera pushes in at slow speed." in text
    assert "At 00:10.000, the camera holds a static shot." in text
    assert "[Shot 2]" not in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_i2v_templates.py -q -k "golden or unbound or continuous_beat or look_is or shapes or assemble_without"`
Expected: FAIL — `ImportError: cannot import name 'assemble_i2v_template_prompt'`.

- [ ] **Step 3: Implement**

Add to the imports at the top of `metascan/core/i2v_templates.py`:

```python
from metascan.core.i2v_compiler import _COMMON, _OPENING, ALIGNMENT_LINE, I2vError, _sentence
```

Append before `__all__`:

```python
# ---- assembly ----------------------------------------------------------


def _join_names(names: Sequence[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _capital(text: str) -> str:
    return text[0].upper() + text[1:] if text else text


class _Names:
    """First mention -> description, afterwards -> tag."""

    def __init__(self, fill: I2vTemplateFill) -> None:
        self._fill = fill
        self._seen: set[str] = set()

    def __call__(self, role_id: str) -> str:
        r = self._fill.roles[role_id]
        if role_id in self._seen:
            return r.tag
        self._seen.add(role_id)
        return r.description

    def seen(self, role_id: str) -> bool:
        return role_id in self._seen


class _Speakers:
    """(S1), (S2) ... in order of first line."""

    def __init__(self) -> None:
        self._ids: Dict[str, int] = {}

    def __call__(self, role_id: str) -> str:
        if role_id not in self._ids:
            self._ids[role_id] = len(self._ids) + 1
        return f"(S{self._ids[role_id]})"


def _line_sentence(
    name: str, sid: str, line: str, *, off_screen: bool = False, capital: bool = True
) -> str:
    """``The man in the leather jacket (S1) says: <d>[English] ...</d>``.
    ``capital`` is False when the sentence continues an ``At MM:SS.mmm,``
    stamp (the j_cut form); the "Off-screen, " prefix takes the capital
    itself."""
    prefix = "Off-screen, " if off_screen else ""
    shown = name if (off_screen or not capital) else _capital(name)
    return f"{prefix}{shown} {sid} says: <d>[English] {_sentence(line)}</d>"


def _framing(cam: I2vCamera, names: Sequence[str], *, opening: bool) -> str:
    """The sentence that establishes a shot's framing and who is in it."""
    if cam.angle == "ots" and len(names) >= 2:
        size = SHOT_SIZE_PHRASES[cam.shot_size].split(" ", 1)[1]  # drop article
        if opening:
            return (
                f"An over-the-shoulder {size} looks past {names[0]} onto "
                f"{_join_names(names[1:])}."
            )
        return (
            f"the shot cuts to an over-the-shoulder {size} looking past "
            f"{names[0]} onto {_join_names(names[1:])}."
        )
    phrase = framing_phrase(cam)
    if opening:
        who = _join_names(names) or "the scene"
        return f"{_capital(phrase)} frames {who}."
    return f"the shot cuts to {phrase}" + (f" of {_join_names(names)}." if names else ".")


def assemble_i2v_template_prompt(
    t: I2vTemplate, fill: I2vTemplateFill
) -> Tuple[str, List[str]]:
    """Deterministic H3 document from template + fill. Returns the text
    and advisory notes (the off-screen rule). Code owns 100% of the
    document structure; the VLM's words appear only as actions, lines,
    role descriptions/tags and the two sound fields."""
    names = _Names(fill)
    speakers = _Speakers()
    notes: List[str] = []
    parts: List[str] = []
    shot_no = 0

    for i, b in enumerate(t.beats):
        cast = list(b.cast)
        off_screen_speaker = False
        if i == 0:
            for rid in b.cast:
                if not fill.roles[rid].bound:
                    cast.remove(rid)
                    later = next(
                        (j + 1 for j in range(1, len(t.beats)) if rid in t.beats[j].cast),
                        None,
                    )
                    notes.append(
                        f"role {rid} is not in the picture; the template casts it in "
                        f"the first shot, so it enters at "
                        f"{'beat ' + str(later) if later else 'no later beat'} instead"
                    )
                    if b.speaker == rid:
                        off_screen_speaker = True

        cast_names = [names(rid) for rid in cast]
        opens_shot = i == 0 or b.transition != "continuous"
        segment: List[str] = []

        if i == 0:
            shot_no = 1
            head = "[Shot 1]"
            if t.look:
                head += " " + _sentence(_capital(t.look))
            segment.append(head + " " + _OPENING)
            segment.append(_framing(b.camera, cast_names, opening=True))
            segment.append(camera_sentence(b.camera))
            segment.append(_sentence(fill.beats[i].action))
            if b.speaker:
                segment.append(
                    _line_sentence(
                        names(b.speaker), speakers(b.speaker), fill.beats[i].line or "",
                        off_screen=off_screen_speaker,
                    )
                )
        elif opens_shot:
            shot_no += 1
            stamp = f"[Shot {shot_no}] At {_timestamp(b.start_s)},"
            if b.transition == "j_cut" and b.speaker:
                line = _line_sentence(
                    names(b.speaker),
                    speakers(b.speaker),
                    fill.beats[i].line or "",
                    capital=False,
                )
                segment.append(
                    f"{stamp} {line}, the words carrying over from the previous "
                    f"shot, as {_framing(b.camera, cast_names, opening=False)}"
                )
                segment.append(camera_sentence(b.camera))
                segment.append(_sentence(fill.beats[i].action))
            else:
                segment.append(f"{stamp} {_framing(b.camera, cast_names, opening=False)}")
                segment.append(camera_sentence(b.camera))
                segment.append(_sentence(fill.beats[i].action))
                if b.speaker:
                    segment.append(
                        _line_sentence(names(b.speaker), speakers(b.speaker), fill.beats[i].line or "")
                    )
        else:
            newly = [rid for rid in cast if not names.seen(rid)]
            segment.append(
                f"At {_timestamp(b.start_s)}, {camera_sentence(b.camera, capital=False)}"
            )
            for rid in newly:
                segment.append(f"{_capital(names(rid))} is now in frame.")
            segment.append(_sentence(fill.beats[i].action))
            if b.speaker:
                segment.append(
                    _line_sentence(names(b.speaker), speakers(b.speaker), fill.beats[i].line or "")
                )
        parts.append(" ".join(segment))

    description = " ".join(parts)
    text = (
        f"{ALIGNMENT_LINE}\n\n"
        f"integrated_multimodal_description: {description}\n\n"
        f"overall_soundscape: {_sentence(_capital(fill.overall_soundscape))}\n\n"
        f"non_diegetic_music: {_sentence(_capital(fill.non_diegetic_music))}"
    )
    return text, notes
```

Note on `_framing` for the `j_cut` case: `_framing(..., opening=False)` returns a string starting with `the shot cuts to …` and ending in `.`; it is used as the tail of the `as …` clause. In the golden, line 3 renders `…</d>, the words carrying over from the previous shot, as the shot cuts to a medium shot of …`. The `_line_sentence` result ends in `</d>` (the `_sentence` is applied to the line inside the tag), so the comma follows `</d>` directly — matching the golden.

One wrinkle the golden exposes: in `test_unbound_role_is_kept_out_of_shot_one_and_enters_later`, A's first mention is in beat 2 (Shot 3) as the *speaker* of a j_cut, so `names("A")` yields the description there — the test asserts exactly that.

Add `"assemble_i2v_template_prompt"` to `__all__`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_i2v_templates.py -q`
Expected: all pass. Diff the golden carefully on the first failure — the rules above are the authority; fix the implementation, not the expected string, unless the expected string violates a rule.

- [ ] **Step 5: Format, type-check, commit**

```bash
venv/bin/black metascan/core/i2v_templates.py tests/test_i2v_templates.py
venv/bin/mypy metascan/core/i2v_templates.py
git add metascan/core/i2v_templates.py tests/test_i2v_templates.py
git commit -m "feat(i2v): assemble the H3 document from template and fill

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Template-aware lint, and the single-take golden pin

**Files:**
- Modify: `metascan/core/i2v_templates.py`, `metascan/core/i2v_compiler.py:215-268` (`lint_i2v_prompt`)
- Test: `tests/test_i2v_templates.py` (append), `tests/test_i2v_compiler.py` (append)

**Interfaces:**
- Produces:
  - `lint_against_template(text: str, t: I2vTemplate) -> List[str]` in `i2v_templates.py`
  - `lint_i2v_prompt(text: str, duration_s: float, template: Optional[I2vTemplate] = None) -> List[str]` — existing checks unchanged, plus `lint_against_template` when `template` is given. The import of `i2v_templates` inside `lint_i2v_prompt` is **function-level** (i2v_templates imports i2v_compiler; a module-level import would be a cycle — the `lint_i2v_report` precedent).

Spec deviation to record: §5.6's "every role's tag appears in the span of every beat that casts it" cannot be checked from text alone (lint has no fill). Assembly guarantees it for generated text; hand edits are not checked. Note this in the module docstring.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_i2v_templates.py`:

```python
# ---- lint --------------------------------------------------------------

from metascan.core.i2v_compiler import lint_i2v_prompt  # noqa: E402
from metascan.core.i2v_templates import lint_against_template  # noqa: E402


def _dialog_text():
    t = parse_i2v_template(DIALOG)
    fill = validate_i2v_template_fill(json.dumps(DIALOG_FILL), t)
    return t, assemble_i2v_template_prompt(t, fill)[0]


def test_generated_text_lints_clean_against_its_template():
    t, text = _dialog_text()
    assert lint_against_template(text, t) == []
    # and the full lint (base + template) adds nothing template-related
    assert [w for w in lint_i2v_prompt(text, 15.0, template=t) if "Shot" in w or "beat" in w] == []


def test_lint_flags_a_missing_shot():
    t, text = _dialog_text()
    text = text.replace("[Shot 3] At 00:05.000, ", "")
    issues = lint_against_template(text, t)
    assert any("2 shots" in w and "3" in w for w in issues)


def test_lint_flags_non_contiguous_numbering():
    t, text = _dialog_text()
    text = text.replace("[Shot 3]", "[Shot 4]")
    assert any("contiguous" in w for w in lint_against_template(text, t))


def test_lint_flags_a_timestamp_on_shot_one():
    t, text = _dialog_text()
    text = text.replace("[Shot 1] ", "[Shot 1] At 00:00.000, ")
    assert any("Shot 1" in w and "timestamp" in w for w in lint_against_template(text, t))


def test_lint_flags_cut_times_out_of_order_or_past_the_end():
    t, text = _dialog_text()
    issues = lint_against_template(text.replace("At 00:05.000", "At 00:03.000"), t)
    assert any("increase" in w for w in issues)
    issues = lint_against_template(text.replace("At 00:05.000", "At 00:16.000"), t)
    assert any("15" in w and "duration" in w for w in issues)


def test_lint_flags_a_missing_camera_phrase_in_a_shot():
    t, text = _dialog_text()
    text = text.replace("The camera pulls out at slow speed. ", "")
    issues = lint_against_template(text, t)
    assert any("Shot 3" in w and "pulls out" in w for w in issues)


def test_lint_flags_a_missing_line_where_a_speaker_is_set():
    t, text = _dialog_text()
    text = text.replace(
        "The man in the leather jacket (S1) says: <d>[English] I told you, we shouldn't be here.</d>",
        "",
    )
    issues = lint_against_template(text, t)
    assert any("Shot 1" in w and "B speaks" in w for w in issues)


def test_lint_flags_a_cut_time_that_moved_from_the_template():
    t, text = _dialog_text()
    text = text.replace("At 00:04.000", "At 00:04.500")
    issues = lint_against_template(text, t)
    assert any("Shot 2" in w and "00:04.000" in w for w in issues)


def test_base_lint_is_unchanged_when_no_template_is_given():
    _, text = _dialog_text()
    assert lint_i2v_prompt(text, 15.0) == lint_i2v_prompt(text, 15.0, template=None)
```

Append to `tests/test_i2v_compiler.py` (read the file's imports first and reuse its existing fixtures/helpers if any produce an `I2vResult`; otherwise build one):

```python
def test_single_take_output_is_pinned():
    """template_id = null must stay byte-for-byte what it was before
    découpage templates existed (spec §5.1). If this test fails, the
    single-take path changed -- that is a bug, not a golden to update."""
    from metascan.core.i2v_compiler import (
        ALIGNMENT_LINE,
        I2vBeat,
        I2vResult,
        assemble_i2v_prompt,
    )

    result = I2vResult(
        beats=[
            I2vBeat(action="She lifts the cup", camera="static"),
            I2vBeat(action="She turns to the window", camera="push_in"),
            I2vBeat(action="She smiles", camera="static"),
        ],
        overall_soundscape="quiet room",
        non_diegetic_music="none",
    )
    assert assemble_i2v_prompt(result) == (
        ALIGNMENT_LINE
        + "\n\nintegrated_multimodal_description: [Shot 1] The subjects, "
        "composition, and setting shown in <Picture 1> are established at 0.00 "
        "seconds and keep their appearance, clothing, colors, and spatial "
        "relationships. She lifts the cup. She turns to the window. The camera "
        "pushes in. She smiles."
        "\n\noverall_soundscape: quiet room."
        "\n\nnon_diegetic_music: none."
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_i2v_templates.py tests/test_i2v_compiler.py -q`
Expected: the lint tests FAIL with `ImportError: cannot import name 'lint_against_template'`; `test_single_take_output_is_pinned` PASSES (it pins current behaviour — if it fails, inspect `assemble_i2v_prompt` before continuing; the golden was written from the code as it is).

- [ ] **Step 3: Implement**

Append to `metascan/core/i2v_templates.py` before `__all__`:

```python
# ---- lint --------------------------------------------------------------
#
# Advisory only. Runs on generated AND hand-edited text. Text-only: the
# fill is not available here, so "every role's tag appears in every beat
# that casts it" (spec §5.6) is guaranteed by assembly for generated text
# and NOT checked on hand edits.

_SHOT_MARK = re.compile(r"\[Shot (\d+)\](?: At (\d\d):(\d\d\.\d{3}),)?")


def _shot_spans(text: str) -> List[Tuple[int, Optional[float], str]]:
    """[(shot_no, cut_time_s or None, span_text), ...] in document order."""
    marks = list(_SHOT_MARK.finditer(text))
    out: List[Tuple[int, Optional[float], str]] = []
    for k, m in enumerate(marks):
        end = marks[k + 1].start() if k + 1 < len(marks) else len(text)
        stamp = None
        if m.group(2) is not None:
            stamp = int(m.group(2)) * 60 + float(m.group(3))
        out.append((int(m.group(1)), stamp, text[m.end() : end]))
    return out


def lint_against_template(text: str, t: I2vTemplate) -> List[str]:
    issues: List[str] = []
    spans = _shot_spans(text)
    expected = shots_of(t)

    if len(spans) != len(expected):
        issues.append(
            f"text has {len(spans)} shots; template '{t.id}' has {len(expected)}"
        )
    if [s[0] for s in spans] != list(range(1, len(spans) + 1)):
        issues.append("[Shot n] numbers must be contiguous from 1")
    if spans and spans[0][1] is not None:
        issues.append("Shot 1 must not carry a timestamp (base guide 4.2)")

    last = 0.0
    for shot_no, stamp, _ in spans[1:]:
        if stamp is None:
            issues.append(f"Shot {shot_no} has no 'At MM:SS.mmm,' cut time")
            continue
        if stamp <= last:
            issues.append(f"Shot {shot_no} cut time must increase (after {_timestamp(last)})")
        if stamp >= t.duration_s:
            issues.append(
                f"Shot {shot_no} cut time {_timestamp(stamp)} is past the "
                f"{t.duration_s:.0f}s duration"
            )
        last = stamp

    for (shot_no, stamp, span), beat_ids in zip(spans, expected):
        first = t.beats[beat_ids[0]]
        if shot_no != 1 and stamp is not None and abs(stamp - first.start_s) > 1e-6:
            issues.append(
                f"Shot {shot_no} cuts at {_timestamp(stamp)}; template has "
                f"{_timestamp(first.start_s)}"
            )
        for bi in beat_ids:
            b = t.beats[bi]
            phrase = (
                "static shot" if b.camera.camera_motion == "static"
                else _CAMERA_PHRASES[b.camera.camera_motion]
            )
            if phrase not in span:
                issues.append(
                    f"Shot {shot_no} lacks its camera move '{phrase}' "
                    f"({describe_beat(t, bi).split(':', 1)[0]})"
                )
        need = sum(1 for bi in beat_ids if t.beats[bi].speaker)
        have = span.count("<d>")
        if have < need:
            who = ", ".join(f"{t.beats[bi].speaker} speaks" for bi in beat_ids if t.beats[bi].speaker)
            issues.append(
                f"Shot {shot_no} has {have} spoken line(s) but the template has "
                f"{need} ({who})"
            )
    return issues
```

Add `"lint_against_template"` to `__all__`.

In `metascan/core/i2v_compiler.py`, change the signature and the end of `lint_i2v_prompt`:

```python
def lint_i2v_prompt(
    text: str, duration_s: float, template: Optional["I2vTemplate"] = None
) -> List[str]:
    """Expectation-driven advisory lint over the final document text.
    Warnings only -- runs on generated AND hand-edited prompts. With a
    découpage ``template`` the structural checks in
    ``i2v_templates.lint_against_template`` run as well."""
    ...  # existing body unchanged up to the i2v_fixes lines
    issues.extend(f.message for f in camera_findings(text))
    issues.extend(f.message for f in speech_findings(text))
    if template is not None:
        # Function-level: i2v_templates imports this module.
        from metascan.core.i2v_templates import lint_against_template

        issues.extend(lint_against_template(text, template))
    return issues
```

Add at the top of `i2v_compiler.py`, under `from __future__` / typing imports:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover -- import cycle at runtime
    from metascan.core.i2v_templates import I2vTemplate
```

The word-count check in the existing lint uses `beat_count(duration_s)`; with a template, that floor still applies as a rough minimum — leave it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_i2v_templates.py tests/test_i2v_compiler.py tests/test_i2v_fixes.py -q`
Expected: all pass.

- [ ] **Step 5: Format, type-check, commit**

```bash
venv/bin/black metascan/core/i2v_templates.py metascan/core/i2v_compiler.py tests/test_i2v_templates.py tests/test_i2v_compiler.py
venv/bin/mypy metascan/core/i2v_templates.py metascan/core/i2v_compiler.py
git add metascan/core/i2v_templates.py metascan/core/i2v_compiler.py tests/test_i2v_templates.py tests/test_i2v_compiler.py
git commit -m "feat(i2v): template-aware lint; pin the single-take output

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `template_id` in the clip's form state

**Files:**
- Modify: `metascan/core/i2v_form.py`
- Test: `tests/test_i2v_form.py` (append)

**Interfaces:**
- Produces: `FORM_FIELDS` gains `"template_id"`; `build_form_state(..., template_id: Optional[str] = None)`; `validate_form_patch` accepts `template_id: str | None`; `form_state_for_row` fills `template_id` with `None` when absent.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_i2v_form.py`:

```python
def test_template_id_is_a_form_field_defaulting_to_none():
    assert "template_id" in FORM_FIELDS
    state = build_form_state(
        idea="", prompt="p", duration_s=6, quality="fast",
        megapixels=0.5, steps=None, seed=1, loras=None,
    )
    assert state["template_id"] is None
    state = build_form_state(
        idea="", prompt="p", duration_s=15, quality="fast",
        megapixels=0.5, steps=None, seed=1, loras=None, template_id="dialog_ots_15",
    )
    assert state["template_id"] == "dialog_ots_15"


def test_legacy_row_form_state_has_no_template():
    state = form_state_for_row({"form_state": None, "seed": 3}, MEGAPIXEL_OPTIONS)
    assert state["template_id"] is None


def test_validate_accepts_a_template_id_or_null():
    assert validate_form_patch({"template_id": "melee_12"}) == {"template_id": "melee_12"}
    assert validate_form_patch({"template_id": None}) == {"template_id": None}


@pytest.mark.parametrize("bad", [5, "", "   ", ["x"]])
def test_validate_rejects_a_non_string_or_blank_template_id(bad):
    with pytest.raises(FormStateError) as excinfo:
        validate_form_patch({"template_id": bad})
    assert "template_id" in str(excinfo.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_i2v_form.py -q`
Expected: the four new tests FAIL (`"template_id" in FORM_FIELDS` is False; `build_form_state` rejects the kwarg; `validate_form_patch` says unknown field).

- [ ] **Step 3: Implement**

In `metascan/core/i2v_form.py`:

```python
FORM_FIELDS = (
    "idea",
    "prompt",
    "duration_s",
    "quality",
    "megapixels",
    "steps",
    "seed",
    "loras",
    "template_id",
)
```

Add a validator and register it:

```python
def _template_id(value: Any) -> Optional[str]:
    # The découpage cadence the clip was made with; None = single take.
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise FormStateError("template_id must be a non-empty string or null")
    return value


_VALIDATORS = {
    ...existing entries...,
    "template_id": _template_id,
}
```

`build_form_state` gains a keyword `template_id: Optional[str] = None` and returns `"template_id": template_id`. In `form_state_for_row`, add `"template_id": row.get("template_id")` to `fallback` (rows have no such column, so this is `None`; a stored `form_state` overrides it).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_i2v_form.py tests/test_i2v_runner.py tests/test_i2v_api.py -q`
Expected: all pass (the runner's existing form-state test compares the whole dict — it now includes `"template_id": None`; update that expected dict in `tests/test_i2v_runner.py::TestFormStateAtIngest::test_the_form_as_submitted_becomes_the_clips_form_state` by adding `"template_id": None`).

- [ ] **Step 5: Format, type-check, commit**

```bash
venv/bin/black metascan/core/i2v_form.py tests/test_i2v_form.py tests/test_i2v_runner.py
venv/bin/mypy metascan/core/i2v_form.py
git add metascan/core/i2v_form.py tests/test_i2v_form.py tests/test_i2v_runner.py
git commit -m "feat(i2v): template_id joins the clip form state

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Runner — the template branch of `generate_prompt`, and the system prompt

**Files:**
- Modify: `metascan/core/i2v_runner.py` (`generate_prompt`, `generate`), `data/meta_prompt.yml`
- Test: `tests/test_i2v_runner.py` (append)

**Interfaces:**
- Consumes: Task 3/4/5 functions; `get_i2v_template`, `I2vTemplateError`.
- Produces:
  - `I2vRunner.generate_prompt(source_path, idea, duration_s, template_id: Optional[str] = None) -> Tuple[str, List[str]]`. With a template: unknown id → `I2vRequestError` (message starts `unknown i2v template`); `duration_s != template.duration_s` → `I2vRequestError` naming both.
  - `I2vRunner.generate(..., template_id: Optional[str] = None)` — recorded into `_job_meta[job_id]["form"]["template_id"]` only.
  - `I2V_TEMPLATE_SYSTEM` key in `data/meta_prompt.yml`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_i2v_runner.py`:

```python
class TestGeneratePromptWithTemplate(I2vRunnerBase):
    def setUp(self):
        super().setUp()
        from metascan.core.i2v_templates import get_i2v_template

        self.template = get_i2v_template("dialog_ots_15")
        roles = [
            {"id": "A", "bound": True, "description": "a woman in a red coat", "tag": "the woman"},
            {"id": "B", "bound": True, "description": "a man in a leather jacket", "tag": "the man"},
        ]
        beats = [
            {"action": "He leans in", "line": "We shouldn't be here"},
            {"action": "His jaw tightens"},
            {"action": "She folds her arms", "line": "Too late"},
            {"action": "He nods"},
        ]
        self.vlm = FakeVlm(
            json.dumps(
                {"roles": roles, "beats": beats, "overall_soundscape": "jazz", "non_diegetic_music": "none"}
            )
        )
        self.runner.get_vlm = lambda: self.vlm

    def test_template_drives_grammar_prompt_and_assembly(self):
        from metascan.core.i2v_templates import build_i2v_template_user_prompt, i2v_template_grammar

        text, warnings = self.run_async(
            self.runner.generate_prompt(str(self.src), "an argument", 15.0, "dialog_ots_15")
        )
        call = self.vlm.calls[0]
        self.assertEqual(call["grammar"], i2v_template_grammar(self.template))
        self.assertEqual(call["user_prompt"], build_i2v_template_user_prompt(self.template, "an argument"))
        self.assertIn("fixed shot plan", call["system_prompt"].lower() + call["user_prompt"].lower())
        self.assertIn("[Shot 2] At 00:04.000, the shot cuts to a close-up of the man.", text)
        self.assertIn("[Shot 3] At 00:05.000, the woman (S2) says:", text)
        self.assertEqual([w for w in warnings if "Shot" in w], [])

    def test_off_screen_note_reaches_the_warnings(self):
        data = json.loads(self.vlm.response)
        data["roles"][0]["bound"] = False
        self.vlm.response = json.dumps(data)
        _, warnings = self.run_async(
            self.runner.generate_prompt(str(self.src), "", 15.0, "dialog_ots_15")
        )
        self.assertTrue(any(w.startswith("role A is not in the picture") for w in warnings))

    def test_duration_must_match_the_template(self):
        with self.assertRaises(I2vRequestError) as ctx:
            self.run_async(self.runner.generate_prompt(str(self.src), "", 10.0, "dialog_ots_15"))
        self.assertIn("15", str(ctx.exception))
        self.assertIn("10", str(ctx.exception))

    def test_unknown_template_is_a_request_error(self):
        with self.assertRaises(I2vRequestError) as ctx:
            self.run_async(self.runner.generate_prompt(str(self.src), "", 15.0, "nope"))
        self.assertIn("unknown i2v template", str(ctx.exception))

    def test_no_template_uses_the_single_take_path_unchanged(self):
        from metascan.core.i2v_compiler import i2v_grammar

        self.vlm = FakeVlm(_beats_response(3))
        self.runner.get_vlm = lambda: self.vlm
        self.run_async(self.runner.generate_prompt(str(self.src), "x", 6.0))
        self.assertEqual(self.vlm.calls[0]["grammar"], i2v_grammar(6.0))


class TestGenerateRecordsTemplateId(TestFormStateAtIngest):
    def test_template_id_is_in_the_form_snapshot_at_ingest(self):
        job_id = self._generate(duration_s=15.0, template_id="dialog_ots_15")
        self._ingest(job_id)
        row = self.db.list_i2v_videos(str(self.src))[0]
        self.assertEqual(row["form_state"]["template_id"], "dialog_ots_15")

    def test_generate_does_not_require_the_template_to_exist(self):
        # The prompt text already embodies the cadence; a renamed template
        # file must not block a render.
        job_id = self._generate(template_id="gone_20")
        self._ingest(job_id)
        row = self.db.list_i2v_videos(str(self.src))[0]
        self.assertEqual(row["form_state"]["template_id"], "gone_20")
```

Check `I2vRequestError` is imported at the top of `tests/test_i2v_runner.py`; add `from metascan.core.i2v_runner import I2vRequestError` if not.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_i2v_runner.py -q -k "Template"`
Expected: FAIL — `TypeError: generate_prompt() takes 4 positional arguments but 5 were given` and the ingest tests fail on `template_id`.

- [ ] **Step 3: Implement**

In `data/meta_prompt.yml`, add after `I2V_BEATS_SYSTEM`:

```yaml
I2V_TEMPLATE_SYSTEM: |-
  You are a video director filling in a fixed shot plan for a MiniMax
  image-to-video generation. You see the exact first frame of the video,
  the user's idea, a cast of roles, and a beat-by-beat shot plan that
  fixes timing, framing and camera. Respond with JSON only, following the
  grammar you are given. First bind each role: if a person in the image
  fits, set bound true and describe THAT person; otherwise set bound
  false and invent a fitting person. Then write each beat's visible
  action and, where asked, the exact words spoken. Describe only what is
  concretely visible or audible; never restate the camera move (the plan
  owns it); never mention "the image", "the frame", or "the video". Keep
  every person's identity, clothing and the scene's layout consistent
  across every beat.
```

In `metascan/core/i2v_runner.py`, imports:

```python
from metascan.core.i2v_templates import (
    I2vTemplateError,
    assemble_i2v_template_prompt,
    build_i2v_template_user_prompt,
    get_i2v_template,
    i2v_template_grammar,
    i2v_template_max_tokens,
    validate_i2v_template_fill,
)
```

Replace `generate_prompt`:

```python
    async def generate_prompt(
        self,
        source_path: str,
        idea: str,
        duration_s: float,
        template_id: Optional[str] = None,
    ) -> Tuple[str, List[str]]:
        """Expand an idea into the H3 prompt. With ``template_id`` the
        découpage template owns the shot structure and the VLM fills only
        prose; without it, this is the single-take path, unchanged."""
        template = None
        if template_id:
            try:
                template = get_i2v_template(template_id)
            except I2vTemplateError as exc:
                raise I2vRequestError(str(exc)) from exc
            if abs(float(duration_s) - template.duration_s) > 1e-6:
                raise I2vRequestError(
                    f"Template '{template.id}' is a {template.duration_s:.0f}s "
                    f"cadence; duration_s was {float(duration_s):.0f}"
                )

        vlm = self.get_vlm()
        if vlm is None:
            raise I2vUnavailableError("VLM subsystem is not running")
        path = Path(to_native_path(source_path))
        if not vlm.is_image_path(path):
            raise I2vRequestError(f"Not an image: {source_path}")
        if not path.exists():
            raise I2vRequestError(f"File not found: {source_path}")
        model_id = pick_vlm_model(vlm)
        await vlm.ensure_started(model_id)

        if template is None:
            raw = await vlm.generate_text(
                system_prompt=get_prompt_store().get("I2V_BEATS_SYSTEM"),
                user_prompt=build_i2v_user_prompt(idea, duration_s),
                image_path=path,
                grammar=i2v_grammar(duration_s),
                temperature=0.6,
                max_tokens=i2v_max_tokens(duration_s),
                timeout=240.0,
            )
            result = validate_i2v_beats(raw)
            text = assemble_i2v_prompt(result)
            return text, lint_i2v_prompt(text, duration_s)

        raw = await vlm.generate_text(
            system_prompt=get_prompt_store().get("I2V_TEMPLATE_SYSTEM"),
            user_prompt=build_i2v_template_user_prompt(template, idea),
            image_path=path,
            grammar=i2v_template_grammar(template),
            temperature=0.6,
            max_tokens=i2v_template_max_tokens(template),
            timeout=240.0,
        )
        fill = validate_i2v_template_fill(raw, template)
        text, notes = assemble_i2v_template_prompt(template, fill)
        return text, lint_i2v_prompt(text, duration_s, template=template) + notes
```

In `generate(...)`, add the keyword `template_id: Optional[str] = None` after `steps`, and pass it: `build_form_state(..., template_id=template_id)`. It is deliberately **not** validated against the library (see the test's comment).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_i2v_runner.py tests/test_prompt_store.py -q`
Expected: all pass (`test_file_watcher_triggers_reload` may flake on WSL2 in the full suite; it passes alone).

- [ ] **Step 5: Format, type-check, commit**

```bash
venv/bin/black metascan/core/i2v_runner.py tests/test_i2v_runner.py
venv/bin/mypy metascan/core/i2v_runner.py
git add metascan/core/i2v_runner.py data/meta_prompt.yml tests/test_i2v_runner.py
git commit -m "feat(i2v): generate_prompt fills a découpage template

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: API — templates route and `template_id` on prompt / lint / generate

**Files:**
- Modify: `backend/services/i2v_service.py`, `backend/api/i2v.py`
- Test: `tests/test_i2v_api.py` (append)

Before editing `backend/`, confirm no `queued`/`running` jobs (see Global Constraints).

**Interfaces:**
- Produces:
  - `I2vService.list_templates(durations: Sequence[float]) -> List[Dict[str, Any]]` — `summarize_i2v_template` plus `available: bool` and `unavailable_reason: Optional[str]` (`"duration 12s is not one of the configured durations (6, 10, 15, 20)"`).
  - `GET /api/i2v/templates` → that list (200 always; a malformed library file → 500 with the `I2vTemplateError` message, since a bad file must not load silently).
  - `PromptRequest.template_id: Optional[str] = None`; unknown → 404; duration mismatch → 400.
  - `LintRequest.template_id: Optional[str] = None`; unknown → the base lint plus the warning `unknown i2v template '<id>'; linted as a single take`.
  - `GenerateRequest.template_id: Optional[str] = None` → passed to `runner.generate`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_i2v_api.py` (inside the module, after `TestI2vFormStateApi`). The `FakeRunner` in this file records `generate_prompt` and `generate` kwargs — read it first; extend it so `generate_prompt` accepts and records `template_id`, and `generate` records `template_id`, if it does not already accept `**kwargs`.

```python
class TestI2vTemplatesApi(_I2vApiBase):
    def setUp(self):
        super().setUp()
        self._config_patch.stop()
        self._config_patch = patch(
            "backend.api.i2v.load_app_config",
            return_value={"i2v": {"fast_preset_id": 5, "durations": [6, 10, 15, 20]}},
        )
        self._config_patch.start()

    def test_templates_route_lists_the_library_with_availability(self):
        resp = self.client.get("/api/i2v/templates")
        self.assertEqual(resp.status_code, 200)
        by_id = {t["id"]: t for t in resp.json()}
        self.assertEqual(sorted(by_id), ["dialog_ots_15", "intimate_15", "melee_12"])
        self.assertTrue(by_id["dialog_ots_15"]["available"])
        self.assertIsNone(by_id["dialog_ots_15"]["unavailable_reason"])
        self.assertFalse(by_id["melee_12"]["available"])
        self.assertIn("12", by_id["melee_12"]["unavailable_reason"])
        self.assertEqual(by_id["dialog_ots_15"]["beats"][1]["transition"], "cut")

    def test_prompt_passes_template_id_to_the_runner(self):
        resp = self.client.post(
            "/api/i2v/prompt",
            json={"source_path": "/lib/a.png", "idea": "x", "duration_s": 15,
                  "template_id": "dialog_ots_15"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.runner.prompt_calls[-1]["template_id"], "dialog_ots_15")

    def test_prompt_unknown_template_is_404(self):
        self.runner.prompt_error = I2vRequestError("unknown i2v template 'nope'; available: ...")
        resp = self.client.post(
            "/api/i2v/prompt",
            json={"source_path": "/lib/a.png", "idea": "x", "duration_s": 15, "template_id": "nope"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_prompt_duration_mismatch_is_400(self):
        self.runner.prompt_error = I2vRequestError("Template 'dialog_ots_15' is a 15s cadence; duration_s was 10")
        resp = self.client.post(
            "/api/i2v/prompt",
            json={"source_path": "/lib/a.png", "idea": "x", "duration_s": 10, "template_id": "dialog_ots_15"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_lint_with_a_template_runs_the_structural_checks(self):
        text = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\nintegrated_multimodal_description: [Shot 1] words.\n\noverall_soundscape: x.\n\nnon_diegetic_music: y."
        resp = self.client.post(
            "/api/i2v/lint", json={"prompt": text, "duration_s": 15, "template_id": "dialog_ots_15"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any("1 shots" in w and "3" in w for w in resp.json()["warnings"]))

    def test_lint_with_an_unknown_template_warns_and_still_lints(self):
        resp = self.client.post(
            "/api/i2v/lint", json={"prompt": "x", "duration_s": 15, "template_id": "nope"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any("unknown i2v template 'nope'" in w for w in resp.json()["warnings"]))

    def test_generate_forwards_template_id(self):
        resp = self.client.post(
            "/api/i2v/generate",
            json={"source_path": "/lib/a.png", "prompt": "p", "duration_s": 15, "quality": "fast",
                  "seed": 1, "megapixels": 0.5, "template_id": "dialog_ots_15"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self.runner.generate_calls[-1]["template_id"], "dialog_ots_15")
```

Adjust the attribute names (`prompt_calls`, `prompt_error`, `generate_calls`) to whatever `FakeRunner` in this file actually uses; if it lacks an error hook, add `prompt_error: Optional[Exception] = None` raised at the top of `generate_prompt`. How the existing `/prompt` route maps `I2vRequestError` to a status must be read from `backend/api/i2v.py` — today it maps to 400; the 404 branch below is keyed on the message prefix `unknown i2v template`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_i2v_api.py -q -k Templates`
Expected: FAIL — 404 on `/api/i2v/templates`, and the runner never receives `template_id`.

- [ ] **Step 3: Implement**

`backend/services/i2v_service.py` — add:

```python
from metascan.core.i2v_templates import load_i2v_templates, summarize_i2v_template

    async def list_templates(self, durations: Sequence[float]) -> List[Dict[str, Any]]:
        """The découpage library, each stamped with whether its fixed
        duration is one the dialog can select."""
        allowed = {float(d) for d in durations}
        out: List[Dict[str, Any]] = []
        for t in (await asyncio.to_thread(load_i2v_templates)).values():
            summary = summarize_i2v_template(t)
            ok = t.duration_s in allowed
            summary["available"] = ok
            summary["unavailable_reason"] = (
                None
                if ok
                else f"duration {t.duration_s:.0f}s is not one of the configured "
                f"durations ({', '.join(f'{d:.0f}' for d in sorted(allowed))})"
            )
            out.append(summary)
        return out
```

`backend/api/i2v.py`:

```python
from metascan.core.i2v_templates import I2vTemplateError, get_i2v_template


class PromptRequest(BaseModel):
    source_path: str
    idea: str = ""
    duration_s: float
    # Découpage cadence; null = single take (the pre-template behaviour).
    template_id: Optional[str] = None


class LintRequest(BaseModel):
    prompt: str
    duration_s: float
    template_id: Optional[str] = None


class GenerateRequest(BaseModel):
    ...existing fields...
    # Recorded into the clip's form_state only; the prompt text already
    # embodies the cadence, so the render never needs the template.
    template_id: Optional[str] = None


@router.get("/templates")
async def list_templates() -> List[Dict[str, Any]]:
    cfg = get_i2v_config(load_app_config())
    try:
        return await _service().list_templates(cfg["durations"])
    except I2vTemplateError as exc:
        # A malformed library file must not load silently.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

In `generate_prompt` (the `/prompt` route), pass `body.template_id` to `runner.generate_prompt(...)` and, in its existing `except I2vRequestError as exc:` handler, answer 404 when `str(exc).startswith("unknown i2v template")`, else the existing 400.

In `lint_prompt`:

```python
    template = None
    warnings_extra: List[str] = []
    if body.template_id:
        try:
            template = get_i2v_template(body.template_id)
        except I2vTemplateError:
            warnings_extra.append(
                f"unknown i2v template '{body.template_id}'; linted as a single take"
            )
    report = lint_i2v_report(body.prompt, body.duration_s)
    if template is not None:
        from metascan.core.i2v_templates import lint_against_template

        report["warnings"] = list(report["warnings"]) + lint_against_template(body.prompt, template)
    report["warnings"] = list(report["warnings"]) + warnings_extra
    return report
```

In the `/generate` route, pass `template_id=body.template_id` to `runner.generate(...)`.

Note `GET /templates` is registered on the router — there is no `/{id}` route on this router that could shadow it, so ordering does not matter here.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_i2v_api.py -q`
Expected: all pass.

- [ ] **Step 5: Format, type-check, full gate, commit**

```bash
venv/bin/black backend/api/i2v.py backend/services/i2v_service.py tests/test_i2v_api.py
make quality test
git add backend/api/i2v.py backend/services/i2v_service.py tests/test_i2v_api.py
git commit -m "feat(i2v): templates route; template_id on prompt, lint and generate

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

Then confirm the dev server reloaded cleanly: `tail -5 logs/server.log` and `curl -s localhost:8700/api/i2v/templates | head -c 300`.

---

### Task 9: Frontend — types, API, store

**Files:**
- Modify: `frontend/src/types/i2v.ts`, `frontend/src/api/i2v.ts`, `frontend/src/stores/i2v.ts`

**Interfaces:**
- Produces:
  - `interface I2vTemplateBeat { start_s; end_s; transition: 'continuous'|'cut'|'j_cut'; cast: string[]; speaker: string|null; camera: { shot_size; angle; camera_motion; camera_amplitude: string|null; camera_speed: string|null; lens: string|null }; note: string }`
  - `interface I2vTemplate { id; name; description; duration_s; look; soundscape_hint; roles: {id; note}[]; beats: I2vTemplateBeat[]; available: boolean; unavailable_reason: string|null }`
  - `I2vFormState.template_id: string | null`
  - `cadenceChips(t: I2vTemplate): { label: string; kind: 'continuous'|'cut'|'j_cut' }[]`
  - `listI2vTemplates(): Promise<I2vTemplate[]>`; `template_id?: string | null` on `generatePrompt`, `lintI2vPrompt`, `generateVideo` bodies
  - store: `templates: Ref<I2vTemplate[]>` loaded in `open()` (failure → `[]`, non-fatal)

- [ ] **Step 1: Types**

In `frontend/src/types/i2v.ts`, add `template_id: string | null` to `I2vFormState` (with the comment `// Découpage cadence the clip was made with; null = single take.`), and add:

```ts
export interface I2vTemplateBeat {
  start_s: number
  end_s: number
  transition: 'continuous' | 'cut' | 'j_cut'
  cast: string[]
  speaker: string | null
  camera: {
    shot_size: string
    angle: string
    camera_motion: string
    camera_amplitude: string | null
    camera_speed: string | null
    lens: string | null
  }
  note: string
}

/** One découpage template from data/i2v_templates/, as GET /api/i2v/templates returns it. */
export interface I2vTemplate {
  id: string
  name: string
  description: string
  duration_s: number
  look: string
  soundscape_hint: string
  roles: { id: string; note: string }[]
  beats: I2vTemplateBeat[]
  available: boolean
  unavailable_reason: string | null
}

const MOTION_GLYPH: Record<string, string> = {
  static: '·',
  push_in: '⇢',
  pull_out: '⇠',
  zoom_in: '+',
  zoom_out: '−',
  pan_left: '←',
  pan_right: '→',
  truck_left: '⇐',
  truck_right: '⇒',
  tilt_up: '↑',
  tilt_down: '↓',
  pedestal_up: '⇑',
  pedestal_down: '⇓',
  arc: '↻',
  tracking: '⇶',
  shake_slight: '≈',
  shake_strong: '≋',
  roll_cw: '↷',
  roll_ccw: '↶',
}

/**
 * The read-only cadence strip under the Cadence select: one chip per beat,
 * e.g. "0–4s · OTS MS · ⇒", "4–5s · CU · CUT", "5–10s · MS · J-CUT ⇠".
 * Pure, so it is reviewable by reading (there is no frontend test runner).
 */
export function cadenceChips(t: I2vTemplate): { label: string; kind: I2vTemplateBeat['transition'] }[] {
  return t.beats.map((b) => {
    const size = b.camera.angle === 'ots' ? `OTS ${b.camera.shot_size}` : b.camera.shot_size
    const how = b.transition === 'cut' ? 'CUT ' : b.transition === 'j_cut' ? 'J-CUT ' : ''
    const motion = MOTION_GLYPH[b.camera.camera_motion] ?? b.camera.camera_motion
    const speaker = b.speaker ? ` · ${b.speaker} speaks` : ''
    return {
      label: `${b.start_s}–${b.end_s}s · ${size} · ${how}${motion}${speaker}`,
      kind: b.transition,
    }
  })
}
```

- [ ] **Step 2: API**

In `frontend/src/api/i2v.ts`:

```ts
import type { I2vConfig, I2vFormState, I2vLintReport, I2vTemplate, I2vVideo } from '../types/i2v'

export function listI2vTemplates(): Promise<I2vTemplate[]> {
  return get('/i2v/templates')
}
```

Add `template_id?: string | null` to the body types of `generatePrompt`, `lintI2vPrompt` and `generateVideo`.

- [ ] **Step 3: Store**

In `frontend/src/stores/i2v.ts`:

```ts
import type { I2vConfig, I2vFormState, I2vJobChip, I2vTemplate, I2vVideo } from '../types/i2v'
import { fetchI2vConfig, listI2vTemplates, listI2vVideos, updateI2vFormState } from '../api/i2v'

  // The découpage library. Loaded once per open(); a failure leaves it
  // empty and the dialog offers only Single take.
  const templates = ref<I2vTemplate[]>([])
```

In `open()`, after the config load, add:

```ts
    try {
      templates.value = await listI2vTemplates()
    } catch {
      templates.value = []
    }
```

Export `templates` from the store's returned object.

- [ ] **Step 4: Type-check**

Run: `cd frontend && npm run build`
Expected: passes (nothing consumes the new pieces yet).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/i2v.ts frontend/src/api/i2v.ts frontend/src/stores/i2v.ts
git commit -m "feat(i2v): template types, API and store

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Frontend — the Cadence select, duration lock and cadence strip

**Files:**
- Modify: `frontend/src/components/dialogs/I2VDialog.vue`

**Interfaces:**
- Consumes: `store.templates`, `cadenceChips`, `I2vTemplate`, the `template_id` body fields (Task 9).

Read the file before editing: it holds the form refs (`idea`, `prompt`, `durationS`, …), `currentForm()`, `requestSignature()`, `loadClip()`, `onExpandPrompt()`, `runLint()`, `onGenerate()`, and the `<div class="params">` block with the Duration select.

- [ ] **Step 1: Script — state and derived values**

After `const loras = ref<LoraEntry[]>([])` add:

```ts
// Découpage cadence. '' = Single take (today's single-shot path). A
// template fixes the clip duration, so selecting one sets durationS and
// locks the Duration select; Single take unlocks it and keeps the value.
const templateId = ref<string>('')
const selectedTemplate = computed(
  () => store.templates.find((t) => t.id === templateId.value) ?? null,
)
const durationLocked = computed(() => selectedTemplate.value !== null)
const cadence = computed(() => (selectedTemplate.value ? cadenceChips(selectedTemplate.value) : []))
// Loading a clip whose template file has since been removed.
const missingTemplateNote = ref('')

watch(selectedTemplate, (t) => {
  if (t) durationS.value = t.duration_s
})
```

Import `cadenceChips` from `'../../types/i2v'`.

- [ ] **Step 2: Script — thread `template_id` through the form, signature, load, prompt, lint, generate**

- `currentForm()`: add `template_id: templateId.value || null`.
- `requestSignature()`: add `template: templateId.value || null` to the JSON.
- `loadClip(v)`: after the existing field loads, add:

  ```ts
  const tid = f.template_id ?? ''
  if (tid && !store.templates.some((t) => t.id === tid)) {
    templateId.value = ''
    missingTemplateNote.value = `This clip used cadence "${tid}", which is no longer in the library; loaded as Single take.`
  } else {
    templateId.value = tid
    missingTemplateNote.value = ''
  }
  ```

  Place it **before** `savedSnapshot = JSON.stringify(currentForm())` so the snapshot includes it, and note the `watch(selectedTemplate)` will set `durationS` on the next tick — so also set `durationS.value = f.duration_s` only when `f.duration_s != null` (already the case) and rely on the watcher for the template case; both agree because a clip's `duration_s` equals its template's.
- The autosave `watch([...])` array: add `templateId`.
- `onExpandPrompt()`: add `template_id: templateId.value || null` to the `generatePrompt` body.
- `runLint()`: add `template_id: templateId.value || null` to the `lintI2vPrompt` body.
- `onGenerate()`: add `template_id: templateId.value || null` to the `generateVideo` body.
- `savableFields(form)`: nothing to do — `template_id` is a string or null, always savable.

- [ ] **Step 3: Template — the select and strip**

Above the Idea `<label>` (inside the same left column), add:

```vue
<label class="fld">
  <span>Cadence</span>
  <select v-model="templateId">
    <option value="">Single take</option>
    <option
      v-for="t in store.templates"
      :key="t.id"
      :value="t.id"
      :disabled="!t.available"
      :title="t.unavailable_reason ?? t.description"
    >
      {{ t.name }} · {{ t.duration_s }}s{{ t.available ? '' : ' (unavailable)' }}
    </option>
  </select>
  <small v-if="selectedTemplate" class="dims-hint">{{ selectedTemplate.description }}</small>
  <small v-if="missingTemplateNote" class="dims-hint warn">{{ missingTemplateNote }}</small>
</label>
<div v-if="cadence.length" class="cadence" title="The shot plan this cadence fixes; the prompt is written to it">
  <span v-for="(c, i) in cadence" :key="i" class="cadence-chip" :class="c.kind">{{ c.label }}</span>
</div>
```

Change the Duration select to:

```vue
<label class="fld" :class="{ 'fld-off': durationLocked }" :title="durationLocked ? 'Set by the cadence' : ''">
  <span>Duration</span>
  <select v-model.number="durationS" :disabled="durationLocked">
    <option v-for="d in durations" :key="d" :value="d">{{ d }}s</option>
  </select>
  <small v-if="durationLocked" class="dims-hint">set by the cadence</small>
</label>
```

- [ ] **Step 4: Styles**

Append to the component's `<style scoped>`:

```css
.cadence { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 8px; }
.cadence-chip {
  font-size: 11px; padding: 2px 8px; border-radius: 10px; white-space: nowrap;
  color: var(--text-color-secondary); background: var(--surface-ground);
  border: 1px solid var(--surface-border);
}
.cadence-chip.cut { border-color: var(--primary-color, #6366f1); color: var(--text-color); }
.cadence-chip.j_cut { border-style: dashed; border-color: var(--primary-color, #6366f1); color: var(--text-color); }
.dims-hint.warn { color: #c33; }
```

- [ ] **Step 5: Type-check and build**

Run: `cd frontend && npm run build`
Expected: passes. Fix any `vue-tsc` complaint about `f.template_id` by confirming Task 9's `I2vFormState` change landed.

- [ ] **Step 6: Manual check in the browser (dev server)**

With `npm run dev` running and the backend up: open an image → Image to Video. Confirm: the Cadence select lists Single take + three templates (`melee_12` disabled unless 12 is in your configured durations); choosing Dialog sets Duration to 15 and locks it; the cadence strip shows four chips with the 2nd marked CUT and the 3rd J-CUT; Generate prompt produces `[Shot 2] At 00:04.000, …`; switching back to Single take unlocks Duration. Click a generated clip: its cadence reloads.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/dialogs/I2VDialog.vue
git commit -m "feat(i2v): Cadence picker with duration lock and cadence strip

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Documentation

**Files:**
- Create: `docs/i2v-templates.md`
- Modify: `docs/api-reference.md`, `docs/features.md`, `README.md` (Documentation section), `CLAUDE.md`

- [ ] **Step 1: `docs/i2v-templates.md`**

```markdown
[← Back to README](../README.md)

# Image-to-Video cadence templates

A cadence template fixes the shot structure of an Image-to-Video clip —
timing, shot size, angle, camera motion, cuts, who is on screen and who
speaks. The VLM fills in only the prose. Metascan then assembles the
MiniMax H3 prompt and lints it against the template.

Templates are JSON files under `data/i2v_templates/`, one per file, loaded
at first use and validated against the same camera vocabulary the
storyboard uses. A bad file fails loudly, naming the file and field.

## Beats and shots

A template lists **beats**. H3 **shots** are derived from them:

| `transition` | Effect |
|---|---|
| `continuous` | Stays in the current shot; rendered as `At MM:SS.mmm, the camera …`. |
| `cut` | Opens a new `[Shot n] At MM:SS.mmm, the shot cuts to …`. |
| `j_cut` | Opens a new shot, but the speaker's line is rendered first, "carrying over from the previous shot" (audio leads picture). |

The first beat is always `continuous`: it is anchored to the source image.
Use a cut when the shot introduces new information (subject, space,
viewpoint); use camera motion when only the distance changes — that is
the MiniMax guide's own rule.

## Fields

(the field table from the spec §4.1, verbatim)

## Roles and the source picture

A template declares roles (`A`, `B`). Before writing, the VLM binds each
to a person it sees in the source image and describes them once. If the
image shows fewer people than the roles need, the VLM invents the rest;
an invented person is kept out of the first shot (the only shot the
picture anchors) and enters at their next beat. The prompt's warnings say
when this happened.

## Duration

A template fixes the clip length. Choosing it in the dialog sets Duration
and locks the selector. A template whose `duration_s` is not one of your
configured `i2v.durations` is listed but unavailable.

## Worked examples

`dialog_ots_15.json`, `melee_12.json` and `intimate_15.json` ship with
metascan and cover the three transitions. Copy one to start a new
template; the `id` must equal the file name.
```

Paste the §4.1 field table from the spec into the Fields section.

- [ ] **Step 2: `docs/api-reference.md`**

Add to the routes table: `| GET | /api/i2v/templates | List cadence templates (with availability) |`. Add a section:

```markdown
### `GET /api/i2v/templates`
Every template under `data/i2v_templates/`, as
`[{id, name, description, duration_s, look, soundscape_hint, roles,
beats, available, unavailable_reason}, ...]`. `available` is false when
the template's fixed `duration_s` is not one of the configured
`i2v.durations`. A malformed template file is a **500** naming it.

`POST /api/i2v/prompt`, `POST /api/i2v/lint` and `POST /api/i2v/generate`
accept `template_id` (string or null). On `/prompt` an unknown id is
**404** and a `duration_s` that differs from the template's is **400**;
on `/lint` an unknown id adds a warning and lints as a single take; on
`/generate` it is recorded into the clip's `form_state` only.
```

Update the `form_state` field list in the `GET /api/i2v/videos` section to include `template_id`.

- [ ] **Step 3: `docs/features.md`**

Under Image to Video, add:

```markdown
- Pick a **Cadence**: Single take, or a découpage template that fixes the shot plan — timing, framing, camera and cuts — while the VLM writes only the action and lines. A template sets the clip duration. See [Image-to-Video cadence templates](i2v-templates.md).
```

- [ ] **Step 4: `README.md`**

Add `- [Image-to-Video cadence templates](docs/i2v-templates.md)` to the Documentation section.

- [ ] **Step 5: `CLAUDE.md`**

Add a bullet in the i2v section, after the form-state bullet:

```markdown
  **Découpage templates own an i2v clip's shot structure; the VLM fills
  only prose.** `metascan/core/i2v_templates.py` loads
  `data/i2v_templates/*.json` (id must equal the file stem; cached until
  `reload_i2v_templates()`), validated at load against the storyboard
  vocabularies imported from `storyboard_parse.py`/`storyboard_story.py`
  — never redeclare them; `pov` is excluded from angle and motion. Beats
  must tile `[0, duration_s]`; **shots are derived**: `cut`/`j_cut` open
  a `[Shot n] At MM:SS.mmm,`, `continuous` stays in the shot with an
  intra-shot timestamp (`shots_of`). The first beat is always
  `continuous`. `i2v_template_grammar` bakes role ids and beat count into
  the GBNF and makes `line` required exactly where a beat has a
  `speaker`. `assemble_i2v_template_prompt` renders the H3 document in
  code: first mention of a role → its `description`, after → its `tag`;
  `(S1)`/`(S2)` in first-line order; camera through
  `h3_compiler._CAMERA_PHRASES` with amplitude then speed; a `j_cut`
  puts the line before the cut with "the words carrying over from the
  previous shot". **Off-screen rule:** an unbound role (VLM said it is
  not in the picture) is dropped from Shot 1's framing (the only shot the
  picture anchors) and enters at its next beat; an unbound Shot-1 speaker
  speaks "Off-screen,". Assembly returns `(text, notes)` — the runner
  appends `notes` to the lint warnings. `lint_against_template` is
  text-only and warnings-only (shot count/numbering, Shot 1 has no
  timestamp, cut times increasing/inside duration/equal to the template,
  camera phrase per shot, a `<d>` per speaker beat); it cannot check role
  tags without the fill, so that is guaranteed by assembly for generated
  text and unchecked on hand edits. `lint_i2v_prompt(..., template=)`
  imports it at function level (import cycle). **`template_id = null` is
  the single-take path, byte-for-byte** — `tests/test_i2v_compiler.py::
  test_single_take_output_is_pinned` guards it; never route Single take
  through the template code. A template **fixes the duration**: `/prompt`
  400s on a mismatch, the dialog locks the Duration select. `template_id`
  is a `FORM_FIELDS` entry (null = single take) and rides `_job_meta["form"]`
  to ingest; `generate()` does not validate it against the library, since
  the prompt text already embodies the cadence and a renamed file must not
  block a render. `GET /api/i2v/templates` stamps `available` against
  `i2v.durations`. No aspect ratio (output follows the source) and no
  constraints/negative section (H3 has none) — by decision.
```

- [ ] **Step 6: Full gate and commit**

```bash
make quality test
cd frontend && npm run build && cd ..
git add docs/i2v-templates.md docs/api-reference.md docs/features.md README.md CLAUDE.md
git commit -m "docs: i2v cadence templates

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage.** §4 format/fields → Task 1 (+ Task 2 files). §4.2 shots vs beats → `shots_of`, Task 1; rendering, Task 4. §5.1 single take unchanged → Task 5 golden pin + Task 7 branch. §5.2 one VLM call, grammar shape → Task 3 + Task 7. §5.3 validation → Task 3. §5.4 off-screen rule → Task 4. §5.5 assembly rules → Task 4 (rules restated in the task). §5.6 lint → Task 5, with the tag check recorded as a text-only limitation. §6 API → Task 8; `form_state.template_id` → Task 6. §7 dialog → Tasks 9–10. §8 shipped templates → Task 2. §9 docs → Task 11. §10 tests → each task. §11 out of scope — nothing added.

**Placeholders.** `docs/i2v-templates.md` says "(the field table from the spec §4.1, verbatim)" — that is an instruction to paste, made explicit in the step. No TBD/TODO elsewhere.

**Type consistency.** `assemble_i2v_template_prompt(t, fill) -> Tuple[str, List[str]]` is used with that shape in Task 7. `lint_i2v_prompt(text, duration_s, template=)` matches Tasks 5, 7, 8. `I2vRequestError` message prefix `unknown i2v template` is produced by `get_i2v_template` (Task 1) and keyed on in Task 8. `cadenceChips` returns `{label, kind}` and Task 10 reads `c.kind`/`c.label`. `store.templates` is exported in Task 9 and read in Task 10. `FORM_FIELDS` gains `template_id` in Task 6 before Task 7 writes it via `build_form_state(template_id=)`.
