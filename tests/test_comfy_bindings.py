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
