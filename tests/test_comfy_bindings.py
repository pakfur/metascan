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
        "4": _node(
            "CheckpointLoaderSimple", "Load Checkpoint", {"ckpt_name": "x.safetensors"}
        ),
        "5": _node(
            "EmptyLatentImage",
            "MS_LATENT",
            {"width": 512, "height": 512, "batch_size": 1},
        ),
        "6": _node("CLIPTextEncode", "MS_POSITIVE", {"text": "", "clip": ["4", 1]}),
        "7": _node("CLIPTextEncode", "MS_NEGATIVE", {"text": "", "clip": ["4", 1]}),
        "9": _node(
            "SaveImage", "MS_SAVE", {"images": ["8", 0], "filename_prefix": "ms"}
        ),
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
