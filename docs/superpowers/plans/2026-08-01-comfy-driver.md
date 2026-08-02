# ComfyUI Driver (Phase A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give metascan the ability to submit jobs to a ComfyUI server — register a workflow preset, inject prompt/seed/dimension overrides, track execution live, retrieve the generated images, and ingest them into the media library.

**Architecture:** Two pure modules carry the domain logic with no I/O (`comfy_bindings.py` for resolving the `MS_*` node-title contract and injecting parameters). `ComfyClient` is an asyncio supervisor modeled on the existing `InferenceClient`: one persistent WebSocket to ComfyUI for the whole app, a metascan-side job queue that holds at most `in_flight` jobs inside ComfyUI at a time, and an output pipeline that fetches images over HTTP and ingests them through the scanner. A thin REST router and a service layer expose it. **This phase contains no storyboard concepts** — it is a standalone generation primitive.

**Tech Stack:** Python 3.11, FastAPI, SQLite (WAL), `httpx` (already a dependency), `websockets` (currently pulled in transitively by `uvicorn[standard]`; this plan makes it explicit). Tests use `pytest` + `aiohttp` (already a dev dependency) for an in-process fake ComfyUI. **No new frontend code and no new runtime dependency beyond promoting `websockets` to an explicit one.**

**Source spec:** `docs/superpowers/specs/2026-08-01-storyboard-generator-design.md`, sections 3 (Phase A), 4.1, 5, 9, 10.

## Global Constraints

- **Python 3.11+.** Not 3.13.
- **`make quality test` must pass at the end of every task.** That is `flake8` (E9/F63/F7/F82 must be zero) + `black --check` + `mypy` + `pytest`.
- **`black` formats everything.** Run `black metascan/ backend/ tests/` before committing.
- **`mypy` is strict on `metascan/core/*`.** Every new function there needs full annotations, including `-> None`.
- **Never import a UI framework** (`PyQt6`, `qt_material`, `tkinter`) anywhere in `metascan/` or `backend/`.
- **DB access is synchronous**, guarded by the existing `threading.Lock` in `DatabaseManager`, and wrapped with `asyncio.to_thread()` in the service layer. Never call a `DatabaseManager` method directly from an async route handler.
- **No real ComfyUI, CLIP, or VLM in tests.** Use the fake server from Task 5.
- **DELETE endpoints return `{"status": "deleted"}`, not 204.** The frontend `request<T>` wrapper calls `res.json()` on every response.
- **`generation_jobs.panel_id` is a plain `INTEGER` with no `REFERENCES` clause.** `panels` does not exist until Phase B; with `PRAGMA foreign_keys = ON` an insert naming a FK to a missing table fails at runtime.
- **`media.hidden` is Phase B.** Do not add it here.
- Line length and style follow the existing codebase; `flake8` config is in the repo.

---

## File Structure

**Create:**

| File | Responsibility |
|---|---|
| `metascan/core/comfy_bindings.py` | Pure: `MS_*` title resolution and parameter injection. No I/O, no network, no DB. |
| `metascan/core/comfy_client.py` | Asyncio supervisor: HTTP + WebSocket to ComfyUI, job queue, output pipeline. |
| `backend/services/comfy_service.py` | `asyncio.to_thread` wrappers over the new `DatabaseManager` methods. |
| `backend/api/comfy.py` | REST router (`/api/comfy/*`) + `set_comfy_client` singleton installer. |
| `tests/_fake_comfy_server.py` | In-process aiohttp fake ComfyUI (`/prompt`, `/history`, `/view`, `/upload/image`, `/queue`, `/interrupt`, `/ws`). |
| `tests/test_comfy_bindings.py` | Pure-unit tests for Task 1 and Task 2. |
| `tests/test_comfy_db.py` | Temp-DB CRUD tests for Task 3. |
| `tests/test_scanner_ingest_file.py` | Tests for Task 4. |
| `tests/test_fake_comfy_server.py` | Proves the fixture itself behaves (Task 5). |
| `tests/test_comfy_client.py` | Client tests for Tasks 6–10. |
| `tests/test_comfy_api.py` | `TestClient` route tests for Task 11. |

**Modify:**

| File | Change |
|---|---|
| `metascan/core/database_sqlite.py` | Two `CREATE TABLE` blocks in `_init_database`; nine CRUD methods. |
| `metascan/core/scanner.py` | Extract `Scanner.ingest_file()` from `scan_directory`'s loop body. |
| `backend/config.py` | `get_comfy_config(config) -> dict`. |
| `backend/main.py` | Construct `ComfyClient` in `lifespan`, install it, register the router. |
| `requirements.txt` | Add explicit `websockets>=12.0`. |
| `docs/api-reference.md`, `docs/configuration.md`, `CLAUDE.md` | Documentation (Task 11). |

**Out of scope for Phase A:** storyboard tables, prompt synthesis, `media.hidden`, aspect-ratio dimension bucketing, any frontend code.

**`comfy.unload_vlm_during_generation` is read but unused in Phase A.** The
config key is defined here so the whole `comfy` section lands in one place,
but the synthesis/generation phase separation it controls (spec §5.7) is
orchestrated by Phase B's storyboard run flow. Do not add VLM shutdown logic
to `ComfyClient` — it has no business knowing the VLM exists.

---

## Task Sequence and Dependencies

- **Tasks 1–4 are independent of each other** and of everything else. Pure logic and DB work; no network.
- **Task 5** (fake server) gates Tasks 6–10.
- **Tasks 6 → 7 → 8 → 9 → 10** are strictly sequential; each builds on the `ComfyClient` the previous one produced.
- **Task 11** needs Tasks 3, 9, and 10.

---

## Task 1: Workflow binding resolution

Parse an API-format ComfyUI graph, find the `MS_*`-titled nodes, and produce a
`Bindings` record naming which node and widget each override targets. Pure —
no network, no files, no DB.

An API-format workflow is a JSON object keyed by node id (a **string**), each
value shaped `{"class_type": str, "inputs": {...}, "_meta": {"title": str}}`.

**Files:**
- Create: `metascan/core/comfy_bindings.py`
- Test: `tests/test_comfy_bindings.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class BindingError(ValueError)`
  - `@dataclass(frozen=True) Bindings` with fields `positive: str`, `seed: str`, `seed_widget: str`, `latent: str`, `save: str`, `negative: Optional[str]`, `lora: Optional[str]`, `ref_image: Optional[str]`, plus `to_json(self) -> str` and `from_json(cls, raw: str) -> "Bindings"`.
  - `resolve_bindings(workflow: Dict[str, Any], kind: str) -> Bindings`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_comfy_bindings.py
"""Pure-unit tests for the MS_* node-title binding contract.

No network, no ComfyUI, no DB — these tests operate on dict literals
shaped like ComfyUI's API-format workflow export.
"""

from __future__ import annotations

import pytest

from metascan.core.comfy_bindings import (
    BindingError,
    Bindings,
    resolve_bindings,
)


def _node(class_type: str, title: str, inputs: dict) -> dict:
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}


def minimal_t2i() -> dict:
    """A valid t2i graph with every required MS_* title present."""
    return {
        "3": _node("KSampler", "MS_SEED", {"seed": 0, "steps": 20, "cfg": 7.0}),
        "4": _node("CheckpointLoaderSimple", "Load Checkpoint", {"ckpt_name": "x.safetensors"}),
        "5": _node("EmptyLatentImage", "MS_LATENT", {"width": 512, "height": 512, "batch_size": 1}),
        "6": _node("CLIPTextEncode", "MS_POSITIVE", {"text": "", "clip": ["4", 1]}),
        "7": _node("CLIPTextEncode", "MS_NEGATIVE", {"text": "", "clip": ["4", 1]}),
        "9": _node("SaveImage", "MS_SAVE", {"images": ["8", 0], "filename_prefix": "ms"}),
    }


def test_resolves_required_t2i_titles():
    b = resolve_bindings(minimal_t2i(), "t2i")
    assert b.positive == "6"
    assert b.negative == "7"
    assert b.seed == "3"
    assert b.seed_widget == "seed"
    assert b.latent == "5"
    assert b.save == "9"
    assert b.lora is None
    assert b.ref_image is None


def test_optional_titles_absent_is_fine():
    wf = minimal_t2i()
    del wf["7"]  # no MS_NEGATIVE
    b = resolve_bindings(wf, "t2i")
    assert b.negative is None


def test_missing_required_title_raises_listing_all_missing():
    wf = minimal_t2i()
    del wf["5"]  # MS_LATENT
    del wf["9"]  # MS_SAVE
    with pytest.raises(BindingError) as exc:
        resolve_bindings(wf, "t2i")
    msg = str(exc.value)
    assert "MS_LATENT" in msg
    assert "MS_SAVE" in msg


def test_noise_seed_widget_is_detected():
    wf = minimal_t2i()
    wf["3"] = _node("SamplerCustom", "MS_SEED", {"noise_seed": 0, "cfg": 7.0})
    b = resolve_bindings(wf, "t2i")
    assert b.seed_widget == "noise_seed"


def test_seed_node_without_a_seed_widget_raises():
    wf = minimal_t2i()
    wf["3"] = _node("KSampler", "MS_SEED", {"steps": 20})
    with pytest.raises(BindingError) as exc:
        resolve_bindings(wf, "t2i")
    assert "seed" in str(exc.value)


def test_latent_missing_a_required_widget_raises():
    wf = minimal_t2i()
    wf["5"] = _node("EmptyLatentImage", "MS_LATENT", {"width": 512, "height": 512})
    with pytest.raises(BindingError) as exc:
        resolve_bindings(wf, "t2i")
    assert "batch_size" in str(exc.value)


def test_duplicate_title_raises():
    wf = minimal_t2i()
    wf["10"] = _node("CLIPTextEncode", "MS_POSITIVE", {"text": ""})
    with pytest.raises(BindingError) as exc:
        resolve_bindings(wf, "t2i")
    assert "MS_POSITIVE" in str(exc.value)
    assert "duplicate" in str(exc.value).lower()


def test_ref_kind_requires_ref_image_node():
    with pytest.raises(BindingError) as exc:
        resolve_bindings(minimal_t2i(), "ref")
    assert "MS_REF_IMAGE" in str(exc.value)


def test_ref_kind_resolves_when_present():
    wf = minimal_t2i()
    wf["11"] = _node("LoadImage", "MS_REF_IMAGE", {"image": "placeholder.png"})
    b = resolve_bindings(wf, "ref")
    assert b.ref_image == "11"


def test_lora_binding_is_optional_and_validated():
    wf = minimal_t2i()
    wf["12"] = _node(
        "LoraLoader",
        "MS_LORA",
        {"lora_name": "a.safetensors", "strength_model": 1.0, "strength_clip": 1.0},
    )
    assert resolve_bindings(wf, "t2i").lora == "12"

    wf["12"] = _node("LoraLoader", "MS_LORA", {"lora_name": "a.safetensors"})
    with pytest.raises(BindingError) as exc:
        resolve_bindings(wf, "t2i")
    assert "strength_model" in str(exc.value)


def test_unknown_kind_raises():
    with pytest.raises(BindingError):
        resolve_bindings(minimal_t2i(), "video")


def test_bindings_json_round_trip():
    b = resolve_bindings(minimal_t2i(), "t2i")
    assert Bindings.from_json(b.to_json()) == b
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_comfy_bindings.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'metascan.core.comfy_bindings'`

- [ ] **Step 3: Implement the module**

```python
# metascan/core/comfy_bindings.py
"""Resolve the MS_* node-title contract in a ComfyUI API-format workflow.

Metascan binds to nodes by their ``_meta.title`` rather than by node id
because ComfyUI renumbers node ids when a workflow is re-saved. Titles
survive that, need no mapping UI, and document the contract inside
ComfyUI itself.

This module is pure: no network, no filesystem, no database. Everything
here is exercised by dict literals in tests.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

# Titles metascan looks for. Values are the widget keys each node must
# expose in its ``inputs`` block for the binding to be usable.
_REQUIRED_WIDGETS: Dict[str, Tuple[str, ...]] = {
    "MS_POSITIVE": ("text",),
    "MS_NEGATIVE": ("text",),
    "MS_LATENT": ("width", "height", "batch_size"),
    "MS_LORA": ("lora_name", "strength_model", "strength_clip"),
    "MS_REF_IMAGE": ("image",),
    # MS_SEED is special-cased: either "seed" or "noise_seed".
    # MS_SAVE is an output node; metascan only needs its id.
}

_REQUIRED_TITLES: Dict[str, Tuple[str, ...]] = {
    "t2i": ("MS_POSITIVE", "MS_SEED", "MS_LATENT", "MS_SAVE"),
    "ref": ("MS_POSITIVE", "MS_SEED", "MS_LATENT", "MS_SAVE", "MS_REF_IMAGE"),
}

KINDS: Tuple[str, ...] = tuple(_REQUIRED_TITLES)


class BindingError(ValueError):
    """A workflow does not satisfy the MS_* contract.

    Raised at preset-registration time, never mid-run — a bad setup must
    surface before dozens of jobs are queued against it.
    """


@dataclass(frozen=True)
class Bindings:
    """Resolved node ids (and the seed widget name) for one preset."""

    positive: str
    seed: str
    seed_widget: str
    latent: str
    save: str
    negative: Optional[str] = None
    lora: Optional[str] = None
    ref_image: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "Bindings":
        return cls(**json.loads(raw))


def _titles(workflow: Dict[str, Any]) -> Dict[str, str]:
    """Map MS_* title -> node id, rejecting duplicates."""
    found: Dict[str, str] = {}
    duplicates: List[str] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        title = (node.get("_meta") or {}).get("title")
        if not isinstance(title, str) or not title.startswith("MS_"):
            continue
        if title in found:
            duplicates.append(title)
        else:
            found[title] = str(node_id)
    if duplicates:
        raise BindingError(
            "Duplicate MS_* node titles: "
            + ", ".join(sorted(set(duplicates)))
            + ". Each title may appear on at most one node."
        )
    return found


def _inputs(workflow: Dict[str, Any], node_id: str) -> Dict[str, Any]:
    node = workflow.get(node_id) or {}
    inputs = node.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def _check_widgets(workflow: Dict[str, Any], title: str, node_id: str) -> None:
    required = _REQUIRED_WIDGETS.get(title)
    if not required:
        return
    inputs = _inputs(workflow, node_id)
    missing = [w for w in required if w not in inputs]
    if missing:
        raise BindingError(
            f"Node {node_id} titled {title} is missing required widget(s): "
            + ", ".join(missing)
        )


def _seed_widget(workflow: Dict[str, Any], node_id: str) -> str:
    inputs = _inputs(workflow, node_id)
    for candidate in ("seed", "noise_seed"):
        if candidate in inputs:
            return candidate
    raise BindingError(
        f"Node {node_id} titled MS_SEED exposes neither a 'seed' nor a "
        "'noise_seed' widget."
    )


def resolve_bindings(workflow: Dict[str, Any], kind: str) -> Bindings:
    """Resolve MS_* titles in ``workflow`` into a Bindings record.

    Raises BindingError listing every problem found, so a user fixing
    their workflow sees the full list rather than one error per attempt.
    """
    if kind not in _REQUIRED_TITLES:
        raise BindingError(
            f"Unknown preset kind {kind!r}. Expected one of: "
            + ", ".join(KINDS)
        )

    found = _titles(workflow)

    missing = [t for t in _REQUIRED_TITLES[kind] if t not in found]
    if missing:
        raise BindingError(
            f"Workflow is missing required node title(s) for kind {kind!r}: "
            + ", ".join(missing)
            + ". Title the corresponding nodes in ComfyUI and re-export "
            "in API format."
        )

    for title, node_id in found.items():
        _check_widgets(workflow, title, node_id)

    return Bindings(
        positive=found["MS_POSITIVE"],
        seed=found["MS_SEED"],
        seed_widget=_seed_widget(workflow, found["MS_SEED"]),
        latent=found["MS_LATENT"],
        save=found["MS_SAVE"],
        negative=found.get("MS_NEGATIVE"),
        lora=found.get("MS_LORA"),
        ref_image=found.get("MS_REF_IMAGE"),
    )


__all__ = ["BindingError", "Bindings", "KINDS", "resolve_bindings"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_comfy_bindings.py -v`
Expected: 12 passed

- [ ] **Step 5: Run quality checks**

Run: `black metascan/ tests/ && make quality test`
Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add metascan/core/comfy_bindings.py tests/test_comfy_bindings.py
git commit -m "feat(comfy): resolve MS_* node-title bindings from a workflow"
```

---

## Task 2: Parameter injection

Given a workflow, its `Bindings`, and a set of generation parameters, produce a
**new** graph with the bound widget values overwritten. The stored preset is
never mutated.

**Files:**
- Modify: `metascan/core/comfy_bindings.py`
- Test: `tests/test_comfy_bindings.py` (append)

**Interfaces:**
- Consumes: `Bindings`, `BindingError` from Task 1.
- Produces:
  - `@dataclass(frozen=True) GenerationParams` with `positive: str`, `seed: int`, `width: int`, `height: int`, `batch_size: int`, `negative: Optional[str] = None`, `lora_name: Optional[str] = None`, `lora_strength: Optional[float] = None`, `ref_image: Optional[str] = None`, plus `to_json(self) -> str` and `from_json(cls, raw: str) -> "GenerationParams"`.
  - `apply_overrides(workflow: Dict[str, Any], bindings: Bindings, params: GenerationParams) -> Dict[str, Any]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_comfy_bindings.py`:

```python
from metascan.core.comfy_bindings import GenerationParams, apply_overrides


def base_params(**kw) -> GenerationParams:
    defaults = dict(positive="a cat", seed=42, width=1024, height=576, batch_size=4)
    defaults.update(kw)
    return GenerationParams(**defaults)


def test_apply_overrides_writes_bound_widgets():
    wf = minimal_t2i()
    b = resolve_bindings(wf, "t2i")
    out = apply_overrides(wf, b, base_params(negative="blurry"))

    assert out["6"]["inputs"]["text"] == "a cat"
    assert out["7"]["inputs"]["text"] == "blurry"
    assert out["3"]["inputs"]["seed"] == 42
    assert out["5"]["inputs"]["width"] == 1024
    assert out["5"]["inputs"]["height"] == 576
    assert out["5"]["inputs"]["batch_size"] == 4


def test_apply_overrides_does_not_mutate_the_source():
    wf = minimal_t2i()
    b = resolve_bindings(wf, "t2i")
    apply_overrides(wf, b, base_params())
    assert wf["6"]["inputs"]["text"] == ""
    assert wf["3"]["inputs"]["seed"] == 0


def test_apply_overrides_leaves_unbound_widgets_alone():
    wf = minimal_t2i()
    b = resolve_bindings(wf, "t2i")
    out = apply_overrides(wf, b, base_params())
    assert out["3"]["inputs"]["steps"] == 20
    assert out["4"]["inputs"]["ckpt_name"] == "x.safetensors"


def test_apply_overrides_uses_noise_seed_when_bound():
    wf = minimal_t2i()
    wf["3"] = _node("SamplerCustom", "MS_SEED", {"noise_seed": 0, "cfg": 7.0})
    b = resolve_bindings(wf, "t2i")
    out = apply_overrides(wf, b, base_params(seed=7))
    assert out["3"]["inputs"]["noise_seed"] == 7
    assert "seed" not in out["3"]["inputs"]


def test_negative_without_a_binding_raises_rather_than_dropping_it():
    wf = minimal_t2i()
    del wf["7"]
    b = resolve_bindings(wf, "t2i")
    with pytest.raises(BindingError) as exc:
        apply_overrides(wf, b, base_params(negative="blurry"))
    assert "MS_NEGATIVE" in str(exc.value)


def test_empty_negative_without_a_binding_is_fine():
    wf = minimal_t2i()
    del wf["7"]
    b = resolve_bindings(wf, "t2i")
    out = apply_overrides(wf, b, base_params(negative=None))
    assert "7" not in out


def test_lora_without_a_binding_raises():
    wf = minimal_t2i()
    b = resolve_bindings(wf, "t2i")
    with pytest.raises(BindingError) as exc:
        apply_overrides(wf, b, base_params(lora_name="maya.safetensors"))
    assert "MS_LORA" in str(exc.value)


def test_lora_is_written_to_both_strength_widgets():
    wf = minimal_t2i()
    wf["12"] = _node(
        "LoraLoader",
        "MS_LORA",
        {"lora_name": "x.safetensors", "strength_model": 1.0, "strength_clip": 1.0},
    )
    b = resolve_bindings(wf, "t2i")
    out = apply_overrides(
        wf, b, base_params(lora_name="maya.safetensors", lora_strength=0.7)
    )
    assert out["12"]["inputs"]["lora_name"] == "maya.safetensors"
    assert out["12"]["inputs"]["strength_model"] == 0.7
    assert out["12"]["inputs"]["strength_clip"] == 0.7


def test_ref_image_is_written():
    wf = minimal_t2i()
    wf["11"] = _node("LoadImage", "MS_REF_IMAGE", {"image": "placeholder.png"})
    b = resolve_bindings(wf, "ref")
    out = apply_overrides(wf, b, base_params(ref_image="maya_ref.png"))
    assert out["11"]["inputs"]["image"] == "maya_ref.png"


def test_ref_image_without_a_binding_raises():
    wf = minimal_t2i()
    b = resolve_bindings(wf, "t2i")
    with pytest.raises(BindingError) as exc:
        apply_overrides(wf, b, base_params(ref_image="maya_ref.png"))
    assert "MS_REF_IMAGE" in str(exc.value)


def test_generation_params_json_round_trip():
    p = base_params(negative="blurry", lora_name="x.safetensors", lora_strength=0.8)
    assert GenerationParams.from_json(p.to_json()) == p
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_comfy_bindings.py -v`
Expected: `ImportError: cannot import name 'GenerationParams'`

- [ ] **Step 3: Implement**

Add to `metascan/core/comfy_bindings.py` (above `__all__`, and extend `__all__`):

```python
@dataclass(frozen=True)
class GenerationParams:
    """One generation request's override values.

    ``ref_image`` is a ComfyUI-side filename as returned by
    ``POST /upload/image`` — not a local path.
    """

    positive: str
    seed: int
    width: int
    height: int
    batch_size: int
    negative: Optional[str] = None
    lora_name: Optional[str] = None
    lora_strength: Optional[float] = None
    ref_image: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "GenerationParams":
        return cls(**json.loads(raw))


def apply_overrides(
    workflow: Dict[str, Any],
    bindings: Bindings,
    params: GenerationParams,
) -> Dict[str, Any]:
    """Return a deep copy of ``workflow`` with bound widgets overwritten.

    A parameter supplied without a corresponding binding raises rather
    than being silently dropped — quietly discarding a negative prompt or
    a LoRA would produce a wrong image with no signal to the user.
    """
    graph: Dict[str, Any] = json.loads(json.dumps(workflow))

    def write(node_id: str, widget: str, value: Any) -> None:
        graph[node_id]["inputs"][widget] = value

    write(bindings.positive, "text", params.positive)
    write(bindings.seed, bindings.seed_widget, params.seed)
    write(bindings.latent, "width", params.width)
    write(bindings.latent, "height", params.height)
    write(bindings.latent, "batch_size", params.batch_size)

    if params.negative is not None:
        if bindings.negative is None:
            raise BindingError(
                "A negative prompt was supplied but this workflow has no "
                "MS_NEGATIVE node. Add one, or clear the negative prompt."
            )
        write(bindings.negative, "text", params.negative)

    if params.lora_name is not None:
        if bindings.lora is None:
            raise BindingError(
                "A LoRA was supplied but this workflow has no MS_LORA node. "
                "Add one, or clear the LoRA."
            )
        strength = 0.8 if params.lora_strength is None else params.lora_strength
        write(bindings.lora, "lora_name", params.lora_name)
        write(bindings.lora, "strength_model", strength)
        write(bindings.lora, "strength_clip", strength)

    if params.ref_image is not None:
        if bindings.ref_image is None:
            raise BindingError(
                "A reference image was supplied but this workflow has no "
                "MS_REF_IMAGE node. Register it with kind='ref'."
            )
        write(bindings.ref_image, "image", params.ref_image)

    return graph
```

Update the module's final line to:

```python
__all__ = [
    "BindingError",
    "Bindings",
    "GenerationParams",
    "KINDS",
    "apply_overrides",
    "resolve_bindings",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_comfy_bindings.py -v`
Expected: 23 passed

- [ ] **Step 5: Run quality checks**

Run: `black metascan/ tests/ && make quality test`

- [ ] **Step 6: Commit**

```bash
git add metascan/core/comfy_bindings.py tests/test_comfy_bindings.py
git commit -m "feat(comfy): inject generation parameters into a bound workflow"
```

---

## Task 3: `workflow_presets` and `generation_jobs` tables

**Files:**
- Modify: `metascan/core/database_sqlite.py`
- Test: `tests/test_comfy_db.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (`bindings` and `params` arrive as opaque JSON strings).
- Produces, on `DatabaseManager`:
  - `create_workflow_preset(name: str, kind: str, workflow_json: str, bindings: str) -> int`
  - `get_workflow_preset(preset_id: int) -> Optional[Dict[str, Any]]` — includes `workflow_json`
  - `list_workflow_presets() -> List[Dict[str, Any]]` — omits `workflow_json`
  - `delete_workflow_preset(preset_id: int) -> bool`
  - `create_generation_job(preset_id: int, params: str, panel_id: Optional[int] = None) -> int`
  - `get_generation_job(job_id: int) -> Optional[Dict[str, Any]]`
  - `get_job_by_comfy_prompt_id(prompt_id: str) -> Optional[Dict[str, Any]]`
  - `update_generation_job(job_id: int, **fields: Any) -> None` — accepts `state`, `comfy_prompt_id`, `error`, `started_at`, `finished_at`
  - `list_generation_jobs(states: Optional[List[str]] = None, limit: int = 100) -> List[Dict[str, Any]]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_comfy_db.py
"""CRUD tests for workflow_presets and generation_jobs.

Uses an isolated temp DB, following tests/test_folders_db.py.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from metascan.core.database_sqlite import DatabaseManager


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield DatabaseManager(Path(tmp))


def test_create_and_get_preset(db):
    pid = db.create_workflow_preset("sdxl-sb", "t2i", '{"3": {}}', '{"positive": "6"}')
    assert isinstance(pid, int)

    row = db.get_workflow_preset(pid)
    assert row["name"] == "sdxl-sb"
    assert row["kind"] == "t2i"
    assert row["workflow_json"] == '{"3": {}}'
    assert row["bindings"] == '{"positive": "6"}'
    assert row["created_at"]


def test_get_missing_preset_returns_none(db):
    assert db.get_workflow_preset(9999) is None


def test_list_presets_omits_the_workflow_blob(db):
    db.create_workflow_preset("a", "t2i", '{"big": "blob"}', "{}")
    db.create_workflow_preset("b", "ref", '{"big": "blob"}', "{}")
    rows = db.list_workflow_presets()
    assert [r["name"] for r in rows] == ["a", "b"]
    assert "workflow_json" not in rows[0]


def test_preset_name_is_unique(db):
    db.create_workflow_preset("dup", "t2i", "{}", "{}")
    with pytest.raises(Exception):
        db.create_workflow_preset("dup", "t2i", "{}", "{}")


def test_delete_preset(db):
    pid = db.create_workflow_preset("gone", "t2i", "{}", "{}")
    assert db.delete_workflow_preset(pid) is True
    assert db.get_workflow_preset(pid) is None
    assert db.delete_workflow_preset(pid) is False


def test_invalid_kind_is_rejected(db):
    with pytest.raises(Exception):
        db.create_workflow_preset("bad", "video", "{}", "{}")


def test_create_job_defaults_to_queued(db):
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, '{"seed": 1}')
    job = db.get_generation_job(jid)
    assert job["state"] == "queued"
    assert job["preset_id"] == pid
    assert job["params"] == '{"seed": 1}'
    assert job["panel_id"] is None
    assert job["comfy_prompt_id"] is None
    assert job["error"] is None


def test_job_accepts_a_panel_id_without_a_panels_table(db):
    """panel_id is a plain column — Phase B's panels table does not exist yet."""
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}", panel_id=1234)
    assert db.get_generation_job(jid)["panel_id"] == 1234


def test_update_job_fields(db):
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}")

    db.update_generation_job(jid, state="running", comfy_prompt_id="abc-123")
    job = db.get_generation_job(jid)
    assert job["state"] == "running"
    assert job["comfy_prompt_id"] == "abc-123"

    db.update_generation_job(jid, state="failed", error="CheckpointLoaderSimple: nope")
    assert db.get_generation_job(jid)["error"] == "CheckpointLoaderSimple: nope"


def test_update_job_rejects_unknown_fields(db):
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}")
    with pytest.raises(ValueError):
        db.update_generation_job(jid, sneaky="value")


def test_update_job_rejects_invalid_state(db):
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}")
    with pytest.raises(Exception):
        db.update_generation_job(jid, state="exploded")


def test_lookup_job_by_comfy_prompt_id(db):
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}")
    db.update_generation_job(jid, comfy_prompt_id="pid-9")
    assert db.get_job_by_comfy_prompt_id("pid-9")["id"] == jid
    assert db.get_job_by_comfy_prompt_id("nope") is None


def test_list_jobs_filters_by_state(db):
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    a = db.create_generation_job(pid, "{}")
    b = db.create_generation_job(pid, "{}")
    db.update_generation_job(b, state="done")

    assert [j["id"] for j in db.list_generation_jobs(states=["queued"])] == [a]
    assert {j["id"] for j in db.list_generation_jobs()} == {a, b}


def test_deleting_a_preset_is_blocked_while_jobs_reference_it(db):
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    db.create_generation_job(pid, "{}")
    with pytest.raises(Exception):
        db.delete_workflow_preset(pid)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_comfy_db.py -v`
Expected: FAIL — `AttributeError: 'DatabaseManager' object has no attribute 'create_workflow_preset'`

- [ ] **Step 3: Add the tables**

In `metascan/core/database_sqlite.py`, inside `_init_database`, immediately after the `saved_prompts` index creation (`idx_saved_prompts_file`) and **before** the `PRAGMA user_version` migration block:

```python
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_presets (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    name          TEXT NOT NULL UNIQUE,
                    kind          TEXT NOT NULL CHECK(kind IN ('t2i','ref')),
                    workflow_json TEXT NOT NULL,
                    bindings      TEXT NOT NULL,
                    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            # NOTE: panel_id deliberately carries no REFERENCES clause. The
            # panels table arrives in Phase B; with PRAGMA foreign_keys = ON
            # an INSERT naming a FK to a missing table fails at runtime, and
            # SQLite cannot add a FK to an existing table without rebuilding
            # it. Phase B deletes matching job rows explicitly instead.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS generation_jobs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    preset_id       INTEGER NOT NULL
                                    REFERENCES workflow_presets(id),
                    panel_id        INTEGER,
                    state           TEXT NOT NULL DEFAULT 'queued'
                                    CHECK(state IN ('queued','running','done',
                                                    'failed','cancelled')),
                    comfy_prompt_id TEXT,
                    params          TEXT NOT NULL,
                    error           TEXT,
                    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                    started_at      TEXT,
                    finished_at     TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_generation_jobs_state "
                "ON generation_jobs(state)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_generation_jobs_prompt "
                "ON generation_jobs(comfy_prompt_id)"
            )
```

- [ ] **Step 4: Add the CRUD methods**

Append to `DatabaseManager` (place them after the saved-prompt methods to keep related code together):

```python
    # ---- ComfyUI workflow presets ---------------------------------------

    _JOB_UPDATABLE: ClassVar[frozenset] = frozenset(
        {"state", "comfy_prompt_id", "error", "started_at", "finished_at"}
    )

    # NOTE: the lock attribute is `self.lock` (database_sqlite.py:49), and the
    # established pattern is `with self.lock:` wrapping `with
    # self._get_connection() as conn:`. The combined form below is equivalent.

    def create_workflow_preset(
        self, name: str, kind: str, workflow_json: str, bindings: str
    ) -> int:
        with self.lock, self._get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO workflow_presets (name, kind, workflow_json, "
                "bindings) VALUES (?, ?, ?, ?)",
                (name, kind, workflow_json, bindings),
            )
            conn.commit()
            return int(cur.lastrowid)

    def get_workflow_preset(self, preset_id: int) -> Optional[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_presets WHERE id = ?", (preset_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_workflow_presets(self) -> List[Dict[str, Any]]:
        """Summary rows. Omits workflow_json — the graphs are large and no
        list view needs them."""
        with self.lock, self._get_connection() as conn:
            rows = conn.execute(
                "SELECT id, name, kind, bindings, created_at, updated_at "
                "FROM workflow_presets ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_workflow_preset(self, preset_id: int) -> bool:
        with self.lock, self._get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM workflow_presets WHERE id = ?", (preset_id,)
            )
            conn.commit()
            return cur.rowcount > 0

    # ---- ComfyUI generation jobs ----------------------------------------

    def create_generation_job(
        self, preset_id: int, params: str, panel_id: Optional[int] = None
    ) -> int:
        with self.lock, self._get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO generation_jobs (preset_id, params, panel_id) "
                "VALUES (?, ?, ?)",
                (preset_id, params, panel_id),
            )
            conn.commit()
            return int(cur.lastrowid)

    def get_generation_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM generation_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_job_by_comfy_prompt_id(
        self, prompt_id: str
    ) -> Optional[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM generation_jobs WHERE comfy_prompt_id = ?",
                (prompt_id,),
            ).fetchone()
            return dict(row) if row else None

    def update_generation_job(self, job_id: int, **fields: Any) -> None:
        """Update a whitelisted subset of job columns.

        The whitelist is what keeps this from becoming a SQL-injection
        surface — column names cannot be parameterized.
        """
        unknown = set(fields) - self._JOB_UPDATABLE
        if unknown:
            raise ValueError(
                f"Not updatable on generation_jobs: {', '.join(sorted(unknown))}"
            )
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [job_id]
        with self.lock, self._get_connection() as conn:
            conn.execute(
                f"UPDATE generation_jobs SET {assignments} WHERE id = ?", values
            )
            conn.commit()

    def list_generation_jobs(
        self, states: Optional[List[str]] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM generation_jobs"
        params: List[Any] = []
        if states:
            sql += " WHERE state IN (" + ",".join("?" * len(states)) + ")"
            params.extend(states)
        sql += " ORDER BY id LIMIT ?"
        params.append(limit)
        with self.lock, self._get_connection() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
```

Add `ClassVar` to the `typing` import at the top of the file if it is not already imported.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_comfy_db.py -v`
Expected: 14 passed

If `test_deleting_a_preset_is_blocked_while_jobs_reference_it` fails, confirm `PRAGMA foreign_keys = ON` is present in `_get_connection` (it is, at `database_sqlite.py:500`).

- [ ] **Step 6: Run quality checks**

Run: `black metascan/ tests/ && make quality test`

- [ ] **Step 7: Commit**

```bash
git add metascan/core/database_sqlite.py tests/test_comfy_db.py
git commit -m "feat(comfy): add workflow_presets and generation_jobs tables"
```

---

## Task 4: `Scanner.ingest_file()`

The full single-file ingest sequence — process, `save_media`, pHash, thumbnail
— currently lives inline in `Scanner.scan_directory`'s loop. Extract it to a
public method so the output pipeline (Task 9) can call it without duplicating
the sequence or reaching into `_process_media_file`.

**Files:**
- Modify: `metascan/core/scanner.py`
- Test: `tests/test_scanner_ingest_file.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Scanner.ingest_file(self, file_path: Path) -> Optional[Media]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scanner_ingest_file.py
"""Tests for Scanner.ingest_file — the public single-file ingest path.

Used by the ComfyUI output pipeline, which has one file at a time and no
directory to scan.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from PIL import Image

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.scanner import Scanner


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "media").mkdir()
        yield root


def _png(path: Path, size=(64, 64), color=(200, 30, 30)) -> Path:
    Image.new("RGB", size, color).save(path)
    return path


def test_ingest_file_returns_media_and_persists_it(workspace):
    db = DatabaseManager(workspace / "db")
    scanner = Scanner(db)
    img = _png(workspace / "media" / "a.png")

    media = scanner.ingest_file(img)

    assert media is not None
    assert media.width == 64
    assert media.height == 64
    assert db.get_media(img) is not None


def test_ingest_file_stores_a_perceptual_hash(workspace):
    db = DatabaseManager(workspace / "db")
    scanner = Scanner(db)
    img = _png(workspace / "media" / "b.png")

    scanner.ingest_file(img)

    assert str(img) in db.get_all_phashes()


def test_ingest_file_returns_none_for_a_non_image(workspace):
    db = DatabaseManager(workspace / "db")
    scanner = Scanner(db)
    junk = workspace / "media" / "c.png"
    junk.write_bytes(b"not an image")

    assert scanner.ingest_file(junk) is None


def test_ingest_file_is_idempotent(workspace):
    """Re-ingesting the same file upserts rather than duplicating.

    save_media uses INSERT ... ON CONFLICT(file_path) DO UPDATE, so a
    second ingest must not add a row.
    """
    db = DatabaseManager(workspace / "db")
    scanner = Scanner(db)
    img = _png(workspace / "media" / "d.png")

    scanner.ingest_file(img)
    scanner.ingest_file(img)

    assert len(db.get_existing_file_paths()) == 1


def test_scan_directory_still_works_through_the_extracted_method(workspace):
    db = DatabaseManager(workspace / "db")
    scanner = Scanner(db)
    _png(workspace / "media" / "e.png")
    _png(workspace / "media" / "f.png")

    assert scanner.scan_directory(str(workspace / "media")) == 2
```

Accessor names used above, all verified against
`metascan/core/database_sqlite.py`: `get_media(file_path: Path)` (takes a
`Path`, not a string), `get_all_phashes() -> Dict[str, str]` (there is no
single-file phash getter), and `get_existing_file_paths()`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_scanner_ingest_file.py -v`
Expected: FAIL — `AttributeError: 'Scanner' object has no attribute 'ingest_file'`
(`test_scan_directory_still_works_through_the_extracted_method` passes already — it is the regression guard for the refactor.)

- [ ] **Step 3: Extract the method**

Add to `Scanner` in `metascan/core/scanner.py`:

```python
    def ingest_file(self, file_path: Path) -> Optional[Media]:
        """Process, persist, hash, and thumbnail one media file.

        This is the whole single-file ingest sequence, extracted from
        ``scan_directory`` so callers with a single known file — the
        ComfyUI output pipeline, for one — do not have to duplicate it or
        reach into ``_process_media_file``.

        Returns the persisted Media, or None if the file could not be
        read as media.
        """
        media = self._process_media_file(file_path)
        if media is None:
            return None

        self.db_manager.save_media(media)

        try:
            phash = compute_phash_for_file(file_path)
            if phash:
                self.db_manager.save_media_hash(file_path, phash)
        except Exception as e:
            logger.debug(f"pHash computation failed for {file_path}: {e}")

        if self.thumbnail_cache:
            try:
                thumbnail_path = self.thumbnail_cache.get_or_create_thumbnail(
                    file_path
                )
                if not thumbnail_path:
                    logger.debug(f"Failed to generate thumbnail for {file_path}")
            except Exception as e:
                logger.warning(f"Thumbnail generation failed for {file_path}: {e}")

        return media
```

- [ ] **Step 4: Rewrite `scan_directory`'s loop body to call it**

In `Scanner.scan_directory`, replace the block that currently starts at
`media = self._process_media_file(file_path)` and runs through the thumbnail
`try/except` — everything from that line down to and including
`processed_count += 1` — with:

```python
                if self.ingest_file(file_path) is not None:
                    processed_count += 1
```

Leave the surrounding `try/except Exception` and the progress-callback block
exactly as they are.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_scanner_ingest_file.py -v`
Expected: 5 passed

- [ ] **Step 6: Run the full suite — this task refactors a hot path**

Run: `make quality test`
Expected: every pre-existing scanner test still passes. If any fail, the
extraction changed behavior; re-read the original loop body and match it
exactly rather than adjusting the tests.

- [ ] **Step 7: Commit**

```bash
git add metascan/core/scanner.py tests/test_scanner_ingest_file.py
git commit -m "refactor(scanner): extract public ingest_file from scan_directory"
```

---

## Task 5: Fake ComfyUI server fixture

An in-process `aiohttp` server speaking enough of ComfyUI's API to drive
Tasks 6–10. In-process rather than a subprocess (unlike
`tests/_fake_llama_server.py`) because metascan does not spawn ComfyUI — it
connects to one that already exists.

**Files:**
- Create: `tests/_fake_comfy_server.py`
- Test: `tests/test_fake_comfy_server.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class FakeComfy` with `async start(self) -> None`, `async stop(self) -> None`, `base_url: str`, and the knobs `fail_with: Optional[str]`, `execution_delay: float`, `images_per_job: int`.
  - `async fake_comfy() -> AsyncIterator[FakeComfy]` — a pytest fixture exported for use in other test modules.

- [ ] **Step 1: Write the fixture**

```python
# tests/_fake_comfy_server.py
"""An in-process stand-in for a ComfyUI server.

Speaks the subset of ComfyUI's API that ComfyClient uses: POST /prompt,
GET /history/{id}, GET /view, POST /upload/image, POST /queue, POST
/interrupt, and the /ws event stream.

In-process (not a subprocess like _fake_llama_server) because metascan
connects to an existing ComfyUI rather than spawning one.

Knobs:
  fail_with        -- when set, jobs emit execution_error with this text
  execution_delay  -- seconds between execution_start and completion
  images_per_job   -- how many output images each job produces
"""

from __future__ import annotations

import asyncio
import io
import json
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

import pytest
from aiohttp import web
from PIL import Image


def _png_bytes(color: tuple) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


class FakeComfy:
    """A minimal ComfyUI look-alike bound to an ephemeral port."""

    def __init__(self) -> None:
        self.fail_with: Optional[str] = None
        self.execution_delay: float = 0.0
        self.images_per_job: int = 2

        # Observability for assertions.
        self.submitted: List[Dict[str, Any]] = []
        self.uploaded: List[str] = []
        self.interrupted: int = 0
        self.deleted: List[str] = []

        self._history: Dict[str, Dict[str, Any]] = {}
        self._sockets: List[web.WebSocketResponse] = []
        self._tasks: List[asyncio.Task] = []
        self._runner: Optional[web.AppRunner] = None
        self._port: int = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/prompt", self._post_prompt)
        app.router.add_get("/history/{prompt_id}", self._get_history)
        app.router.add_get("/view", self._get_view)
        app.router.add_post("/upload/image", self._post_upload)
        app.router.add_post("/queue", self._post_queue)
        app.router.add_post("/interrupt", self._post_interrupt)
        app.router.add_get("/ws", self._ws)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self._port = site._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for ws in list(self._sockets):
            await ws.close()
        if self._runner is not None:
            await self._runner.cleanup()

    async def broadcast(self, message: Dict[str, Any]) -> None:
        for ws in list(self._sockets):
            try:
                await ws.send_str(json.dumps(message))
            except Exception:
                pass

    # ---- routes ------------------------------------------------------

    async def _post_prompt(self, request: web.Request) -> web.Response:
        body = await request.json()
        prompt_id = str(uuid.uuid4())
        self.submitted.append({"prompt_id": prompt_id, "body": body})
        self._tasks.append(asyncio.create_task(self._execute(prompt_id, body)))
        return web.json_response({"prompt_id": prompt_id, "number": 1})

    async def _get_history(self, request: web.Request) -> web.Response:
        prompt_id = request.match_info["prompt_id"]
        entry = self._history.get(prompt_id)
        return web.json_response({prompt_id: entry} if entry else {})

    async def _get_view(self, request: web.Request) -> web.Response:
        filename = request.query.get("filename", "")
        return web.Response(
            body=_png_bytes((len(filename) * 7 % 255, 100, 150)),
            content_type="image/png",
        )

    async def _post_upload(self, request: web.Request) -> web.Response:
        reader = await request.multipart()
        field = await reader.next()
        name = getattr(field, "filename", None) or "upload.png"
        await field.read()
        self.uploaded.append(name)
        return web.json_response({"name": name, "subfolder": "", "type": "input"})

    async def _post_queue(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.deleted.extend(body.get("delete", []))
        return web.json_response({})

    async def _post_interrupt(self, request: web.Request) -> web.Response:
        self.interrupted += 1
        return web.json_response({})

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._sockets.append(ws)
        try:
            async for _ in ws:
                pass
        finally:
            if ws in self._sockets:
                self._sockets.remove(ws)
        return ws

    # ---- simulated execution ----------------------------------------

    def _save_node_id(self, body: Dict[str, Any]) -> str:
        """Find the node metascan will collect outputs from."""
        graph = body.get("prompt", {})
        for node_id, node in graph.items():
            if (node.get("_meta") or {}).get("title") == "MS_SAVE":
                return str(node_id)
        return "9"

    async def _execute(self, prompt_id: str, body: Dict[str, Any]) -> None:
        await self.broadcast(
            {"type": "execution_start", "data": {"prompt_id": prompt_id}}
        )
        if self.execution_delay:
            await asyncio.sleep(self.execution_delay)

        if self.fail_with is not None:
            self._history[prompt_id] = {
                "status": {"completed": False},
                "outputs": {},
            }
            await self.broadcast(
                {
                    "type": "execution_error",
                    "data": {
                        "prompt_id": prompt_id,
                        "node_type": "CheckpointLoaderSimple",
                        "exception_message": self.fail_with,
                    },
                }
            )
            return

        save_node = self._save_node_id(body)
        images = [
            {
                "filename": f"{prompt_id[:8]}_{i:05d}_.png",
                "subfolder": "",
                "type": "output",
            }
            for i in range(self.images_per_job)
        ]
        self._history[prompt_id] = {
            "status": {"completed": True},
            "outputs": {save_node: {"images": images}},
        }
        await self.broadcast(
            {
                "type": "executed",
                "data": {
                    "prompt_id": prompt_id,
                    "node": save_node,
                    "output": {"images": images},
                },
            }
        )


@pytest.fixture
async def fake_comfy() -> AsyncIterator[FakeComfy]:
    server = FakeComfy()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()
```

- [ ] **Step 2: Write a test proving the fixture works**

```python
# tests/test_fake_comfy_server.py
"""The fake ComfyUI must behave before anything is tested against it."""

from __future__ import annotations

import asyncio
import json

import httpx
import websockets

from tests._fake_comfy_server import fake_comfy  # noqa: F401


async def test_submit_returns_a_prompt_id(fake_comfy):  # noqa: F811
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{fake_comfy.base_url}/prompt", json={"prompt": {}})
    assert r.status_code == 200
    assert r.json()["prompt_id"]


async def test_execution_emits_ws_events_and_populates_history(fake_comfy):  # noqa: F811
    events = []
    async with websockets.connect(f"ws://127.0.0.1:{fake_comfy._port}/ws") as ws:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{fake_comfy.base_url}/prompt",
                json={
                    "prompt": {
                        "9": {
                            "class_type": "SaveImage",
                            "inputs": {},
                            "_meta": {"title": "MS_SAVE"},
                        }
                    }
                },
            )
            prompt_id = r.json()["prompt_id"]

            for _ in range(2):
                events.append(json.loads(await asyncio.wait_for(ws.recv(), 5)))

    assert [e["type"] for e in events] == ["execution_start", "executed"]

    async with httpx.AsyncClient() as client:
        h = await client.get(f"{fake_comfy.base_url}/history/{prompt_id}")
    images = h.json()[prompt_id]["outputs"]["9"]["images"]
    assert len(images) == 2


async def test_fail_with_emits_execution_error(fake_comfy):  # noqa: F811
    fake_comfy.fail_with = "value not in list: ckpt_name"
    async with websockets.connect(f"ws://127.0.0.1:{fake_comfy._port}/ws") as ws:
        async with httpx.AsyncClient() as client:
            await client.post(f"{fake_comfy.base_url}/prompt", json={"prompt": {}})
        types = []
        for _ in range(2):
            types.append(json.loads(await asyncio.wait_for(ws.recv(), 5))["type"])

    assert types == ["execution_start", "execution_error"]


async def test_view_returns_png_bytes(fake_comfy):  # noqa: F811
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{fake_comfy.base_url}/view", params={"filename": "a.png"})
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
```

- [ ] **Step 3: Add the explicit `websockets` dependency**

The test above imports `websockets`, and Task 7's client will too. It is
currently installed only as a transitive dependency of `uvicorn[standard]`.
Add to `requirements.txt`, in the Web Server block near `uvicorn`:

```
# WebSocket client for the ComfyUI driver. Already pulled in transitively
# by uvicorn[standard]; declared explicitly because metascan imports it
# directly rather than relying on uvicorn's extras.
websockets>=12.0
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_fake_comfy_server.py -v`
Expected: 4 passed

If the fixture is not found, confirm `tests/` is importable as a package —
`tests/_fake_llama_server.py` is already imported the same way by
`tests/test_fake_llama_server.py`, so follow whatever import form that file uses.

- [ ] **Step 5: Run quality checks**

Run: `black tests/ && make quality test`

- [ ] **Step 6: Commit**

```bash
git add tests/_fake_comfy_server.py tests/test_fake_comfy_server.py requirements.txt
git commit -m "test(comfy): add in-process fake ComfyUI server fixture"
```

---

## Task 6: `ComfyClient` — submit and retrieve history

The HTTP half of the client: submit a bound workflow, get a `prompt_id`, read
`/history`. No WebSocket yet, no queue, no downloads.

**Files:**
- Create: `metascan/core/comfy_client.py`
- Test: `tests/test_comfy_client.py`

**Interfaces:**
- Consumes: `Bindings`, `GenerationParams`, `apply_overrides` (Tasks 1–2); `DatabaseManager` preset/job methods (Task 3).
- Produces:
  - `class ComfyError(RuntimeError)`
  - `class ComfyClient` with `__init__(self, base_url: str, output_root: Path, db: Any, scanner: Any = None, in_flight: int = 2, request_timeout_s: float = 30.0, client_id: Optional[str] = None)`, `async register_preset(self, name: str, kind: str, workflow: Dict[str, Any]) -> int`, `async submit_now(self, preset_id: int, params: GenerationParams, panel_id: Optional[int] = None) -> int`, `async fetch_history(self, prompt_id: str) -> Dict[str, Any]`, `async aclose(self) -> None`, `def snapshot(self) -> Dict[str, Any]`.

`submit_now` bypasses the queue and submits immediately; Task 8 adds the
queued `submit()` on top of it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_comfy_client.py
"""ComfyClient tests, driven entirely by the in-process fake ComfyUI."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from metascan.core.comfy_bindings import BindingError, GenerationParams
from metascan.core.comfy_client import ComfyClient, ComfyError
from metascan.core.database_sqlite import DatabaseManager
from tests._fake_comfy_server import fake_comfy  # noqa: F401


def _node(class_type: str, title: str, inputs: dict) -> dict:
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}


def t2i_workflow() -> dict:
    return {
        "3": _node("KSampler", "MS_SEED", {"seed": 0, "steps": 20}),
        "5": _node(
            "EmptyLatentImage",
            "MS_LATENT",
            {"width": 512, "height": 512, "batch_size": 1},
        ),
        "6": _node("CLIPTextEncode", "MS_POSITIVE", {"text": ""}),
        "9": _node("SaveImage", "MS_SAVE", {"filename_prefix": "ms"}),
    }


def params(**kw) -> GenerationParams:
    base = dict(positive="a cat", seed=42, width=1024, height=576, batch_size=2)
    base.update(kw)
    return GenerationParams(**base)


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
async def client(fake_comfy, workspace):  # noqa: F811
    db = DatabaseManager(workspace / "db")
    c = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=db,
        in_flight=2,
    )
    try:
        yield c
    finally:
        await c.aclose()


async def test_register_preset_stores_workflow_and_bindings(client):
    pid = await client.register_preset("sdxl", "t2i", t2i_workflow())
    row = client.db.get_workflow_preset(pid)
    assert row["name"] == "sdxl"
    assert row["kind"] == "t2i"
    assert '"positive": "6"' in row["bindings"]


async def test_register_preset_rejects_an_unbindable_workflow(client):
    wf = t2i_workflow()
    del wf["9"]
    with pytest.raises(BindingError) as exc:
        await client.register_preset("broken", "t2i", wf)
    assert "MS_SAVE" in str(exc.value)


async def test_submit_now_sends_the_overridden_graph(client, fake_comfy):  # noqa: F811
    pid = await client.register_preset("sdxl", "t2i", t2i_workflow())
    await client.submit_now(pid, params())

    sent = fake_comfy.submitted[-1]["body"]["prompt"]
    assert sent["6"]["inputs"]["text"] == "a cat"
    assert sent["3"]["inputs"]["seed"] == 42
    assert sent["5"]["inputs"]["batch_size"] == 2
    # the stored preset is untouched
    assert '"text": ""' in client.db.get_workflow_preset(pid)["workflow_json"]


async def test_submit_now_records_a_running_job(client):
    pid = await client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await client.submit_now(pid, params())

    job = client.db.get_generation_job(job_id)
    assert job["state"] == "running"
    assert job["comfy_prompt_id"]
    assert job["started_at"]


async def test_submit_now_with_a_missing_preset_raises(client):
    with pytest.raises(ComfyError) as exc:
        await client.submit_now(9999, params())
    assert "9999" in str(exc.value)


async def test_unreachable_server_raises_comfy_error(workspace):
    db = DatabaseManager(workspace / "db")
    c = ComfyClient(
        base_url="http://127.0.0.1:1",  # nothing listens here
        output_root=workspace / "out",
        db=db,
        request_timeout_s=1.0,
    )
    pid = await c.register_preset("sdxl", "t2i", t2i_workflow())
    try:
        with pytest.raises(ComfyError) as exc:
            await c.submit_now(pid, params())
        assert "127.0.0.1:1" in str(exc.value)
    finally:
        await c.aclose()


async def test_failed_submit_marks_the_job_failed(workspace):
    db = DatabaseManager(workspace / "db")
    c = ComfyClient(
        base_url="http://127.0.0.1:1",
        output_root=workspace / "out",
        db=db,
        request_timeout_s=1.0,
    )
    pid = await c.register_preset("sdxl", "t2i", t2i_workflow())
    try:
        with pytest.raises(ComfyError):
            await c.submit_now(pid, params())
        jobs = db.list_generation_jobs(states=["failed"])
        assert len(jobs) == 1
        assert jobs[0]["error"]
    finally:
        await c.aclose()


async def test_fetch_history_returns_the_entry_for_a_prompt(client, fake_comfy):  # noqa: F811
    pid = await client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await client.submit_now(pid, params())
    prompt_id = client.db.get_generation_job(job_id)["comfy_prompt_id"]

    entry = None
    for _ in range(50):
        entry = await client.fetch_history(prompt_id)
        if entry:
            break
        await asyncio.sleep(0.02)

    assert entry["outputs"]["9"]["images"]
```

Add `import asyncio` to the test file's imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_comfy_client.py -v`
Expected: `ModuleNotFoundError: No module named 'metascan.core.comfy_client'`

- [ ] **Step 3: Implement**

```python
# metascan/core/comfy_client.py
"""Asyncio driver for a ComfyUI server.

Modeled on metascan.core.inference_client.InferenceClient: constructed
once in the FastAPI lifespan, installed as a singleton, and owning all
network state for the subsystem.

This module talks to a ComfyUI that already exists — it never spawns
one. All job bookkeeping lives in the generation_jobs table so state
survives a restart.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

import httpx

from metascan.core.comfy_bindings import (
    Bindings,
    GenerationParams,
    apply_overrides,
    resolve_bindings,
)

logger = logging.getLogger(__name__)


class ComfyError(RuntimeError):
    """A ComfyUI request failed, or was made against unusable state."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ComfyClient:
    def __init__(
        self,
        base_url: str,
        output_root: Path,
        db: Any,
        scanner: Any = None,
        in_flight: int = 2,
        request_timeout_s: float = 30.0,
        client_id: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.output_root = Path(output_root)
        self.db = db
        self.scanner = scanner
        self.in_flight = max(1, int(in_flight))
        self.client_id = client_id or str(uuid4())
        self._http = httpx.AsyncClient(timeout=request_timeout_s)

    # ---- lifecycle ---------------------------------------------------

    async def aclose(self) -> None:
        await self._http.aclose()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "base_url": self.base_url,
            "client_id": self.client_id,
            "in_flight": self.in_flight,
        }

    # ---- presets -----------------------------------------------------

    async def register_preset(
        self, name: str, kind: str, workflow: Dict[str, Any]
    ) -> int:
        """Resolve MS_* bindings and persist the preset.

        Raises BindingError (from comfy_bindings) before anything is
        written, so an unusable workflow never reaches the database.
        """
        bindings = resolve_bindings(workflow, kind)
        return int(
            self.db.create_workflow_preset(
                name, kind, json.dumps(workflow), bindings.to_json()
            )
        )

    def _load_preset(self, preset_id: int) -> tuple:
        row = self.db.get_workflow_preset(preset_id)
        if row is None:
            raise ComfyError(f"No workflow preset with id {preset_id}")
        return json.loads(row["workflow_json"]), Bindings.from_json(row["bindings"])

    # ---- submission --------------------------------------------------

    async def submit_now(
        self,
        preset_id: int,
        params: GenerationParams,
        panel_id: Optional[int] = None,
    ) -> int:
        """Bypass the queue and hand a job straight to ComfyUI.

        Returns the generation_jobs row id.
        """
        workflow, bindings = self._load_preset(preset_id)
        graph = apply_overrides(workflow, bindings, params)

        job_id = int(
            self.db.create_generation_job(preset_id, params.to_json(), panel_id)
        )
        try:
            resp = await self._http.post(
                f"{self.base_url}/prompt",
                json={"prompt": graph, "client_id": self.client_id},
            )
            resp.raise_for_status()
            prompt_id = resp.json().get("prompt_id")
            if not prompt_id:
                raise ComfyError("ComfyUI accepted the prompt but returned no id")
        except ComfyError:
            self.db.update_generation_job(
                job_id, state="failed", error="no prompt_id in response",
                finished_at=_now(),
            )
            raise
        except Exception as exc:
            message = f"ComfyUI at {self.base_url} rejected the job: {exc}"
            logger.warning(message)
            self.db.update_generation_job(
                job_id, state="failed", error=message, finished_at=_now()
            )
            raise ComfyError(message) from exc

        self.db.update_generation_job(
            job_id, state="running", comfy_prompt_id=prompt_id, started_at=_now()
        )
        return job_id

    # ---- history -----------------------------------------------------

    async def fetch_history(self, prompt_id: str) -> Dict[str, Any]:
        """Return ComfyUI's history entry for a prompt, or {} if absent."""
        try:
            resp = await self._http.get(f"{self.base_url}/history/{prompt_id}")
            resp.raise_for_status()
        except Exception as exc:
            raise ComfyError(
                f"Could not read history from {self.base_url}: {exc}"
            ) from exc
        payload = resp.json()
        entry = payload.get(prompt_id)
        return entry if isinstance(entry, dict) else {}


__all__ = ["ComfyClient", "ComfyError"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_comfy_client.py -v`
Expected: 8 passed

- [ ] **Step 5: Run quality checks**

Run: `black metascan/ tests/ && make quality test`

- [ ] **Step 6: Commit**

```bash
git add metascan/core/comfy_client.py tests/test_comfy_client.py
git commit -m "feat(comfy): submit jobs to ComfyUI and read execution history"
```

---

## Task 7: WebSocket event loop

One persistent connection for the whole app, auto-reconnecting, translating
ComfyUI events into job state transitions and metascan WS broadcasts.

**Files:**
- Modify: `metascan/core/comfy_client.py`
- Test: `tests/test_comfy_client.py` (append)

**Interfaces:**
- Consumes: everything from Task 6.
- Produces, on `ComfyClient`:
  - `async start(self) -> None` — opens the socket and starts the reader task
  - `async shutdown(self) -> None` — cancels the reader and closes HTTP
  - `def on_job_event(self, cb: Callable[[str, Dict[str, Any]], None]) -> None` — register a listener called as `cb(event_name, payload)`; `event_name` is one of `"job_update"`, `"job_progress"`
  - `async wait_for_job(self, job_id: int, timeout: float = 10.0) -> Dict[str, Any]` — test/consumer helper that resolves when the job leaves `running`
  - `self.connected: bool`

Job completion **does not** download images yet — that is Task 9. This task
stops at `state='done'`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_comfy_client.py`:

```python
@pytest.fixture
async def started_client(fake_comfy, workspace):  # noqa: F811
    db = DatabaseManager(workspace / "db")
    c = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=db,
        in_flight=2,
    )
    await c.start()
    try:
        yield c
    finally:
        await c.shutdown()


async def test_start_connects_the_websocket(started_client):
    assert started_client.connected is True


async def test_successful_execution_marks_the_job_done(started_client):
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await started_client.submit_now(pid, params())

    job = await started_client.wait_for_job(job_id, timeout=5.0)
    assert job["state"] == "done"
    assert job["finished_at"]


async def test_execution_error_marks_the_job_failed_with_node_context(
    started_client, fake_comfy  # noqa: F811
):
    fake_comfy.fail_with = "value not in list: ckpt_name"
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await started_client.submit_now(pid, params())

    job = await started_client.wait_for_job(job_id, timeout=5.0)
    assert job["state"] == "failed"
    assert "value not in list" in job["error"]
    assert "CheckpointLoaderSimple" in job["error"]


async def test_job_events_are_emitted_to_listeners(started_client):
    seen = []
    started_client.on_job_event(lambda event, payload: seen.append((event, payload)))

    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await started_client.submit_now(pid, params())
    await started_client.wait_for_job(job_id, timeout=5.0)

    updates = [p for e, p in seen if e == "job_update"]
    assert any(p["job_id"] == job_id and p["state"] == "done" for p in updates)


async def test_events_for_unknown_prompt_ids_are_ignored(started_client, fake_comfy):  # noqa: F811
    await fake_comfy.broadcast(
        {"type": "executed", "data": {"prompt_id": "not-ours", "node": "9"}}
    )
    await asyncio.sleep(0.1)
    assert started_client.connected is True  # no crash, no reconnect


async def test_reconnects_after_the_server_drops_the_socket(started_client, fake_comfy):  # noqa: F811
    for ws in list(fake_comfy._sockets):
        await ws.close()

    for _ in range(100):
        await asyncio.sleep(0.05)
        if started_client.connected and fake_comfy._sockets:
            break
    assert started_client.connected is True

    # and the reconnected socket still drives jobs to completion
    pid = await started_client.register_preset("sdxl2", "t2i", t2i_workflow())
    job_id = await started_client.submit_now(pid, params())
    assert (await started_client.wait_for_job(job_id, timeout=5.0))["state"] == "done"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_comfy_client.py -v -k "started_client or connect or execution or events or reconnect"`
Expected: FAIL — `AttributeError: 'ComfyClient' object has no attribute 'start'`

- [ ] **Step 3: Implement**

Add to the imports in `metascan/core/comfy_client.py`:

```python
import asyncio
import contextlib
from typing import Callable, List
from urllib.parse import urlparse, urlunparse

import websockets
```

Add these constants below `logger`:

```python
_RECONNECT_BACKOFF_SECONDS = (1.0, 3.0, 10.0)

JobEventCb = Callable[[str, Dict[str, Any]], None]
```

Extend `__init__` (append to the existing body):

```python
        self.connected: bool = False
        self._ws_task: Optional[asyncio.Task] = None
        self._listeners: List[JobEventCb] = []
        self._job_done: Dict[int, asyncio.Event] = {}
        self._stopping = False
```

Add the methods:

```python
    def _ws_url(self) -> str:
        parsed = urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunparse(
            (scheme, parsed.netloc, "/ws", "", f"clientId={self.client_id}", "")
        )

    async def start(self) -> None:
        """Open the persistent event socket and begin consuming events."""
        if self._ws_task is not None:
            return
        self._stopping = False
        ready = asyncio.Event()
        self._ws_task = asyncio.create_task(self._reader_loop(ready))
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(ready.wait(), timeout=5.0)

    async def shutdown(self) -> None:
        self._stopping = True
        if self._ws_task is not None:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_task
            self._ws_task = None
        self.connected = False
        await self.aclose()

    def on_job_event(self, cb: JobEventCb) -> None:
        self._listeners.append(cb)

    def _emit(self, event: str, payload: Dict[str, Any]) -> None:
        for cb in list(self._listeners):
            try:
                cb(event, payload)
            except Exception:
                logger.debug("comfy job-event listener raised", exc_info=True)

    async def _reader_loop(self, ready: asyncio.Event) -> None:
        """Consume ComfyUI's event stream, reconnecting on drop."""
        attempt = 0
        while not self._stopping:
            try:
                async with websockets.connect(self._ws_url()) as ws:
                    self.connected = True
                    attempt = 0
                    ready.set()
                    async for raw in ws:
                        try:
                            self._handle_event(json.loads(raw))
                        except Exception:
                            logger.debug("bad comfy event: %r", raw, exc_info=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("comfy websocket dropped: %s", exc)
            finally:
                self.connected = False
                ready.set()  # never block start() on an unreachable server

            if self._stopping:
                return
            delay = _RECONNECT_BACKOFF_SECONDS[
                min(attempt, len(_RECONNECT_BACKOFF_SECONDS) - 1)
            ]
            attempt += 1
            await asyncio.sleep(delay)

    def _job_for(self, prompt_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not prompt_id:
            return None
        row = self.db.get_job_by_comfy_prompt_id(prompt_id)
        return dict(row) if row else None

    def _handle_event(self, msg: Dict[str, Any]) -> None:
        kind = msg.get("type")
        data = msg.get("data") or {}
        job = self._job_for(data.get("prompt_id"))
        if job is None:
            return  # an event for someone else's client, or a stale prompt

        if kind == "progress":
            self._emit(
                "job_progress",
                {
                    "job_id": job["id"],
                    "value": data.get("value"),
                    "max": data.get("max"),
                },
            )
            return

        if kind == "execution_error":
            error = "{}: {}".format(
                data.get("node_type") or "unknown node",
                data.get("exception_message") or "execution failed",
            )
            self._finish_job(job["id"], "failed", error=error)
            return

        if kind == "executed":
            self._on_executed(job, data)

    def _on_executed(self, job: Dict[str, Any], data: Dict[str, Any]) -> None:
        """Overridden in Task 9 to download outputs before finishing."""
        self._finish_job(job["id"], "done")

    def _finish_job(
        self, job_id: int, state: str, error: Optional[str] = None
    ) -> None:
        fields: Dict[str, Any] = {"state": state, "finished_at": _now()}
        if error is not None:
            fields["error"] = error
        self.db.update_generation_job(job_id, **fields)
        self._emit("job_update", {"job_id": job_id, "state": state, "error": error})
        event = self._job_done.get(job_id)
        if event is not None:
            event.set()

    async def wait_for_job(
        self, job_id: int, timeout: float = 10.0
    ) -> Dict[str, Any]:
        """Block until a job leaves 'running'. Returns the final row."""
        current = self.db.get_generation_job(job_id)
        if current is None:
            raise ComfyError(f"No generation job with id {job_id}")
        if current["state"] not in ("queued", "running"):
            return dict(current)

        event = self._job_done.setdefault(job_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise ComfyError(
                f"Job {job_id} did not finish within {timeout}s"
            ) from exc
        finally:
            self._job_done.pop(job_id, None)
        return dict(self.db.get_generation_job(job_id))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_comfy_client.py -v`
Expected: 14 passed

- [ ] **Step 5: Run quality checks**

Run: `black metascan/ tests/ && make quality test`

- [ ] **Step 6: Commit**

```bash
git add metascan/core/comfy_client.py tests/test_comfy_client.py
git commit -m "feat(comfy): consume ComfyUI's event stream and track job state"
```

---

## Task 8: Queue discipline, cancellation, and priority

Metascan keeps its own queue and holds at most `in_flight` jobs inside
ComfyUI. Dumping every job into ComfyUI's FIFO would make prioritizing a
user-requested reroll impossible and cancellation coarse.

**Files:**
- Modify: `metascan/core/comfy_client.py`
- Test: `tests/test_comfy_client.py` (append)

**Interfaces:**
- Consumes: Tasks 6–7.
- Produces, on `ComfyClient`:
  - `async submit(self, preset_id: int, params: GenerationParams, panel_id: Optional[int] = None, priority: bool = False) -> int` — enqueues and returns the job id immediately
  - `async cancel(self, job_id: int) -> None`
  - `async cancel_all(self) -> None`
  - `def queue_depth(self) -> int`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_comfy_client.py`:

```python
async def test_submit_enqueues_and_returns_immediately(started_client, fake_comfy):  # noqa: F811
    fake_comfy.execution_delay = 0.3
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())

    job_ids = [await started_client.submit(pid, params()) for _ in range(5)]

    assert len(set(job_ids)) == 5
    await asyncio.sleep(0.1)
    assert len(fake_comfy.submitted) <= started_client.in_flight

    for jid in job_ids:
        assert (await started_client.wait_for_job(jid, timeout=10.0))["state"] == "done"
    assert len(fake_comfy.submitted) == 5


async def test_priority_jobs_jump_the_queue(started_client, fake_comfy):  # noqa: F811
    fake_comfy.execution_delay = 0.2
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())

    for _ in range(4):
        await started_client.submit(pid, params(positive="bulk"))
    await asyncio.sleep(0.05)
    urgent = await started_client.submit(
        pid, params(positive="urgent"), priority=True
    )

    await started_client.wait_for_job(urgent, timeout=10.0)
    order = [s["body"]["prompt"]["6"]["inputs"]["text"] for s in fake_comfy.submitted]
    assert "urgent" in order
    assert order.index("urgent") < len(order) - 1


async def test_cancel_a_queued_job_never_reaches_comfyui(started_client, fake_comfy):  # noqa: F811
    fake_comfy.execution_delay = 0.4
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())

    for _ in range(2):
        await started_client.submit(pid, params(positive="bulk"))
    victim = await started_client.submit(pid, params(positive="victim"))
    await started_client.cancel(victim)

    await asyncio.sleep(1.2)
    sent = [s["body"]["prompt"]["6"]["inputs"]["text"] for s in fake_comfy.submitted]
    assert "victim" not in sent
    assert started_client.db.get_generation_job(victim)["state"] == "cancelled"


async def test_cancel_a_running_job_interrupts_comfyui(started_client, fake_comfy):  # noqa: F811
    fake_comfy.execution_delay = 1.0
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await started_client.submit(pid, params())

    for _ in range(50):
        await asyncio.sleep(0.02)
        if started_client.db.get_generation_job(job_id)["state"] == "running":
            break

    await started_client.cancel(job_id)

    assert fake_comfy.interrupted == 1
    assert started_client.db.get_generation_job(job_id)["state"] == "cancelled"


async def test_cancel_all_clears_the_queue(started_client, fake_comfy):  # noqa: F811
    fake_comfy.execution_delay = 0.5
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_ids = [await started_client.submit(pid, params()) for _ in range(6)]

    await asyncio.sleep(0.05)
    await started_client.cancel_all()

    assert started_client.queue_depth() == 0
    states = {started_client.db.get_generation_job(j)["state"] for j in job_ids}
    assert states <= {"cancelled", "done", "running"}
    assert "queued" not in states


async def test_a_failing_job_does_not_stall_its_siblings(started_client, fake_comfy):  # noqa: F811
    fake_comfy.fail_with = "boom"
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_ids = [await started_client.submit(pid, params()) for _ in range(4)]

    for jid in job_ids:
        assert (await started_client.wait_for_job(jid, timeout=10.0))["state"] == "failed"
    assert len(fake_comfy.submitted) == 4
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_comfy_client.py -v -k "queue or priority or cancel or stall"`
Expected: FAIL — `AttributeError: 'ComfyClient' object has no attribute 'submit'`

- [ ] **Step 3: Implement**

Add `from collections import deque` to the imports.

Extend `__init__`:

```python
        self._queue: "deque[int]" = deque()
        self._running: set = set()
        self._pump_wake = asyncio.Event()
        self._pump_task: Optional[asyncio.Task] = None
```

In `start()`, after creating `self._ws_task`, add:

```python
        if self._pump_task is None:
            self._pump_task = asyncio.create_task(self._pump_loop())
```

In `shutdown()`, before cancelling `_ws_task`, add the mirror:

```python
        if self._pump_task is not None:
            self._pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump_task
            self._pump_task = None
```

Add the queue methods:

```python
    def queue_depth(self) -> int:
        return len(self._queue)

    async def submit(
        self,
        preset_id: int,
        params: GenerationParams,
        panel_id: Optional[int] = None,
        priority: bool = False,
    ) -> int:
        """Enqueue a job. Returns its id immediately; it reaches ComfyUI
        when a slot frees up.

        The preset is validated up front so a bad preset id fails at the
        call site rather than silently inside the pump.
        """
        self._load_preset(preset_id)
        job_id = int(
            self.db.create_generation_job(preset_id, params.to_json(), panel_id)
        )
        if priority:
            self._queue.appendleft(job_id)
        else:
            self._queue.append(job_id)
        self._pump_wake.set()
        self._emit("job_update", {"job_id": job_id, "state": "queued", "error": None})
        return job_id

    async def _pump_loop(self) -> None:
        """Keep at most ``in_flight`` jobs inside ComfyUI."""
        while True:
            try:
                while self._queue and len(self._running) < self.in_flight:
                    job_id = self._queue.popleft()
                    job = self.db.get_generation_job(job_id)
                    if job is None or job["state"] != "queued":
                        continue  # cancelled while waiting
                    await self._dispatch(job_id, job)
                self._pump_wake.clear()
                try:
                    await asyncio.wait_for(self._pump_wake.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("comfy pump loop error")
                await asyncio.sleep(0.5)

    async def _dispatch(self, job_id: int, job: Dict[str, Any]) -> None:
        self._running.add(job_id)
        try:
            workflow, bindings = self._load_preset(job["preset_id"])
            graph = apply_overrides(
                workflow, bindings, GenerationParams.from_json(job["params"])
            )
            resp = await self._http.post(
                f"{self.base_url}/prompt",
                json={"prompt": graph, "client_id": self.client_id},
            )
            resp.raise_for_status()
            prompt_id = resp.json().get("prompt_id")
            if not prompt_id:
                raise ComfyError("ComfyUI returned no prompt_id")
            self.db.update_generation_job(
                job_id,
                state="running",
                comfy_prompt_id=prompt_id,
                started_at=_now(),
            )
            self._emit(
                "job_update", {"job_id": job_id, "state": "running", "error": None}
            )
        except Exception as exc:
            message = f"ComfyUI at {self.base_url} rejected the job: {exc}"
            logger.warning(message)
            self._running.discard(job_id)
            self._finish_job(job_id, "failed", error=message)

    async def cancel(self, job_id: int) -> None:
        """Cancel a queued or running job.

        Queued jobs are simply dropped. A running job is interrupted in
        ComfyUI; already-generated images from earlier jobs are kept.
        """
        job = self.db.get_generation_job(job_id)
        if job is None or job["state"] not in ("queued", "running"):
            return

        if job["state"] == "queued":
            with contextlib.suppress(ValueError):
                self._queue.remove(job_id)
            self._finish_job(job_id, "cancelled")
            return

        prompt_id = job["comfy_prompt_id"]
        try:
            if prompt_id:
                await self._http.post(
                    f"{self.base_url}/queue", json={"delete": [prompt_id]}
                )
            await self._http.post(f"{self.base_url}/interrupt")
        except Exception as exc:
            logger.warning("Could not interrupt ComfyUI: %s", exc)
        self._running.discard(job_id)
        self._finish_job(job_id, "cancelled")
        self._pump_wake.set()

    async def cancel_all(self) -> None:
        """Drop every queued job and interrupt anything running."""
        queued = list(self._queue)
        self._queue.clear()
        for job_id in queued:
            self._finish_job(job_id, "cancelled")
        for job_id in list(self._running):
            await self.cancel(job_id)
```

Finally, release the slot when a job finishes. In `_finish_job`, add as the
first statement:

```python
        self._running.discard(job_id)
```

and as the last statement:

```python
        self._pump_wake.set()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_comfy_client.py -v`
Expected: 20 passed

- [ ] **Step 5: Run quality checks**

Run: `black metascan/ tests/ && make quality test`

- [ ] **Step 6: Commit**

```bash
git add metascan/core/comfy_client.py tests/test_comfy_client.py
git commit -m "feat(comfy): metascan-side job queue with priority and cancellation"
```

---

## Task 9: Output retrieval and ingest

On completion, read `/history`, fetch each image over `/view`, write it under
`output_root`, and ingest it through `Scanner.ingest_file`.

Fetching over HTTP rather than reading ComfyUI's output directory is what lets
a remote or containerized ComfyUI work unchanged, and removes any watcher race.

**Files:**
- Modify: `metascan/core/comfy_client.py`
- Test: `tests/test_comfy_client.py` (append)

**Interfaces:**
- Consumes: Tasks 6–8; `Scanner.ingest_file` (Task 4).
- Produces:
  - `ComfyClient.output_dir_for(self, job_id: int) -> Path`
  - `async ComfyClient.collect_outputs(self, job_id: int, prompt_id: str) -> List[Path]`
  - A new emitted event, `"job_outputs"`, with payload `{"job_id": int, "files": List[str]}`
  - Jobs reach `state='done'` **only after** their images are on disk and ingested.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_comfy_client.py`:

```python
from metascan.core.scanner import Scanner


@pytest.fixture
async def ingesting_client(fake_comfy, workspace):  # noqa: F811
    db = DatabaseManager(workspace / "db")
    c = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=db,
        scanner=Scanner(db),
        in_flight=2,
    )
    await c.start()
    try:
        yield c
    finally:
        await c.shutdown()


async def test_outputs_are_written_under_the_output_root(ingesting_client):
    pid = await ingesting_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await ingesting_client.submit(pid, params())
    await ingesting_client.wait_for_job(job_id, timeout=10.0)

    files = sorted(ingesting_client.output_dir_for(job_id).glob("*.png"))
    assert len(files) == 2
    assert all(f.stat().st_size > 0 for f in files)


async def test_outputs_are_ingested_into_the_media_database(ingesting_client):
    pid = await ingesting_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await ingesting_client.submit(pid, params())
    await ingesting_client.wait_for_job(job_id, timeout=10.0)

    for f in ingesting_client.output_dir_for(job_id).glob("*.png"):
        assert ingesting_client.db.get_media(str(f)) is not None


async def test_a_job_is_only_done_after_its_images_land(ingesting_client):
    pid = await ingesting_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await ingesting_client.submit(pid, params())
    await ingesting_client.wait_for_job(job_id, timeout=10.0)

    assert list(ingesting_client.output_dir_for(job_id).glob("*.png"))


async def test_job_outputs_event_carries_the_written_paths(ingesting_client):
    seen = []
    ingesting_client.on_job_event(
        lambda event, payload: seen.append((event, payload))
    )
    pid = await ingesting_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await ingesting_client.submit(pid, params())
    await ingesting_client.wait_for_job(job_id, timeout=10.0)

    outputs = [p for e, p in seen if e == "job_outputs"]
    assert outputs and len(outputs[0]["files"]) == 2


async def test_a_download_failure_fails_the_job_rather_than_hanging(
    ingesting_client, fake_comfy, monkeypatch  # noqa: F811
):
    async def boom(*args, **kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(ingesting_client, "_download_image", boom)

    pid = await ingesting_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await ingesting_client.submit(pid, params())

    job = await ingesting_client.wait_for_job(job_id, timeout=10.0)
    assert job["state"] == "failed"
    assert "connection reset" in job["error"]


async def test_a_failed_execution_writes_no_files(ingesting_client, fake_comfy):  # noqa: F811
    fake_comfy.fail_with = "boom"
    pid = await ingesting_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await ingesting_client.submit(pid, params())
    await ingesting_client.wait_for_job(job_id, timeout=10.0)

    assert not list(ingesting_client.output_dir_for(job_id).glob("*.png"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_comfy_client.py -v -k "outputs or ingested or download or landed"`
Expected: FAIL — `AttributeError: 'ComfyClient' object has no attribute 'output_dir_for'`

- [ ] **Step 3: Implement**

Replace the placeholder `_on_executed` from Task 7 with a version that
schedules collection, and add the collection machinery:

```python
    def output_dir_for(self, job_id: int) -> Path:
        """Where a job's images land. Flat per-job in Phase A; Phase B
        overrides this with a storyboard/scene/panel tree."""
        return self.output_root / f"job_{job_id:06d}"

    def _on_executed(self, job: Dict[str, Any], data: Dict[str, Any]) -> None:
        """Kick off output collection; the job stays 'running' until the
        files are on disk and ingested."""
        prompt_id = job.get("comfy_prompt_id") or data.get("prompt_id")
        asyncio.create_task(self._complete_job(int(job["id"]), str(prompt_id)))

    async def _complete_job(self, job_id: int, prompt_id: str) -> None:
        try:
            files = await self.collect_outputs(job_id, prompt_id)
        except Exception as exc:
            logger.warning("Output collection failed for job %s: %s", job_id, exc)
            self._finish_job(job_id, "failed", error=str(exc))
            return
        self._emit("job_outputs", {"job_id": job_id, "files": [str(f) for f in files]})
        self._finish_job(job_id, "done")

    async def _download_image(self, entry: Dict[str, Any], target: Path) -> None:
        resp = await self._http.get(
            f"{self.base_url}/view",
            params={
                "filename": entry.get("filename", ""),
                "subfolder": entry.get("subfolder", ""),
                "type": entry.get("type", "output"),
            },
        )
        resp.raise_for_status()
        target.write_bytes(resp.content)

    async def collect_outputs(self, job_id: int, prompt_id: str) -> List[Path]:
        """Fetch, persist, and ingest every image the job produced.

        Images are pulled over HTTP rather than read from ComfyUI's output
        directory so a remote or containerized ComfyUI works unchanged.
        """
        job = self.db.get_generation_job(job_id)
        if job is None:
            raise ComfyError(f"No generation job with id {job_id}")

        entry = await self.fetch_history(prompt_id)
        _, bindings = self._load_preset(job["preset_id"])
        images = ((entry.get("outputs") or {}).get(bindings.save) or {}).get(
            "images"
        ) or []

        target_dir = self.output_dir_for(job_id)
        target_dir.mkdir(parents=True, exist_ok=True)

        written: List[Path] = []
        for entry_image in images:
            name = entry_image.get("filename")
            if not name:
                continue
            target = target_dir / name
            await self._download_image(entry_image, target)
            written.append(target)
            if self.scanner is not None:
                try:
                    await asyncio.to_thread(self.scanner.ingest_file, target)
                except Exception as exc:
                    # A file that fails to ingest is still on disk and still
                    # reported; losing the whole job over it would be worse.
                    logger.warning("Could not ingest %s: %s", target, exc)
        return written
```

`asyncio.to_thread` around `ingest_file` is required: ingest does SQLite
writes and Pillow work, and running it on the event loop stalls the WebSocket
reader — the same reason the VLM tag pump wraps its DB writes.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_comfy_client.py -v`
Expected: 26 passed

- [ ] **Step 5: Run quality checks**

Run: `black metascan/ tests/ && make quality test`

- [ ] **Step 6: Commit**

```bash
git add metascan/core/comfy_client.py tests/test_comfy_client.py
git commit -m "feat(comfy): fetch generated images and ingest them into the library"
```

---

## Task 10: Reference-image upload with content-hash caching

Uploading a subject's reference once per run rather than once per panel.

**Files:**
- Modify: `metascan/core/comfy_client.py`
- Test: `tests/test_comfy_client.py` (append)

**Interfaces:**
- Consumes: Tasks 6–9.
- Produces: `async ComfyClient.upload_image(self, path: Path) -> str` — returns the ComfyUI-side filename suitable for `GenerationParams.ref_image`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_comfy_client.py`:

```python
from PIL import Image


def _make_png(path: Path, color=(10, 200, 90)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), color).save(path)
    return path


async def test_upload_image_returns_the_comfy_side_filename(
    started_client, workspace, fake_comfy  # noqa: F811
):
    src = _make_png(workspace / "refs" / "maya.png")
    name = await started_client.upload_image(src)

    assert name
    assert len(fake_comfy.uploaded) == 1


async def test_identical_content_is_uploaded_once(
    started_client, workspace, fake_comfy  # noqa: F811
):
    a = _make_png(workspace / "refs" / "a.png", color=(1, 2, 3))
    b = _make_png(workspace / "refs" / "b.png", color=(1, 2, 3))

    first = await started_client.upload_image(a)
    second = await started_client.upload_image(b)

    assert first == second
    assert len(fake_comfy.uploaded) == 1


async def test_different_content_uploads_twice(
    started_client, workspace, fake_comfy  # noqa: F811
):
    a = _make_png(workspace / "refs" / "c.png", color=(1, 2, 3))
    b = _make_png(workspace / "refs" / "d.png", color=(9, 9, 9))

    await started_client.upload_image(a)
    await started_client.upload_image(b)

    assert len(fake_comfy.uploaded) == 2


async def test_uploading_a_missing_file_raises(started_client, workspace):
    with pytest.raises(ComfyError) as exc:
        await started_client.upload_image(workspace / "refs" / "nope.png")
    assert "nope.png" in str(exc.value)


async def test_a_reference_workflow_receives_the_uploaded_name(
    started_client, workspace, fake_comfy  # noqa: F811
):
    wf = t2i_workflow()
    wf["11"] = _node("LoadImage", "MS_REF_IMAGE", {"image": "placeholder.png"})
    pid = await started_client.register_preset("sdxl-ref", "ref", wf)

    name = await started_client.upload_image(_make_png(workspace / "refs" / "m.png"))
    job_id = await started_client.submit(pid, params(ref_image=name))
    await started_client.wait_for_job(job_id, timeout=10.0)

    sent = fake_comfy.submitted[-1]["body"]["prompt"]
    assert sent["11"]["inputs"]["image"] == name
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_comfy_client.py -v -k upload`
Expected: FAIL — `AttributeError: 'ComfyClient' object has no attribute 'upload_image'`

- [ ] **Step 3: Implement**

Add `import hashlib` to the imports. Extend `__init__`:

```python
        self._upload_cache: Dict[str, str] = {}
```

Add the method:

```python
    async def upload_image(self, path: Path) -> str:
        """Upload a local image to ComfyUI's input directory.

        Returns the ComfyUI-side filename for GenerationParams.ref_image.
        Cached by content hash, so a subject's reference is uploaded once
        per run rather than once per panel.
        """
        path = Path(path)
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ComfyError(f"Cannot read reference image {path}: {exc}") from exc

        digest = hashlib.sha256(payload).hexdigest()
        cached = self._upload_cache.get(digest)
        if cached is not None:
            return cached

        upload_name = f"metascan_{digest[:16]}{path.suffix or '.png'}"
        files = {"image": (upload_name, payload, "image/png")}
        try:
            resp = await self._http.post(
                f"{self.base_url}/upload/image",
                files=files,
                data={"overwrite": "true"},
            )
            resp.raise_for_status()
        except Exception as exc:
            raise ComfyError(
                f"Reference upload to {self.base_url} failed: {exc}"
            ) from exc

        body = resp.json()
        name = body.get("name") or upload_name
        subfolder = body.get("subfolder") or ""
        resolved = f"{subfolder}/{name}" if subfolder else str(name)
        self._upload_cache[digest] = resolved
        return resolved
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_comfy_client.py -v`
Expected: 31 passed

- [ ] **Step 5: Run quality checks**

Run: `black metascan/ tests/ && make quality test`

- [ ] **Step 6: Commit**

```bash
git add metascan/core/comfy_client.py tests/test_comfy_client.py
git commit -m "feat(comfy): upload reference images with content-hash caching"
```

---

## Task 11: Config, REST API, lifespan wiring, and docs

Makes the subsystem reachable. This is the task that turns Phase A into
something usable via `curl`.

**Files:**
- Create: `backend/services/comfy_service.py`, `backend/api/comfy.py`, `tests/test_comfy_api.py`
- Modify: `backend/config.py`, `backend/main.py`, `docs/api-reference.md`, `docs/configuration.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: `ComfyClient` (Tasks 6–10), the DB methods (Task 3).
- Produces:
  - `backend.config.get_comfy_config(config: dict) -> dict`
  - `backend.api.comfy.set_comfy_client(client) -> None` / `get_comfy_client()`
  - Routes:
    - `GET /api/comfy/status`
    - `GET /api/comfy/presets`
    - `POST /api/comfy/presets` → `{"id": int}`
    - `DELETE /api/comfy/presets/{id}` → `{"status": "deleted"}`
    - `POST /api/comfy/submit` → `{"job_id": int}`
    - `GET /api/comfy/jobs`
    - `GET /api/comfy/jobs/{id}`
    - `POST /api/comfy/jobs/{id}/cancel` → `{"status": "cancelled"}`
  - A `comfy` WebSocket channel carrying `job_update`, `job_progress`, `job_outputs`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_comfy_api.py
"""Route tests for /api/comfy/*, against a stubbed ComfyClient.

Follows tests/test_lifespan_vlm.py: the client singleton is installed
directly rather than by constructing a real one.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api import comfy as comfy_api
from backend.main import create_app
from metascan.core.comfy_bindings import BindingError
from metascan.core.database_sqlite import DatabaseManager


def _node(class_type: str, title: str, inputs: dict) -> dict:
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}


def t2i_workflow() -> dict:
    return {
        "3": _node("KSampler", "MS_SEED", {"seed": 0}),
        "5": _node(
            "EmptyLatentImage",
            "MS_LATENT",
            {"width": 512, "height": 512, "batch_size": 1},
        ),
        "6": _node("CLIPTextEncode", "MS_POSITIVE", {"text": ""}),
        "9": _node("SaveImage", "MS_SAVE", {}),
    }


class StubComfy:
    """Records calls; performs no network I/O.

    Also stands in for ComfyClient inside the lifespan (see the fixture),
    so it must accept the same constructor kwargs and expose start /
    shutdown / on_job_event.
    """

    def __init__(self, db=None, **kwargs):
        self.db = db
        self.base_url = "http://stub:8188"
        self.submitted = []
        self.cancelled = []
        self.listeners = []
        self.raise_on_register = None

    async def start(self):
        return None

    async def shutdown(self):
        return None

    def on_job_event(self, cb):
        self.listeners.append(cb)

    def snapshot(self):
        return {"base_url": self.base_url, "client_id": "stub", "in_flight": 2}

    async def register_preset(self, name, kind, workflow):
        if self.raise_on_register:
            raise self.raise_on_register
        from metascan.core.comfy_bindings import resolve_bindings
        import json as _json

        bindings = resolve_bindings(workflow, kind)
        return self.db.create_workflow_preset(
            name, kind, _json.dumps(workflow), bindings.to_json()
        )

    async def submit(self, preset_id, params, panel_id=None, priority=False):
        self.submitted.append((preset_id, params, panel_id, priority))
        return self.db.create_generation_job(preset_id, params.to_json(), panel_id)

    async def cancel(self, job_id):
        self.cancelled.append(job_id)
        self.db.update_generation_job(job_id, state="cancelled")


@pytest.fixture
def client(monkeypatch):
    """Install StubComfy *as* ComfyClient before the app starts.

    The lifespan constructs a ComfyClient and calls set_comfy_client
    itself, so installing a stub beforehand would simply be overwritten.
    Patching the class in backend.main is what makes the stub survive
    startup.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db = DatabaseManager(Path(tmp))
        stubs = []

        def make_stub(*args, **kwargs):
            stub = StubComfy(db=db)
            stubs.append(stub)
            return stub

        monkeypatch.setattr("backend.dependencies.get_db", lambda: db)
        monkeypatch.setattr("backend.api.comfy.get_db", lambda: db)
        monkeypatch.setattr("backend.main.ComfyClient", make_stub)

        app = create_app()
        with TestClient(app) as c:
            assert stubs, "lifespan did not construct a ComfyClient"
            c.stub = stubs[0]
            c.db = db
            yield c
        comfy_api.set_comfy_client(None)


def test_status_reports_the_configured_server(client):
    r = client.get("/api/comfy/status")
    assert r.status_code == 200
    assert r.json()["base_url"] == "http://stub:8188"


def test_status_without_a_client_is_not_an_error(client):
    comfy_api.set_comfy_client(None)
    r = client.get("/api/comfy/status")
    assert r.status_code == 200
    assert r.json()["base_url"] is None


def test_register_a_preset(client):
    r = client.post(
        "/api/comfy/presets",
        json={"name": "sdxl", "kind": "t2i", "workflow": t2i_workflow()},
    )
    assert r.status_code == 200
    assert isinstance(r.json()["id"], int)


def test_registering_an_unbindable_workflow_returns_400_with_the_missing_titles(client):
    client.stub.raise_on_register = BindingError(
        "Workflow is missing required node title(s) for kind 't2i': MS_SAVE"
    )
    r = client.post(
        "/api/comfy/presets",
        json={"name": "broken", "kind": "t2i", "workflow": {}},
    )
    assert r.status_code == 400
    assert "MS_SAVE" in r.json()["detail"]


def test_list_presets_omits_the_workflow_blob(client):
    client.post(
        "/api/comfy/presets",
        json={"name": "sdxl", "kind": "t2i", "workflow": t2i_workflow()},
    )
    rows = client.get("/api/comfy/presets").json()
    assert rows[0]["name"] == "sdxl"
    assert "workflow_json" not in rows[0]


def test_delete_a_preset_returns_a_json_body(client):
    pid = client.post(
        "/api/comfy/presets",
        json={"name": "sdxl", "kind": "t2i", "workflow": t2i_workflow()},
    ).json()["id"]
    r = client.delete(f"/api/comfy/presets/{pid}")
    assert r.status_code == 200
    assert r.json() == {"status": "deleted"}


def test_delete_a_missing_preset_is_404(client):
    assert client.delete("/api/comfy/presets/9999").status_code == 404


def test_submit_returns_a_job_id(client):
    pid = client.post(
        "/api/comfy/presets",
        json={"name": "sdxl", "kind": "t2i", "workflow": t2i_workflow()},
    ).json()["id"]

    r = client.post(
        "/api/comfy/submit",
        json={
            "preset_id": pid,
            "positive": "a cat",
            "seed": 42,
            "width": 1024,
            "height": 576,
            "batch_size": 2,
        },
    )
    assert r.status_code == 200
    assert isinstance(r.json()["job_id"], int)
    assert client.stub.submitted[0][0] == pid


def test_submit_without_a_client_is_503(client):
    comfy_api.set_comfy_client(None)
    r = client.post(
        "/api/comfy/submit",
        json={
            "preset_id": 1,
            "positive": "x",
            "seed": 1,
            "width": 512,
            "height": 512,
            "batch_size": 1,
        },
    )
    assert r.status_code == 503


def test_get_and_list_jobs(client):
    pid = client.post(
        "/api/comfy/presets",
        json={"name": "sdxl", "kind": "t2i", "workflow": t2i_workflow()},
    ).json()["id"]
    job_id = client.post(
        "/api/comfy/submit",
        json={
            "preset_id": pid,
            "positive": "a cat",
            "seed": 1,
            "width": 512,
            "height": 512,
            "batch_size": 1,
        },
    ).json()["job_id"]

    assert client.get(f"/api/comfy/jobs/{job_id}").json()["id"] == job_id
    assert client.get("/api/comfy/jobs").json()[0]["id"] == job_id
    assert client.get("/api/comfy/jobs/9999").status_code == 404


def test_cancel_a_job(client):
    pid = client.post(
        "/api/comfy/presets",
        json={"name": "sdxl", "kind": "t2i", "workflow": t2i_workflow()},
    ).json()["id"]
    job_id = client.post(
        "/api/comfy/submit",
        json={
            "preset_id": pid,
            "positive": "a cat",
            "seed": 1,
            "width": 512,
            "height": 512,
            "batch_size": 1,
        },
    ).json()["job_id"]

    r = client.post(f"/api/comfy/jobs/{job_id}/cancel")
    assert r.json() == {"status": "cancelled"}
    assert client.stub.cancelled == [job_id]
```

`tests/test_lifespan_vlm.py` is the reference for app construction and
`get_db` patching in this codebase. If its approach differs from the fixture
above, follow the existing file — it is known to work against the real
`create_app`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_comfy_api.py -v`
Expected: `ModuleNotFoundError: No module named 'backend.api.comfy'`

- [ ] **Step 3: Add the config accessor**

Append to `backend/config.py`:

```python
def get_comfy_config(config: dict) -> dict:
    """Return the ``comfy`` section with defaults filled in.

    Shape:
        {
            "base_url": "http://127.0.0.1:8188",
            "in_flight": 2,                  # jobs held inside ComfyUI at once
            "unload_vlm_during_generation": True,
            "output_root": "data/storyboards",
            "request_timeout_s": 30.0,
        }
    """
    raw = config.get("comfy", {}) or {}
    return {
        "base_url": str(raw.get("base_url") or "http://127.0.0.1:8188"),
        "in_flight": max(1, int(raw.get("in_flight") or 2)),
        "unload_vlm_during_generation": bool(
            raw.get("unload_vlm_during_generation", True)
        ),
        "output_root": str(raw.get("output_root") or "data/storyboards"),
        "request_timeout_s": float(raw.get("request_timeout_s") or 30.0),
    }
```

- [ ] **Step 4: Add the service layer**

```python
# backend/services/comfy_service.py
"""Async wrappers over the ComfyUI preset/job DB methods.

DatabaseManager is synchronous and guarded by a threading.Lock; route
handlers must never call it directly on the event loop. Every method here
is a thin asyncio.to_thread hop, matching backend/services/folders_service.py.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional


class ComfyService:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def list_presets(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.list_workflow_presets)

    async def get_preset(self, preset_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.get_workflow_preset, preset_id)

    async def delete_preset(self, preset_id: int) -> bool:
        return await asyncio.to_thread(self.db.delete_workflow_preset, preset_id)

    async def get_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.get_generation_job, job_id)

    async def list_jobs(
        self, states: Optional[List[str]] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(
            self.db.list_generation_jobs, states, limit
        )
```

- [ ] **Step 5: Add the router**

```python
# backend/api/comfy.py
"""REST endpoints for the ComfyUI driver.

The ComfyClient singleton is installed by the FastAPI lifespan via
``set_comfy_client(...)``. Endpoints that need the network fail fast with
503 when it is missing; read-only endpoints degrade gracefully so the UI
can still show what is configured.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.dependencies import get_db
from backend.services.comfy_service import ComfyService
from metascan.core.comfy_bindings import BindingError, GenerationParams
from metascan.core.comfy_client import ComfyError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/comfy", tags=["comfy"])

_comfy_client: Optional[Any] = None


def set_comfy_client(client: Any) -> None:
    """Install the ComfyClient singleton. Pass None to clear (tests)."""
    global _comfy_client
    _comfy_client = client


def get_comfy_client() -> Any:
    return _comfy_client


def _require_client() -> Any:
    if _comfy_client is None:
        raise HTTPException(
            status_code=503,
            detail="ComfyUI driver is not running. Check the 'comfy' section "
            "of config.json and restart the server.",
        )
    return _comfy_client


def _service() -> ComfyService:
    return ComfyService(get_db())


class PresetRequest(BaseModel):
    name: str
    kind: str = "t2i"
    workflow: Dict[str, Any]


class SubmitRequest(BaseModel):
    preset_id: int
    positive: str
    seed: int
    width: int
    height: int
    batch_size: int = Field(default=1, ge=1)
    negative: Optional[str] = None
    lora_name: Optional[str] = None
    lora_strength: Optional[float] = None
    ref_image: Optional[str] = None
    priority: bool = False


@router.get("/status")
async def status() -> Dict[str, Any]:
    client = _comfy_client
    if client is None:
        return {"base_url": None, "client_id": None, "in_flight": 0}
    return client.snapshot()


@router.get("/presets")
async def list_presets() -> List[Dict[str, Any]]:
    return await _service().list_presets()


@router.post("/presets")
async def create_preset(body: PresetRequest) -> Dict[str, int]:
    client = _require_client()
    try:
        preset_id = await client.register_preset(body.name, body.kind, body.workflow)
    except BindingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": int(preset_id)}


@router.delete("/presets/{preset_id}")
async def delete_preset(preset_id: int) -> Dict[str, str]:
    if not await _service().delete_preset(preset_id):
        raise HTTPException(status_code=404, detail=f"No preset {preset_id}")
    return {"status": "deleted"}


@router.post("/submit")
async def submit(body: SubmitRequest) -> Dict[str, int]:
    client = _require_client()
    params = GenerationParams(
        positive=body.positive,
        seed=body.seed,
        width=body.width,
        height=body.height,
        batch_size=body.batch_size,
        negative=body.negative,
        lora_name=body.lora_name,
        lora_strength=body.lora_strength,
        ref_image=body.ref_image,
    )
    try:
        job_id = await client.submit(body.preset_id, params, priority=body.priority)
    except BindingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ComfyError as exc:
        # 503, not 502: spec §9 wants "ComfyUI unreachable" to read as a
        # service-availability problem, and ComfyError's message already
        # carries the configured base_url.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"job_id": int(job_id)}


@router.get("/jobs")
async def list_jobs(
    state: Optional[str] = None, limit: int = 100
) -> List[Dict[str, Any]]:
    states = [state] if state else None
    return await _service().list_jobs(states=states, limit=limit)


@router.get("/jobs/{job_id}")
async def get_job(job_id: int) -> Dict[str, Any]:
    job = await _service().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job {job_id}")
    return job


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: int) -> Dict[str, str]:
    client = _require_client()
    job = await _service().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job {job_id}")
    await client.cancel(job_id)
    return {"status": "cancelled"}
```

- [ ] **Step 6: Wire the lifespan and register the router**

In `backend/main.py`:

Add to the `backend.api` import list: `comfy as comfy_api`. Add alongside the
other imports:

```python
from backend.config import get_comfy_config
from backend.api.comfy import set_comfy_client
from metascan.core.comfy_client import ComfyClient
from metascan.core.scanner import Scanner
```

Inside `lifespan`, after the `VlmClient` is constructed and installed, add:

```python
    comfy_cfg = get_comfy_config(load_app_config())
    comfy_client = ComfyClient(
        base_url=comfy_cfg["base_url"],
        output_root=Path(comfy_cfg["output_root"]),
        db=get_db(),
        scanner=Scanner(get_db(), thumbnail_cache=get_thumbnail_cache()),
        in_flight=comfy_cfg["in_flight"],
        request_timeout_s=comfy_cfg["request_timeout_s"],
    )
    comfy_client.on_job_event(
        lambda event, payload: ws_manager.broadcast_sync("comfy", event, payload)
    )
    set_comfy_client(comfy_client)
    # start() never blocks on an unreachable ComfyUI — the reader loop
    # retries with backoff, so a server that is not running yet simply
    # connects later.
    await comfy_client.start()
```

In the shutdown half of `lifespan` (after the `yield`), add:

```python
    await comfy_client.shutdown()
```

Register the router alongside the others:

```python
    app.include_router(comfy_api.router)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_comfy_api.py -v`
Expected: 12 passed

- [ ] **Step 8: Verify end-to-end against the fake server manually**

```bash
python - <<'PY'
import asyncio, json
from pathlib import Path
from tests._fake_comfy_server import FakeComfy
from metascan.core.comfy_client import ComfyClient
from metascan.core.comfy_bindings import GenerationParams
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.scanner import Scanner
import tempfile

async def main():
    server = FakeComfy(); await server.start()
    tmp = Path(tempfile.mkdtemp())
    db = DatabaseManager(tmp / "db")
    c = ComfyClient(server.base_url, tmp / "out", db, scanner=Scanner(db))
    await c.start()
    wf = {
      "3": {"class_type": "KSampler", "inputs": {"seed": 0}, "_meta": {"title": "MS_SEED"}},
      "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}, "_meta": {"title": "MS_LATENT"}},
      "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}, "_meta": {"title": "MS_POSITIVE"}},
      "9": {"class_type": "SaveImage", "inputs": {}, "_meta": {"title": "MS_SAVE"}},
    }
    pid = await c.register_preset("demo", "t2i", wf)
    jid = await c.submit(pid, GenerationParams("a cat", 42, 1024, 576, 2))
    print(await c.wait_for_job(jid, timeout=10))
    print(sorted(p.name for p in c.output_dir_for(jid).glob("*.png")))
    await c.shutdown(); await server.stop()

asyncio.run(main())
PY
```

Expected: a job dict with `state: 'done'` and two PNG filenames.

- [ ] **Step 9: Document**

**`docs/configuration.md`** — add a `comfy` section documenting every key from
`get_comfy_config` with its default, and note that `output_root` is relative to
the repo root.

**`docs/api-reference.md`** — add the eight `/api/comfy/*` routes with request
and response shapes, and add `comfy` to the WebSocket channel list with its
three events (`job_update`, `job_progress`, `job_outputs`).

**`CLAUDE.md`** — add to the Architecture bullet list:

```markdown
- **ComfyUI is driven, not just parsed.** `metascan/core/comfy_client.py`
  submits jobs to a ComfyUI server (the extractors in
  `metascan/extractors/comfyui*.py` remain read-only metadata parsers, a
  separate concern). A workflow is registered as an API-format graph whose
  nodes are titled with the `MS_*` convention (`MS_POSITIVE`, `MS_NEGATIVE`,
  `MS_SEED`, `MS_LATENT`, `MS_SAVE`, optional `MS_LORA` / `MS_REF_IMAGE`);
  `comfy_bindings.resolve_bindings` maps titles to node ids at registration
  time and **fails loudly** on a missing required title. Titles are used
  rather than node ids because ComfyUI renumbers nodes on re-save.
- **Metascan owns the ComfyUI job queue.** `ComfyClient` holds at most
  `comfy.in_flight` jobs inside ComfyUI at a time so a user-requested reroll
  can jump the queue and cancellation stays responsive. One persistent
  WebSocket per app (not per job) consumes ComfyUI's event stream; the
  `execution_error` node type and message go verbatim into
  `generation_jobs.error`.
- **Generated images are fetched over HTTP, never read from disk.**
  `collect_outputs` pulls each image via `/view` and writes it under
  `comfy.output_root`, so a remote or containerized ComfyUI works unchanged
  and there is no watcher race. Ingest goes through the public
  `Scanner.ingest_file`, wrapped in `asyncio.to_thread` — it does SQLite
  writes and Pillow work, and running it on the event loop stalls the
  WebSocket reader.
- **`generation_jobs.panel_id` has no `REFERENCES` clause.** The `panels`
  table arrives in Phase B of the storyboard feature; with
  `PRAGMA foreign_keys = ON`, an INSERT naming a foreign key to a missing
  table fails at runtime, and SQLite cannot add a foreign key to an existing
  table without rebuilding it.
```

- [ ] **Step 10: Run quality checks**

Run: `black metascan/ backend/ tests/ && make quality test`
Expected: all clean, full suite green

- [ ] **Step 11: Commit**

```bash
git add backend/api/comfy.py backend/services/comfy_service.py \
        backend/config.py backend/main.py tests/test_comfy_api.py \
        docs/api-reference.md docs/configuration.md CLAUDE.md
git commit -m "feat(comfy): REST API, config, and lifespan wiring for the driver"
```

---

## Done Criteria

Phase A is complete when all of the following hold:

1. `make quality test` passes.
2. A workflow exported from ComfyUI in API format, with `MS_*` titles applied, registers through `POST /api/comfy/presets`.
3. `POST /api/comfy/submit` produces images that appear in the metascan library grid without a rescan.
4. Killing ComfyUI mid-run marks the affected job `failed` with ComfyUI's own error text, leaves siblings alone, and the client reconnects when ComfyUI returns.
5. `POST /api/comfy/jobs/{id}/cancel` on a queued job prevents it from ever reaching ComfyUI.

## Not in this phase

Storyboard tables, prompt synthesis, `media.hidden`, aspect-ratio bucketing, and all frontend code. Those are Phases B and C.
