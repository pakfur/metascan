"""MS_DENOISE and MS_MODEL: the refine-upscale bindings (Moss + Ultimate SD
Upscale), and the shipped refine workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from metascan.core.comfy_bindings import (
    BindingError,
    Bindings,
    GenerationParams,
    apply_overrides,
    baked_model,
    resolve_bindings,
)
from metascan.core.workflow_validation import validate_workflow

ROOT = Path(__file__).resolve().parent.parent / "data" / "workflows"
REFINE = "upscale_image_moss_refine_api.json"
REFINE_SEEDVR2 = "upscale_image_moss_refine_seedvr2_api.json"


def node(class_type: str, title: str, **inputs: Any) -> Dict[str, Any]:
    return {"class_type": class_type, "_meta": {"title": title}, "inputs": inputs}


def graph(**extra: Dict[str, Any]) -> Dict[str, Any]:
    wf = {
        "1": node("LoadImage", "MS_SOURCE_IMAGE", image="baked.png"),
        "2": node("SaveImage", "MS_SAVE", images=["1", 0]),
    }
    wf.update(extra)
    return wf


def params(**kw: Any) -> GenerationParams:
    values: Dict[str, Any] = dict(
        positive=None, seed=None, width=0, height=0, batch_size=1
    )
    values.update(kw)
    return GenerationParams(**values)


def test_denoise_binds_a_sampler_widget():
    wf = graph(**{"3": node("KSampler", "MS_DENOISE", denoise=1.0, seed=0)})
    b = resolve_bindings(wf, "upscale_image")
    assert (b.denoise, b.denoise_widget) == ("3", "denoise")
    out = apply_overrides(wf, b, params(denoise=0.35))
    assert out["3"]["inputs"]["denoise"] == 0.35
    assert wf["3"]["inputs"]["denoise"] == 1.0  # the source is not mutated


def test_denoise_binds_a_primitive_value():
    # A node has one title: a sampler titled MS_SEED takes its denoise from
    # a primitive titled MS_DENOISE.
    wf = graph(
        **{
            "3": node("PrimitiveFloat", "MS_DENOISE", value=0.3),
            "4": node("KSampler", "MS_SEED", seed=0, denoise=["3", 0]),
        }
    )
    b = resolve_bindings(wf, "upscale_image")
    assert (b.denoise, b.denoise_widget) == ("3", "value")
    out = apply_overrides(wf, b, params(denoise=0.45))
    assert out["3"]["inputs"]["value"] == 0.45
    assert out["4"]["inputs"]["denoise"] == ["3", 0]


def test_a_linked_denoise_is_not_a_widget():
    wf = graph(**{"3": node("KSampler", "MS_DENOISE", denoise=["9", 0])})
    with pytest.raises(BindingError, match="MS_DENOISE"):
        resolve_bindings(wf, "upscale_image")


def test_denoise_outside_0_to_1_is_refused():
    wf = graph(**{"3": node("PrimitiveFloat", "MS_DENOISE", value=0.3)})
    b = resolve_bindings(wf, "upscale_image")
    with pytest.raises(BindingError, match="outside"):
        apply_overrides(wf, b, params(denoise=1.5))


def test_denoise_without_a_binding_is_refused():
    wf = graph()
    with pytest.raises(BindingError, match="MS_DENOISE"):
        apply_overrides(wf, resolve_bindings(wf, "upscale_image"), params(denoise=0.3))


@pytest.mark.parametrize(
    "class_type, widget",
    [("UNETLoader", "unet_name"), ("CheckpointLoaderSimple", "ckpt_name")],
)
def test_model_binds_the_loader_file(class_type, widget):
    wf = graph(**{"3": node(class_type, "MS_MODEL", **{widget: "moss\\a.safetensors"})})
    b = resolve_bindings(wf, "upscale_image")
    assert (b.model, b.model_widget) == ("3", widget)
    assert baked_model(wf, b) == "moss\\a.safetensors"
    out = apply_overrides(wf, b, params(model="moss\\b.safetensors"))
    assert out["3"]["inputs"][widget] == "moss\\b.safetensors"


def test_none_keeps_the_baked_model_and_denoise():
    wf = graph(
        **{
            "3": node("UNETLoader", "MS_MODEL", unet_name="moss\\a.safetensors"),
            "4": node("PrimitiveFloat", "MS_DENOISE", value=0.3),
        }
    )
    out = apply_overrides(wf, resolve_bindings(wf, "upscale_image"), params())
    assert out["3"]["inputs"]["unet_name"] == "moss\\a.safetensors"
    assert out["4"]["inputs"]["value"] == 0.3


def test_a_model_node_without_a_file_widget_is_refused():
    wf = graph(**{"3": node("VAELoader", "MS_MODEL", vae_name="v.safetensors")})
    with pytest.raises(BindingError, match="MS_MODEL"):
        resolve_bindings(wf, "upscale_image")
    report = validate_workflow(wf, "upscale_image", None, None)
    assert any(f.code == "missing_widget" for f in report.findings)


def test_model_without_a_binding_is_refused():
    wf = graph()
    with pytest.raises(BindingError, match="MS_MODEL"):
        apply_overrides(wf, resolve_bindings(wf, "upscale_image"), params(model="m"))


def test_the_new_titles_are_known_to_validation():
    wf = graph(
        **{
            "3": node("UNETLoader", "MS_MODEL", unet_name="m"),
            "4": node("PrimitiveFloat", "MS_DENOISE", value=0.3),
        }
    )
    report = validate_workflow(wf, "upscale_image", None, None)
    assert not [f for f in report.findings if f.code == "unknown_title"]


def test_an_older_bindings_snapshot_still_loads():
    # A preset registered before these bindings has none of their keys.
    old = json.dumps(
        {
            "positive": None,
            "seed": "3",
            "seed_widget": "seed",
            "latent": None,
            "save": "2",
        }
    )
    b = Bindings.from_json(old)
    assert (b.denoise, b.denoise_widget, b.model, b.model_widget) == (None,) * 4


@pytest.mark.parametrize("name", [REFINE, REFINE_SEEDVR2])
def test_the_shipped_refine_workflows_take_everything_the_dialog_sends(name):
    wf = json.loads((ROOT / name).read_text(encoding="utf-8"))
    assert not validate_workflow(wf, "upscale_image", None, None).findings
    b = resolve_bindings(wf, "upscale_image")
    for field in (
        "positive",
        "negative",
        "seed",
        "scale",
        "denoise",
        "model",
        "lora_stack",
        "source_image",
    ):
        assert getattr(b, field) is not None, field
    assert wf[b.seed]["class_type"] == "UltimateSDUpscale"
    assert wf[b.seed]["inputs"]["denoise"] == [b.denoise, 0]
    assert baked_model(wf, b) == "moss\\moss_image_sft_bf16.safetensors"
    out = apply_overrides(
        wf,
        b,
        params(
            positive="p",
            negative="n",
            seed=7,
            scale=2.0,
            denoise=0.4,
            model="moss\\dirtyMoss_bf16.safetensors",
            loras=[{"name": "moss\\l.safetensors", "strength": 0.6}],
            source_image="in.png",
        ),
    )
    assert out[b.seed]["inputs"]["seed"] == 7
    assert out[b.denoise]["inputs"]["value"] == 0.4
    assert out[b.lora_stack]["inputs"]["lora_1"] == {
        "on": True,
        "lora": "moss\\l.safetensors",
        "strength": 0.6,
    }
    for node_id, n in out.items():
        for key, value in n["inputs"].items():
            if isinstance(value, list):
                assert value[0] in out, (node_id, key)
