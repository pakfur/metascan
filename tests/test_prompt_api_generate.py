"""Endpoint tests for /api/prompt/generate, /transform, /clean.

Builds a minimal FastAPI app with only the prompt router so that
``backend.main.create_app``'s lifespan (which installs a real
VlmClient) doesn't clobber our stub. The router accesses the
VlmClient via ``backend.api.vlm.get_vlm_client`` — we set the
module-level ``_vlm_client`` directly.

Generate uses the new ``meta_prompt_templates`` module (full
Qwen3-VL meta-prompts plus a Negative-block parser); transform and
clean still use the legacy ``prompt_templates`` builders.
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
                "target_model": "sd",
                "architecture": "t2i",
                "extras": [],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["prompt"] == "stubbed prompt"
    assert body["vlm_model_id"] == "qwen3vl-4b"
    assert "elapsed_ms" in body
    # No "Negative:" block in the stubbed reply -> negative is None even
    # though sd is a negative-bearing target.
    assert body["negative"] is None


def test_generate_uses_meta_prompt_for_target(stub_vlm, img_file):
    """The new module embeds the full meta-prompt, which has stable
    target-specific phrases the legacy builder didn't produce."""
    with TestClient(_build_app()) as c:
        c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "flux1",
                "architecture": "t2i",
                "extras": [],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    assert len(stub_vlm.calls) == 1
    sys_prompt = stub_vlm.calls[0]["system_prompt"]
    assert "Flux.1 prompt engineer" in sys_prompt
    assert "natural-language prompt" in sys_prompt


def test_generate_safety_extra_appends_sfw_directive(stub_vlm, img_file):
    with TestClient(_build_app()) as c:
        c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "flux1",
                "architecture": "t2i",
                "extras": ["includeSafety"],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    sys_prompt = stub_vlm.calls[0]["system_prompt"]
    assert "Content constraint" in sys_prompt
    assert "fully SFW" in sys_prompt


def test_generate_uncensored_extra_appends_explicit_directive(stub_vlm, img_file):
    with TestClient(_build_app()) as c:
        c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "chroma",
                "architecture": "t2i",
                "extras": ["includeUncensored"],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    sys_prompt = stub_vlm.calls[0]["system_prompt"]
    assert "Content constraint" in sys_prompt
    assert "anatomically-correct" in sys_prompt


def test_generate_returns_negative_for_sd_when_model_emits_one(stub_vlm, img_file):
    """sd / pony / chroma / qwen ask Qwen3 for a Negative block; the
    parser splits it out into the GenerateResponse.negative field."""
    stub_vlm.next_response = (
        "masterpiece, best quality, a young woman in a red sweater\n"
        "\n"
        "Negative: low quality, blurry, deformed"
    )
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "sd",
                "architecture": "t2i",
                "extras": [],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert "Negative:" not in body["prompt"]
    assert "young woman" in body["prompt"]
    assert body["negative"] is not None
    assert "low quality" in body["negative"]


def test_generate_no_negative_field_for_flux1(stub_vlm, img_file):
    """Flux.1 / Flux.2 / Z-Image meta-prompts forbid a Negative block —
    the response field stays None even if the model accidentally emits one
    (Flux targets are configured as ``has_negative=False``)."""
    stub_vlm.next_response = "a quiet prose description of the image"
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "flux1",
                "architecture": "t2i",
                "extras": [],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["prompt"] == "a quiet prose description of the image"
    assert body["negative"] is None


def test_generate_404_when_file_missing(stub_vlm):
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/generate",
            json={
                "file_path": "/nonexistent/x.jpg",
                "target_model": "sd",
                "architecture": "t2i",
                "extras": [],
                "temperature": 0.6,
                "max_tokens": 250,
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
                "target_model": "sd",
                "architecture": "t2i",
                "extras": [],
                "temperature": 0.6,
                "max_tokens": 250,
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
                    "target_model": "sd",
                    "architecture": "t2i",
                    "extras": [],
                    "temperature": 0.6,
                    "max_tokens": 250,
                },
            )
        assert r.status_code == 503
    finally:
        vlm_api.set_vlm_client(None)


def test_generate_422_on_invalid_extra(stub_vlm, img_file):
    """Old (16-option) extras now fail validation under the narrowed
    Literal — the API accepts only 'includeSafety' / 'includeUncensored'."""
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "sd",
                "architecture": "t2i",
                "extras": ["includeLighting"],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    assert r.status_code == 422


def test_generate_502_when_vlm_raises(stub_vlm, img_file):
    stub_vlm.next_error = VlmError("upstream boom")
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "sd",
                "architecture": "t2i",
                "extras": [],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    assert r.status_code == 502
    assert "upstream boom" in r.json()["detail"]


# --- Transform / Clean (still on legacy prompt_templates) -----------------


def test_transform_passes_source_prompt_through(stub_vlm):
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/transform",
            json={
                "source_prompt": "old prompt here",
                "target_model": "chroma",
                "architecture": "t2i",
                "extras": [],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    assert r.status_code == 200
    user = stub_vlm.calls[0]["user_prompt"]
    assert "old prompt here" in user


def test_transform_uncensored_extra_reaches_legacy_builder(stub_vlm):
    """The new ExtraOption vocabulary is mapped onto legacy keys before
    being handed to compose_transform_prompts. ``includeUncensored`` keeps
    its name; ``includeSafety`` is mapped to legacy ``keepPG``."""
    with TestClient(_build_app()) as c:
        c.post(
            "/api/prompt/transform",
            json={
                "source_prompt": "subject in a forest",
                "target_model": "qwen",
                "architecture": "t2i",
                "extras": ["includeUncensored"],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    sys_prompt = stub_vlm.calls[0]["system_prompt"]
    # Legacy qwen builder emits a sentence about explicit, anatomically
    # correct terminology when includeUncensored is set.
    assert "anatomically correct" in sys_prompt.lower()
    # Confirm the transform framing is preserved.
    assert "Rewrite the supplied prompt" in sys_prompt


def test_transform_safety_extra_maps_to_legacy_keep_pg(stub_vlm):
    with TestClient(_build_app()) as c:
        c.post(
            "/api/prompt/transform",
            json={
                "source_prompt": "any subject",
                "target_model": "flux1",
                "architecture": "t2i",
                "extras": ["includeSafety"],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    sys_prompt = stub_vlm.calls[0]["system_prompt"]
    # Legacy flux1 builder emits "Keep the description SFW." for keepPG.
    assert "Keep the description SFW" in sys_prompt


def test_clean_uses_clean_template(stub_vlm):
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/clean",
            json={
                "source_prompt": "messy,, prompt",
                "temperature": 0.4,
                "max_tokens": 250,
            },
        )
    assert r.status_code == 200
    sys = stub_vlm.calls[0]["system_prompt"]
    assert "clean" in sys.lower()


# --- /elements + element_overrides ----------------------------------------


def test_list_elements_returns_target_specific_rows():
    """GET /api/prompt/elements should return the parsed numbered list
    in the meta-prompt's authored order. No VLM stub needed — the
    endpoint is pure parsing."""
    with TestClient(_build_app()) as c:
        r = c.get("/api/prompt/elements", params={"target_model": "flux1"})
    assert r.status_code == 200
    body = r.json()
    assert body["target_model"] == "flux1"
    # Flux.1's numbered list has 9 items; the first is the Subject row.
    assert len(body["elements"]) == 9
    assert body["elements"][0]["title"] == "Subject"
    assert "who/what" in body["elements"][0]["default_body"]


def test_list_elements_404_or_422_on_unknown_target():
    """Unknown target falls into FastAPI's Literal validation -> 422."""
    with TestClient(_build_app()) as c:
        r = c.get("/api/prompt/elements", params={"target_model": "nonsense"})
    assert r.status_code == 422


def test_generate_element_overrides_replace_meta_body(stub_vlm, img_file):
    """Element overrides should land inside the system prompt at the
    spot where the corresponding row's default body lives — so the
    Qwen3 server sees the user's edited wording, not the default."""
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "flux1",
                "architecture": "t2i",
                "extras": [],
                # Replace element 0 (Subject) and leave the rest at default.
                "element_overrides": ["MY CUSTOM SUBJECT TEXT", None, None],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    assert r.status_code == 200
    sys_prompt = stub_vlm.calls[0]["system_prompt"]
    assert "MY CUSTOM SUBJECT TEXT" in sys_prompt
    # The default Subject body should be GONE; the rest of the
    # meta-prompt's structure must still be present.
    assert "who/what, with distinguishing details" not in sys_prompt
    # And later element (Lighting) defaults should still be intact.
    assert "direction, quality (hard/soft)" in sys_prompt


def test_generate_empty_element_overrides_uses_defaults(stub_vlm, img_file):
    """The frontend sends ``[]`` when the table never populated; the
    backend must treat that as "use defaults" — equivalent to omitting
    the field entirely."""
    with TestClient(_build_app()) as c:
        c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "flux1",
                "architecture": "t2i",
                "extras": [],
                "element_overrides": [],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    sys_prompt = stub_vlm.calls[0]["system_prompt"]
    assert "who/what, with distinguishing details" in sys_prompt
