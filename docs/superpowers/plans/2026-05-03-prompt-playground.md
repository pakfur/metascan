# Prompt Playground Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Prompt Playground" UI accessible from the thumbnail context menu that lets the user generate, transform, or clean up image-generation prompts for a single image using the active Qwen3-VL VLM, with named persistence in a new `saved_prompts` SQLite table. Build a reusable `/api/prompt/*` API surface that is decoupled from the playground UI so future features (ComfyUI integration, "image-to-prompt" right-click action, batch tools) can call it directly.

**Architecture:** Three thin VLM-backed endpoints (`/api/prompt/generate|transform|clean`) call the existing `VlmClient` non-streaming via a new `generate_text` method. Three CRUD endpoints (`/api/prompt/save`, `/api/prompt/by-image`, `/api/prompt/{id}`) manage the new `saved_prompts` table. Saved prompts are a side channel — the existing `Media.prompt` and the inverted `indices` tag table are untouched, so a saved playground prompt does **not** alter search behavior. A new Vue dialog (`PromptPlayground.vue`) is opened from the existing thumbnail context menu, uses an `AbortController` to cancel in-flight requests on Stop/close, and surfaces saved prompts in a basic Details-panel section.

**Tech Stack:** Python 3.11, FastAPI, httpx (existing), SQLite with WAL, Pydantic v2; Vue 3 + Pinia + PrimeVue (existing); existing `VlmClient` + `llama-server`. **No new Python or JS dependencies.**

**Source spec:** `docs/future_ideas.md` entry **TA-10** (Prompt Playground), plus the Q1–Q6 / G1–G8 decisions captured in conversation on 2026-05-03:
- Q1 = C: non-streaming responses (frontend shows "generating…", final block on completion).
- Q2: multi-select up to 3 styles, concatenated as a single clause in the system prompt.
- Q3 = A: t2i only in v1; t2v deferred.
- Q4 = C: positive prompt only in v1; `negative` column reserved (always NULL).
- Q5 = A: use whichever VLM is currently active; surface "Activate Qwen3-VL first" if none.
- Q6: schema as proposed (file_path FK, name, prompt, negative, target_model, architecture, styles JSON, temperature, max_tokens, source_prompt, mode, vlm_model_id, created_at, updated_at).
- G1: v1 ships `generate`, `transform`, `clean` (no other transforms).
- G2 = A: transform mode disabled when no existing prompt.
- G3: copy / edit / regenerate (with or without tweaks) / close (with confirm-if-unsaved).
- G4 = C: explicit "Generate" / "Re-run" click — no auto-regeneration on setting change.
- G5: confirmed — saved prompts are a side channel; existing `Media.prompt` and tag indices are untouched, no new tag rows are emitted.
- G6: concurrent playground sessions reuse the existing per-model `Semaphore(parallel_slots)` pattern; competing with batch tagging is acceptable.
- G7: Stop button + abort-on-close; non-streaming responses are aborted via `AbortController` propagating to FastAPI → httpx → llama-server.
- G8: globally last-used settings in localStorage; per-image saved-prompts list comes from DB.

---

## Phase Order and Dependencies

Ten tasks across four phases:

- **Phase 1 — Backend foundation (Tasks 1–3):** DB schema, prompt-template composer, VlmClient text generation. Each is independently testable; nothing here is wired into the running app yet.
- **Phase 2 — Backend API (Tasks 4–6):** Generation endpoints, CRUD endpoints, router registration. By the end the backend is fully functional via `curl`.
- **Phase 3 — Frontend infra (Task 7):** API client + Pinia store. No UI yet.
- **Phase 4 — Frontend UI (Tasks 8–10):** Dialog component, context-menu wiring + App.vue plumbing, MetadataPanel section. By the end the feature is usable end-to-end.

Each task ends with `make quality test` (backend) or `cd frontend && npm run build` (frontend) plus a commit. Skipping ahead is unsafe — Task 4 needs Tasks 1–3, Task 8 needs Task 7.

---

## Cross-Cutting Conventions

**Backend test fixtures.** DB tests use `pytest.fixture` + `tempfile.TemporaryDirectory` + `DatabaseManager(db_file)` — see `tests/test_folders_db.py` and `tests/test_database_photo_columns.py` for the pattern. API tests use `fastapi.testclient.TestClient` + `backend.api.vlm.set_vlm_client(stub)` — see `tests/test_lifespan_vlm.py`. Avoid spinning up the real `llama-server` in unit tests; use either a stub object or the existing `tests/_fake_llama_server.py` fixture (which speaks the same OpenAI chat-completions shape).

**Image input.** The `VlmClient` already provides `_encode_image_b64(path)` with 1024-px max-edge JPEG resize. Reuse it; do NOT add a parallel encoder. Supported extensions are gated by `VlmClient.is_image_path(path)` — videos / archives short-circuit.

**Side-channel discipline.** Saved prompts must NOT touch the existing `indices` table (`source='prompt'` rows would pollute tag search) or the `Media.prompt` field. They live exclusively in `saved_prompts`. This is asserted in Task 1's tests.

**Abort propagation.** Frontend uses `AbortController.abort()` on Stop and on dialog unmount. The `fetch` is rejected → FastAPI sees a `ClientDisconnect`/`asyncio.CancelledError` → `httpx` cancels its open POST to llama-server → llama-server detects the disconnect on `/v1/chat/completions` and aborts generation. No explicit Stop endpoint is needed. The `client.ts` shared `post()` helper is extended in Task 7 to pass through an optional `AbortSignal`.

**No grammar.** Unlike `generate_tags` (which uses GBNF for strict JSON), playground generation is free-form text. The system prompt instructs "output ONLY the prompt text — no preamble." Any leakage (e.g., `Sure, here is …`) is visible to the user in the editable result textarea — they fix or regenerate. We do NOT post-process the response in v1.

**Existing patterns to mirror.** `backend/api/vlm.py` is the closest analog for the new `backend/api/prompt.py` — same import shape, same `get_vlm_client()` access, same `asyncio.to_thread(db.…)` discipline for DB writes. `frontend/src/components/dialogs/UpscaleDialog.vue` is the closest analog for the new `PromptPlayground.vue` (same `dialog-overlay` / `dialog-card` styling, same `emit('close')` shape).

---

## Phase 1: Backend Foundation

### Task 1: `saved_prompts` table + DB CRUD methods

**Files:**
- Modify: `metascan/core/database_sqlite.py` (add CREATE TABLE inside `_init_database` after the `folder_items` index block at ~line 392; add 4 methods)
- Test: `tests/test_saved_prompts_db.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_saved_prompts_db.py
"""CRUD tests for the saved_prompts table.

Asserts side-channel discipline: saving a prompt does NOT touch the
indices table, so tag-search behavior is unaffected by playground use.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        m = DatabaseManager(Path(d))
        # Minimal media row so the FK passes (see tests/test_folders_api.py).
        m.save_media(Media(
            file_path=Path("/tmp/img.jpg"),
            file_size=1, width=1, height=1, format="jpg",
            created_at=datetime.now(), modified_at=datetime.now(),
        ))
        yield m


def test_save_then_list_returns_inserted_row(db):
    new_id = db.save_prompt(
        file_path="/tmp/img.jpg",
        name="anime variant",
        prompt="masterpiece, anime girl, blue eyes",
        target_model="sdxl",
        architecture="t2i",
        styles=["anime", "cinematic"],
        temperature=0.6,
        max_tokens=250,
        source_prompt=None,
        mode="generate",
        negative=None,
        vlm_model_id="qwen3vl-4b",
    )
    assert isinstance(new_id, int) and new_id > 0
    rows = db.list_saved_prompts("/tmp/img.jpg")
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == new_id
    assert r["name"] == "anime variant"
    assert r["styles"] == ["anime", "cinematic"]  # JSON-decoded
    assert r["mode"] == "generate"
    assert r["negative"] is None


def test_list_returns_newest_first(db):
    a = db.save_prompt(
        file_path="/tmp/img.jpg", name="a", prompt="p1",
        target_model="sdxl", architecture="t2i", styles=[],
        temperature=0.6, max_tokens=250, source_prompt=None,
        mode="generate", negative=None, vlm_model_id=None,
    )
    b = db.save_prompt(
        file_path="/tmp/img.jpg", name="b", prompt="p2",
        target_model="flux-chroma", architecture="t2i", styles=[],
        temperature=0.6, max_tokens=250, source_prompt=None,
        mode="generate", negative=None, vlm_model_id=None,
    )
    rows = db.list_saved_prompts("/tmp/img.jpg")
    assert [r["id"] for r in rows] == [b, a]  # DESC by created_at / id


def test_get_returns_single_row_or_none(db):
    new_id = db.save_prompt(
        file_path="/tmp/img.jpg", name="x", prompt="p",
        target_model="sdxl", architecture="t2i", styles=[],
        temperature=0.6, max_tokens=250, source_prompt=None,
        mode="generate", negative=None, vlm_model_id=None,
    )
    assert db.get_saved_prompt(new_id)["id"] == new_id
    assert db.get_saved_prompt(99999) is None


def test_delete_returns_bool_and_removes_row(db):
    new_id = db.save_prompt(
        file_path="/tmp/img.jpg", name="x", prompt="p",
        target_model="sdxl", architecture="t2i", styles=[],
        temperature=0.6, max_tokens=250, source_prompt=None,
        mode="generate", negative=None, vlm_model_id=None,
    )
    assert db.delete_saved_prompt(new_id) is True
    assert db.delete_saved_prompt(new_id) is False  # idempotent / missing
    assert db.list_saved_prompts("/tmp/img.jpg") == []


def test_save_does_not_touch_indices_table(db):
    """Side-channel discipline: saving a prompt must NOT emit tag rows."""
    db.save_prompt(
        file_path="/tmp/img.jpg", name="x",
        prompt="cyberpunk neon city, raining, octane render",
        target_model="sdxl", architecture="t2i", styles=[],
        temperature=0.6, max_tokens=250, source_prompt=None,
        mode="generate", negative=None, vlm_model_id=None,
    )
    with db.lock, db._get_connection() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM indices WHERE file_path=?",
            ("/tmp/img.jpg",),
        ).fetchone()
    assert rows["n"] == 0


def test_delete_media_cascades_saved_prompts(db):
    db.save_prompt(
        file_path="/tmp/img.jpg", name="x", prompt="p",
        target_model="sdxl", architecture="t2i", styles=[],
        temperature=0.6, max_tokens=250, source_prompt=None,
        mode="generate", negative=None, vlm_model_id=None,
    )
    db.delete_media(Path("/tmp/img.jpg"))
    assert db.list_saved_prompts("/tmp/img.jpg") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `KMP_DUPLICATE_LIB_OK=TRUE pytest tests/test_saved_prompts_db.py -v`
Expected: FAIL with `AttributeError: 'DatabaseManager' object has no attribute 'save_prompt'`.

- [ ] **Step 3: Add the table to `_init_database`**

In `metascan/core/database_sqlite.py`, add this block immediately after the `idx_folder_items_by_file` index creation (around line 393, before the `version_row = conn.execute("PRAGMA user_version")` block):

```python
# Saved prompts. Side-channel storage for the Prompt Playground feature
# (TA-10): named, persisted prompts associated with an image. NOT linked
# to the inverted `indices` table — these are user-curated experimental
# prompts, NOT canonical metadata, and must not affect tag search.
# CASCADE on file_path so deleting a media row clears its saved prompts.
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS saved_prompts (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path     TEXT NOT NULL
                      REFERENCES media(file_path) ON DELETE CASCADE,
        name          TEXT NOT NULL,
        prompt        TEXT NOT NULL,
        negative      TEXT,
        target_model  TEXT NOT NULL,
        architecture  TEXT NOT NULL,
        styles        TEXT NOT NULL DEFAULT '[]',
        temperature   REAL,
        max_tokens    INTEGER,
        source_prompt TEXT,
        mode          TEXT NOT NULL
                      CHECK(mode IN ('generate','transform','clean')),
        vlm_model_id  TEXT,
        created_at    TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """
)
conn.execute(
    "CREATE INDEX IF NOT EXISTS idx_saved_prompts_file "
    "ON saved_prompts(file_path)"
)
```

- [ ] **Step 4: Add the four CRUD methods**

In the same file, add these methods near `save_media` (around line 561). The exact placement isn't critical — group them logically near other CRUD methods:

```python
def save_prompt(
    self,
    *,
    file_path: str,
    name: str,
    prompt: str,
    target_model: str,
    architecture: str,
    styles: List[str],
    temperature: Optional[float],
    max_tokens: Optional[int],
    source_prompt: Optional[str],
    mode: str,
    negative: Optional[str],
    vlm_model_id: Optional[str],
) -> int:
    """Insert a saved prompt; return its new auto-incremented id."""
    import json as _json
    with self.lock:
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO saved_prompts (
                    file_path, name, prompt, negative,
                    target_model, architecture, styles,
                    temperature, max_tokens, source_prompt,
                    mode, vlm_model_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_path, name, prompt, negative,
                    target_model, architecture, _json.dumps(list(styles)),
                    temperature, max_tokens, source_prompt,
                    mode, vlm_model_id,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)


def list_saved_prompts(self, file_path: str) -> List[Dict[str, Any]]:
    """All saved prompts for a media file_path, newest first."""
    import json as _json
    with self.lock:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM saved_prompts WHERE file_path=? "
                "ORDER BY id DESC",
                (file_path,),
            ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["styles"] = _json.loads(d["styles"]) if d.get("styles") else []
        except (TypeError, ValueError):
            d["styles"] = []
        out.append(d)
    return out


def get_saved_prompt(self, prompt_id: int) -> Optional[Dict[str, Any]]:
    import json as _json
    with self.lock:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM saved_prompts WHERE id=?", (prompt_id,)
            ).fetchone()
    if row is None:
        return None
    d = dict(row)
    try:
        d["styles"] = _json.loads(d["styles"]) if d.get("styles") else []
    except (TypeError, ValueError):
        d["styles"] = []
    return d


def delete_saved_prompt(self, prompt_id: int) -> bool:
    """Return True if a row was deleted, False if no row existed."""
    with self.lock:
        with self._get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM saved_prompts WHERE id=?", (prompt_id,)
            )
            conn.commit()
            return cur.rowcount > 0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `KMP_DUPLICATE_LIB_OK=TRUE pytest tests/test_saved_prompts_db.py -v`
Expected: 6 tests PASS.

- [ ] **Step 6: Quality + commit**

Run: `make quality test`
Expected: full suite still green.

```bash
git add metascan/core/database_sqlite.py tests/test_saved_prompts_db.py
git commit -m "Add saved_prompts table + CRUD for Prompt Playground

Side-channel storage for user-curated experimental prompts. Does not
touch the inverted indices or Media.prompt, so tag search is unaffected.
ON DELETE CASCADE keeps the table clean when media rows are deleted."
```

---

### Task 2: Prompt template composer module

**Files:**
- Create: `metascan/core/prompt_templates.py`
- Test: `tests/test_prompt_templates.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompt_templates.py
"""Pure-Python tests for the prompt-template composer.

No model invocation — just verifies that the system + user prompts
returned by each composer mention the right model, contain (or omit)
the style clause as appropriate, and reject invalid input.
"""

from __future__ import annotations

import pytest

from metascan.core.prompt_templates import (
    STYLE_PHRASES,
    TARGET_MODEL_GUIDANCE,
    compose_clean_prompts,
    compose_generate_prompts,
    compose_transform_prompts,
)


def test_generate_includes_target_model_and_format():
    sys, usr = compose_generate_prompts("sdxl", "t2i", [])
    assert "sdxl" in sys.lower()
    assert "t2i" in sys.lower()
    assert TARGET_MODEL_GUIDANCE["sdxl"] in sys
    assert "no preamble" in sys.lower()
    assert usr  # non-empty


def test_generate_no_styles_has_no_style_clause():
    sys, _ = compose_generate_prompts("flux-chroma", "t2i", [])
    assert "stylistic directions" not in sys.lower()


def test_generate_with_styles_concatenates_phrases():
    sys, _ = compose_generate_prompts(
        "pony", "t2i", ["anime", "cinematic"]
    )
    assert STYLE_PHRASES["anime"] in sys
    assert STYLE_PHRASES["cinematic"] in sys
    assert "stylistic directions" in sys.lower()


def test_generate_rejects_more_than_three_styles():
    with pytest.raises(ValueError, match="3"):
        compose_generate_prompts(
            "sdxl", "t2i",
            ["anime", "cinematic", "watercolor", "comic"],
        )


def test_transform_includes_source_and_target():
    sys, usr = compose_transform_prompts(
        "an old prompt", "qwen-t2i", "t2i"
    )
    assert "qwen-t2i" in sys.lower()
    assert "an old prompt" in usr
    assert "rewrite" in sys.lower()


def test_clean_returns_terse_system():
    sys, usr = compose_clean_prompts("messy,, prompt,, here  ")
    assert "clean" in sys.lower()
    assert "messy,, prompt,, here  " in usr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `KMP_DUPLICATE_LIB_OK=TRUE pytest tests/test_prompt_templates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'metascan.core.prompt_templates'`.

- [ ] **Step 3: Create the composer module**

Create `metascan/core/prompt_templates.py`:

```python
"""Prompt-template composer for the /api/prompt endpoints (TA-10).

Pure functions — no I/O, no model calls. Each composer returns a
``(system_prompt, user_prompt)`` tuple ready to feed into
``VlmClient.generate_text``.

The Literal types are the single source of truth for the playground
target-model / style enums; the API layer (`backend/api/prompt.py`) and
the frontend (`frontend/src/api/prompt.ts`) mirror them.
"""

from __future__ import annotations

from typing import Literal


TargetModel = Literal["sdxl", "flux-chroma", "qwen-t2i", "pony"]
Architecture = Literal["t2i"]  # t2v / i2v / i2i deferred to v2
StyleEnhancement = Literal[
    "anime",
    "photorealistic",
    "cinematic",
    "cartoon",
    "watercolor",
    "oil-painting",
    "comic",
    "hyperdetailed",
    "minimalist",
    "moody-lighting",
]


TARGET_MODEL_GUIDANCE: dict[TargetModel, str] = {
    "sdxl": (
        "comma-separated descriptive phrases, subject first then "
        "attributes/style/lighting/composition; weighted parens optional."
    ),
    "flux-chroma": (
        "a single natural-language paragraph describing subject, "
        "setting, lighting, mood, in flowing prose."
    ),
    "qwen-t2i": (
        "a natural-language sentence describing the subject and key "
        "attributes; no syntax conventions."
    ),
    "pony": (
        "Danbooru-style underscored tags, comma-separated, leading with "
        "score_9, score_8_up, score_7_up, then character/series/attributes."
    ),
}


STYLE_PHRASES: dict[StyleEnhancement, str] = {
    "anime": "anime aesthetic with cel-shaded shapes",
    "photorealistic": "photorealistic style with realistic lighting and textures",
    "cinematic": "cinematic composition with dramatic lighting",
    "cartoon": "cartoon / illustrated style with bold outlines",
    "watercolor": "watercolor painting with soft washes",
    "oil-painting": "oil painting with visible brushwork",
    "comic": "comic-book style with cel shading and ink lines",
    "hyperdetailed": "hyperdetailed rendering, intricate fine detail",
    "minimalist": "minimalist composition with limited color palette",
    "moody-lighting": "moody, low-key lighting with deep shadows",
}


_OUTPUT_RULE = (
    "Output ONLY the prompt text — no preamble, no commentary, no quotes."
)


def _style_clause(styles: list[StyleEnhancement]) -> str:
    if not styles:
        return ""
    if len(styles) > 3:
        raise ValueError("at most 3 style enhancements allowed")
    phrases = [STYLE_PHRASES[s] for s in styles]
    return " Apply these stylistic directions: " + "; ".join(phrases) + "."


def compose_generate_prompts(
    target_model: TargetModel,
    architecture: Architecture,
    styles: list[StyleEnhancement],
) -> tuple[str, str]:
    """System + user prompts for a fresh generate-from-image request."""
    style_clause = _style_clause(styles)
    system = (
        f"You are an expert prompt engineer for AI image generation. "
        f"Look at the supplied image and produce a prompt suitable for "
        f"a {target_model} {architecture} model.{style_clause} "
        f"Format: {TARGET_MODEL_GUIDANCE[target_model]} "
        f"{_OUTPUT_RULE}"
    )
    user = "Write a prompt that would generate this image."
    return system, user


def compose_transform_prompts(
    source_prompt: str,
    target_model: TargetModel,
    architecture: Architecture,
) -> tuple[str, str]:
    """System + user prompts for rewriting an existing prompt for a new target."""
    system = (
        f"You are an expert prompt engineer. Rewrite the supplied prompt "
        f"to suit a {target_model} {architecture} model. Preserve the "
        f"subject and key attributes; adapt syntax and conventions to the "
        f"target. Format: {TARGET_MODEL_GUIDANCE[target_model]} "
        f"{_OUTPUT_RULE}"
    )
    user = (
        f"Original prompt:\n{source_prompt}\n\n"
        f"Rewrite for {target_model} {architecture}."
    )
    return system, user


def compose_clean_prompts(source_prompt: str) -> tuple[str, str]:
    """System + user prompts for a cleanup pass — no target-model semantics."""
    system = (
        "Clean up the supplied AI image-generation prompt: remove "
        "redundancies, fix typos, normalize separators, but preserve "
        f"all meaningful content and style. {_OUTPUT_RULE}"
    )
    user = f"Prompt to clean:\n{source_prompt}"
    return system, user


__all__ = [
    "TargetModel",
    "Architecture",
    "StyleEnhancement",
    "TARGET_MODEL_GUIDANCE",
    "STYLE_PHRASES",
    "compose_generate_prompts",
    "compose_transform_prompts",
    "compose_clean_prompts",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `KMP_DUPLICATE_LIB_OK=TRUE pytest tests/test_prompt_templates.py -v`
Expected: 6 tests PASS.

- [ ] **Step 5: Quality + commit**

Run: `make quality test`

```bash
git add metascan/core/prompt_templates.py tests/test_prompt_templates.py
git commit -m "Add prompt-template composer for /api/prompt endpoints

Pure functions for generate / transform / clean. The Literal types are
the source of truth for target-model / style enums; the API layer and
frontend mirror them."
```

---

### Task 3: `VlmClient.generate_text`

**Files:**
- Modify: `metascan/core/vlm_client.py` (add `generate_text` method near `generate_tags` at line 401; add no other changes)
- Test: `tests/test_vlm_generate_text.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vlm_generate_text.py
"""Tests for VlmClient.generate_text.

Uses a small in-process httpx stub instead of the fake llama-server
subprocess: generate_text only exercises the request-marshalling /
response-parsing path, so spinning up a subprocess for every test is
overkill. We poke at ``client._http`` and ``client._state`` directly —
this is a known boundary violation, accepted because the tests live in
the same package and the alternative (extending the fake server with
admin endpoints for body inspection) is more code without more clarity.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from PIL import Image

from metascan.core.vlm_client import (
    STATE_IDLE,
    STATE_READY,
    VlmClient,
    VlmError,
)


class _StubHttp:
    """Replaces VlmClient._http for unit testing generate_text."""

    def __init__(self, response_content: str = "generated text"):
        self.calls: list[dict] = []
        self.response_content = response_content
        self.next_status = 200
        self.next_error_message = "stub error"

    async def post(self, path: str, *, json=None, timeout=None):
        assert path == "/v1/chat/completions"
        self.calls.append(json)
        if self.next_status >= 400:
            req = httpx.Request("POST", "http://stub" + path)
            resp = httpx.Response(
                self.next_status,
                json={"error": {"message": self.next_error_message}},
                request=req,
            )
            raise httpx.HTTPStatusError("stub", request=req, response=resp)
        return _StubResp(self.response_content)


class _StubResp:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


@pytest.fixture
def ready_client():
    """VlmClient pre-flipped to READY with a stub http transport."""
    client = VlmClient()
    stub = _StubHttp()
    client._http = stub  # type: ignore[assignment]
    client._state = STATE_READY
    client._model_id = "qwen3vl-4b"
    try:
        yield client, stub
    finally:
        client._http = None
        client._state = STATE_IDLE


@pytest.mark.asyncio
async def test_generate_text_returns_content(ready_client):
    client, _ = ready_client
    text = await client.generate_text(
        system_prompt="You are a prompt engineer.",
        user_prompt="Write a prompt.",
    )
    assert text == "generated text"


@pytest.mark.asyncio
async def test_generate_text_text_only_uses_string_content(ready_client):
    """Without image_path the user message content is a plain string,
    not the image_url-array shape used when grounding on an image."""
    client, stub = ready_client
    await client.generate_text(system_prompt="sys", user_prompt="say hi")
    body = stub.calls[-1]
    assert isinstance(body["messages"][1]["content"], str)
    assert body["messages"][1]["content"] == "say hi"


@pytest.mark.asyncio
async def test_generate_text_with_image_attaches_image_part(
    ready_client, tmp_path
):
    client, stub = ready_client
    img = tmp_path / "x.jpg"
    Image.new("RGB", (4, 4), color="white").save(img, "JPEG")

    await client.generate_text(
        system_prompt="sys", user_prompt="describe", image_path=img
    )
    body = stub.calls[-1]
    parts = body["messages"][1]["content"]
    assert isinstance(parts, list)
    assert any(p.get("type") == "image_url" for p in parts)
    assert any(p.get("type") == "text" and p["text"] == "describe" for p in parts)


@pytest.mark.asyncio
async def test_generate_text_raises_vlm_error_on_500(ready_client):
    """Unlike generate_tags (which swallows errors and returns []),
    generate_text must surface failures so the playground can show them."""
    client, stub = ready_client
    stub.next_status = 500
    stub.next_error_message = "upstream boom"
    with pytest.raises(VlmError, match="500"):
        await client.generate_text(system_prompt="sys", user_prompt="x")


@pytest.mark.asyncio
async def test_generate_text_passes_temperature_and_max_tokens(ready_client):
    client, stub = ready_client
    await client.generate_text(
        system_prompt="sys", user_prompt="x",
        temperature=0.9, max_tokens=42,
    )
    body = stub.calls[-1]
    assert body["temperature"] == pytest.approx(0.9)
    assert body["max_tokens"] == 42


@pytest.mark.asyncio
async def test_generate_text_raises_when_not_ready():
    """No flip to READY -> immediate VlmError, no HTTP call attempted."""
    client = VlmClient()  # state == IDLE, _http is None
    with pytest.raises(VlmError, match="not ready"):
        await client.generate_text(system_prompt="s", user_prompt="u")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `KMP_DUPLICATE_LIB_OK=TRUE pytest tests/test_vlm_generate_text.py -v`
Expected: FAIL with `AttributeError: 'VlmClient' object has no attribute 'generate_text'`.

- [ ] **Step 3: Add the method**

In `metascan/core/vlm_client.py`, immediately after `generate_tags` (line ~482), add:

```python
async def generate_text(
    self,
    *,
    system_prompt: str,
    user_prompt: str,
    image_path: Optional[Path] = None,
    temperature: float = 0.6,
    max_tokens: int = 250,
    timeout: float = 120.0,
) -> str:
    """Free-form text generation (image-grounded or text-only).

    Sends a single chat completion to llama-server. Unlike
    :meth:`generate_tags`, this raises :class:`VlmError` on failure rather
    than swallowing it — playground / API callers want to surface errors
    to the user instead of silently returning empty.

    When ``image_path`` is provided, the file is base64-encoded as JPEG
    and attached as an ``image_url`` part. When ``None``, the request is
    text-only — Qwen3-VL handles text-only inference fine, no model swap
    is needed.
    """
    if self._http is None or self._state != STATE_READY:
        raise VlmError(
            f"VlmClient not ready (state={self._state}); "
            "call ensure_started() first"
        )

    user_content: Any
    if image_path is not None:
        if not self.is_image_path(image_path):
            raise VlmError(
                f"unsupported image type: {image_path.suffix}"
            )
        image_b64 = await asyncio.to_thread(
            self._encode_image_b64, image_path
        )
        user_content = [
            {"type": "text", "text": user_prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{image_b64}"
                },
            },
        ]
    else:
        user_content = user_prompt

    body = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    try:
        r = await self._http.post(
            "/v1/chat/completions", json=body, timeout=timeout
        )
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            payload = e.response.json()
            detail = (
                payload.get("error", {}).get("message")
                or str(payload)
            )
        except Exception:
            detail = (e.response.text or "")[:300]
        raise VlmError(
            f"llama-server returned HTTP "
            f"{e.response.status_code}: {detail}"
        ) from e
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise VlmError(f"llama-server request failed: {e}") from e
    return str(content).strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `KMP_DUPLICATE_LIB_OK=TRUE pytest tests/test_vlm_generate_text.py -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Quality + commit**

Run: `make quality test`

Expected: full suite green. Note the test also requires `pytest-asyncio` — verify it's already in `requirements-dev.txt` (existing async tests under `tests/` confirm this; no install needed).

```bash
git add metascan/core/vlm_client.py tests/test_vlm_generate_text.py
git commit -m "Add VlmClient.generate_text for free-form prompt generation

Image-grounded or text-only chat-completion against the active VLM.
Raises VlmError on failure (playground callers want errors surfaced,
unlike generate_tags which silently returns []). No grammar — system
prompt instructs 'output ONLY the prompt'."
```

---

## Phase 2: Backend API

### Task 4: Generation endpoints — `/api/prompt/generate|transform|clean`

**Files:**
- Create: `backend/api/prompt.py`
- Test: `tests/test_prompt_api_generate.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompt_api_generate.py
"""Endpoint tests for /api/prompt/generate, /transform, /clean.

Builds a minimal FastAPI app with only the prompt router so that
``backend.main.create_app``'s lifespan (which installs a real
VlmClient) doesn't clobber our stub. The router accesses the
VlmClient via ``backend.api.vlm.get_vlm_client`` — we set the
module-level ``_vlm_client`` directly.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import prompt as prompt_api
from backend.api import vlm as vlm_api
from metascan.core.vlm_client import STATE_READY, VlmError


class _StubVlm:
    state = STATE_READY
    model_id = "qwen3vl-4b"

    def __init__(self):
        self.calls = []
        self.next_response = "stubbed prompt"
        self.next_error: VlmError | None = None

    async def generate_text(self, **kwargs):
        self.calls.append(kwargs)
        if self.next_error is not None:
            raise self.next_error
        return self.next_response


def _build_app() -> FastAPI:
    """Minimal app with just the prompt router — skips the heavyweight
    create_app lifespan that would install a real VlmClient."""
    app = FastAPI()
    app.include_router(prompt_api.router)
    return app


@pytest.fixture
def stub_vlm():
    s = _StubVlm()
    vlm_api.set_vlm_client(s)
    try:
        yield s
    finally:
        vlm_api.set_vlm_client(None)


@pytest.fixture
def img_file():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test.jpg"
        Image.new("RGB", (8, 8), color="red").save(p, "JPEG")
        yield p


def test_generate_returns_prompt_and_metadata(stub_vlm, img_file):
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "sdxl",
                "architecture": "t2i",
                "styles": [],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["prompt"] == "stubbed prompt"
    assert body["vlm_model_id"] == "qwen3vl-4b"
    assert "elapsed_ms" in body


def test_generate_passes_styles_into_system_prompt(stub_vlm, img_file):
    with TestClient(_build_app()) as c:
        c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "pony", "architecture": "t2i",
                "styles": ["anime", "cinematic"],
                "temperature": 0.6, "max_tokens": 250,
            },
        )
    assert len(stub_vlm.calls) == 1
    sys_prompt = stub_vlm.calls[0]["system_prompt"]
    assert "anime" in sys_prompt
    assert "cinematic" in sys_prompt


def test_generate_404_when_file_missing(stub_vlm):
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/generate",
            json={
                "file_path": "/nonexistent/x.jpg",
                "target_model": "sdxl", "architecture": "t2i",
                "styles": [], "temperature": 0.6, "max_tokens": 250,
            },
        )
    assert r.status_code == 404


def test_generate_503_when_vlm_not_installed(img_file):
    """No stub fixture used -> _vlm_client is None -> 503."""
    vlm_api.set_vlm_client(None)
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "sdxl", "architecture": "t2i",
                "styles": [], "temperature": 0.6, "max_tokens": 250,
            },
        )
    assert r.status_code == 503


def test_generate_503_when_vlm_idle(img_file):
    """Idle (non-READY) client -> 503 with explicit 'activate' message."""
    class _Idle:
        state = "idle"
        model_id = None
    vlm_api.set_vlm_client(_Idle())
    try:
        with TestClient(_build_app()) as c:
            r = c.post(
                "/api/prompt/generate",
                json={
                    "file_path": str(img_file),
                    "target_model": "sdxl", "architecture": "t2i",
                    "styles": [], "temperature": 0.6, "max_tokens": 250,
                },
            )
        assert r.status_code == 503
    finally:
        vlm_api.set_vlm_client(None)


def test_generate_400_when_too_many_styles(stub_vlm, img_file):
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "sdxl", "architecture": "t2i",
                "styles": ["anime", "cinematic", "watercolor", "comic"],
                "temperature": 0.6, "max_tokens": 250,
            },
        )
    # Pydantic Literal validation runs before our handler — invalid styles
    # produce a 422 from FastAPI, but a too-large valid-styles list reaches
    # _style_clause and returns 400. Both are acceptable for "too many".
    assert r.status_code in (400, 422)


def test_generate_502_when_vlm_raises(stub_vlm, img_file):
    stub_vlm.next_error = VlmError("upstream boom")
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "sdxl", "architecture": "t2i",
                "styles": [], "temperature": 0.6, "max_tokens": 250,
            },
        )
    assert r.status_code == 502
    assert "upstream boom" in r.json()["detail"]


def test_transform_passes_source_prompt_through(stub_vlm):
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/transform",
            json={
                "source_prompt": "old prompt here",
                "target_model": "flux-chroma",
                "architecture": "t2i",
                "temperature": 0.6, "max_tokens": 250,
            },
        )
    assert r.status_code == 200
    user = stub_vlm.calls[0]["user_prompt"]
    assert "old prompt here" in user


def test_clean_uses_clean_template(stub_vlm):
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/clean",
            json={
                "source_prompt": "messy,, prompt",
                "temperature": 0.4, "max_tokens": 250,
            },
        )
    assert r.status_code == 200
    sys = stub_vlm.calls[0]["system_prompt"]
    assert "clean" in sys.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `KMP_DUPLICATE_LIB_OK=TRUE pytest tests/test_prompt_api_generate.py -v`
Expected: FAIL with `404 Not Found` for `/api/prompt/generate` (router not registered yet) or `ModuleNotFoundError`.

- [ ] **Step 3: Create the router file**

Create `backend/api/prompt.py`:

```python
"""REST endpoints for VLM-backed prompt generation, transformation, and cleanup.

Decoupled from the playground UI — these endpoints are reusable by
ComfyUI custom nodes, batch tooling, and right-click actions. They are
non-streaming (the frontend shows "generating…" and renders the final
block) and abortable via standard client-disconnect propagation.

The VlmClient singleton is shared with backend.api.vlm; if no model is
active the endpoints return 503. Use POST /api/vlm/active first.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.api import vlm as vlm_api
from backend.dependencies import get_db
from metascan.core.prompt_templates import (
    Architecture,
    StyleEnhancement,
    TargetModel,
    compose_clean_prompts,
    compose_generate_prompts,
    compose_transform_prompts,
)
from metascan.core.vlm_client import STATE_READY, VlmError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prompt", tags=["prompt"])


# ---- Request / response models -------------------------------------------


class GenerateRequest(BaseModel):
    file_path: str
    target_model: TargetModel
    architecture: Architecture
    styles: List[StyleEnhancement] = Field(default_factory=list)
    temperature: float = 0.6
    max_tokens: int = 250


class TransformRequest(BaseModel):
    source_prompt: str
    target_model: TargetModel
    architecture: Architecture
    file_path: Optional[str] = None  # optional image grounding
    temperature: float = 0.6
    max_tokens: int = 250


class CleanRequest(BaseModel):
    source_prompt: str
    temperature: float = 0.4
    max_tokens: int = 250


class GenerateResponse(BaseModel):
    prompt: str
    vlm_model_id: str
    elapsed_ms: int


# ---- Helpers --------------------------------------------------------------


def _require_ready_client() -> Any:
    client = vlm_api.get_vlm_client()
    if client is None or client.state != STATE_READY:
        raise HTTPException(
            status_code=503,
            detail="VLM not ready — activate Qwen3-VL first",
        )
    return client


def _require_existing_file(path_str: str) -> Path:
    p = Path(path_str)
    if not p.is_file():
        raise HTTPException(
            status_code=404, detail=f"file not found: {path_str}"
        )
    return p


# ---- Generation endpoints ------------------------------------------------


@router.post("/generate", response_model=GenerateResponse)
async def generate(body: GenerateRequest) -> GenerateResponse:
    client = _require_ready_client()
    p = _require_existing_file(body.file_path)
    try:
        system, user = compose_generate_prompts(
            body.target_model, body.architecture, list(body.styles)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    start = time.monotonic()
    try:
        text = await client.generate_text(
            system_prompt=system,
            user_prompt=user,
            image_path=p,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
        )
    except VlmError as e:
        raise HTTPException(status_code=502, detail=str(e))
    elapsed = int((time.monotonic() - start) * 1000)
    return GenerateResponse(
        prompt=text,
        vlm_model_id=client.model_id or "",
        elapsed_ms=elapsed,
    )


@router.post("/transform", response_model=GenerateResponse)
async def transform(body: TransformRequest) -> GenerateResponse:
    client = _require_ready_client()
    image_path = (
        _require_existing_file(body.file_path) if body.file_path else None
    )
    system, user = compose_transform_prompts(
        body.source_prompt, body.target_model, body.architecture
    )
    start = time.monotonic()
    try:
        text = await client.generate_text(
            system_prompt=system,
            user_prompt=user,
            image_path=image_path,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
        )
    except VlmError as e:
        raise HTTPException(status_code=502, detail=str(e))
    elapsed = int((time.monotonic() - start) * 1000)
    return GenerateResponse(
        prompt=text,
        vlm_model_id=client.model_id or "",
        elapsed_ms=elapsed,
    )


@router.post("/clean", response_model=GenerateResponse)
async def clean(body: CleanRequest) -> GenerateResponse:
    client = _require_ready_client()
    system, user = compose_clean_prompts(body.source_prompt)
    start = time.monotonic()
    try:
        text = await client.generate_text(
            system_prompt=system,
            user_prompt=user,
            image_path=None,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
        )
    except VlmError as e:
        raise HTTPException(status_code=502, detail=str(e))
    elapsed = int((time.monotonic() - start) * 1000)
    return GenerateResponse(
        prompt=text,
        vlm_model_id=client.model_id or "",
        elapsed_ms=elapsed,
    )
```

- [ ] **Step 4: Register the router temporarily for tests to pass**

In `backend/main.py`, add the import + `include_router` call. (Permanent registration also lands in Task 6, but the test in this task needs the router live.)

```python
# Add to imports near other backend.api imports:
from backend.api import prompt as prompt_api

# Add to the include_router block:
app.include_router(prompt_api.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `KMP_DUPLICATE_LIB_OK=TRUE pytest tests/test_prompt_api_generate.py -v`
Expected: 8 tests PASS.

- [ ] **Step 6: Quality + commit**

Run: `make quality test`

```bash
git add backend/api/prompt.py backend/main.py tests/test_prompt_api_generate.py
git commit -m "Add /api/prompt/generate, /transform, /clean endpoints

Backed by VlmClient.generate_text. Decoupled from the playground UI so
ComfyUI / batch tooling can call the same surface. Non-streaming;
abortable via client-disconnect propagation. 503 if VLM inactive,
400 on style overflow, 404 on missing file, 502 on VLM error."
```

---

### Task 5: CRUD endpoints — `/api/prompt/save`, `/by-image`, `/{id}`

**Files:**
- Modify: `backend/api/prompt.py` (append CRUD endpoints + Pydantic models)
- Test: `tests/test_prompt_api_crud.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompt_api_crud.py
"""Endpoint tests for the saved-prompt CRUD surface.

``get_db`` is a singleton accessor (not a FastAPI dependency-injected
function), so the override pattern is to set ``backend.dependencies.
_db_singleton`` directly to a temp DB. This mirrors
``tests/test_folders_api.py:setUp``.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.dependencies as deps
from backend.api import prompt as prompt_api
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


@pytest.fixture
def client_with_db():
    """Yield (TestClient, DB) backed by a temp SQLite file. Restores the
    singleton on teardown so other tests see no spillover."""
    saved = deps._db_singleton
    with tempfile.TemporaryDirectory() as d:
        db = DatabaseManager(Path(d))
        db.save_media(Media(
            file_path=Path("/tmp/img.jpg"),
            file_size=1, width=1, height=1, format="jpg",
            created_at=datetime.now(), modified_at=datetime.now(),
        ))
        deps._db_singleton = db
        app = FastAPI()
        app.include_router(prompt_api.router)
        try:
            with TestClient(app) as c:
                yield c, db
        finally:
            deps._db_singleton = saved


def test_save_then_list_returns_inserted_row(client_with_db):
    c, _ = client_with_db
    r = c.post(
        "/api/prompt/save",
        json={
            "file_path": "/tmp/img.jpg",
            "name": "my anime variant",
            "prompt": "masterpiece, anime girl",
            "target_model": "sdxl",
            "architecture": "t2i",
            "styles": ["anime"],
            "temperature": 0.6, "max_tokens": 250,
            "source_prompt": None,
            "mode": "generate",
            "negative": None,
            "vlm_model_id": "qwen3vl-4b",
        },
    )
    assert r.status_code == 200
    new_id = r.json()["id"]

    r2 = c.get(
        "/api/prompt/by-image",
        params={"file_path": "/tmp/img.jpg"},
    )
    assert r2.status_code == 200
    rows = r2.json()
    assert len(rows) == 1
    assert rows[0]["id"] == new_id
    assert rows[0]["name"] == "my anime variant"
    assert rows[0]["styles"] == ["anime"]


def test_delete_removes_row(client_with_db):
    c, _ = client_with_db
    new_id = c.post(
        "/api/prompt/save",
        json={
            "file_path": "/tmp/img.jpg",
            "name": "x", "prompt": "p",
            "target_model": "sdxl", "architecture": "t2i",
            "styles": [], "temperature": 0.6, "max_tokens": 250,
            "source_prompt": None, "mode": "generate",
            "negative": None, "vlm_model_id": None,
        },
    ).json()["id"]

    r = c.delete(f"/api/prompt/{new_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "deleted"

    r2 = c.delete(f"/api/prompt/{new_id}")
    assert r2.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `KMP_DUPLICATE_LIB_OK=TRUE pytest tests/test_prompt_api_crud.py -v`
Expected: FAIL with 404 on `/api/prompt/save` (endpoint not yet defined).

- [ ] **Step 3: Append CRUD endpoints to `backend/api/prompt.py`**

Append at the bottom of `backend/api/prompt.py`:

```python
# ---- Saved-prompt CRUD ---------------------------------------------------


class SaveRequest(BaseModel):
    file_path: str
    name: str
    prompt: str
    target_model: str
    architecture: str
    styles: List[str] = Field(default_factory=list)
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    source_prompt: Optional[str] = None
    mode: Literal["generate", "transform", "clean"]
    negative: Optional[str] = None
    vlm_model_id: Optional[str] = None


class SavedPromptOut(BaseModel):
    id: int
    file_path: str
    name: str
    prompt: str
    negative: Optional[str]
    target_model: str
    architecture: str
    styles: List[str]
    temperature: Optional[float]
    max_tokens: Optional[int]
    source_prompt: Optional[str]
    mode: str
    vlm_model_id: Optional[str]
    created_at: str
    updated_at: str


@router.post("/save")
async def save_prompt(body: SaveRequest) -> Dict[str, int]:
    db = get_db()
    try:
        new_id = await asyncio.to_thread(
            db.save_prompt,
            file_path=body.file_path,
            name=body.name,
            prompt=body.prompt,
            target_model=body.target_model,
            architecture=body.architecture,
            styles=list(body.styles),
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            source_prompt=body.source_prompt,
            mode=body.mode,
            negative=body.negative,
            vlm_model_id=body.vlm_model_id,
        )
    except Exception as e:
        # Most likely an FK violation (file_path not in media). Surface it.
        logger.warning("save_prompt failed: %s", e)
        raise HTTPException(status_code=400, detail=f"save failed: {e}")
    return {"id": new_id}


@router.get("/by-image", response_model=List[SavedPromptOut])
async def list_by_image(file_path: str) -> List[SavedPromptOut]:
    db = get_db()
    rows = await asyncio.to_thread(db.list_saved_prompts, file_path)
    return [SavedPromptOut(**r) for r in rows]


@router.delete("/{prompt_id}")
async def delete_prompt(prompt_id: int) -> Dict[str, str]:
    db = get_db()
    deleted = await asyncio.to_thread(db.delete_saved_prompt, prompt_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"saved prompt {prompt_id} not found"
        )
    return {"status": "deleted"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `KMP_DUPLICATE_LIB_OK=TRUE pytest tests/test_prompt_api_crud.py -v`
Expected: 2 tests PASS.

- [ ] **Step 5: Quality + commit**

Run: `make quality test`

```bash
git add backend/api/prompt.py tests/test_prompt_api_crud.py
git commit -m "Add /api/prompt CRUD endpoints for saved prompts

POST /save persists, GET /by-image lists by file_path, DELETE /{id}
removes. Wraps DB calls in asyncio.to_thread per the project pattern."
```

---

### Task 6: Confirm router registration + integration smoke test

**Files:**
- Modify: `backend/main.py` (verify the import + include_router are present from Task 4)

This task is a checkpoint. Task 4 added the registration; this task confirms it survived and that the FastAPI app starts cleanly with the new router.

- [ ] **Step 1: Verify the registration is present**

Open `backend/main.py` and confirm:
- `from backend.api import prompt as prompt_api` is among the imports.
- `app.include_router(prompt_api.router)` is in the `create_app` registration block (between `app.include_router(vlm.router)` and `app.include_router(websocket.router)` is fine).

If either is missing, add it.

- [ ] **Step 2: Smoke test the registered routes via TestClient**

Run this one-off:

```bash
KMP_DUPLICATE_LIB_OK=TRUE python -c "
from fastapi.testclient import TestClient
from backend.main import create_app
app = create_app()
with TestClient(app) as c:
    paths = [r.path for r in app.routes]
    expected = [
        '/api/prompt/generate',
        '/api/prompt/transform',
        '/api/prompt/clean',
        '/api/prompt/save',
        '/api/prompt/by-image',
        '/api/prompt/{prompt_id}',
    ]
    missing = [p for p in expected if p not in paths]
    assert not missing, f'missing routes: {missing}'
    print('all 6 routes registered')
"
```

Expected output: `all 6 routes registered`.

- [ ] **Step 3: Run the full backend suite**

Run: `make quality test`
Expected: full suite green.

- [ ] **Step 4: Commit (only if main.py changed during this task)**

If Task 4 already wrote main.py and nothing else changed here, skip the commit. Otherwise:

```bash
git add backend/main.py
git commit -m "Register /api/prompt router in FastAPI"
```

---

## Phase 3: Frontend Infrastructure

### Task 7: Frontend API client + Pinia store + abortable post()

**Files:**
- Modify: `frontend/src/api/client.ts` (one method signature change)
- Create: `frontend/src/api/prompt.ts`
- Create: `frontend/src/stores/prompt.ts`

- [ ] **Step 1: Extend `client.ts` post() to accept an AbortSignal**

Edit `frontend/src/api/client.ts`. Change the `post` function signature and pass `signal` through to `fetch`:

```typescript
export function post<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  })
}
```

(The `request<T>` helper already forwards arbitrary `RequestInit` options to `fetch`, so no other change is needed.)

- [ ] **Step 2: Create the typed API client**

Create `frontend/src/api/prompt.ts`:

```typescript
import { del, get, post } from './client'

export type TargetModel = 'sdxl' | 'flux-chroma' | 'qwen-t2i' | 'pony'
export type Architecture = 't2i'
export type StyleEnhancement =
  | 'anime'
  | 'photorealistic'
  | 'cinematic'
  | 'cartoon'
  | 'watercolor'
  | 'oil-painting'
  | 'comic'
  | 'hyperdetailed'
  | 'minimalist'
  | 'moody-lighting'
export type PromptMode = 'generate' | 'transform' | 'clean'

export const TARGET_MODEL_LABELS: Record<TargetModel, string> = {
  'sdxl': 'SDXL',
  'flux-chroma': 'Flux / Chroma',
  'qwen-t2i': 'Qwen-Image',
  'pony': 'Pony / Illustrious',
}

export const STYLE_LABELS: Record<StyleEnhancement, string> = {
  'anime': 'Anime',
  'photorealistic': 'Photorealistic',
  'cinematic': 'Cinematic',
  'cartoon': 'Cartoon',
  'watercolor': 'Watercolor',
  'oil-painting': 'Oil painting',
  'comic': 'Comic',
  'hyperdetailed': 'Hyperdetailed',
  'minimalist': 'Minimalist',
  'moody-lighting': 'Moody lighting',
}

export interface GenerateBody {
  file_path: string
  target_model: TargetModel
  architecture: Architecture
  styles: StyleEnhancement[]
  temperature: number
  max_tokens: number
}

export interface TransformBody {
  source_prompt: string
  target_model: TargetModel
  architecture: Architecture
  file_path?: string
  temperature: number
  max_tokens: number
}

export interface CleanBody {
  source_prompt: string
  temperature: number
  max_tokens: number
}

export interface GenerateResponse {
  prompt: string
  vlm_model_id: string
  elapsed_ms: number
}

export interface SaveBody {
  file_path: string
  name: string
  prompt: string
  target_model: TargetModel
  architecture: Architecture
  styles: StyleEnhancement[]
  temperature: number | null
  max_tokens: number | null
  source_prompt: string | null
  mode: PromptMode
  negative: string | null
  vlm_model_id: string | null
}

export interface SavedPrompt {
  id: number
  file_path: string
  name: string
  prompt: string
  negative: string | null
  target_model: string
  architecture: string
  styles: string[]
  temperature: number | null
  max_tokens: number | null
  source_prompt: string | null
  mode: PromptMode
  vlm_model_id: string | null
  created_at: string
  updated_at: string
}

export function generatePrompt(body: GenerateBody, signal?: AbortSignal): Promise<GenerateResponse> {
  return post<GenerateResponse>('/prompt/generate', body, signal)
}

export function transformPrompt(body: TransformBody, signal?: AbortSignal): Promise<GenerateResponse> {
  return post<GenerateResponse>('/prompt/transform', body, signal)
}

export function cleanPrompt(body: CleanBody, signal?: AbortSignal): Promise<GenerateResponse> {
  return post<GenerateResponse>('/prompt/clean', body, signal)
}

export function savePrompt(body: SaveBody): Promise<{ id: number }> {
  return post<{ id: number }>('/prompt/save', body)
}

export function listByImage(filePath: string): Promise<SavedPrompt[]> {
  const q = encodeURIComponent(filePath)
  return get<SavedPrompt[]>(`/prompt/by-image?file_path=${q}`)
}

export function deleteSavedPrompt(id: number): Promise<{ status: string }> {
  return del<{ status: string }>(`/prompt/${id}`)
}
```

- [ ] **Step 3: Create the Pinia store**

Create `frontend/src/stores/prompt.ts`:

```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as api from '../api/prompt'

const STORAGE_KEY = 'metascan_prompt_playground_settings'

export interface PlaygroundSettings {
  target_model: api.TargetModel
  architecture: api.Architecture
  styles: api.StyleEnhancement[]
  temperature: number
  max_tokens: number
}

const DEFAULTS: PlaygroundSettings = {
  target_model: 'sdxl',
  architecture: 't2i',
  styles: [],
  temperature: 0.6,
  max_tokens: 250,
}

function loadSettings(): PlaygroundSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<PlaygroundSettings>
      return { ...DEFAULTS, ...parsed }
    }
  } catch {
    /* ignore */
  }
  return { ...DEFAULTS }
}

export const usePromptStore = defineStore('prompt', () => {
  const settings = ref<PlaygroundSettings>(loadSettings())
  const savedByPath = ref<Record<string, api.SavedPrompt[]>>({})

  function persistSettings() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings.value))
    } catch {
      /* ignore */
    }
  }

  async function loadSavedPrompts(filePath: string): Promise<api.SavedPrompt[]> {
    const rows = await api.listByImage(filePath)
    savedByPath.value = { ...savedByPath.value, [filePath]: rows }
    return rows
  }

  async function savePrompt(body: api.SaveBody): Promise<number> {
    const { id } = await api.savePrompt(body)
    await loadSavedPrompts(body.file_path)
    return id
  }

  async function deleteSavedPrompt(id: number, filePath: string): Promise<void> {
    await api.deleteSavedPrompt(id)
    await loadSavedPrompts(filePath)
  }

  return {
    settings,
    savedByPath,
    persistSettings,
    loadSavedPrompts,
    savePrompt,
    deleteSavedPrompt,
  }
})
```

- [ ] **Step 4: Verify type-check + build**

Run: `cd frontend && npm run build`
Expected: clean build, no TypeScript errors. The new modules compile.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/prompt.ts frontend/src/stores/prompt.ts
git commit -m "Add typed prompt API client + Pinia store

post() now accepts an AbortSignal so callers can abort generation
in-flight. Settings are persisted globally to localStorage; saved-
prompts cache is keyed by file_path."
```

---

## Phase 4: Frontend UI

### Task 8: PromptPlayground.vue dialog

**Files:**
- Create: `frontend/src/components/dialogs/PromptPlayground.vue`

- [ ] **Step 1: Create the dialog component**

Create `frontend/src/components/dialogs/PromptPlayground.vue`:

```vue
<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { ApiError } from '../../api/client'
import { streamUrl } from '../../api/client'
import * as promptApi from '../../api/prompt'
import { usePromptStore } from '../../stores/prompt'
import { useToast } from '../../composables/useToast'
import { copyToClipboard } from '../../utils/clipboard'
import type { Media } from '../../types/media'

const props = defineProps<{ media: Media }>()
const emit = defineEmits<{ close: [] }>()

const promptStore = usePromptStore()
const toast = useToast()

type Mode = 'generate' | 'transform' | 'clean'

const mode = ref<Mode>('generate')
const target = ref<promptApi.TargetModel>(promptStore.settings.target_model)
const architecture = ref<promptApi.Architecture>(promptStore.settings.architecture)
const styles = ref<promptApi.StyleEnhancement[]>([...promptStore.settings.styles])
const temperature = ref(promptStore.settings.temperature)
const maxTokens = ref(promptStore.settings.max_tokens)
const sourcePrompt = ref(props.media.prompt ?? '')

const generated = ref('')
const generating = ref(false)
const error = ref<string | null>(null)
const elapsedMs = ref<number | null>(null)
const dirty = ref(false)  // generated text modified since last save / clear

let abortCtrl: AbortController | null = null

const STYLE_OPTIONS: promptApi.StyleEnhancement[] = [
  'anime', 'photorealistic', 'cinematic', 'cartoon',
  'watercolor', 'oil-painting', 'comic',
  'hyperdetailed', 'minimalist', 'moody-lighting',
]
const TARGET_OPTIONS: promptApi.TargetModel[] = [
  'sdxl', 'flux-chroma', 'qwen-t2i', 'pony',
]

const hasExistingPrompt = computed(() => sourcePrompt.value.trim().length > 0)
const transformDisabled = computed(() => !hasExistingPrompt.value)
const cleanDisabled = computed(() => !hasExistingPrompt.value)
const stylesAtMax = computed(() => styles.value.length >= 3)

const fullImageUrl = computed(() => streamUrl(props.media.file_path))

const savedPrompts = computed(() =>
  promptStore.savedByPath[props.media.file_path] ?? [],
)

onMounted(() => {
  promptStore.loadSavedPrompts(props.media.file_path).catch(() => {/* non-fatal */})
})

onBeforeUnmount(() => {
  if (abortCtrl) abortCtrl.abort()
})

watch(
  [mode, target, architecture, styles, temperature, maxTokens],
  () => {
    promptStore.settings.target_model = target.value
    promptStore.settings.architecture = architecture.value
    promptStore.settings.styles = [...styles.value]
    promptStore.settings.temperature = temperature.value
    promptStore.settings.max_tokens = maxTokens.value
    promptStore.persistSettings()
  },
  { deep: true },
)

function toggleStyle(s: promptApi.StyleEnhancement) {
  const idx = styles.value.indexOf(s)
  if (idx >= 0) {
    styles.value.splice(idx, 1)
  } else if (!stylesAtMax.value) {
    styles.value.push(s)
  } else {
    toast.show('Up to 3 styles', 'warn')
  }
}

async function run() {
  if (generating.value) return
  generating.value = true
  error.value = null
  elapsedMs.value = null
  abortCtrl = new AbortController()
  try {
    let resp: promptApi.GenerateResponse
    if (mode.value === 'generate') {
      resp = await promptApi.generatePrompt(
        {
          file_path: props.media.file_path,
          target_model: target.value,
          architecture: architecture.value,
          styles: [...styles.value],
          temperature: temperature.value,
          max_tokens: maxTokens.value,
        },
        abortCtrl.signal,
      )
    } else if (mode.value === 'transform') {
      resp = await promptApi.transformPrompt(
        {
          source_prompt: sourcePrompt.value,
          target_model: target.value,
          architecture: architecture.value,
          file_path: props.media.file_path,
          temperature: temperature.value,
          max_tokens: maxTokens.value,
        },
        abortCtrl.signal,
      )
    } else {
      resp = await promptApi.cleanPrompt(
        {
          source_prompt: sourcePrompt.value,
          temperature: temperature.value,
          max_tokens: maxTokens.value,
        },
        abortCtrl.signal,
      )
    }
    generated.value = resp.prompt
    elapsedMs.value = resp.elapsed_ms
    dirty.value = true
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      // user-initiated stop; not an error
      return
    }
    error.value = e instanceof ApiError ? e.message : String(e)
  } finally {
    generating.value = false
    abortCtrl = null
  }
}

function stop() {
  if (abortCtrl) abortCtrl.abort()
}

async function copyGenerated() {
  if (!generated.value) return
  await copyToClipboard(generated.value)
  toast.show('Copied to clipboard', 'success')
}

async function regenerate() {
  generated.value = ''
  await run()
}

async function saveCurrent() {
  if (!generated.value.trim()) return
  const name = window.prompt('Name this prompt:')
  if (!name) return
  try {
    await promptStore.savePrompt({
      file_path: props.media.file_path,
      name,
      prompt: generated.value,
      target_model: target.value,
      architecture: architecture.value,
      styles: [...styles.value],
      temperature: temperature.value,
      max_tokens: maxTokens.value,
      source_prompt: mode.value !== 'generate' ? sourcePrompt.value : null,
      mode: mode.value,
      negative: null,
      vlm_model_id: null,
    })
    dirty.value = false
    toast.show(`Saved "${name}"`, 'success')
  } catch (e) {
    toast.show(`Save failed: ${e instanceof Error ? e.message : String(e)}`, 'warn')
  }
}

function tryClose() {
  if (generating.value && abortCtrl) abortCtrl.abort()
  if (dirty.value && generated.value.trim()) {
    if (!window.confirm('Discard unsaved generated prompt?')) return
  }
  emit('close')
}
</script>

<template>
  <div class="dialog-overlay" @click.self="tryClose">
    <div class="dialog-card playground-card">
      <div class="dialog-header">
        <h3>Prompt Playground</h3>
        <button class="close-btn" @click="tryClose" title="Close">×</button>
      </div>

      <div class="playground-body">
        <!-- Top row: image + controls -->
        <div class="top-row">
          <img class="preview-img" :src="fullImageUrl" :alt="media.file_name ?? ''" />

          <div class="controls">
            <div class="ctrl-row">
              <span class="ctrl-label">Mode</span>
              <label><input type="radio" v-model="mode" value="generate" /> Generate</label>
              <label :class="{ disabled: transformDisabled }">
                <input type="radio" v-model="mode" value="transform" :disabled="transformDisabled" />
                Transform
              </label>
              <label :class="{ disabled: cleanDisabled }">
                <input type="radio" v-model="mode" value="clean" :disabled="cleanDisabled" />
                Clean
              </label>
            </div>

            <div class="ctrl-row" v-if="mode !== 'clean'">
              <span class="ctrl-label">Target model</span>
              <select v-model="target">
                <option v-for="t in TARGET_OPTIONS" :key="t" :value="t">
                  {{ promptApi.TARGET_MODEL_LABELS[t] }}
                </option>
              </select>
            </div>

            <div class="ctrl-row" v-if="mode === 'generate'">
              <span class="ctrl-label">Styles ({{ styles.length }}/3)</span>
              <div class="style-chips">
                <button
                  v-for="s in STYLE_OPTIONS"
                  :key="s"
                  type="button"
                  class="chip"
                  :class="{ active: styles.includes(s), disabled: !styles.includes(s) && stylesAtMax }"
                  @click="toggleStyle(s)"
                >{{ promptApi.STYLE_LABELS[s] }}</button>
              </div>
            </div>

            <div class="ctrl-row">
              <span class="ctrl-label">Temperature</span>
              <input type="range" min="0" max="1.5" step="0.05" v-model.number="temperature" />
              <span class="ctrl-value">{{ temperature.toFixed(2) }}</span>
            </div>

            <div class="ctrl-row">
              <span class="ctrl-label">Max tokens</span>
              <input type="range" min="50" max="1000" step="10" v-model.number="maxTokens" />
              <span class="ctrl-value">{{ maxTokens }}</span>
            </div>
          </div>
        </div>

        <!-- Existing prompt (for transform/clean mode) -->
        <div class="section" v-if="mode !== 'generate'">
          <label class="section-label">Existing prompt</label>
          <textarea
            v-model="sourcePrompt"
            class="prompt-area"
            rows="4"
            :placeholder="hasExistingPrompt ? '' : '(no embedded prompt — paste one to transform)'"
          />
        </div>

        <!-- Run controls -->
        <div class="run-row">
          <button class="primary" :disabled="generating" @click="run">
            {{ generating ? 'Generating…' : (generated ? 'Re-run' : 'Generate') }}
          </button>
          <button v-if="generating" class="secondary" @click="stop">Stop</button>
          <span v-if="elapsedMs !== null && !generating" class="elapsed">
            {{ (elapsedMs / 1000).toFixed(1) }}s
          </span>
          <span v-if="error" class="error">{{ error }}</span>
        </div>

        <!-- Generated -->
        <div class="section">
          <label class="section-label">Generated prompt</label>
          <textarea
            v-model="generated"
            @input="dirty = true"
            class="prompt-area"
            rows="6"
            placeholder="(generated prompt will appear here)"
          />
          <div class="action-row">
            <button :disabled="!generated.trim()" @click="copyGenerated">Copy</button>
            <button :disabled="!generated.trim()" @click="saveCurrent">Save…</button>
            <button :disabled="!generated.trim()" @click="regenerate">Regenerate</button>
          </div>
        </div>

        <!-- Saved list -->
        <div class="section" v-if="savedPrompts.length">
          <label class="section-label">Saved for this image</label>
          <div v-for="p in savedPrompts" :key="p.id" class="saved-row">
            <span class="saved-name">{{ p.name }}</span>
            <span class="saved-meta">{{ p.target_model }} · {{ p.architecture }}</span>
            <button class="link-btn" @click="generated = p.prompt; dirty = false">Load</button>
            <button class="link-btn danger" @click="promptStore.deleteSavedPrompt(p.id, media.file_path)">Delete</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.playground-card { width: min(960px, 95vw); max-height: 90vh; display: flex; flex-direction: column; }
.dialog-header { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; border-bottom: 1px solid var(--border, #2a2a2a); }
.dialog-header h3 { margin: 0; }
.close-btn { background: none; border: none; font-size: 20px; cursor: pointer; color: inherit; }
.playground-body { padding: 12px 16px; overflow: auto; display: flex; flex-direction: column; gap: 14px; }

.top-row { display: flex; gap: 16px; align-items: flex-start; }
.preview-img { width: 320px; height: auto; max-height: 320px; object-fit: contain; background: #000; border-radius: 6px; flex-shrink: 0; }
.controls { flex: 1; display: flex; flex-direction: column; gap: 10px; }
.ctrl-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ctrl-label { font-size: 12px; opacity: 0.75; min-width: 110px; }
.ctrl-value { font-variant-numeric: tabular-nums; min-width: 44px; text-align: right; opacity: 0.8; }
.ctrl-row label.disabled { opacity: 0.4; }

.style-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.chip { font-size: 11px; padding: 3px 8px; border-radius: 12px; border: 1px solid var(--border, #444); background: transparent; color: inherit; cursor: pointer; }
.chip.active { background: var(--primary, #3b82f6); color: white; border-color: var(--primary, #3b82f6); }
.chip.disabled { opacity: 0.35; cursor: not-allowed; }

.section { display: flex; flex-direction: column; gap: 4px; }
.section-label { font-size: 11px; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.5px; }
.prompt-area { width: 100%; box-sizing: border-box; resize: vertical; font-family: inherit; font-size: 13px; padding: 8px; background: var(--bg-2, #1a1a1a); color: inherit; border: 1px solid var(--border, #2a2a2a); border-radius: 4px; }

.run-row { display: flex; align-items: center; gap: 12px; }
.run-row .primary { padding: 6px 14px; background: var(--primary, #3b82f6); color: white; border: none; border-radius: 4px; cursor: pointer; }
.run-row .primary:disabled { opacity: 0.6; cursor: not-allowed; }
.run-row .secondary { padding: 6px 14px; background: transparent; border: 1px solid var(--border, #444); color: inherit; border-radius: 4px; cursor: pointer; }
.run-row .elapsed { font-size: 12px; opacity: 0.7; }
.run-row .error { color: var(--danger, #ef4444); font-size: 12px; }
.action-row { display: flex; gap: 8px; margin-top: 4px; }
.action-row button { padding: 4px 10px; background: transparent; border: 1px solid var(--border, #444); color: inherit; border-radius: 4px; cursor: pointer; font-size: 12px; }
.action-row button:disabled { opacity: 0.4; cursor: not-allowed; }

.saved-row { display: flex; align-items: center; gap: 8px; padding: 4px 0; border-bottom: 1px solid var(--border, #2a2a2a); }
.saved-name { font-weight: 600; flex: 1; }
.saved-meta { font-size: 11px; opacity: 0.7; }
.link-btn { background: none; border: none; color: var(--primary, #3b82f6); cursor: pointer; font-size: 12px; }
.link-btn.danger { color: var(--danger, #ef4444); }
</style>
```

- [ ] **Step 2: Verify build**

Run: `cd frontend && npm run build`
Expected: clean build.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/dialogs/PromptPlayground.vue
git commit -m "Add PromptPlayground dialog component

Modal overlay matching UpscaleDialog styling. Generate / Transform /
Clean modes; multi-select up to 3 style chips; abortable runs via
AbortController; copy/save/regenerate actions; loads saved prompts
for the image and offers Load + Delete on each."
```

---

### Task 9: Wire context menu + App.vue plumbing

**Files:**
- Modify: `frontend/src/components/thumbnails/ThumbnailGrid.vue` (context menu item + emit)
- Modify: `frontend/src/App.vue` (handle event + mount dialog)

- [ ] **Step 1: Add context-menu item in ThumbnailGrid.vue**

In `frontend/src/components/thumbnails/ThumbnailGrid.vue`:

(a) Add a new `playground` event to the emit declaration. Find the existing `defineEmits` (search for `emit('upscale'`) and add `'playground'`. The exact existing line will look something like:

```typescript
const emit = defineEmits<{
  open: [Media]
  upscale: [Media[]]
  // ...existing events
}>()
```

Add:
```typescript
  playground: [Media]
```

(b) Add a handler. Near the existing `ctxRetagWithVlm` (line 262):

```typescript
function ctxPlayground() {
  if (!contextMenu.value) return
  const target = contextMenu.value.media
  closeContextMenu()
  emit('playground', target)
}
```

(c) Add the menu button in the template. Find the existing button block (around line 440 — the `Re-tag with Qwen3-VL` button). Add immediately after that button:

```html
<button
  v-if="modelsStore.isVlmReady"
  @click="ctxPlayground"
>
  Prompt Playground…
</button>
```

- [ ] **Step 2: Wire the event in App.vue**

In `frontend/src/App.vue`, follow the existing UpscaleDialog wiring pattern:

(a) Add import + ref near the other dialog refs:

```typescript
import PromptPlayground from './components/dialogs/PromptPlayground.vue'
import type { Media } from './types/media'

const playgroundMedia = ref<Media | null>(null)
```

(b) Add a handler:

```typescript
function openPlayground(m: Media) {
  playgroundMedia.value = m
}
function closePlayground() {
  playgroundMedia.value = null
}
```

(c) Bind on the `<ThumbnailGrid>` element (alongside the existing `@upscale=…` binding):

```html
@playground="openPlayground"
```

(d) Mount the dialog. In the template, alongside other dialog mounts:

```html
<PromptPlayground
  v-if="playgroundMedia"
  :media="playgroundMedia"
  @close="closePlayground"
/>
```

- [ ] **Step 3: Verify build + manual smoke**

Run: `cd frontend && npm run build`
Expected: clean build.

Manual smoke (one-time, don't commit until it works):
- Start backend (`source venv/bin/activate && python run_server.py`)
- Start frontend (`cd frontend && npm run dev`)
- Right-click a thumbnail → "Prompt Playground…" should appear (only when VLM is ready).
- Click it → dialog opens with the image preview + controls.
- Click Generate → prompt streams in (well, lands when done; non-streaming v1).
- Click Stop mid-generation → request aborts cleanly (no error toast).
- Close dialog while generating → request aborts.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/thumbnails/ThumbnailGrid.vue frontend/src/App.vue
git commit -m "Wire Prompt Playground into thumbnail context menu

Right-click → 'Prompt Playground…' opens the dialog. Visible only
when VLM is ready (matches the existing 'Re-tag with Qwen3-VL'
visibility pattern)."
```

---

### Task 10: SavedPromptsSection in MetadataPanel

**Files:**
- Create: `frontend/src/components/metadata/SavedPromptsSection.vue`
- Modify: `frontend/src/components/metadata/MetadataPanel.vue`

- [ ] **Step 1: Create the section component**

Create `frontend/src/components/metadata/SavedPromptsSection.vue`:

```vue
<script setup lang="ts">
import { computed, watch } from 'vue'
import { useMediaStore } from '../../stores/media'
import { usePromptStore } from '../../stores/prompt'
import { useToast } from '../../composables/useToast'
import { copyToClipboard } from '../../utils/clipboard'

const mediaStore = useMediaStore()
const promptStore = usePromptStore()
const toast = useToast()

const media = computed(() => mediaStore.selectedMedia)
const saved = computed(() =>
  media.value ? (promptStore.savedByPath[media.value.file_path] ?? []) : [],
)

watch(
  media,
  async (m) => {
    if (m && !(m.file_path in promptStore.savedByPath)) {
      try {
        await promptStore.loadSavedPrompts(m.file_path)
      } catch {
        /* non-fatal */
      }
    }
  },
  { immediate: true },
)

async function handleCopy(text: string) {
  await copyToClipboard(text)
  toast.show('Copied to clipboard', 'success')
}

async function handleDelete(id: number) {
  if (!media.value) return
  if (!window.confirm('Delete this saved prompt?')) return
  try {
    await promptStore.deleteSavedPrompt(id, media.value.file_path)
    toast.show('Deleted')
  } catch (e) {
    toast.show(`Delete failed: ${e instanceof Error ? e.message : String(e)}`, 'warn')
  }
}
</script>

<template>
  <details v-if="saved.length" class="meta-section" open>
    <summary>Saved Prompts ({{ saved.length }})</summary>
    <div v-for="p in saved" :key="p.id" class="saved-prompt-row">
      <div class="saved-prompt-header">
        <span class="saved-prompt-name">{{ p.name }}</span>
        <span class="saved-prompt-meta">{{ p.target_model }} · {{ p.architecture }}</span>
        <button class="icon-btn" :title="'Copy'" @click="handleCopy(p.prompt)">⧉</button>
        <button class="icon-btn danger" :title="'Delete'" @click="handleDelete(p.id)">×</button>
      </div>
      <pre class="saved-prompt-body">{{ p.prompt }}</pre>
    </div>
  </details>
</template>

<style scoped>
.saved-prompt-row { padding: 6px 0; border-bottom: 1px solid var(--border, #2a2a2a); }
.saved-prompt-row:last-child { border-bottom: none; }
.saved-prompt-header { display: flex; gap: 8px; align-items: center; }
.saved-prompt-name { font-weight: 600; flex: 1; }
.saved-prompt-meta { font-size: 11px; opacity: 0.7; }
.icon-btn { background: none; border: none; color: inherit; cursor: pointer; font-size: 14px; padding: 0 4px; }
.icon-btn.danger { color: var(--danger, #ef4444); }
.saved-prompt-body { white-space: pre-wrap; word-break: break-word; margin: 4px 0 0; padding: 6px; background: var(--bg-2, #1a1a1a); border-radius: 4px; font-size: 12px; }
</style>
```

- [ ] **Step 2: Mount the section in MetadataPanel.vue**

Edit `frontend/src/components/metadata/MetadataPanel.vue`:

(a) Add the import:

```typescript
import SavedPromptsSection from './SavedPromptsSection.vue'
```

(b) Mount it in the template after the existing sections (e.g., after `LocationSection` or `CameraSection`):

```html
<SavedPromptsSection />
```

The section internally `v-if`s on `saved.length`, so it costs nothing when there are no saved prompts.

- [ ] **Step 3: Verify build**

Run: `cd frontend && npm run build`
Expected: clean build.

- [ ] **Step 4: Manual end-to-end smoke**

(Don't commit until verified.)
- Open the playground for an image, generate a prompt, save with a name.
- Close the playground.
- Confirm a "Saved Prompts (1)" section appears in the Details panel.
- Click the copy icon → toast says "Copied".
- Click ×, confirm → row disappears, section header re-counts (or hides if 0).
- Re-open the playground for the same image — the saved prompt list at the bottom should also reflect the deletion.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/metadata/SavedPromptsSection.vue frontend/src/components/metadata/MetadataPanel.vue
git commit -m "Add SavedPromptsSection to MetadataPanel

Read-only listing in the Details panel: name, target model chip, copy
+ delete actions, expandable prompt body. Hidden when there are no
saved prompts. Loads on selected-media change via the prompt store
cache (one fetch per file_path per session)."
```

---

## Self-Review Notes

After writing the plan, the following spec coverage was verified:

- **TA-10 spec:** every UI element in the user's description (image preview, mode selector, target-model dropdown, multi-style chips, temperature/token sliders, existing-prompt area, generated-prompt area, save/copy/regenerate/stop, saved-prompts list in Details panel) is implemented in Tasks 8–10.
- **Q1 (non-streaming):** `VlmClient.generate_text` is non-streaming; endpoints await the full response; frontend uses a single `await` with no progressive rendering. ✓
- **Q2 (multi-select up to 3):** enforced in `_style_clause`, in the API request validation (style overflow → 400), and in the UI (`stylesAtMax` blocks adding a 4th). ✓
- **Q3 (t2i only):** `Architecture` Literal includes only `"t2i"`. The UI shows `architecture` but it's a fixed value. ✓
- **Q4 (no negatives in v1):** column reserved (always NULL); save/list payloads include the field but clients always pass null. ✓
- **Q5 (active VLM only):** endpoints 503 when VLM is not ready; UI shows error in red text in the run-row. ✓
- **Q6 (schema):** all proposed columns present, JSON-encoded styles, FK CASCADE on file_path. ✓
- **G1 (generate/transform/clean):** all three endpoints + UI modes. ✓
- **G2 (transform disabled when no prompt):** `transformDisabled = !hasExistingPrompt`. Same for clean. ✓
- **G3 (copy/edit/regenerate/close-confirm):** copy button, generated textarea is editable, regenerate button, `tryClose` confirms when dirty. ✓
- **G4 (no auto-regenerate):** Run is explicit; no `watch` that triggers `run()`. ✓
- **G5 (side-channel discipline):** Task 1 includes `test_save_does_not_touch_indices_table`. ✓
- **G6 (concurrency):** generate_text uses the existing httpx client; the `--parallel` slots semantics apply at the llama-server level. ✓
- **G7 (Stop + abort-on-close):** `AbortController` instantiated per run; `stop()`, `tryClose()`, and `onBeforeUnmount` all call `.abort()`. ✓
- **G8 (settings in localStorage, prompts in DB):** `usePromptStore` watches settings and persists; `savedByPath` is loaded from `/api/prompt/by-image`. ✓

No placeholders detected. Type names (`TargetModel`, `Architecture`, `StyleEnhancement`) and method names (`save_prompt`, `list_saved_prompts`, `get_saved_prompt`, `delete_saved_prompt`, `generate_text`, `compose_*_prompts`) are consistent across Python and TypeScript layers.
