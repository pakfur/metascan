# Storyboard Story Engine (Phase V1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From a few-sentence premise, staged Qwen3-VL calls build a complete editable storyboard tree — outline → scenes → shots (panels) → timed beats — stored in SQLite and edited in StoryboardView.

**Architecture:** A new pure module `metascan/core/storyboard_story.py` (grammars, validators, prompt builders — mirroring `storyboard_parse.py`), a new `beats` table + three column adds, four staged `StoryboardRunner.compose_story` stages under the existing `_synth_lock`/semaphore idiom with `story_*` WS events, REST routes mirroring `/synthesize`, and frontend beats editing + an outline dialog.

**Tech Stack:** Python 3.11 / FastAPI / SQLite (sync + `asyncio.to_thread`), llama-server GBNF grammars via `VlmClient.generate_text`, Vue 3 `<script setup>` + Pinia + TypeScript.

**Spec:** `docs/superpowers/specs/2026-08-15-storyboard-story-engine-design.md`

## Global Constraints

- `make quality test` (flake8 + black --check + mypy strict on `metascan/core/*` + pytest) must pass before every commit; frontend tasks additionally `cd frontend && npm run build`.
- Tests never load a real VLM/CLIP/ComfyUI — fake objects only (existing suite convention).
- GBNF: `\-` is an invalid escape and SIGSEGVs llama-server; hyphens only as literal range operators inside character classes. Build grammars with `str.format()` on `{{`/`}}`-escaped templates, exactly like `PARSE_GRAMMAR` (`metascan/core/storyboard_parse.py:53-74`).
- DB access is synchronous under `self.lock`; service layer wraps with `asyncio.to_thread`.
- All new DB columns via `_idempotent_add_column` (`database_sqlite.py:18`); new tables via `CREATE TABLE IF NOT EXISTS` in `_init_database` next to the existing storyboard DDL (~line 556-679).
- Known pre-existing flake: `test_file_watcher_triggers_reload` fails in full-suite runs on WSL2 (passes in isolation) — not a regression signal.
- `metascan/core/meta_prompt_templates.py` has an unrelated uncommitted user change — never `git add` it.
- Prompt text lives in `data/meta_prompt.yml` (hot-reload store); grammars live in Python (deviation from spec §4.6, recorded in Task 2).

---

### Task 1: Beats table, column migrations, beat CRUD, tree embedding

**Files:**
- Modify: `metascan/core/database_sqlite.py` (DDL block ~line 649-679; updatable sets ~lines 1187-1240; `create_subject` line 1345; `create_panel` line 1488; `get_storyboard_tree` line 1786)
- Test: `tests/test_storyboard_beats_db.py` (create)

**Interfaces:**
- Consumes: existing `DatabaseManager` patterns (`self.lock`, `_get_connection`, `_idempotent_add_column`).
- Produces (later tasks rely on these exact signatures):
  - `create_beat(panel_id: int, *, action: str, sort_order: int = 0, duration_s: float = 4.0, camera_motion: Optional[str] = None, camera_amplitude: Optional[str] = None, camera_speed: Optional[str] = None, is_cut: int = 0, dialog: Optional[List[Dict[str, Any]]] = None, sound: Optional[str] = None) -> int`
  - `get_beat(beat_id: int) -> Optional[Dict[str, Any]]` (dialog JSON-decoded to a list)
  - `update_beat(beat_id: int, **fields) -> None` (bumps `updated_at`; `dialog` list is JSON-encoded)
  - `delete_beat(beat_id: int) -> bool`
  - `replace_panel_beats(panel_id: int, beats: List[Dict[str, Any]]) -> List[int]` (transactional delete+insert, beats dicts use the `create_beat` keyword names)
  - `create_subject(..., voice: Optional[str] = None, ...)`; `create_panel(..., duration_s: float = 12.0, ...)`
  - `_STORYBOARD_UPDATABLE` + `{"outline"}`, `_SUBJECT_UPDATABLE` + `{"voice"}`, `_PANEL_UPDATABLE` + `{"duration_s"}`, new `_BEAT_UPDATABLE`
  - `get_storyboard_tree`: each panel dict gains `"beats": [...]` ordered by `sort_order, id`, `"duration_s"`; storyboard dict gains `"outline"`; subjects gain `"voice"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_storyboard_beats_db.py`. Follow `tests/test_storyboard_db.py`'s fixture style (temp-dir `DatabaseManager`); read that file's fixture first and reuse it verbatim (it constructs `DatabaseManager(tmp_path / "test.db")` or similar — copy the exact fixture).

```python
"""Beat CRUD + migrations for the story engine (spec V1 §3)."""

import pytest

from metascan.core.database_sqlite import DatabaseManager


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(tmp_path / "test.db")


@pytest.fixture
def panel_id(db):
    sb = db.create_storyboard(
        name="B", target_model="sd", architecture="t2i", base_seed=1
    )
    scene = db.create_scene(sb, name="S1")
    return db.create_panel(scene, action="she opens the door")


def test_beat_crud_roundtrip(db, panel_id):
    bid = db.create_beat(
        panel_id,
        action="hand touches the handle",
        duration_s=3.5,
        camera_motion="push_in",
        camera_amplitude="small",
        camera_speed="slow",
        dialog=[{"subject_id": None, "voice": "low male voice",
                 "delivery": None, "language": "English", "text": "Wait."}],
        sound="hinge creak",
    )
    beat = db.get_beat(bid)
    assert beat["action"] == "hand touches the handle"
    assert beat["duration_s"] == 3.5
    assert beat["camera_motion"] == "push_in"
    assert beat["is_cut"] == 0
    assert beat["dialog"][0]["text"] == "Wait."
    old_updated = beat["updated_at"]
    db.update_beat(bid, action="hand grips the handle", dialog=[])
    beat2 = db.get_beat(bid)
    assert beat2["action"] == "hand grips the handle"
    assert beat2["dialog"] == []
    assert db.delete_beat(bid) is True
    assert db.get_beat(bid) is None
    assert db.delete_beat(bid) is False


def test_update_beat_rejects_unknown_field(db, panel_id):
    bid = db.create_beat(panel_id, action="x")
    with pytest.raises(ValueError, match="Not updatable"):
        db.update_beat(bid, panel_id=999)


def test_replace_panel_beats_is_transactional(db, panel_id):
    db.create_beat(panel_id, action="old one")
    ids = db.replace_panel_beats(
        panel_id,
        [
            {"action": "new a", "duration_s": 4.0, "sort_order": 0},
            {"action": "new b", "duration_s": 5.0, "sort_order": 1,
             "camera_motion": "static"},
        ],
    )
    assert len(ids) == 2
    tree_beats = [
        b["action"] for b in db.list_beats(panel_id)
    ]
    assert tree_beats == ["new a", "new b"]


def test_beats_cascade_with_panel(db, panel_id):
    bid = db.create_beat(panel_id, action="x")
    db.delete_panel(panel_id)
    assert db.get_beat(bid) is None


def test_tree_embeds_beats_and_new_columns(db, panel_id):
    db.create_beat(panel_id, action="b2", sort_order=1)
    db.create_beat(panel_id, action="b1", sort_order=0)
    sb_id = db.storyboard_id_for_panel(panel_id)
    db.update_storyboard(sb_id, outline='{"logline": "x"}')
    subj = db.create_subject(sb_id, name="Maya", description="d", voice="warm alto")
    db.update_subject(subj, voice="clear alto")
    db.update_panel(panel_id, duration_s=10.0)
    tree = db.get_storyboard_tree(sb_id)
    assert tree["outline"] == '{"logline": "x"}'
    assert tree["subjects"][0]["voice"] == "clear alto"
    panel = tree["scenes"][0]["panels"][0]
    assert panel["duration_s"] == 10.0
    assert [b["action"] for b in panel["beats"]] == ["b1", "b2"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source venv/bin/activate && pytest tests/test_storyboard_beats_db.py -v`
Expected: FAIL/ERROR — `create_beat` / `outline` column etc. do not exist.

- [ ] **Step 3: Implement**

In `_init_database`, immediately after the `panel_images` DDL + storyboard index block (~line 679), add:

```python
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS beats (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                panel_id    INTEGER NOT NULL
                            REFERENCES panels(id) ON DELETE CASCADE,
                sort_order  INTEGER NOT NULL DEFAULT 0,
                duration_s  REAL NOT NULL DEFAULT 4.0,
                action      TEXT NOT NULL,
                camera_motion    TEXT,
                camera_amplitude TEXT,
                camera_speed     TEXT,
                is_cut      INTEGER NOT NULL DEFAULT 0,
                dialog      TEXT NOT NULL DEFAULT '[]',
                sound       TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_beats_panel ON beats(panel_id)")
        _idempotent_add_column(
            conn, "storyboards", "outline",
            "ALTER TABLE storyboards ADD COLUMN outline TEXT",
        )
        _idempotent_add_column(
            conn, "storyboard_subjects", "voice",
            "ALTER TABLE storyboard_subjects ADD COLUMN voice TEXT",
        )
        _idempotent_add_column(
            conn, "panels", "duration_s",
            "ALTER TABLE panels ADD COLUMN duration_s REAL NOT NULL DEFAULT 12.0",
        )
```

Extend the updatable sets (`database_sqlite.py:1187-1240`): add `"outline"` to `_STORYBOARD_UPDATABLE`, `"voice"` to `_SUBJECT_UPDATABLE`, `"duration_s"` to `_PANEL_UPDATABLE`, and define:

```python
    _BEAT_UPDATABLE: ClassVar[frozenset] = frozenset(
        {
            "sort_order",
            "duration_s",
            "action",
            "camera_motion",
            "camera_amplitude",
            "camera_speed",
            "is_cut",
            "dialog",
            "sound",
        }
    )
```

Add `voice: Optional[str] = None` to `create_subject`'s keyword args and INSERT column list; add `duration_s: float = 12.0` to `create_panel` likewise. Then, after `delete_panel` (~line 1702), add a `# ---- Beats ----` section:

```python
    def create_beat(
        self,
        panel_id: int,
        *,
        action: str,
        sort_order: int = 0,
        duration_s: float = 4.0,
        camera_motion: Optional[str] = None,
        camera_amplitude: Optional[str] = None,
        camera_speed: Optional[str] = None,
        is_cut: int = 0,
        dialog: Optional[List[Dict[str, Any]]] = None,
        sound: Optional[str] = None,
    ) -> int:
        import json as _json

        with self.lock, self._get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO beats (panel_id, sort_order, duration_s, action, "
                "camera_motion, camera_amplitude, camera_speed, is_cut, "
                "dialog, sound) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    panel_id,
                    sort_order,
                    duration_s,
                    action,
                    camera_motion,
                    camera_amplitude,
                    camera_speed,
                    is_cut,
                    _json.dumps(list(dialog or [])),
                    sound,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    @staticmethod
    def _decode_beat_row(row: sqlite3.Row) -> Dict[str, Any]:
        import json as _json

        d = dict(row)
        try:
            d["dialog"] = _json.loads(d["dialog"] or "[]")
        except (ValueError, TypeError):
            d["dialog"] = []
        return d

    def get_beat(self, beat_id: int) -> Optional[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM beats WHERE id = ?", (beat_id,)
            ).fetchone()
            return self._decode_beat_row(row) if row is not None else None

    def list_beats(self, panel_id: int) -> List[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM beats WHERE panel_id = ? ORDER BY sort_order, id",
                (panel_id,),
            ).fetchall()
            return [self._decode_beat_row(r) for r in rows]

    def update_beat(self, beat_id: int, **fields: Any) -> None:
        import json as _json

        unknown = set(fields) - self._BEAT_UPDATABLE
        if unknown:
            raise ValueError(f"Not updatable on beats: {', '.join(sorted(unknown))}")
        if not fields:
            return
        if "dialog" in fields:
            fields["dialog"] = _json.dumps(list(fields["dialog"] or []))
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [beat_id]
        with self.lock, self._get_connection() as conn:
            conn.execute(
                f"UPDATE beats SET {assignments}, "
                "updated_at = datetime('now') WHERE id = ?",
                values,
            )
            conn.commit()

    def delete_beat(self, beat_id: int) -> bool:
        with self.lock, self._get_connection() as conn:
            cur = conn.execute("DELETE FROM beats WHERE id = ?", (beat_id,))
            conn.commit()
            return int(cur.rowcount) > 0

    def replace_panel_beats(
        self, panel_id: int, beats: List[Dict[str, Any]]
    ) -> List[int]:
        """Transactionally replace a panel's beats (compose stage 4)."""
        import json as _json

        with self.lock, self._get_connection() as conn:
            conn.execute("DELETE FROM beats WHERE panel_id = ?", (panel_id,))
            new_ids: List[int] = []
            for i, b in enumerate(beats):
                cur = conn.execute(
                    "INSERT INTO beats (panel_id, sort_order, duration_s, "
                    "action, camera_motion, camera_amplitude, camera_speed, "
                    "is_cut, dialog, sound) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        panel_id,
                        b.get("sort_order", i),
                        b.get("duration_s", 4.0),
                        b["action"],
                        b.get("camera_motion"),
                        b.get("camera_amplitude"),
                        b.get("camera_speed"),
                        int(b.get("is_cut", 0)),
                        _json.dumps(list(b.get("dialog") or [])),
                        b.get("sound"),
                    ),
                )
                new_ids.append(int(cur.lastrowid))
            conn.commit()
            return new_ids
```

In `get_storyboard_tree` (line 1786), after `panel["images"] = images`, add:

```python
                    panel["beats"] = [
                        self._decode_beat_row(r)
                        for r in conn.execute(
                            "SELECT * FROM beats WHERE panel_id = ? "
                            "ORDER BY sort_order, id",
                            (panel["id"],),
                        ).fetchall()
                    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_storyboard_beats_db.py tests/test_storyboard_db.py -v`
Expected: all PASS (existing storyboard DB tests must not regress).

- [ ] **Step 5: Quality gate + commit**

Run: `make quality test` — expected pass (modulo the known watcher flake).

```bash
git add metascan/core/database_sqlite.py tests/test_storyboard_beats_db.py
git commit -m "feat(storyboard): beats table, outline/voice/duration_s columns, beat CRUD"
```

---

### Task 2: `storyboard_story.py` — constants, grammars, validators, prompt builders + YAML prompts

**Files:**
- Create: `metascan/core/storyboard_story.py`
- Modify: `data/meta_prompt.yml` (append keys at end)
- Modify: `docs/superpowers/specs/2026-08-15-storyboard-story-engine-design.md` §4.6 (record the grammars-in-Python deviation)
- Test: `tests/test_storyboard_story.py` (create)

**Interfaces:**
- Consumes: `metascan.core.prompt_store.get_prompt_store()` (`.get(key) -> str`, raises `KeyError`); enum tuples from `storyboard_parse` (`SHOT_SIZE_VALUES`, `ANGLE_VALUES`, `LENS_VALUES`).
- Produces (Task 3 uses all of these):
  - Constants: `CAMERA_MOTION_VALUES`, `CAMERA_AMPLITUDE_VALUES = ("small", "large")`, `CAMERA_SPEED_VALUES = ("slow", "fast")`, `ARC_BEAT_VALUES = ("setup", "rising", "turn", "climax", "resolution")`, `STAGES = ("outline", "scenes", "shots", "beats")`
  - Grammars: `OUTLINE_GRAMMAR`, `SCENES_GRAMMAR`, `SHOTS_GRAMMAR`, `BEATS_GRAMMAR` (module constants, str)
  - `class StoryError(ValueError)`
  - `build_outline_user_prompt(premise: str, subjects: Sequence[Mapping[str, Any]]) -> str`
  - `build_scenes_user_prompt(outline_json: str) -> str`
  - `build_shots_user_prompt(outline_json: str, scene: Mapping[str, Any], subjects: Sequence[Mapping[str, Any]], prev_scene_name: Optional[str], next_scene_name: Optional[str]) -> str`
  - `build_beats_user_prompt(logline: str, scene: Mapping[str, Any], panel: Mapping[str, Any], subjects: Sequence[Mapping[str, Any]]) -> str`
  - `validate_outline_response(raw: str) -> Dict[str, Any]` — `{logline, tone, duration_target_s: float, subjects: [{name, description, voice|None}], arc: [{beat, summary}]}`; raises `StoryError`
  - `validate_scenes_response(raw: str) -> List[Dict[str, Any]]` — scene dicts with `name, subtitle, setting, location, time_of_day, mood, lighting, notes` (nullables `None`); raises `StoryError` if empty
  - `validate_shots_response(raw: str, roster: Mapping[str, int]) -> Tuple[List[Dict[str, Any]], List[str]]` — panel dicts `{shot_size, angle, lens, action, subject_ids: List[int], duration_s: float}` + warnings for dropped unknown subject names; raises `StoryError` if empty
  - `validate_beats_response(raw: str, roster: Mapping[str, int]) -> List[Dict[str, Any]]` — beat dicts matching `replace_panel_beats` input (dialog lines `{subject_id, voice, delivery, language, text}`); raises `StoryError` if empty
  - `rescale_beat_durations(beats: List[Dict[str, Any]], target_s: float, tolerance: float = 0.25) -> List[Dict[str, Any]]` — proportional in-place rescale (rounded to 0.1) when the sum is outside `target_s * (1 ± tolerance)`; no-op otherwise; returns the list
  - Module `__getattr__` resolving `STORY_OUTLINE_SYSTEM`, `STORY_SCENES_SYSTEM`, `STORY_SHOTS_SYSTEM`, `STORY_BEATS_SYSTEM` from the prompt store (mirrors `vlm_prompts.__getattr__`).

- [ ] **Step 1: Write the failing tests**

```python
"""Pure story-engine module: grammars, validators, duration math (spec V1 §4)."""

import json

import pytest

from metascan.core import storyboard_story as story
from metascan.core.storyboard_story import (
    BEATS_GRAMMAR,
    CAMERA_MOTION_VALUES,
    OUTLINE_GRAMMAR,
    SCENES_GRAMMAR,
    SHOTS_GRAMMAR,
    StoryError,
    rescale_beat_durations,
    validate_beats_response,
    validate_outline_response,
    validate_scenes_response,
    validate_shots_response,
)

ALL_GRAMMARS = (OUTLINE_GRAMMAR, SCENES_GRAMMAR, SHOTS_GRAMMAR, BEATS_GRAMMAR)


def test_grammars_have_no_invalid_hyphen_escape():
    for g in ALL_GRAMMARS:
        assert r"\-" not in g  # SIGSEGVs llama-server (CLAUDE.md)


def test_grammars_have_root_and_balanced_quotes():
    for g in ALL_GRAMMARS:
        assert g.startswith("root ::=")
        assert g.count('"') % 2 == 0


def test_validate_outline_happy_path():
    raw = json.dumps(
        {
            "logline": "A scavenger finds a live ship.",
            "tone": "tense, hopeful",
            "duration_target_s": 90,
            "subjects": [
                {"name": "Maya", "description": "late 20s, shaved head",
                 "voice": "clear alto"},
                {"name": "The Ship", "description": "rusted hull", "voice": None},
            ],
            "arc": [
                {"beat": "setup", "summary": "Maya scavenges alone."},
                {"beat": "resolution", "summary": "The ship lifts off."},
            ],
        }
    )
    out = validate_outline_response(raw)
    assert out["logline"].startswith("A scavenger")
    assert out["duration_target_s"] == 90.0
    assert out["subjects"][0]["voice"] == "clear alto"
    assert [a["beat"] for a in out["arc"]] == ["setup", "resolution"]


def test_validate_outline_rejects_garbage_and_empty_arc():
    with pytest.raises(StoryError):
        validate_outline_response("not json")
    with pytest.raises(StoryError):
        validate_outline_response(
            json.dumps({"logline": "x", "tone": "y",
                        "duration_target_s": 60, "subjects": [], "arc": []})
        )


def test_validate_scenes_happy_and_empty():
    raw = json.dumps([
        {"name": "Salvage yard", "subtitle": None, "setting": "twisted hulls",
         "location": "yard", "time_of_day": "dusk", "mood": "tense",
         "lighting": "amber haze", "notes": None},
    ])
    scenes = validate_scenes_response(raw)
    assert scenes[0]["name"] == "Salvage yard"
    assert scenes[0]["subtitle"] is None
    with pytest.raises(StoryError):
        validate_scenes_response("[]")


def test_validate_shots_resolves_roster_and_warns_on_unknown():
    raw = json.dumps([
        {"shot_size": "WS", "angle": "eye", "lens": "wide",
         "action": "Maya crosses the yard", "subjects": ["maya", "Ghost"],
         "duration_s": 12},
    ])
    panels, warnings = validate_shots_response(raw, {"maya": 7})
    assert panels[0]["subject_ids"] == [7]
    assert panels[0]["duration_s"] == 12.0
    assert any("Ghost" in w for w in warnings)
    with pytest.raises(StoryError):
        validate_shots_response("[]", {})


def test_validate_beats_camera_enums_and_dialog_roster():
    raw = json.dumps([
        {"duration_s": 4, "action": "she kneels",
         "camera_motion": "push_in", "camera_amplitude": "small",
         "camera_speed": "slow", "is_cut": False, "sound": "wind",
         "dialog": [{"subject": "Maya", "voice": None, "delivery": "whispered",
                     "language": "English", "text": "Hello, old girl."}]},
        {"duration_s": 5, "action": "hatch opens",
         "camera_motion": "not_a_motion", "camera_amplitude": None,
         "camera_speed": None, "is_cut": True, "sound": None, "dialog": []},
    ])
    beats = validate_beats_response(raw, {"maya": 7})
    assert beats[0]["camera_motion"] == "push_in"
    assert beats[0]["dialog"][0]["subject_id"] == 7
    assert beats[0]["dialog"][0]["text"] == "Hello, old girl."
    assert beats[1]["camera_motion"] is None  # unknown enum dropped to None
    assert beats[1]["is_cut"] == 1
    with pytest.raises(StoryError):
        validate_beats_response("[]", {})


def test_rescale_beat_durations_only_outside_tolerance():
    beats = [{"duration_s": 2.0}, {"duration_s": 2.0}]
    # sum 4.0 vs target 16.0 -> way out, rescaled to sum ~16
    rescale_beat_durations(beats, 16.0)
    assert abs(sum(b["duration_s"] for b in beats) - 16.0) < 0.3
    beats2 = [{"duration_s": 5.0}, {"duration_s": 6.0}]
    rescale_beat_durations(beats2, 12.0)  # sum 11 within ±25% of 12 -> untouched
    assert [b["duration_s"] for b in beats2] == [5.0, 6.0]


def test_camera_vocabulary_matches_spec():
    for v in ("push_in", "static", "roll_ccw", "tracking", "pov"):
        assert v in CAMERA_MOTION_VALUES


def test_system_prompts_resolve_from_store():
    for key in ("STORY_OUTLINE_SYSTEM", "STORY_SCENES_SYSTEM",
                "STORY_SHOTS_SYSTEM", "STORY_BEATS_SYSTEM"):
        assert isinstance(getattr(story, key), str)
        assert len(getattr(story, key)) > 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_storyboard_story.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `metascan/core/storyboard_story.py`**

```python
"""Story-engine stage plumbing: grammars, validators, prompt builders.

Pure module (no I/O beyond the prompt store): the VLM calls live in
storyboard_runner.compose_story. Mirrors storyboard_parse.py. System
prompts are YAML-backed (hot reload); grammars are built here from the
enum constants — deviation from spec §4.6, recorded there.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from metascan.core.storyboard_parse import (
    ANGLE_VALUES,
    LENS_VALUES,
    SHOT_SIZE_VALUES,
)

STAGES = ("outline", "scenes", "shots", "beats")

CAMERA_MOTION_VALUES = (
    "zoom_in", "zoom_out", "push_in", "pull_out", "pan_left", "pan_right",
    "truck_left", "truck_right", "tilt_up", "tilt_down", "pedestal_up",
    "pedestal_down", "arc", "tracking", "static", "shake_slight",
    "shake_strong", "pov", "roll_cw", "roll_ccw",
)
CAMERA_AMPLITUDE_VALUES = ("small", "large")
CAMERA_SPEED_VALUES = ("slow", "fast")
ARC_BEAT_VALUES = ("setup", "rising", "turn", "climax", "resolution")

_PROMPT_KEYS = frozenset(
    {
        "STORY_OUTLINE_SYSTEM",
        "STORY_SCENES_SYSTEM",
        "STORY_SHOTS_SYSTEM",
        "STORY_BEATS_SYSTEM",
    }
)


def __getattr__(name: str) -> str:
    if name in _PROMPT_KEYS:
        from metascan.core.prompt_store import get_prompt_store

        return get_prompt_store().get(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class StoryError(ValueError):
    """A stage response could not be validated."""


# -- Grammar building blocks (str.format templates; literal JSON braces
# are doubled). Hyphens never appear as "\-" (CLAUDE.md). ------------------

_COMMON_RULES = r"""nullable ::= string | "null"
number ::= [0-9]+ ("." [0-9]+)?
boolean ::= "true" | "false"
string ::= "\"" char* "\""
char ::= [^"\\\x7F\x00-\x1F] | "\\" (["\\bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])
ws ::= [ \t\n]*
"""


def _alts(values: Tuple[str, ...], with_null: bool = True) -> str:
    quoted = " | ".join('"\\"{0}\\""'.format(v) for v in values)
    return '{0} | "null"'.format(quoted) if with_null else quoted


_OUTLINE_TEMPLATE = (
    r"""root ::= "{{" ws "\"logline\"" ws ":" ws string ws "," ws "\"tone\"" ws ":" ws string ws "," ws "\"duration_target_s\"" ws ":" ws number ws "," ws "\"subjects\"" ws ":" ws subjects ws "," ws "\"arc\"" ws ":" ws arc ws "}}"
subjects ::= "[" ws subject (ws "," ws subject)* ws "]"
subject ::= "{{" ws "\"name\"" ws ":" ws string ws "," ws "\"description\"" ws ":" ws string ws "," ws "\"voice\"" ws ":" ws nullable ws "}}"
arc ::= "[" ws arcitem (ws "," ws arcitem)* ws "]"
arcitem ::= "{{" ws "\"beat\"" ws ":" ws arcbeat ws "," ws "\"summary\"" ws ":" ws string ws "}}"
arcbeat ::= {arcbeat_alts}
"""
    + _COMMON_RULES
)

OUTLINE_GRAMMAR = _OUTLINE_TEMPLATE.format(
    arcbeat_alts=_alts(ARC_BEAT_VALUES, with_null=False)
)

SCENES_GRAMMAR = (
    r"""root ::= "[" ws scene (ws "," ws scene)* ws "]"
scene ::= "{{" ws "\"name\"" ws ":" ws string ws "," ws "\"subtitle\"" ws ":" ws nullable ws "," ws "\"setting\"" ws ":" ws nullable ws "," ws "\"location\"" ws ":" ws nullable ws "," ws "\"time_of_day\"" ws ":" ws nullable ws "," ws "\"mood\"" ws ":" ws nullable ws "," ws "\"lighting\"" ws ":" ws nullable ws "," ws "\"notes\"" ws ":" ws nullable ws "}}"
"""
    + _COMMON_RULES
).format()

_SHOTS_TEMPLATE = (
    r"""root ::= "[" ws shot (ws "," ws shot)* ws "]"
shot ::= "{{" ws "\"shot_size\"" ws ":" ws shotsize ws "," ws "\"angle\"" ws ":" ws angle ws "," ws "\"lens\"" ws ":" ws lens ws "," ws "\"action\"" ws ":" ws string ws "," ws "\"subjects\"" ws ":" ws namelist ws "," ws "\"duration_s\"" ws ":" ws number ws "}}"
shotsize ::= {shotsize_alts}
angle ::= {angle_alts}
lens ::= {lens_alts}
namelist ::= "[" ws (string (ws "," ws string)*)? ws "]"
"""
    + _COMMON_RULES
)

SHOTS_GRAMMAR = _SHOTS_TEMPLATE.format(
    shotsize_alts=_alts(SHOT_SIZE_VALUES),
    angle_alts=_alts(ANGLE_VALUES),
    lens_alts=_alts(LENS_VALUES),
)

_BEATS_TEMPLATE = (
    r"""root ::= "[" ws beat (ws "," ws beat)* ws "]"
beat ::= "{{" ws "\"duration_s\"" ws ":" ws number ws "," ws "\"action\"" ws ":" ws string ws "," ws "\"camera_motion\"" ws ":" ws motion ws "," ws "\"camera_amplitude\"" ws ":" ws amplitude ws "," ws "\"camera_speed\"" ws ":" ws speed ws "," ws "\"is_cut\"" ws ":" ws boolean ws "," ws "\"sound\"" ws ":" ws nullable ws "," ws "\"dialog\"" ws ":" ws dialog ws "}}"
motion ::= {motion_alts}
amplitude ::= {amplitude_alts}
speed ::= {speed_alts}
dialog ::= "[" ws (line (ws "," ws line)*)? ws "]"
line ::= "{{" ws "\"subject\"" ws ":" ws nullable ws "," ws "\"voice\"" ws ":" ws nullable ws "," ws "\"delivery\"" ws ":" ws nullable ws "," ws "\"language\"" ws ":" ws string ws "," ws "\"text\"" ws ":" ws string ws "}}"
"""
    + _COMMON_RULES
)

BEATS_GRAMMAR = _BEATS_TEMPLATE.format(
    motion_alts=_alts(CAMERA_MOTION_VALUES),
    amplitude_alts=_alts(CAMERA_AMPLITUDE_VALUES),
    speed_alts=_alts(CAMERA_SPEED_VALUES),
)


# -- User-prompt builders --------------------------------------------------


def _roster_lines(subjects: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(
        f"- {s['name']}: {s['description']}" for s in subjects
    ) or "(none yet)"


def build_outline_user_prompt(
    premise: str, subjects: Sequence[Mapping[str, Any]]
) -> str:
    return (
        f"Premise:\n{premise}\n\nExisting subjects (reuse names verbatim; "
        f"do not re-describe them):\n{_roster_lines(subjects)}\n\n"
        "Write the story outline JSON."
    )


def build_scenes_user_prompt(outline_json: str) -> str:
    return f"Story outline:\n{outline_json}\n\nWrite the scene list JSON."


def build_shots_user_prompt(
    outline_json: str,
    scene: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
    prev_scene_name: Optional[str],
    next_scene_name: Optional[str],
) -> str:
    return (
        f"Story outline:\n{outline_json}\n\n"
        f"Scene to break into shots: {scene['name']}"
        f" — {scene.get('setting') or scene.get('location') or ''}\n"
        f"Mood: {scene.get('mood') or 'unspecified'}; "
        f"lighting: {scene.get('lighting') or 'unspecified'}; "
        f"time: {scene.get('time_of_day') or 'unspecified'}\n"
        f"Previous scene: {prev_scene_name or '(story opening)'}\n"
        f"Next scene: {next_scene_name or '(story ending)'}\n\n"
        f"Subjects (use these exact names):\n{_roster_lines(subjects)}\n\n"
        "Write the shot list JSON."
    )


def build_beats_user_prompt(
    logline: str,
    scene: Mapping[str, Any],
    panel: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
) -> str:
    return (
        f"Story logline: {logline}\n"
        f"Scene: {scene['name']} — mood {scene.get('mood') or 'unspecified'}\n"
        f"Shot: {panel['action']}\n"
        f"Target duration: {panel.get('duration_s') or 12.0} seconds\n"
        f"Subjects in shot (exact names):\n{_roster_lines(subjects)}\n\n"
        "Write the beat list JSON."
    )


# -- Validators ------------------------------------------------------------


def _loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as e:
        raise StoryError(f"response is not valid JSON: {e}") from e


def _clean(v: Any) -> Optional[str]:
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return None


def validate_outline_response(raw: str) -> Dict[str, Any]:
    data = _loads(raw)
    if not isinstance(data, dict):
        raise StoryError("outline response is not a JSON object")
    logline = _clean(data.get("logline"))
    tone = _clean(data.get("tone"))
    if not logline or not tone:
        raise StoryError("outline is missing logline/tone")
    try:
        duration = float(data.get("duration_target_s") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        duration = 90.0
    subjects = []
    for s in data.get("subjects") or []:
        if not isinstance(s, dict):
            continue
        name = _clean(s.get("name"))
        desc = _clean(s.get("description"))
        if name and desc:
            subjects.append(
                {"name": name, "description": desc, "voice": _clean(s.get("voice"))}
            )
    arc = [
        {"beat": a["beat"], "summary": _clean(a.get("summary")) or ""}
        for a in (data.get("arc") or [])
        if isinstance(a, dict) and a.get("beat") in ARC_BEAT_VALUES
    ]
    if not arc:
        raise StoryError("outline has no arc entries")
    return {
        "logline": logline,
        "tone": tone,
        "duration_target_s": duration,
        "subjects": subjects,
        "arc": arc,
    }


def validate_scenes_response(raw: str) -> List[Dict[str, Any]]:
    data = _loads(raw)
    if not isinstance(data, list):
        raise StoryError("scenes response is not a JSON array")
    scenes = []
    for sc in data:
        if not isinstance(sc, dict):
            continue
        name = _clean(sc.get("name"))
        if not name:
            continue
        scenes.append(
            {
                "name": name,
                "subtitle": _clean(sc.get("subtitle")),
                "setting": _clean(sc.get("setting")),
                "location": _clean(sc.get("location")),
                "time_of_day": _clean(sc.get("time_of_day")),
                "mood": _clean(sc.get("mood")),
                "lighting": _clean(sc.get("lighting")),
                "notes": _clean(sc.get("notes")),
            }
        )
    if not scenes:
        raise StoryError("no scenes in the response")
    return scenes


def validate_shots_response(
    raw: str, roster: Mapping[str, int]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    data = _loads(raw)
    if not isinstance(data, list):
        raise StoryError("shots response is not a JSON array")
    panels: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for p in data:
        if not isinstance(p, dict):
            continue
        action = _clean(p.get("action"))
        if not action:
            continue
        ids: List[int] = []
        for n in p.get("subjects") or []:
            if not isinstance(n, str):
                continue
            key = n.strip().lower()
            if key in roster:
                ids.append(roster[key])
            elif key:
                warnings.append(f"unknown subject {n!r} dropped")
        try:
            duration = float(p.get("duration_s") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        panels.append(
            {
                "shot_size": p.get("shot_size")
                if p.get("shot_size") in SHOT_SIZE_VALUES
                else None,
                "angle": p.get("angle") if p.get("angle") in ANGLE_VALUES else None,
                "lens": p.get("lens") if p.get("lens") in LENS_VALUES else None,
                "action": action,
                "subject_ids": ids,
                "duration_s": duration if duration > 0 else 12.0,
            }
        )
    if not panels:
        raise StoryError("no shots in the response")
    return panels, warnings


def validate_beats_response(
    raw: str, roster: Mapping[str, int]
) -> List[Dict[str, Any]]:
    data = _loads(raw)
    if not isinstance(data, list):
        raise StoryError("beats response is not a JSON array")
    beats: List[Dict[str, Any]] = []
    for b in data:
        if not isinstance(b, dict):
            continue
        action = _clean(b.get("action"))
        if not action:
            continue
        try:
            duration = float(b.get("duration_s") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        dialog = []
        for line in b.get("dialog") or []:
            if not isinstance(line, dict):
                continue
            text = _clean(line.get("text"))
            if not text:
                continue
            name = _clean(line.get("subject"))
            dialog.append(
                {
                    "subject_id": roster.get(name.strip().lower()) if name else None,
                    "voice": _clean(line.get("voice")),
                    "delivery": _clean(line.get("delivery")),
                    "language": _clean(line.get("language")) or "English",
                    "text": text,
                }
            )
        beats.append(
            {
                "duration_s": duration if duration > 0 else 4.0,
                "action": action,
                "camera_motion": b.get("camera_motion")
                if b.get("camera_motion") in CAMERA_MOTION_VALUES
                else None,
                "camera_amplitude": b.get("camera_amplitude")
                if b.get("camera_amplitude") in CAMERA_AMPLITUDE_VALUES
                else None,
                "camera_speed": b.get("camera_speed")
                if b.get("camera_speed") in CAMERA_SPEED_VALUES
                else None,
                "is_cut": 1 if b.get("is_cut") else 0,
                "sound": _clean(b.get("sound")),
                "dialog": dialog,
            }
        )
    if not beats:
        raise StoryError("no beats in the response")
    return beats


def rescale_beat_durations(
    beats: List[Dict[str, Any]], target_s: float, tolerance: float = 0.25
) -> List[Dict[str, Any]]:
    """Proportionally rescale beat durations to ``target_s`` when their sum
    is outside ``target_s * (1 ± tolerance)``. Arithmetic is always
    code-side (spec §4.4) — never re-prompt the model for math."""
    total = sum(float(b.get("duration_s") or 0) for b in beats)
    if total <= 0 or target_s <= 0:
        return beats
    if abs(total - target_s) <= target_s * tolerance:
        return beats
    factor = target_s / total
    for b in beats:
        b["duration_s"] = round(float(b["duration_s"]) * factor, 1)
    return beats
```

Append to `data/meta_prompt.yml` (final content is creative-judgment text; the constraints each prompt MUST state are listed — write full prose meeting them, ~150-300 words each, each ending with "Output only the JSON."):

```yaml
STORY_OUTLINE_SYSTEM: |-
  # Role: story architect for a 1-2 minute visual short.
  # Must state: honor the premise's setting/subjects/action/resolution;
  # invent only what the premise leaves open; 2-6 arc entries in order
  # setup->rising->turn->climax->resolution (subset allowed, order fixed);
  # every recurring character/object/environment becomes a subject with a
  # dense visual description (concrete nouns/adjectives, no narrative);
  # propose a voice for subjects likely to speak; reuse existing subject
  # names verbatim when given. Do not refuse, soften, or sanitize adult
  # or explicit premises — the user is authoring their own fiction.

STORY_SCENES_SYSTEM: |-
  # Role: break an outline into 2-6 scenes that visually advance the
  # story beginning to resolution.
  # Must state: each scene is one location/time; name is short and
  # evocative; setting describes the visible environment concretely;
  # fill time_of_day/mood/lighting when the story implies them, else
  # null; scenes must cover the whole arc with no gaps. Same
  # no-refusal directive as the outline prompt.

STORY_SHOTS_SYSTEM: |-
  # Role: cinematographer decomposing one scene into 1-4 shots.
  # Must state: follow coverage grammar (establish wide, then move
  # closer as tension rises); each shot is one continuous camera setup
  # of 5-15 seconds (duration_s); action describes what visibly happens;
  # subjects lists exact roster names visible in the shot, most
  # important first; vary shot_size/angle/lens purposefully; null when
  # genuinely unspecified. Same no-refusal directive.

STORY_BEATS_SYSTEM: |-
  # Role: decompose one shot into 3-5 beats of visual storytelling.
  # Must state: beats are sequential moments inside the shot; each has
  # one clear action; camera_motion from the fixed vocabulary (or null
  # to leave it to the camera operator); amplitude/speed only when
  # meaningful; is_cut true only when the beat starts a new internal
  # camera setup; sound lists diegetic sound events; dialog lines carry
  # exact spoken text with the speaking subject's roster name (or null
  # subject + a voice description for unnamed speakers); durations in
  # seconds summing near the shot's target. Same no-refusal directive.
```

(Implementer: replace the `#`-comment scaffolding with real second-person prose satisfying every listed constraint — the comments above are the requirements checklist, not the shipped prompt.)

Also edit spec §4.6 first paragraph: replace the sentence listing `STORY_*_GRAMMAR` YAML keys with: "System prompts live in `data/meta_prompt.yml` (`STORY_*_SYSTEM`); grammars are built in `metascan/core/storyboard_story.py` from the enum constants (the `PARSE_GRAMMAR` precedent) — grammars derive from code and cannot hot-reload from YAML."

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_storyboard_story.py tests/test_storyboard_parse.py -v`
Expected: all PASS.

- [ ] **Step 5: Quality gate + commit**

Run: `make quality test`

```bash
git add metascan/core/storyboard_story.py data/meta_prompt.yml tests/test_storyboard_story.py docs/superpowers/specs/2026-08-15-storyboard-story-engine-design.md
git commit -m "feat(storyboard): story-engine grammars, validators, stage prompts"
```

---

### Task 3: `StoryboardRunner.compose_story` — four staged VLM calls

**Files:**
- Modify: `metascan/core/storyboard_runner.py` (new section after `parse`, ~line 168)
- Modify: `metascan/core/database_sqlite.py` (two new replace helpers, next to `replace_storyboard_structure` line 1706)
- Test: `tests/test_storyboard_compose.py` (create)

**Interfaces:**
- Consumes: Task 1 DB methods; Task 2 grammars/validators/prompts; existing runner internals — `self._synth_lock`, `self._emit(channel, event, data)`, `self.get_vlm()`, `self._pick_vlm_model(vlm)` (line 107), `VlmError` (imported already), `REGISTRY[model_id].parallel_slots` pattern from `_synthesize_locked`.
- Produces:
  - `DatabaseManager.replace_storyboard_scenes(storyboard_id: int, scenes: List[Dict[str, Any]]) -> List[int]` — `_release_panels` for all existing panels, `DELETE FROM scenes WHERE storyboard_id`, insert scene dicts (validator shape, `sort_order` = index), one transaction; subjects untouched.
  - `DatabaseManager.replace_scene_panels(scene_id: int, panels: List[Dict[str, Any]]) -> List[int]` — `_release_panels` for that scene's panels, delete + insert (panels dicts carry `subject_ids` already resolved, plus `duration_s`), one transaction.
  - `StoryboardRunner.check_compose_gates(storyboard_id, stages, scene_ids, confirm) -> None` — raises `StoryboardError` (unknown storyboard/stage, missing premise) or `ConfirmRequiredError`; the route calls this synchronously before 202.
  - `StoryboardRunner.compose_story(storyboard_id: int, *, stages: Sequence[str] = STAGES, scene_ids: Optional[List[int]] = None, panel_ids: Optional[List[int]] = None, confirm: bool = False) -> Dict[str, int]` — returns per-stage counts; emits `story_progress {storyboard_id, stage, done, total}` / `story_stage_complete {storyboard_id, stage}` / exactly one of `story_complete {storyboard_id, counts}` or `story_error {storyboard_id, stage, error}` (re-raising after error).

- [ ] **Step 1: Write the failing tests**

Read `tests/test_storyboard_runner.py` first and reuse its fake-VLM/fake-db fixture idioms. Core cases (write with that file's exact fixture style; the `FakeVlm` below shows required behavior):

```python
"""compose_story staging against a scripted fake VLM (spec V1 §4)."""

import asyncio
import json

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.storyboard_runner import (
    ConfirmRequiredError,
    StoryboardError,
    StoryboardRunner,
)

OUTLINE = {
    "logline": "L", "tone": "T", "duration_target_s": 60,
    "subjects": [{"name": "Maya", "description": "desc", "voice": None}],
    "arc": [{"beat": "setup", "summary": "s"}],
}
SCENES = [{"name": "Yard", "subtitle": None, "setting": "hulls",
           "location": None, "time_of_day": "dusk", "mood": "tense",
           "lighting": None, "notes": None}]
SHOTS = [{"shot_size": "WS", "angle": "eye", "lens": None,
          "action": "Maya crosses", "subjects": ["Maya"], "duration_s": 10}]
BEATS = [{"duration_s": 5, "action": "a1", "camera_motion": "static",
          "camera_amplitude": None, "camera_speed": None, "is_cut": False,
          "sound": None, "dialog": []},
         {"duration_s": 5, "action": "a2", "camera_motion": None,
          "camera_amplitude": None, "camera_speed": None, "is_cut": False,
          "sound": None, "dialog": []}]


class FakeVlm:
    model_id = "qwen3vl-30b-a3b"

    def __init__(self):
        self.calls = []

    async def ensure_started(self, model_id):
        pass

    async def generate_text(self, *, system_prompt, user_prompt,
                            grammar=None, temperature=0.6,
                            max_tokens=250, timeout=120.0):
        # Dispatch on the grammar object — system prompts share words
        # ("shot" appears in the beats prompt), so substring matching on
        # them misroutes.
        from metascan.core import storyboard_story as story

        self.calls.append(grammar)
        if grammar == story.OUTLINE_GRAMMAR:
            return json.dumps(OUTLINE)
        if grammar == story.SCENES_GRAMMAR:
            return json.dumps(SCENES)
        if grammar == story.SHOTS_GRAMMAR:
            return json.dumps(SHOTS)
        return json.dumps(BEATS)


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(tmp_path / "t.db")


@pytest.fixture
def runner(db, tmp_path):
    r = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: None, output_root=tmp_path
    )
    return r


def _board(db, premise="A scavenger finds a ship."):
    sb = db.create_storyboard(
        name="B", target_model="sd", architecture="t2i", base_seed=1
    )
    db.update_storyboard(sb, source_text=premise)
    return sb


def test_compose_requires_vlm(db, runner):
    sb = _board(db)
    with pytest.raises(StoryboardError, match="VLM"):
        asyncio.run(runner.compose_story(sb))


def test_full_cascade_builds_tree_and_emits(db, tmp_path):
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    events = []
    runner.on_event(lambda ch, ev, d: events.append((ch, ev, d)))
    sb = _board(db)
    counts = asyncio.run(runner.compose_story(sb))
    tree = db.get_storyboard_tree(sb)
    assert json.loads(tree["outline"])["logline"] == "L"
    assert tree["subjects"][0]["name"] == "Maya"
    assert len(tree["scenes"]) == 1
    panel = tree["scenes"][0]["panels"][0]
    assert panel["subject_ids"] == [tree["subjects"][0]["id"]]
    assert panel["duration_s"] == 10.0
    assert [b["action"] for b in panel["beats"]] == ["a1", "a2"]
    assert counts["scenes"] == 1 and counts["beats"] == 2
    names = [e[1] for e in events if e[0] == "storyboard"]
    assert "story_complete" in names
    assert names.count("story_stage_complete") == 4


def test_outline_confirm_gate(db, tmp_path):
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    sb = _board(db)
    asyncio.run(runner.compose_story(sb, stages=("outline",)))
    with pytest.raises(ConfirmRequiredError):
        asyncio.run(runner.check_compose_gates(sb, ("outline",), None, False))
    # confirm=True passes the gate and re-runs
    asyncio.run(runner.compose_story(sb, stages=("outline",), confirm=True))


def test_missing_premise_rejected(db, tmp_path):
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    sb = _board(db, premise="")
    with pytest.raises(StoryboardError, match="premise"):
        asyncio.run(runner.check_compose_gates(sb, ("outline",), None, False))


def test_beats_only_rerun_replaces_beats(db, tmp_path):
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    sb = _board(db)
    asyncio.run(runner.compose_story(sb))
    tree = db.get_storyboard_tree(sb)
    pid = tree["scenes"][0]["panels"][0]["id"]
    db.update_beat(tree["scenes"][0]["panels"][0]["beats"][0]["id"], action="edited")
    asyncio.run(runner.compose_story(sb, stages=("beats",), panel_ids=[pid]))
    fresh = db.get_storyboard_tree(sb)
    assert [b["action"] for b in fresh["scenes"][0]["panels"][0]["beats"]] == [
        "a1", "a2",
    ]


def test_story_error_emitted_and_reraised(db, tmp_path):
    class BrokenVlm(FakeVlm):
        async def generate_text(self, **kw):
            return "not json"

    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: BrokenVlm(), output_root=tmp_path
    )
    events = []
    runner.on_event(lambda ch, ev, d: events.append((ch, ev, d)))
    sb = _board(db)
    with pytest.raises(Exception):
        asyncio.run(runner.compose_story(sb))
    errs = [e for e in events if e[1] == "story_error"]
    assert len(errs) == 1 and errs[0][2]["stage"] == "outline"
```

Note: beat durations 5+5=10 are within ±25% of `duration_s=10`, so no rescale interferes with the assertions.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_storyboard_compose.py -v` — Expected: FAIL (`compose_story` missing).

- [ ] **Step 3: Implement**

DB helpers in `database_sqlite.py`, after `replace_storyboard_structure` (line 1784):

```python
    def replace_storyboard_scenes(
        self, storyboard_id: int, scenes: List[Dict[str, Any]]
    ) -> List[int]:
        """Destructively replace all scenes (compose stage 2); subjects are
        untouched. Releases panel media/jobs first — see _release_panels."""
        with self.lock, self._get_connection() as conn:
            panel_ids = self._panel_ids_for_storyboard(conn, storyboard_id)
            self._release_panels(conn, panel_ids)
            conn.execute(
                "DELETE FROM scenes WHERE storyboard_id = ?", (storyboard_id,)
            )
            new_ids: List[int] = []
            for i, sc in enumerate(scenes):
                cur = conn.execute(
                    "INSERT INTO scenes (storyboard_id, sort_order, name, "
                    "subtitle, setting, location, time_of_day, mood, "
                    "lighting, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        storyboard_id,
                        i,
                        sc["name"],
                        sc.get("subtitle"),
                        sc.get("setting"),
                        sc.get("location"),
                        sc.get("time_of_day"),
                        sc.get("mood"),
                        sc.get("lighting"),
                        sc.get("notes"),
                    ),
                )
                new_ids.append(int(cur.lastrowid))
            conn.execute(
                "UPDATE storyboards SET updated_at = datetime('now') "
                "WHERE id = ?",
                (storyboard_id,),
            )
            conn.commit()
            return new_ids

    def replace_scene_panels(
        self, scene_id: int, panels: List[Dict[str, Any]]
    ) -> List[int]:
        """Destructively replace one scene's panels (compose stage 3).
        ``panels`` carry resolved subject_ids + duration_s."""
        import json as _json

        with self.lock, self._get_connection() as conn:
            old_ids = [
                int(r["id"])
                for r in conn.execute(
                    "SELECT id FROM panels WHERE scene_id = ?", (scene_id,)
                ).fetchall()
            ]
            self._release_panels(conn, old_ids)
            conn.execute("DELETE FROM panels WHERE scene_id = ?", (scene_id,))
            new_ids: List[int] = []
            for i, p in enumerate(panels):
                cur = conn.execute(
                    "INSERT INTO panels (scene_id, sort_order, shot_size, "
                    "angle, lens, action, subject_ids, duration_s) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        scene_id,
                        i,
                        p.get("shot_size"),
                        p.get("angle"),
                        p.get("lens"),
                        p["action"],
                        _json.dumps(list(p.get("subject_ids") or [])),
                        p.get("duration_s", 12.0),
                    ),
                )
                new_ids.append(int(cur.lastrowid))
            conn.commit()
            return new_ids
```

Runner additions (`storyboard_runner.py`, new section after `parse`; import at top: `from metascan.core import storyboard_story as story`):

```python
    # ---- compose (story engine) --------------------------------------

    async def check_compose_gates(
        self,
        storyboard_id: int,
        stages: Sequence[str],
        scene_ids: Optional[List[int]],
        confirm: bool,
    ) -> None:
        """Synchronous-shaped gate check so the route can 409 before the
        202 fire-and-forget task starts. Small TOCTOU window accepted."""
        unknown = set(stages) - set(story.STAGES)
        if unknown:
            raise StoryboardError(f"unknown stages: {', '.join(sorted(unknown))}")
        tree = await asyncio.to_thread(self.db.get_storyboard_tree, storyboard_id)
        if tree is None:
            raise StoryboardError(f"no storyboard with id {storyboard_id}")
        if "outline" in stages and not (tree.get("source_text") or "").strip():
            raise StoryboardError("storyboard has no premise (source_text)")
        if confirm:
            return
        if "outline" in stages and tree.get("outline"):
            raise ConfirmRequiredError(
                "storyboard already has an outline — pass confirm=true"
            )
        if "scenes" in stages and tree["scenes"]:
            raise ConfirmRequiredError(
                "storyboard already has scenes; rebuilding destroys panel "
                "identity — pass confirm=true"
            )
        if "shots" in stages:
            targets = [
                s for s in tree["scenes"]
                if scene_ids is None or s["id"] in scene_ids
            ]
            if any(s["panels"] for s in targets):
                raise ConfirmRequiredError(
                    "target scenes already have shots — pass confirm=true"
                )

    async def compose_story(
        self,
        storyboard_id: int,
        *,
        stages: Sequence[str] = story.STAGES,
        scene_ids: Optional[List[int]] = None,
        panel_ids: Optional[List[int]] = None,
        confirm: bool = False,
    ) -> Dict[str, int]:
        stage = "outline"
        try:
            counts = await self._compose_locked(
                storyboard_id,
                stages=stages,
                scene_ids=scene_ids,
                panel_ids=panel_ids,
                confirm=confirm,
            )
        except Exception as exc:
            stage = getattr(exc, "_compose_stage", stage)
            self._emit(
                "storyboard",
                "story_error",
                {"storyboard_id": storyboard_id, "stage": stage,
                 "error": str(exc)},
            )
            raise
        self._emit(
            "storyboard",
            "story_complete",
            {"storyboard_id": storyboard_id, "counts": counts},
        )
        return counts

    async def _compose_locked(
        self,
        storyboard_id: int,
        *,
        stages: Sequence[str],
        scene_ids: Optional[List[int]],
        panel_ids: Optional[List[int]],
        confirm: bool,
    ) -> Dict[str, int]:
        vlm = self.get_vlm()
        if vlm is None:
            raise StoryboardError("no VLM client — composing requires a VLM")
        await self.check_compose_gates(storyboard_id, stages, scene_ids, confirm)

        run_stages = [s for s in story.STAGES if s in set(stages)]
        counts: Dict[str, int] = {}
        current = "outline"
        try:
            async with self._synth_lock:
                model_id = self._pick_vlm_model(vlm)
                await vlm.ensure_started(model_id)
                from metascan.core.vlm_models import REGISTRY

                slots = 2
                spec = REGISTRY.get(model_id)
                if spec is not None:
                    slots = spec.parallel_slots
                sem = asyncio.Semaphore(slots)

                for current in run_stages:
                    n = await self._run_stage(
                        current, vlm, sem, storyboard_id, scene_ids, panel_ids
                    )
                    counts[current] = n
                    self._emit(
                        "storyboard",
                        "story_stage_complete",
                        {"storyboard_id": storyboard_id, "stage": current},
                    )
        except Exception as exc:
            exc._compose_stage = current  # type: ignore[attr-defined]
            raise
        return counts

    async def _run_stage(
        self,
        stage: str,
        vlm: Any,
        sem: asyncio.Semaphore,
        storyboard_id: int,
        scene_ids: Optional[List[int]],
        panel_ids: Optional[List[int]],
    ) -> int:
        tree = await asyncio.to_thread(self.db.get_storyboard_tree, storyboard_id)
        assert tree is not None
        roster = {
            s["name"].strip().lower(): int(s["id"]) for s in tree["subjects"]
        }

        def progress(done: int, total: int) -> None:
            self._emit(
                "storyboard",
                "story_progress",
                {"storyboard_id": storyboard_id, "stage": stage,
                 "done": done, "total": total},
            )

        if stage == "outline":
            progress(0, 1)
            raw = await vlm.generate_text(
                system_prompt=story.STORY_OUTLINE_SYSTEM,
                user_prompt=story.build_outline_user_prompt(
                    tree["source_text"], tree["subjects"]
                ),
                grammar=story.OUTLINE_GRAMMAR,
                temperature=0.7,
                max_tokens=1500,
                timeout=600.0,
            )
            outline = story.validate_outline_response(raw)
            await asyncio.to_thread(
                self.db.update_storyboard,
                storyboard_id,
                outline=json.dumps(outline),
            )
            created = 0
            for i, subj in enumerate(outline["subjects"]):
                if subj["name"].strip().lower() in roster:
                    continue  # user's existing description wins
                await asyncio.to_thread(
                    self.db.create_subject,
                    storyboard_id,
                    name=subj["name"],
                    description=subj["description"],
                    voice=subj.get("voice"),
                    sort_order=len(roster) + created,
                )
                created += 1
            progress(1, 1)
            return 1

        outline_json = tree.get("outline") or ""
        if not outline_json:
            raise StoryboardError(f"stage {stage!r} needs an outline first")

        if stage == "scenes":
            progress(0, 1)
            raw = await vlm.generate_text(
                system_prompt=story.STORY_SCENES_SYSTEM,
                user_prompt=story.build_scenes_user_prompt(outline_json),
                grammar=story.SCENES_GRAMMAR,
                temperature=0.7,
                max_tokens=1200,
                timeout=300.0,
            )
            scenes = story.validate_scenes_response(raw)
            await asyncio.to_thread(
                self.db.replace_storyboard_scenes, storyboard_id, scenes
            )
            progress(1, 1)
            return len(scenes)

        if stage == "shots":
            targets = [
                (i, s)
                for i, s in enumerate(tree["scenes"])
                if scene_ids is None or s["id"] in scene_ids
            ]
            total = len(targets)
            done = 0
            lock = asyncio.Lock()
            made = 0

            async def _shots_for(idx: int, scene: Dict[str, Any]) -> int:
                nonlocal done
                prev_name = tree["scenes"][idx - 1]["name"] if idx > 0 else None
                next_name = (
                    tree["scenes"][idx + 1]["name"]
                    if idx + 1 < len(tree["scenes"])
                    else None
                )
                async with sem:
                    raw = await vlm.generate_text(
                        system_prompt=story.STORY_SHOTS_SYSTEM,
                        user_prompt=story.build_shots_user_prompt(
                            outline_json, scene, tree["subjects"],
                            prev_name, next_name,
                        ),
                        grammar=story.SHOTS_GRAMMAR,
                        temperature=0.6,
                        max_tokens=800,
                        timeout=300.0,
                    )
                panels, warnings = story.validate_shots_response(raw, roster)
                for w in warnings:
                    logger.warning("compose shots (%s): %s", scene["name"], w)
                await asyncio.to_thread(
                    self.db.replace_scene_panels, scene["id"], panels
                )
                async with lock:
                    done += 1
                    progress(done, total)
                return len(panels)

            results = await asyncio.gather(
                *(_shots_for(i, s) for i, s in targets)
            )
            made = sum(results)
            return made

        # stage == "beats"
        work = [
            (scene, panel)
            for scene in tree["scenes"]
            for panel in scene["panels"]
            if panel_ids is None or panel["id"] in panel_ids
        ]
        logline = ""
        try:
            logline = json.loads(outline_json).get("logline", "")
        except (TypeError, ValueError):
            pass
        total = len(work)
        done = 0
        lock = asyncio.Lock()

        async def _beats_for(scene: Dict[str, Any], panel: Dict[str, Any]) -> int:
            nonlocal done
            subjects = [
                s for s in tree["subjects"] if s["id"] in panel["subject_ids"]
            ]
            async with sem:
                raw = await vlm.generate_text(
                    system_prompt=story.STORY_BEATS_SYSTEM,
                    user_prompt=story.build_beats_user_prompt(
                        logline, scene, panel, subjects
                    ),
                    grammar=story.BEATS_GRAMMAR,
                    temperature=0.6,
                    max_tokens=900,
                    timeout=300.0,
                )
            beats = story.validate_beats_response(raw, roster)
            story.rescale_beat_durations(
                beats, float(panel.get("duration_s") or 12.0)
            )
            await asyncio.to_thread(
                self.db.replace_panel_beats, panel["id"], beats
            )
            async with lock:
                done += 1
                progress(done, total)
            return len(beats)

        results = await asyncio.gather(*(_beats_for(s, p) for s, p in work))
        return sum(results)
```

(`json` and `logger` are already imported/defined in the module; verify and add `import json` if absent.)

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_storyboard_compose.py tests/test_storyboard_runner.py -v` — Expected: all PASS.

- [ ] **Step 5: Quality gate + commit**

Run: `make quality test`

```bash
git add metascan/core/storyboard_runner.py metascan/core/database_sqlite.py tests/test_storyboard_compose.py
git commit -m "feat(storyboard): compose_story staged synthesis with story_* WS events"
```

---

### Task 4: Service + API routes (compose, beats CRUD, PATCH extensions)

**Files:**
- Modify: `backend/services/storyboard_service.py` (beat wrappers)
- Modify: `backend/api/storyboard.py`
- Test: `tests/test_storyboard_compose_api.py` (create)

**Interfaces:**
- Consumes: Task 1 DB beat methods; Task 3 `check_compose_gates` / `compose_story`; existing route plumbing (`_require_runner` 503 pattern, `_service()`, `_reject_null_for_required`, `ConfirmRequiredError` → 409 mapping used by `/parse`).
- Produces (frontend Task 5 calls these):
  - `POST /api/storyboard/{id}/compose` → 202 `{"status": "started"}`; 409 `{code: "confirm_required"}`; 400 unknown stage / no premise; 503 no runner/VLM.
  - `POST /api/storyboard/panels/{panel_id}/beats` → `{"id": int}`; 404 unknown panel.
  - `PATCH /api/storyboard/beats/{beat_id}` → updated beat dict; 404; 400 null-for-required.
  - `DELETE /api/storyboard/beats/{beat_id}` → `{"status": "deleted"}`; 404.
  - `StoryboardPatch` + `outline: Optional[str]` (nullable-clearable); `PanelPatch` + `duration_s: Optional[float]` (in `_PANEL_NOT_NULLABLE`); `SubjectPatch`/`SubjectCreate` + `voice: Optional[str]` (nullable).

- [ ] **Step 1: Write the failing tests**

Read `tests/test_storyboard_api.py` first; reuse its app/TestClient/temp-DB fixture verbatim (it wires a real `DatabaseManager` and stubs the runner via `set_storyboard_runner`). Cases:

```python
"""Route contracts for compose + beats (spec V1 §5)."""

# Reuse the client/db fixtures from tests/test_storyboard_api.py
# (import or copy them exactly — same app factory, same runner stubbing).


class StubRunner:
    def __init__(self):
        self.compose_calls = []
        self.gate_error = None

    async def check_compose_gates(self, sb_id, stages, scene_ids, confirm):
        if self.gate_error:
            raise self.gate_error

    async def compose_story(self, sb_id, **kw):
        self.compose_calls.append((sb_id, kw))
        return {"outline": 1}


def test_compose_returns_202_and_fires_task(client, stub_runner, board_id):
    r = client.post(f"/api/storyboard/{board_id}/compose", json={})
    assert r.status_code == 202
    assert r.json() == {"status": "started"}


def test_compose_409_on_confirm_required(client, stub_runner, board_id):
    from metascan.core.storyboard_runner import ConfirmRequiredError
    stub_runner.gate_error = ConfirmRequiredError("has outline")
    r = client.post(f"/api/storyboard/{board_id}/compose", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "confirm_required"


def test_compose_400_on_unknown_stage(client, stub_runner, board_id):
    from metascan.core.storyboard_runner import StoryboardError
    stub_runner.gate_error = StoryboardError("unknown stages: bogus")
    r = client.post(
        f"/api/storyboard/{board_id}/compose", json={"stages": ["bogus"]}
    )
    assert r.status_code == 400


def test_beat_crud_routes(client, panel_id):
    r = client.post(
        f"/api/storyboard/panels/{panel_id}/beats",
        json={"action": "she kneels", "duration_s": 3.0},
    )
    assert r.status_code == 200
    bid = r.json()["id"]
    r = client.patch(
        f"/api/storyboard/beats/{bid}",
        json={"action": "she stands",
              "dialog": [{"subject_id": None, "voice": "low voice",
                          "delivery": None, "language": "English",
                          "text": "Up."}]},
    )
    assert r.status_code == 200
    assert r.json()["action"] == "she stands"
    assert r.json()["dialog"][0]["text"] == "Up."
    r = client.patch(f"/api/storyboard/beats/{bid}", json={"action": None})
    assert r.status_code == 400  # null for NOT NULL column
    r = client.delete(f"/api/storyboard/beats/{bid}")
    assert r.json() == {"status": "deleted"}
    assert client.delete(f"/api/storyboard/beats/{bid}").status_code == 404


def test_beat_create_404_on_unknown_panel(client):
    r = client.post(
        "/api/storyboard/panels/999999/beats", json={"action": "x"}
    )
    assert r.status_code == 404


def test_patch_extensions(client, board_id, panel_id, subject_id):
    assert client.patch(
        f"/api/storyboard/{board_id}", json={"outline": '{"logline": "x"}'}
    ).status_code == 200
    assert client.patch(
        f"/api/storyboard/panels/{panel_id}", json={"duration_s": 9.5}
    ).json()["duration_s"] == 9.5
    assert client.patch(
        f"/api/storyboard/panels/{panel_id}", json={"duration_s": None}
    ).status_code == 400
    assert client.patch(
        f"/api/storyboard/subjects/{subject_id}", json={"voice": "warm alto"}
    ).status_code == 200
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_storyboard_compose_api.py -v`

- [ ] **Step 3: Implement**

`StoryboardService` additions (each a thin `asyncio.to_thread` wrapper, matching the file's style): `panel_exists(panel_id)` (via the existing `_row_exists_sync` helper with table `"panels"`), `create_beat(panel_id, **fields) -> int`, `get_beat(beat_id)`, `update_beat(beat_id, **fields)`, `delete_beat(beat_id) -> bool`.

`backend/api/storyboard.py` additions:

```python
class DialogLine(BaseModel):
    subject_id: Optional[int] = None
    voice: Optional[str] = None
    delivery: Optional[str] = None
    language: str = "English"
    text: str


class BeatCreate(BaseModel):
    action: str
    sort_order: int = 0
    duration_s: float = 4.0
    camera_motion: Optional[str] = None
    camera_amplitude: Optional[str] = None
    camera_speed: Optional[str] = None
    is_cut: int = 0
    dialog: List[DialogLine] = []
    sound: Optional[str] = None


class BeatPatch(BaseModel):
    action: Optional[str] = None
    sort_order: Optional[int] = None
    duration_s: Optional[float] = None
    camera_motion: Optional[str] = None
    camera_amplitude: Optional[str] = None
    camera_speed: Optional[str] = None
    is_cut: Optional[int] = None
    dialog: Optional[List[DialogLine]] = None
    sound: Optional[str] = None


class ComposeRequest(BaseModel):
    stages: Optional[List[str]] = None
    scene_ids: Optional[List[int]] = None
    panel_ids: Optional[List[int]] = None
    confirm: bool = False


_BEAT_NOT_NULLABLE = {"sort_order", "duration_s", "action", "is_cut", "dialog"}
```

Extend the existing models: `StoryboardPatch` gains `outline: Optional[str] = None`; `PanelPatch` gains `duration_s: Optional[float] = None` and `"duration_s"` joins `_PANEL_NOT_NULLABLE`; `SubjectCreate`/`SubjectPatch` gain `voice: Optional[str] = None` (nullable — not added to `_SUBJECT_NOT_NULLABLE`).

Routes (mirroring `/synthesize`'s 202 + `asyncio.create_task` shape at line 320, and the panel-route patterns):

```python
@router.post("/{storyboard_id}/compose", status_code=202)
async def compose_storyboard(storyboard_id: int, body: ComposeRequest):
    runner = _require_runner()
    from metascan.core import storyboard_story as story

    stages = tuple(body.stages) if body.stages else story.STAGES
    try:
        await runner.check_compose_gates(
            storyboard_id, stages, body.scene_ids, body.confirm
        )
    except ConfirmRequiredError as e:
        raise HTTPException(
            status_code=409, detail={"code": "confirm_required", "message": str(e)}
        )
    except StoryboardError as e:
        raise HTTPException(status_code=400, detail=str(e))
    asyncio.create_task(
        runner.compose_story(
            storyboard_id,
            stages=stages,
            scene_ids=body.scene_ids,
            panel_ids=body.panel_ids,
            confirm=body.confirm,
        )
    )
    return {"status": "started"}


@router.post("/panels/{panel_id}/beats")
async def create_beat(panel_id: int, body: BeatCreate):
    svc = _service()
    if not await svc.panel_exists(panel_id):
        raise HTTPException(status_code=404, detail="panel not found")
    fields = body.model_dump()
    fields["dialog"] = [line for line in fields["dialog"]]
    beat_id = await svc.create_beat(panel_id, **fields)
    return {"id": beat_id}


@router.patch("/beats/{beat_id}")
async def patch_beat(beat_id: int, body: BeatPatch):
    svc = _service()
    fields = body.model_dump(exclude_unset=True)
    _reject_null_for_required(fields, _BEAT_NOT_NULLABLE)
    if await svc.get_beat(beat_id) is None:
        raise HTTPException(status_code=404, detail="beat not found")
    if fields:
        await svc.update_beat(beat_id, **fields)
    return await svc.get_beat(beat_id)


@router.delete("/beats/{beat_id}")
async def delete_beat(beat_id: int):
    svc = _service()
    if not await svc.delete_beat(beat_id):
        raise HTTPException(status_code=404, detail="beat not found")
    return {"status": "deleted"}
```

(Check `_reject_null_for_required`'s exact name/signature in the file and match it; `BeatCreate.dialog` model instances must be dumped to plain dicts before hitting the DB — `body.model_dump()` already does that.)

- [ ] **Step 4: Run tests** — `pytest tests/test_storyboard_compose_api.py tests/test_storyboard_api.py -v` — all PASS.

- [ ] **Step 5: Quality gate + commit**

```bash
git add backend/api/storyboard.py backend/services/storyboard_service.py tests/test_storyboard_compose_api.py
git commit -m "feat(storyboard): compose + beats REST routes, PATCH field extensions"
```

---

### Task 5: Frontend types, API fetchers, store extensions

**Files:**
- Modify: `frontend/src/types/storyboard.ts`
- Modify: `frontend/src/api/storyboard.ts`
- Modify: `frontend/src/stores/storyboard.ts`

**Interfaces:**
- Consumes: Task 4 routes; existing store patterns (`attachWs` channel guard at `storyboard.ts:486-492`, optimistic patch at `:270-287`).
- Produces (Tasks 6-7 use): `Beat`, `DialogLine`, `CAMERA_MOTIONS/CAMERA_AMPLITUDES/CAMERA_SPEEDS` constants; `composeStory(stages?, opts?)`, `addBeat(panelId, body)`, `patchBeatFields(beatId, body)`, `removeBeat(beatId)` store actions; `story` reactive state `{running, stage, done, total, error}`.

- [ ] **Step 1: Types** (`types/storyboard.ts`)

```typescript
export interface DialogLine {
  subject_id: number | null
  voice: string | null
  delivery: string | null
  language: string
  text: string
}

export interface Beat {
  id: number
  panel_id: number
  sort_order: number
  duration_s: number
  action: string
  camera_motion: string | null
  camera_amplitude: string | null
  camera_speed: string | null
  is_cut: 0 | 1
  dialog: DialogLine[]
  sound: string | null
  created_at: string
  updated_at: string
}

export const CAMERA_MOTIONS = [
  'zoom_in', 'zoom_out', 'push_in', 'pull_out', 'pan_left', 'pan_right',
  'truck_left', 'truck_right', 'tilt_up', 'tilt_down', 'pedestal_up',
  'pedestal_down', 'arc', 'tracking', 'static', 'shake_slight',
  'shake_strong', 'pov', 'roll_cw', 'roll_ccw',
] as const
export const CAMERA_AMPLITUDES = ['small', 'large'] as const
export const CAMERA_SPEEDS = ['slow', 'fast'] as const
export const COMPOSE_STAGES = ['outline', 'scenes', 'shots', 'beats'] as const
export type ComposeStage = (typeof COMPOSE_STAGES)[number]
```

Extend existing interfaces: `Panel` gains `duration_s: number` and `beats: Beat[]`; `Subject` gains `voice: string | null`; `StoryboardTree` gains `outline: string | null`. `PanelWithoutImages` stays `Omit<Panel, 'images'>` — PATCH responses lack `beats` too, but `Object.assign` merge never deletes the key (same as `images`); add a code comment saying so.

- [ ] **Step 2: API fetchers** (`api/storyboard.ts`)

```typescript
export function composeStoryboard(
  id: number,
  body: {
    stages?: ComposeStage[]
    scene_ids?: number[]
    panel_ids?: number[]
    confirm?: boolean
  },
): Promise<{ status: string }> {
  return post(`/storyboard/${id}/compose`, body)
}

export function createBeat(
  panelId: number,
  body: Partial<Omit<Beat, 'id' | 'panel_id' | 'created_at' | 'updated_at'>> & {
    action: string
  },
): Promise<{ id: number }> {
  return post(`/storyboard/panels/${panelId}/beats`, body)
}

export function patchBeat(
  beatId: number,
  body: Partial<Omit<Beat, 'id' | 'panel_id' | 'created_at' | 'updated_at'>>,
): Promise<Beat> {
  return patch(`/storyboard/beats/${beatId}`, body)
}

export function deleteBeat(beatId: number): Promise<{ status: string }> {
  return del(`/storyboard/beats/${beatId}`)
}
```

(Match the file's existing helper names — it may use `request`-style wrappers rather than `post/patch/del`; read the top of the file and mirror exactly.)

- [ ] **Step 3: Store** (`stores/storyboard.ts`)

State after `synthesis` (line 32-37):

```typescript
const story = ref<{
  running: boolean
  stage: ComposeStage | null
  done: number
  total: number
  error: string | null
}>({ running: false, stage: null, done: 0, total: 0, error: null })
```

Actions (same section as `synthesize`):

```typescript
async function composeStory(body: {
  stages?: ComposeStage[]
  scene_ids?: number[]
  panel_ids?: number[]
  confirm?: boolean
}): Promise<void> {
  // Optimistic before await: the WS story_progress can beat the HTTP
  // response (same race as synthesize(), storyboard.ts:365-386).
  story.value = { running: true, stage: null, done: 0, total: 0, error: null }
  try {
    await api.composeStoryboard(requireTreeId(), body)
  } catch (e) {
    story.value.running = false
    throw e
  }
}

async function addBeat(panelId: number, action: string): Promise<void> {
  await api.createBeat(panelId, { action })
  await refresh()
}

async function patchBeatFields(
  beatId: number,
  body: Partial<Omit<Beat, 'id' | 'panel_id' | 'created_at' | 'updated_at'>>,
): Promise<void> {
  const updated = await api.patchBeat(beatId, body)
  const panel = panelById(updated.panel_id)
  if (!panel) return
  const idx = panel.beats.findIndex((b) => b.id === beatId)
  if (idx >= 0) panel.beats[idx] = updated
  panel.beats.sort((a, b) => a.sort_order - b.sort_order || a.id - b.id)
}

async function removeBeat(beatId: number): Promise<void> {
  await api.deleteBeat(beatId)
  for (const scene of tree.value?.scenes ?? [])
    for (const panel of scene.panels)
      panel.beats = panel.beats.filter((b) => b.id !== beatId)
}
```

(`requireTreeId()` — if no such helper exists, inline `if (!tree.value) throw new Error('no board loaded'); tree.value.id`.) WS handling inside the existing `'storyboard'` channel handler (after the `synthesis_*` cases, inside the same `storyboard_id` guard):

```typescript
if (msg.event === 'story_progress') {
  story.value = {
    running: true,
    stage: (d.stage as ComposeStage) ?? null,
    done: Number(d.done ?? 0),
    total: Number(d.total ?? 0),
    error: null,
  }
} else if (msg.event === 'story_stage_complete') {
  void refresh() // each stage lands reviewable state immediately
} else if (msg.event === 'story_complete') {
  story.value.running = false
  void refresh()
} else if (msg.event === 'story_error') {
  story.value.running = false
  story.value.error = String(d.error ?? 'compose failed')
}
```

Export `story`, `composeStory`, `addBeat`, `patchBeatFields`, `removeBeat` from the store's return object.

- [ ] **Step 4: Build check**

Run: `cd frontend && npm run build` — Expected: type-check + build pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/storyboard.ts frontend/src/api/storyboard.ts frontend/src/stores/storyboard.ts
git commit -m "feat(storyboard): beat/compose types, API fetchers, store state + WS"
```

---

### Task 6: OutlineDialog + Compose flow in StoryboardView

**Files:**
- Create: `frontend/src/components/storyboard/OutlineDialog.vue`
- Modify: `frontend/src/views/StoryboardView.vue`

**Interfaces:**
- Consumes: Task 5 store (`story`, `composeStory`), `patchStoryboardFields` (existing), tree fields `source_text`/`outline`.
- Produces: `OutlineDialog` with prop `open: boolean`, emit `close`.

- [ ] **Step 1: Create `OutlineDialog.vue`**

Follow `ImportTextDialog.vue`'s modal markup/classes (read it first; reuse its overlay/dialog classes exactly). Behavior contract:

```vue
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useStoryboardStore } from '@/stores/storyboard'
import type { ComposeStage } from '@/types/storyboard'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()
const store = useStoryboardStore()

const premise = ref('')
const outlineText = ref('')   // pretty-printed JSON, directly editable
const confirmPending = ref<ComposeStage[] | null>(null)
const error = ref<string | null>(null)

watch(
  () => [props.open, store.tree?.outline],
  () => {
    if (!props.open || !store.tree) return
    premise.value = store.tree.source_text ?? ''
    try {
      outlineText.value = store.tree.outline
        ? JSON.stringify(JSON.parse(store.tree.outline), null, 2)
        : ''
    } catch {
      outlineText.value = store.tree.outline ?? ''
    }
  },
  { immediate: true },
)

const hasOutline = computed(() => !!store.tree?.outline)

async function run(stages: ComposeStage[], confirm = false) {
  error.value = null
  try {
    if (premise.value !== (store.tree?.source_text ?? ''))
      await store.patchStoryboardFields({ source_text: premise.value })
    if (outlineText.value && stages[0] !== 'outline')
      await store.patchStoryboardFields({ outline: outlineText.value })
    await store.composeStory({ stages, confirm })
    confirmPending.value = null
  } catch (e: unknown) {
    const err = e as { status?: number; detail?: { code?: string } }
    if (err.status === 409 && err.detail?.code === 'confirm_required') {
      confirmPending.value = stages
    } else {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }
}
</script>
```

Template: premise `<textarea>`; "Generate outline" button → `run(['outline'])`; the outline `<textarea>` (monospace) shown when `hasOutline`; a stage picker — one checkbox per stage from `COMPOSE_STAGES`, defaulting to `scenes`+`shots`+`beats` checked once an outline exists (spec §6: partial re-runs) — with a "Build checked stages" button → `run(checkedStages)`; a confirm banner when `confirmPending` is set ("This replaces existing content — continue?" → `run(confirmPending, true)` / cancel clears it); progress line bound to `store.story` (`{{ story.stage }} {{ story.done }}/{{ story.total }}`); error line; Close button emitting `close`, disabled while `store.story.running`. (Exact `ApiError` shape: check `api/client.ts` — it preserves `detail`; adjust the 409 sniff to its real field names.)

- [ ] **Step 2: Wire into `StoryboardView.vue`**

Header (next to the Import-text button, `StoryboardView.vue:15-75`): add a "Compose" button opening the dialog (`composeOpen` ref), plus a story-progress chip mirroring the synthesis chip, bound to `store.story` (`v-if="store.story.running"` → "Composing {{ store.story.stage }} {{ store.story.done }}/{{ store.story.total }}"; error chip when `store.story.error`). Mount `<OutlineDialog v-if="composeOpen" :open="composeOpen" @close="composeOpen = false" />` beside the other dialogs (lines 85-86).

- [ ] **Step 3: Build check** — `cd frontend && npm run build` — pass.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/storyboard/OutlineDialog.vue frontend/src/views/StoryboardView.vue
git commit -m "feat(storyboard): compose flow — outline dialog + progress chip"
```

---

### Task 7: Beats editor in PanelDetail

**Files:**
- Create: `frontend/src/components/storyboard/BeatsEditor.vue`
- Create: `frontend/src/components/storyboard/BeatRow.vue`
- Modify: `frontend/src/components/storyboard/PanelDetail.vue` (mount + duration field)

**Interfaces:**
- Consumes: Task 5 store actions; `Beat`/`DialogLine` types; camera constants; the local-ref + snapshot resync pattern (`PanelDetail.vue:169-225`).
- Produces: `BeatsEditor` (prop `panel: Panel`), `BeatRow` (prop `beat: Beat`, `subjects: Subject[]`).

- [ ] **Step 1: `BeatRow.vue`** — one beat's editors, commit-on-change with snapshot resync keyed on `[beat.id, beat.updated_at]`:

```vue
<script setup lang="ts">
import { ref, watch } from 'vue'
import { useStoryboardStore } from '@/stores/storyboard'
import type { Beat, DialogLine, Subject } from '@/types/storyboard'
import {
  CAMERA_AMPLITUDES, CAMERA_MOTIONS, CAMERA_SPEEDS,
} from '@/types/storyboard'

const props = defineProps<{ beat: Beat; subjects: Subject[] }>()
const store = useStoryboardStore()

const actionVal = ref(''); const actionSnap = ref('')
const durationVal = ref('0'); const durationSnap = ref('0')
const soundVal = ref(''); const soundSnap = ref('')

function sync() {
  // Only overwrite fields with no pending edit (PanelDetail pattern).
  if (actionVal.value === actionSnap.value) {
    actionVal.value = props.beat.action; actionSnap.value = props.beat.action
  }
  const d = String(props.beat.duration_s)
  if (durationVal.value === durationSnap.value) {
    durationVal.value = d; durationSnap.value = d
  }
  const s = props.beat.sound ?? ''
  if (soundVal.value === soundSnap.value) {
    soundVal.value = s; soundSnap.value = s
  }
}
watch(() => [props.beat.id, props.beat.updated_at], sync, { immediate: true })

async function commit(field: 'action' | 'duration_s' | 'sound', raw: string) {
  if (field === 'action') { actionSnap.value = raw }
  if (field === 'duration_s') { durationSnap.value = raw }
  if (field === 'sound') { soundSnap.value = raw }
  const body =
    field === 'duration_s'
      ? { duration_s: Math.max(0.5, Number(raw) || props.beat.duration_s) }
      : field === 'sound'
        ? { sound: raw.trim() || null }
        : { action: raw }
  await store.patchBeatFields(props.beat.id, body)
}

async function commitSelect(
  field: 'camera_motion' | 'camera_amplitude' | 'camera_speed',
  value: string,
) {
  await store.patchBeatFields(props.beat.id, { [field]: value || null })
}

async function toggleCut() {
  await store.patchBeatFields(props.beat.id, {
    is_cut: props.beat.is_cut ? 0 : 1,
  })
}

async function addDialogLine() {
  const lines: DialogLine[] = [
    ...props.beat.dialog,
    { subject_id: null, voice: null, delivery: null,
      language: 'English', text: '' },
  ]
  await store.patchBeatFields(props.beat.id, { dialog: lines })
}

async function commitDialogLine(i: number, patchLine: Partial<DialogLine>) {
  const lines = props.beat.dialog.map((l, j) =>
    j === i ? { ...l, ...patchLine } : l,
  )
  await store.patchBeatFields(props.beat.id, { dialog: lines })
}

async function removeDialogLine(i: number) {
  await store.patchBeatFields(props.beat.id, {
    dialog: props.beat.dialog.filter((_, j) => j !== i),
  })
}

async function remove() {
  await store.removeBeat(props.beat.id)
}
</script>
```

Template: a compact row — action input (`:value="actionVal"` `@input` updates `actionVal` `@change` commits), duration number input (step 0.5), three camera `<select>`s (empty option = "—", options from the constants, `:value="beat.camera_motion ?? ''"`), a "cut" toggle button (highlighted when `beat.is_cut`), sound input, delete button; below, dialog lines: per line a speaker `<select>` (options: each subject `s.id`/`s.name` + an "other voice" `''` option driving a `voice` text input shown when `subject_id === null`), delivery input, language input, text input, remove button; "+ line" button. Reuse `PanelDetail.vue`'s field/label CSS classes.

- [ ] **Step 2: `BeatsEditor.vue`**

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { useStoryboardStore } from '@/stores/storyboard'
import type { Panel } from '@/types/storyboard'
import BeatRow from './BeatRow.vue'

const props = defineProps<{ panel: Panel }>()
const store = useStoryboardStore()

const total = computed(() =>
  props.panel.beats.reduce((s, b) => s + b.duration_s, 0),
)
const overShot = computed(() => total.value > props.panel.duration_s + 0.5)
const overH3 = computed(() => total.value > 15)
const composing = computed(() => store.story.running)

async function rebeat() {
  await store.composeStory({ stages: ['beats'], panel_ids: [props.panel.id] })
}

async function add() {
  await store.addBeat(props.panel.id, 'new beat')
}
</script>

<template>
  <div class="pd-field">
    <label>
      Beats — {{ total.toFixed(1) }}s / {{ panel.duration_s }}s
      <span v-if="overH3" class="beats-warn">exceeds H3 15s clip cap</span>
      <span v-else-if="overShot" class="beats-warn">exceeds shot duration</span>
    </label>
    <BeatRow
      v-for="b in panel.beats"
      :key="b.id"
      :beat="b"
      :subjects="store.tree?.subjects ?? []"
    />
    <div class="beats-actions">
      <button type="button" @click="add">+ Beat</button>
      <button type="button" :disabled="composing" @click="rebeat">
        Re-beat shot
      </button>
    </div>
  </div>
</template>
```

Add a small `.beats-warn { color: var(--warn, #e0a030); margin-left: 0.5rem; }`-style scoped rule consistent with the component's existing palette.

- [ ] **Step 3: Mount in `PanelDetail.vue`**

In the fields column after the Prompt block (~line 111): `<BeatsEditor :panel="panel" />` (guarded by the existing `panel` presence). Also add a "Duration (s)" number input among the panel fields using the file's own local-ref/snapshot idiom (pair `durationVal`/`durationSnap`, committed via `store.patchPanelFields(panel.id, { duration_s: Number(raw) })`, registered in the resync watcher alongside the other seven fields).

- [ ] **Step 4: Build check** — `cd frontend && npm run build` — pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/storyboard/BeatsEditor.vue frontend/src/components/storyboard/BeatRow.vue frontend/src/components/storyboard/PanelDetail.vue
git commit -m "feat(storyboard): beats editor in panel detail with duration warnings"
```

---

### Task 8: Docs, CLAUDE.md, final verification

**Files:**
- Modify: `CLAUDE.md` (storyboard architecture bullets)
- Modify: `docs/features.md` (feature list entry)

**Interfaces:** none — documentation + full-suite verification.

- [ ] **Step 1: CLAUDE.md**

In the storyboard bullets, after the Phase B bullet, add one bullet summarizing: the story engine's four staged compose calls (outline → scenes → shots → beats) under `_synth_lock` with `story_progress`/`story_stage_complete`/`story_complete`/`story_error` on the `storyboard` WS channel; the `beats` table cascading from panels with dialog as a JSON column and camera enums validated in `storyboard_story.py`; the confirm-gate rules (outline/scenes/shots destructive, beats not); beat-duration arithmetic always code-side (`rescale_beat_durations`); grammars in Python + prompts in `data/meta_prompt.yml` (`STORY_*_SYSTEM`); and that the compose route 409s `confirm_required` synchronously via `check_compose_gates` before the 202 fire-and-forget task.

- [ ] **Step 2: docs/features.md**

Add a "Story engine" line under the storyboard feature section: premise → AI-built outline/scenes/shots/beats, per-level re-roll, editable everywhere.

- [ ] **Step 3: Full verification**

Run: `make quality test` AND `cd frontend && npm run build`
Expected: both pass (watcher flake excepted). Then run the storyboard-scoped suite once more: `pytest tests/test_storyboard_beats_db.py tests/test_storyboard_story.py tests/test_storyboard_compose.py tests/test_storyboard_compose_api.py tests/test_storyboard_api.py tests/test_storyboard_db.py tests/test_storyboard_runner.py -v` — all PASS.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/features.md
git commit -m "docs: story-engine architecture notes and feature entry"
```
