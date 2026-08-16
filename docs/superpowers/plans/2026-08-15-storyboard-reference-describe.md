# Storyboard Reference Describe (Phase V2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attach up to two reference images per subject and one per scene, and let Qwen3-VL write the canonical descriptor text (subject description/voice, scene setting/lighting/mood) — returned for user review, never written to the DB by the server.

**Architecture:** Extend `VlmClient.generate_text` to multi-image; two new nullable FK columns; a pure `ref_describe.py` module (grammars in Python, prompts in YAML — the V1 amendment pattern); two synchronous describe routes that return JSON without persisting; frontend pickers + describe buttons with review-before-apply semantics.

**Tech Stack:** Python 3.11 / FastAPI / SQLite, llama-server GBNF via `VlmClient`, Vue 3 + Pinia + TypeScript.

**Spec:** `docs/superpowers/specs/2026-08-15-storyboard-reference-describe-design.md`

## Global Constraints

- `make quality test` must pass before every commit (known WSL2 flake `test_file_watcher_triggers_reload` excepted); frontend tasks also `cd frontend && npm run build`.
- Tests never load a real VLM — fake objects / stub transports only.
- GBNF: `\-` is an invalid escape (SIGSEGVs llama-server); build grammars from `{{`/`}}`-escaped templates like `storyboard_parse.PARSE_GRAMMAR`.
- Describe endpoints **do not write the DB** — they return JSON; persistence happens through the normal PATCH flow.
- New DB columns via `_idempotent_add_column`; media-FK paths stored POSIX via `to_posix_path` (the `create_subject` precedent at `database_sqlite.py:1345-1379`); unknown media path → `sqlite3.IntegrityError` → `InvalidReferenceError` → HTTP 400.
- mypy strict on `metascan/core/*`.
- `metascan/core/meta_prompt_templates.py` has unrelated uncommitted changes — never stage it; stage files explicitly.
- Prompt prose lives in `data/meta_prompt.yml` (hot-reload store, `__getattr__` accessor pattern); each describe prompt carries the no-refusal directive (mirror `TAGGING_SYSTEM_PROMPT`'s wording) and ends with "Output only the JSON."

---

### Task 1: Reference columns — `storyboard_subjects.reference_path_2`, `scenes.reference_path`

**Files:**
- Modify: `metascan/core/database_sqlite.py` (migration block ~line 700; `_SUBJECT_UPDATABLE`/`_SCENE_UPDATABLE` ~lines 1202-1224; `create_subject` ~1345; `update_subject` ~1381; `create_scene` ~1411; `update_scene` (the `_SCENE_UPDATABLE` consumer); add `get_subject`/`get_scene`)
- Modify: `backend/services/storyboard_service.py` (`update_scene`/`create_scene` gain the `IntegrityError` → `InvalidReferenceError` mapping subjects already have; add `get_subject`/`get_scene` wrappers)
- Test: `tests/test_storyboard_refs_db.py` (create)

**Interfaces:**
- Consumes: `_idempotent_add_column`, `to_posix_path`, existing `InvalidReferenceError` mapping in `create_subject`/`update_subject` service wrappers.
- Produces:
  - Columns: `storyboard_subjects.reference_path_2 TEXT REFERENCES media(file_path) ON DELETE SET NULL`; `scenes.reference_path TEXT REFERENCES media(file_path) ON DELETE SET NULL`.
  - `DatabaseManager.get_subject(subject_id: int) -> Optional[Dict[str, Any]]`, `DatabaseManager.get_scene(scene_id: int) -> Optional[Dict[str, Any]]` (plain `SELECT *` row dicts).
  - `create_subject(..., reference_path_2: Optional[str] = None, ...)`; `create_scene(..., reference_path: Optional[str] = None, ...)`; both update paths POSIX-normalize the new fields exactly like `reference_path` (`update_subject`'s `if fields.get("reference_path"): to_posix_path(...)` pattern).
  - `StoryboardService.get_subject(subject_id)`, `get_scene(scene_id)` (async `to_thread` wrappers); `create_scene`/`update_scene` translate `sqlite3.IntegrityError` to `InvalidReferenceError` (copy the exact try/except shape from `create_subject`/`update_subject` in the same file).
  - Tree treatment of the two new fields matches the existing `storyboard_subjects.reference_path` treatment in `get_storyboard_tree` EXACTLY — read that function first (`database_sqlite.py:1786`): subject rows pass through `dict(r)` unconverted, so the new columns need no tree code at all; verify and do not invent conversions. **Recorded spec deviation:** spec §3 says the tree returns these "native-path converted", but V1's shipped code returns subject `reference_path` unconverted and `StoryboardSettingsDialog` round-trips that raw value through `commitSubjectField` — converting only the new columns would be inconsistent, and converting all of them would change an existing working contract. Mirror reality; the describe routes (Task 4) do their own `to_native_path` at the filesystem boundary, which is where conversion actually matters.

- [ ] **Step 1: Write the failing tests**

Reuse `tests/test_storyboard_db.py`'s fixture verbatim (read it first — `DatabaseManager(tmp_path / "db")`, `yield mgr; mgr.close()`).

```python
"""Second subject reference + scene setting reference (spec V2 §3)."""

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media  # verify actual constructor in tests/test_storyboard_db.py


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


@pytest.fixture
def board(db):
    return db.create_storyboard(
        name="B", target_model="sd", architecture="t2i", base_seed=1
    )


def _add_media(db, posix_path):
    # BINDING DIRECTIVE, not a stub: tests/test_storyboard_db.py already
    # contains a media-row insertion helper for its reference_path tests
    # (it satisfies the media FK prerequisite). Open that file, copy its
    # helper body here verbatim, and delete this comment. Do not invent a
    # new Media-construction path.
    raise NotImplementedError("copy helper from tests/test_storyboard_db.py")


def test_subject_second_reference_roundtrip_posix(db, board):
    _add_media(db, "C:/pics/a.png")
    sid = db.create_subject(
        board, name="Maya", description="d",
        reference_path_2="C:\\pics\\a.png",
    )
    subj = db.get_subject(sid)
    assert subj["reference_path_2"] == "C:/pics/a.png"  # stored POSIX
    db.update_subject(sid, reference_path_2=None)
    assert db.get_subject(sid)["reference_path_2"] is None


def test_scene_reference_roundtrip_and_set_null(db, board):
    _add_media(db, "C:/pics/set.png")
    scene_id = db.create_scene(board, name="Yard", reference_path="C:\\pics\\set.png")
    assert db.get_scene(scene_id)["reference_path"] == "C:/pics/set.png"
    # deleting the media row nulls the reference, not the scene
    db.delete_media("C:/pics/set.png")  # verify actual deletion API name in DatabaseManager
    assert db.get_scene(scene_id)["reference_path"] is None


def test_unknown_reference_raises_integrity_error(db, board):
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        db.create_scene(board, name="Yard", reference_path="C:/nope.png")
    sid = db.create_subject(board, name="M", description="d")
    with pytest.raises(sqlite3.IntegrityError):
        db.update_subject(sid, reference_path_2="C:/nope.png")


def test_get_subject_get_scene_unknown_ids(db):
    assert db.get_subject(999) is None
    assert db.get_scene(999) is None
```

(The two `verify actual … name` comments are instructions to the implementer: open the named file, find the real helper/API, and use it — the intent is fixed, the identifier must match reality.)

- [ ] **Step 2: Run to verify failure** — `venv/bin/pytest tests/test_storyboard_refs_db.py -v` → FAIL (`reference_path_2` unknown, `get_subject` missing).

- [ ] **Step 3: Implement**

Migration block (next to the V1 beat migrations, ~line 700):

```python
        _idempotent_add_column(
            conn, "storyboard_subjects", "reference_path_2",
            "ALTER TABLE storyboard_subjects ADD COLUMN reference_path_2 "
            "TEXT REFERENCES media(file_path) ON DELETE SET NULL",
        )
        _idempotent_add_column(
            conn, "scenes", "reference_path",
            "ALTER TABLE scenes ADD COLUMN reference_path "
            "TEXT REFERENCES media(file_path) ON DELETE SET NULL",
        )
```

Extend `_SUBJECT_UPDATABLE` with `"reference_path_2"` and `_SCENE_UPDATABLE` with `"reference_path"`. In `update_subject`, POSIX-normalize `reference_path_2` alongside `reference_path`; in `update_scene` (and `create_scene`'s new keyword) do the same for `reference_path`. `create_subject` gains `reference_path_2: Optional[str] = None` (normalized + added to the INSERT). Add:

```python
    def get_subject(self, subject_id: int) -> Optional[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM storyboard_subjects WHERE id = ?", (subject_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_scene(self, scene_id: int) -> Optional[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM scenes WHERE id = ?", (scene_id,)
            ).fetchone()
            return dict(row) if row else None
```

Service: `get_subject`/`get_scene` as one-line `asyncio.to_thread` wrappers; wrap `create_scene`/`update_scene` bodies in the same `try/except sqlite3.IntegrityError: raise InvalidReferenceError(...)` shape `create_subject`/`update_subject` already use in `backend/services/storyboard_service.py` (copy it exactly).

NOTE: SQLite table-level FK enforcement — ALTER TABLE ADD COLUMN with REFERENCES works and enforces under `PRAGMA foreign_keys = ON` (already set by `_get_connection`); no rebuild needed.

- [ ] **Step 4: Run tests** — `venv/bin/pytest tests/test_storyboard_refs_db.py tests/test_storyboard_db.py tests/test_storyboard_api.py -v` → all PASS.

- [ ] **Step 5: Quality gate + commit**

```bash
git add metascan/core/database_sqlite.py backend/services/storyboard_service.py tests/test_storyboard_refs_db.py
git commit -m "feat(storyboard): second subject reference + scene setting reference columns"
```

---

### Task 2: `VlmClient.generate_text` multi-image support

**Files:**
- Modify: `metascan/core/vlm_client.py:493-572` (`generate_text`)
- Test: `tests/test_vlm_generate_text.py` (extend — read its existing stub/fixture mechanism first and add cases in the same style)

**Interfaces:**
- Consumes: existing `_encode_image_b64`, `is_image_path`, `VlmError`.
- Produces: `generate_text(..., image_path: Optional[Path] = None, image_paths: Optional[Sequence[Path]] = None, ...)` — passing both raises `ValueError("pass image_path or image_paths, not both")`; `image_path=X` behaves exactly as `image_paths=[X]`; N paths produce a content list `[text part] + [one image_url part per path, in order]`; any non-image path raises `VlmError` before any encoding; empty `image_paths` (`[]`) behaves as text-only.

- [ ] **Step 1: Write the failing tests**

Read `tests/test_vlm_generate_text.py` first; it already has a fake/stub HTTP mechanism for asserting request bodies — reuse it. Add these cases (adapting to the file's helpers):

```python
async def test_generate_text_two_images_two_parts(ready_client, captured):
    # ready_client / captured: the file's existing fixtures for a READY
    # client with a stubbed httpx transport that records request JSON.
    await ready_client.generate_text(
        system_prompt="s", user_prompt="u",
        image_paths=[img_a, img_b],  # two tiny real JPEGs via tmp_path fixture
    )
    content = captured["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "u"}
    assert [p["type"] for p in content[1:]] == ["image_url", "image_url"]


async def test_generate_text_both_image_params_raises(ready_client):
    with pytest.raises(ValueError):
        await ready_client.generate_text(
            system_prompt="s", user_prompt="u",
            image_path=img_a, image_paths=[img_b],
        )


async def test_generate_text_non_image_in_list_raises_before_send(ready_client, captured):
    with pytest.raises(VlmError):
        await ready_client.generate_text(
            system_prompt="s", user_prompt="u",
            image_paths=[img_a, Path("clip.mp4")],
        )
    assert captured == {}  # nothing was sent


async def test_generate_text_single_image_path_unchanged(ready_client, captured):
    await ready_client.generate_text(
        system_prompt="s", user_prompt="u", image_path=img_a,
    )
    content = captured["messages"][1]["content"]
    assert len(content) == 2 and content[1]["type"] == "image_url"
```

- [ ] **Step 2: Run to verify failure** — `venv/bin/pytest tests/test_vlm_generate_text.py -v` → new cases FAIL (`image_paths` unexpected kwarg).

- [ ] **Step 3: Implement**

Replace the `image_path` block in `generate_text` (keep signature ordering; add `image_paths: Optional[Sequence[Path]] = None` after `image_path`; import `Sequence` if absent):

```python
        if image_path is not None and image_paths is not None:
            raise ValueError("pass image_path or image_paths, not both")
        paths: list[Path] = (
            [image_path] if image_path is not None else list(image_paths or [])
        )
        user_content: Any
        if paths:
            for p in paths:
                if not self.is_image_path(p):
                    raise VlmError(f"unsupported image type: {p.suffix}")
            parts: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
            for p in paths:
                image_b64 = await asyncio.to_thread(self._encode_image_b64, p)
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    }
                )
            user_content = parts
        else:
            user_content = user_prompt
```

Update the docstring's image paragraph to cover the plural form (order = prompt reference order: "the first image", "the second image").

- [ ] **Step 4: Run tests** — `venv/bin/pytest tests/test_vlm_generate_text.py tests/test_vlm_client_tags.py -v` → all PASS.

- [ ] **Step 5: Quality gate + commit**

```bash
git add metascan/core/vlm_client.py tests/test_vlm_generate_text.py
git commit -m "feat(vlm): multi-image generate_text via image_paths"
```

---

### Task 3: `ref_describe.py` + `vlm_select.py` + YAML prompts

**Files:**
- Create: `metascan/core/ref_describe.py`
- Create: `metascan/core/vlm_select.py`
- Modify: `metascan/core/storyboard_runner.py:107-124` (`_pick_vlm_model` delegates)
- Modify: `data/meta_prompt.yml` (append two keys)
- Test: `tests/test_ref_describe.py` (create)

**Interfaces:**
- Consumes: prompt store (`get_prompt_store().get`), `storyboard_parse`-style grammar templating.
- Produces:
  - `vlm_select.pick_vlm_model(vlm: Any) -> str` — exact logic lifted from `StoryboardRunner._pick_vlm_model` (`storyboard_runner.py:108-124`): prefer `vlm.model_id`, else first hardware-recommended `qwen3vl-*` gate, else raise `VlmSelectError(RuntimeError)`. The runner method becomes `return pick_vlm_model(vlm)` wrapped to re-raise `VlmSelectError` as `StoryboardError` (preserving its current error message contract).
  - `ref_describe.SUBJECT_DESCRIBE_GRAMMAR`, `SETTING_DESCRIBE_GRAMMAR` (module constants).
  - `ref_describe.SUBJECT_USER_PROMPT = "Describe the subject shown in the reference image(s)."`, `SETTING_USER_PROMPT = "Describe the setting shown in the reference image."`
  - `class DescribeError(ValueError)`; `validate_subject_describe(raw: str) -> Dict[str, Any]` → `{"description": str, "voice": Optional[str]}`; `validate_setting_describe(raw: str) -> Dict[str, Any]` → `{"setting": str, "lighting": Optional[str], "mood": Optional[str]}` — both raise `DescribeError` on bad JSON or empty required field.
  - Module `__getattr__` for `REF_DESCRIBE_SUBJECT_SYSTEM` / `REF_DESCRIBE_SETTING_SYSTEM` (YAML-backed, mirroring `storyboard_story.__getattr__`), plus the staleness caveat comment `vlm_prompts.py` carries (the deferred Task-2 minor from V1 — do it right here).

- [ ] **Step 1: Write the failing tests**

```python
"""Reference-describe grammars, validators, model picker (spec V2 §4-§5)."""

import json

import pytest

from metascan.core import ref_describe as rd
from metascan.core.vlm_select import VlmSelectError, pick_vlm_model


def test_grammars_are_gbnf_safe():
    for g in (rd.SUBJECT_DESCRIBE_GRAMMAR, rd.SETTING_DESCRIBE_GRAMMAR):
        assert g.startswith("root ::=")
        assert r"\-" not in g
        assert g.count('"') % 2 == 0


def test_validate_subject_describe():
    out = rd.validate_subject_describe(
        json.dumps({"description": "late 20s, shaved head", "voice": "clear alto"})
    )
    assert out == {"description": "late 20s, shaved head", "voice": "clear alto"}
    out2 = rd.validate_subject_describe(
        json.dumps({"description": "x", "voice": None})
    )
    assert out2["voice"] is None
    with pytest.raises(rd.DescribeError):
        rd.validate_subject_describe("not json")
    with pytest.raises(rd.DescribeError):
        rd.validate_subject_describe(json.dumps({"description": "  ", "voice": None}))


def test_validate_setting_describe():
    out = rd.validate_setting_describe(
        json.dumps({"setting": "salvage yard", "lighting": None, "mood": "tense"})
    )
    assert out["setting"] == "salvage yard" and out["mood"] == "tense"
    with pytest.raises(rd.DescribeError):
        rd.validate_setting_describe(json.dumps({"setting": "", "lighting": None, "mood": None}))


def test_system_prompts_resolve_and_carry_contract():
    for key in ("REF_DESCRIBE_SUBJECT_SYSTEM", "REF_DESCRIBE_SETTING_SYSTEM"):
        text = getattr(rd, key)
        assert len(text) > 100
        assert text.rstrip().endswith("Output only the JSON.")


class _Vlm:
    model_id = "qwen3vl-8b"


def test_pick_vlm_model_prefers_loaded_model():
    assert pick_vlm_model(_Vlm()) == "qwen3vl-8b"


def test_pick_vlm_model_no_hardware(monkeypatch):
    from metascan.core import vlm_select
    class _Empty:
        model_id = None
    monkeypatch.setattr(vlm_select, "_recommended_qwen_gate", lambda: None)
    with pytest.raises(VlmSelectError):
        pick_vlm_model(_Empty())
```

Also extend `tests/test_storyboard_runner.py`'s existing `_pick_vlm_model` coverage only if it breaks (the delegation must keep its behavior; run that file to confirm).

- [ ] **Step 2: Run to verify failure** — `venv/bin/pytest tests/test_ref_describe.py -v` → FAIL (modules missing).

- [ ] **Step 3: Implement**

`metascan/core/vlm_select.py`:

```python
"""Shared VLM model selection (runner + describe endpoints)."""

from __future__ import annotations

from typing import Any, Optional


class VlmSelectError(RuntimeError):
    """No VLM model is loadable on this hardware."""


def _recommended_qwen_gate() -> Optional[str]:
    from metascan.core.hardware import detect_hardware, feature_gates

    gates = feature_gates(detect_hardware())
    for mid, gate in gates.items():
        if mid.startswith("qwen3vl-") and gate.recommended:
            return mid
    return None


def pick_vlm_model(vlm: Any) -> str:
    """Pick a model id for ``vlm.ensure_started``.

    Prefers whatever the client already has loaded (idempotent restart of
    the same model); otherwise the first hardware-recommended ``qwen3vl-*``
    gate.
    """
    model_id = getattr(vlm, "model_id", None)
    if model_id:
        return str(model_id)
    mid = _recommended_qwen_gate()
    if mid is not None:
        return mid
    raise VlmSelectError("no VLM model available on this hardware")
```

`StoryboardRunner._pick_vlm_model` body becomes:

```python
        from metascan.core.vlm_select import VlmSelectError, pick_vlm_model

        try:
            return pick_vlm_model(vlm)
        except VlmSelectError as e:
            raise StoryboardError(str(e)) from e
```

`metascan/core/ref_describe.py` — grammars share `storyboard_parse`'s common rules pattern:

```python
"""Reference-image describe: grammars, validators, prompt accessors.

Pure module; the VLM calls live in backend/api/storyboard.py's describe
routes. System prompts are YAML-backed via the prompt store.

NOTE: access the *_SYSTEM prompts as module attributes at call time
(``rd.REF_DESCRIBE_SUBJECT_SYSTEM``). A module-scope
``from ... import REF_DESCRIBE_SUBJECT_SYSTEM`` freezes a snapshot and
defeats the YAML hot-reload.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

_COMMON = r"""nullable ::= string | "null"
string ::= "\"" char* "\""
char ::= [^"\\\x7F\x00-\x1F] | "\\" (["\\bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])
ws ::= [ \t\n]*
"""

SUBJECT_DESCRIBE_GRAMMAR = (
    r"""root ::= "{{" ws "\"description\"" ws ":" ws string ws "," ws "\"voice\"" ws ":" ws nullable ws "}}"
"""
    + _COMMON
).format()

SETTING_DESCRIBE_GRAMMAR = (
    r"""root ::= "{{" ws "\"setting\"" ws ":" ws string ws "," ws "\"lighting\"" ws ":" ws nullable ws "," ws "\"mood\"" ws ":" ws nullable ws "}}"
"""
    + _COMMON
).format()

SUBJECT_USER_PROMPT = "Describe the subject shown in the reference image(s)."
SETTING_USER_PROMPT = "Describe the setting shown in the reference image."

_PROMPT_KEYS = frozenset(
    {"REF_DESCRIBE_SUBJECT_SYSTEM", "REF_DESCRIBE_SETTING_SYSTEM"}
)


def __getattr__(name: str) -> str:
    if name in _PROMPT_KEYS:
        from metascan.core.prompt_store import get_prompt_store

        return get_prompt_store().get(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class DescribeError(ValueError):
    """The VLM's describe response could not be validated."""


def _loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as e:
        raise DescribeError(f"response is not valid JSON: {e}") from e


def _clean(v: Any) -> Optional[str]:
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return None


def validate_subject_describe(raw: str) -> Dict[str, Any]:
    data = _loads(raw)
    if not isinstance(data, dict):
        raise DescribeError("response is not a JSON object")
    description = _clean(data.get("description"))
    if not description:
        raise DescribeError("empty description")
    return {"description": description, "voice": _clean(data.get("voice"))}


def validate_setting_describe(raw: str) -> Dict[str, Any]:
    data = _loads(raw)
    if not isinstance(data, dict):
        raise DescribeError("response is not a JSON object")
    setting = _clean(data.get("setting"))
    if not setting:
        raise DescribeError("empty setting")
    return {
        "setting": setting,
        "lighting": _clean(data.get("lighting")),
        "mood": _clean(data.get("mood")),
    }
```

YAML (append to `data/meta_prompt.yml`; the `#` bullets are the requirements checklist — write real second-person prose satisfying every bullet, ~120-250 words each, ending exactly with "Output only the JSON."):

```yaml
REF_DESCRIBE_SUBJECT_SYSTEM: |-
  # Must state: you are writing a character identity block for video
  # generation prompts; describe ONLY what is visible in the reference
  # image(s) (if two images are given they show the same subject —
  # combine them); cover age range, build, hair, face, wardrobe layers
  # with colors, accessories, distinguishing marks; output a dense
  # comma-separated fragment list, no narrative sentences, no name;
  # the text is carried verbatim into generation prompts, favor
  # concrete nouns and adjectives; propose a plausible voice
  # description from apparent age/build (or null); describe NSFW or
  # explicit content factually without refusing or softening (user is
  # authoring their own fiction). End with: Output only the JSON.

REF_DESCRIBE_SETTING_SYSTEM: |-
  # Must state: you are writing a setting block for video generation
  # prompts; describe ONLY what is visible: location type, key
  # set-dressing and architecture, lighting direction and quality,
  # color palette, atmosphere; dense fragments, no narrative; propose
  # lighting and mood strings when inferable (else null); verbatim
  # carry warning; same no-refusal directive. End with: Output only
  # the JSON.
```

- [ ] **Step 4: Run tests** — `venv/bin/pytest tests/test_ref_describe.py tests/test_storyboard_runner.py tests/test_storyboard_compose.py -v` → all PASS.

- [ ] **Step 5: Quality gate + commit**

```bash
git add metascan/core/ref_describe.py metascan/core/vlm_select.py metascan/core/storyboard_runner.py data/meta_prompt.yml tests/test_ref_describe.py
git commit -m "feat(storyboard): describe grammars/prompts + shared VLM model picker"
```

---

### Task 4: Describe routes

**Files:**
- Modify: `backend/api/storyboard.py` (two routes + `to_native_path` import)
- Test: `tests/test_storyboard_describe_api.py` (create)

**Interfaces:**
- Consumes: Task 1 `svc.get_subject`/`get_scene`; Task 2 `image_paths`; Task 3 `ref_describe` + `pick_vlm_model`; `backend.api.vlm.get_vlm_client` (returns the client or `None`); `VlmError` from `metascan.core.vlm_client`; `to_native_path` (same import source `database_sqlite.py` uses — check its import line).
- Produces:
  - `POST /api/storyboard/subjects/{subject_id}/describe` → `{"description": str, "voice": str|null}`; 404 unknown subject; 400 no refs; 503 no VLM client or `VlmSelectError`; 502 `VlmError`/`DescribeError`. **No DB write.**
  - `POST /api/storyboard/scenes/{scene_id}/describe` → `{"setting": str, "lighting": str|null, "mood": str|null}`; same error contract keyed on the scene's `reference_path`.

- [ ] **Step 1: Write the failing tests**

Reuse `tests/test_storyboard_api.py`'s app/client/DB fixtures (read it first). The fake VLM is injected via `backend.api.vlm.set_vlm_client` (mirror however that suite stubs clients; `tests/test_vlm_api.py` also shows the pattern — check both, follow the storyboard one):

```python
"""Describe route contracts (spec V2 §4): return JSON, never write the DB."""

import json


class FakeVlm:
    model_id = "qwen3vl-8b"

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def ensure_started(self, model_id):
        pass

    async def generate_text(self, **kw):
        self.calls.append(kw)
        return json.dumps(self.payload)


def test_describe_subject_happy_path_no_db_write(client, db, board_id, media_path):
    sid = db.create_subject(board_id, name="Maya", description="old text",
                            reference_path=media_path)
    # install FakeVlm({"description": "late 20s, shaved head", "voice": "alto"})
    r = client.post(f"/api/storyboard/subjects/{sid}/describe")
    assert r.status_code == 200
    assert r.json() == {"description": "late 20s, shaved head", "voice": "alto"}
    assert db.get_subject(sid)["description"] == "old text"  # unchanged


def test_describe_subject_sends_both_refs_in_order(client, db, board_id,
                                                   media_path, media_path_2, fake_vlm):
    sid = db.create_subject(board_id, name="M", description="d",
                            reference_path=media_path,
                            reference_path_2=media_path_2)
    client.post(f"/api/storyboard/subjects/{sid}/describe")
    sent = fake_vlm.calls[0]["image_paths"]
    assert [str(p) for p in sent] == [
        # to_native_path of media_path, media_path_2 in that order
    ]


def test_describe_400_when_no_refs(client, db, board_id):
    sid = db.create_subject(board_id, name="M", description="d")
    assert client.post(f"/api/storyboard/subjects/{sid}/describe").status_code == 400


def test_describe_404_and_503(client, db):
    assert client.post("/api/storyboard/subjects/9999/describe").status_code == 404
    # with vlm client set to None:
    # assert ... .status_code == 503


def test_describe_scene_happy_and_502_on_garbage(client, db, board_id, media_path):
    scene_id = db.create_scene(board_id, name="Yard", reference_path=media_path)
    # FakeVlm returning {"setting": "salvage yard", "lighting": None, "mood": "tense"}
    r = client.post(f"/api/storyboard/scenes/{scene_id}/describe")
    assert r.status_code == 200 and r.json()["setting"] == "salvage yard"
    assert db.get_scene(scene_id)["setting"] is None  # never written
    # swap in a FakeVlm whose generate_text returns "not json" → 502
```

(Test skeleton is binding for cases + assertions; fixture names adapt to the real suite's fixtures.)

- [ ] **Step 2: Run to verify failure** — routes 404.

- [ ] **Step 3: Implement** (in `backend/api/storyboard.py`, near the subject/scene routes):

```python
@router.post("/subjects/{subject_id}/describe")
async def describe_subject(subject_id: int) -> Dict[str, Any]:
    from backend.api.vlm import get_vlm_client
    from metascan.core import ref_describe as rd
    from metascan.core.vlm_client import VlmError
    from metascan.core.vlm_select import VlmSelectError, pick_vlm_model

    vlm = get_vlm_client()
    if vlm is None:
        raise HTTPException(status_code=503, detail="no VLM client configured")
    svc = _service()
    subject = await svc.get_subject(subject_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="subject not found")
    refs = [
        p for p in (subject.get("reference_path"), subject.get("reference_path_2"))
        if p
    ]
    if not refs:
        raise HTTPException(status_code=400, detail="no reference image set")
    try:
        model_id = pick_vlm_model(vlm)
    except VlmSelectError as e:
        raise HTTPException(status_code=503, detail=str(e))
    await vlm.ensure_started(model_id)
    try:
        raw = await vlm.generate_text(
            system_prompt=rd.REF_DESCRIBE_SUBJECT_SYSTEM,
            user_prompt=rd.SUBJECT_USER_PROMPT,
            image_paths=[Path(to_native_path(p)) for p in refs],
            grammar=rd.SUBJECT_DESCRIBE_GRAMMAR,
            temperature=0.3,
            max_tokens=400,
            timeout=180.0,
        )
        return rd.validate_subject_describe(raw)
    except VlmError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except rd.DescribeError as e:
        raise HTTPException(status_code=502, detail=f"unusable VLM output: {e}")
```

`describe_scene` is the same shape over `svc.get_scene`, `scene.get("reference_path")` (single ref), `REF_DESCRIBE_SETTING_SYSTEM`/`SETTING_USER_PROMPT`/`SETTING_DESCRIBE_GRAMMAR`/`validate_setting_describe`. Hoist the shared imports to module level per the file's conventions (only `get_vlm_client` may need to stay lazy if a circular import bites — check how the file imports runner symbols and mirror). No `_synth_lock` involvement (spec §4: describes are safe to run alongside synthesis).

- [ ] **Step 4: Run tests** — `venv/bin/pytest tests/test_storyboard_describe_api.py tests/test_storyboard_api.py -v` → all PASS.

- [ ] **Step 5: Quality gate + commit**

```bash
git add backend/api/storyboard.py tests/test_storyboard_describe_api.py
git commit -m "feat(storyboard): subject/scene describe endpoints (review-only, no DB write)"
```

---

### Task 5: Frontend — types, fetchers, dialogs

**Files:**
- Modify: `frontend/src/types/storyboard.ts` (`Subject.reference_path_2: string | null`; `Scene.reference_path: string | null`)
- Modify: `frontend/src/api/storyboard.ts` (fetchers + patch body types gain the new fields)
- Modify: `frontend/src/components/storyboard/StoryboardSettingsDialog.vue`
- Modify: `frontend/src/components/storyboard/SceneEditDialog.vue`

**Interfaces:**
- Consumes: Task 4 routes; existing `ReferenceImagePicker` (emits `select(path)`), `commitSubjectField` mechanism, `patchScene`/`SubjectPatch` fields.
- Produces:
  - `describeSubject(subjectId: number): Promise<{description: string; voice: string | null}>`
  - `describeScene(sceneId: number): Promise<{setting: string; lighting: string | null; mood: string | null}>`
  - Settings dialog: per-subject `voice` input + second reference row + Describe button with an Apply/Dismiss preview card.
  - SceneEditDialog: setting-reference picker row + `lighting`/`mood` inputs + Describe button filling the local form.

- [ ] **Step 1: Types + fetchers**

```typescript
export function describeSubject(
  subjectId: number,
): Promise<{ description: string; voice: string | null }> {
  return post(`/storyboard/subjects/${subjectId}/describe`, {})
}

export function describeScene(
  sceneId: number,
): Promise<{ setting: string; lighting: string | null; mood: string | null }> {
  return post(`/storyboard/scenes/${sceneId}/describe`, {})
}
```

(Match the file's real request-helper names.) `patchSubject` body type gains `reference_path_2?: string | null` and (verify — V1 may already have added it) `voice?: string | null`; `patchScene` body gains `reference_path?: string | null`, and confirm `lighting`/`mood` are already in its body type (they're existing columns — add if absent).

- [ ] **Step 2: StoryboardSettingsDialog**

Per subject row, following the file's existing reference-row markup exactly (read `StoryboardSettingsDialog.vue:340-405` first):

- A **Voice** text input committing `{ voice: value || null }` via the existing `commitSubjectField`.
- A **second reference row** — duplicate of the first reference row bound to `reference_path_2` (thumbnail + `@error` fallback + path input + Browse via `ReferenceImagePicker` + clear). The picker is already mounted once with a target-subject mechanism (`pickerSubjectId`-style ref around line 407) — extend that state with which *slot* is being picked (`{ id, slot: 1 | 2 }`) so one picker instance serves both rows.
- A **"Describe from refs"** button per subject: disabled when neither reference is set or a describe for that subject is in flight; on click calls `describeSubject(s.id)` with a per-subject busy flag. The response goes into a per-subject **preview card** rendered under the row: the returned description (and voice when present) in read-only `<textarea readonly>` / text, with **Apply** (commits `commitSubjectField(id, { description })` and, when the subject's current `voice` is empty and a voice was returned, `{ voice }`; then clears the card) and **Dismiss** (clears the card). Errors surface via the app's toast composable if the dialog already uses one, else an inline error line under the row. Add a small hint line "first call may take up to a minute while the model loads".

State additions in `<script setup>`:

```typescript
const describing = ref<Record<number, boolean>>({})
const describeResult = ref<Record<number, { description: string; voice: string | null }>>({})
const describeError = ref<Record<number, string>>({})
```

- [ ] **Step 3: SceneEditDialog**

This dialog holds a local form committed on Save (read it first). Add:

- `lighting` and `mood` text inputs bound to new local refs (seeded from the `scene` prop in edit mode; included in the save payload).
- A setting-reference row: thumbnail preview + Browse (mount its own `ReferenceImagePicker`) + clear, bound to a local `referencePath` ref, saved as `reference_path` in the payload. **In edit mode the reference must be persisted before describe can use it** — on picking a path in edit mode, immediately `patchScene(scene.id, { reference_path: path })` (the describe endpoint reads the DB); in create mode the Describe button stays disabled with a hint ("save the scene first").
- **"Describe from ref"** button (edit mode, reference set, not busy): calls `describeScene(scene.id)` and fills the local `setting` field (always) and `lighting`/`mood` fields (only when their local values are empty) — plain local-form fill; the user reviews and hits Save as usual. Inline error line on failure.

- [ ] **Step 4: Build check** — `cd frontend && npm run build` → clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/storyboard.ts frontend/src/api/storyboard.ts frontend/src/components/storyboard/StoryboardSettingsDialog.vue frontend/src/components/storyboard/SceneEditDialog.vue
git commit -m "feat(storyboard): reference pickers, voice field, describe-from-refs UI"
```

---

### Task 6: Docs + full verification

**Files:**
- Modify: `CLAUDE.md` (one bullet in the storyboard section)
- Modify: `docs/features.md` (one line)

- [ ] **Step 1: CLAUDE.md bullet** — after the story-engine bullet, add one bullet stating: describe endpoints (`POST /api/storyboard/subjects/{id}/describe`, `/scenes/{id}/describe`) return VLM-generated descriptor JSON and **never write the DB** — persistence goes through the normal PATCH flow so the user reviews first; `VlmClient.generate_text` accepts `image_paths` (multi-image, one `image_url` part per path, order = prompt reference order) with `image_path` kept as single-image sugar (both → `ValueError`); `pick_vlm_model` in `metascan/core/vlm_select.py` is the shared model picker (runner delegates); `storyboard_subjects.reference_path_2` and `scenes.reference_path` follow the `reference_path` POSIX/`InvalidReferenceError` conventions. Match the neighboring bullets' terse register; verify each claim against the code before writing it.

- [ ] **Step 2: docs/features.md** — one line under the storyboard feature: attach up to two subject refs + a per-scene setting ref from the library and have the VLM draft descriptions/voice/setting for review.

- [ ] **Step 3: Full verification** — `make quality test` (or `make VENV_DIR=/home/jk/gws/metascan/venv quality test` in a worktree) AND `cd frontend && npm run build`; then the scoped suite: `venv/bin/pytest tests/test_storyboard_refs_db.py tests/test_vlm_generate_text.py tests/test_ref_describe.py tests/test_storyboard_describe_api.py tests/test_storyboard_api.py tests/test_storyboard_runner.py -v` → all PASS.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/features.md
git commit -m "docs: reference-describe architecture notes and feature entry"
```
