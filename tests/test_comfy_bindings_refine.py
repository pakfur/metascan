"""MS_DENOISE and MS_MODEL: the refine-upscale bindings (Moss + Ultimate SD
Upscale), and the shipped refine workflows."""

from __future__ import annotations

import json
import math
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


MINIMAX = "upscale_minimax_ultimate.json"


def test_the_shipped_minimax_refine_takes_everything_the_dialog_sends():
    wf = json.loads((ROOT / MINIMAX).read_text(encoding="utf-8"))
    assert not validate_workflow(wf, "upscale_video", None, None).findings
    b = resolve_bindings(wf, "upscale_video")
    for field in (
        "positive",
        "seed",
        "scale",
        "denoise",
        "model",
        "lora_stack",
        "source_video",
    ):
        assert getattr(b, field) is not None, field
    # No negative: MiniMax H3 is sampled with a positive-only guider.
    assert b.negative is None
    assert wf[b.source_video]["class_type"] == "VHS_LoadVideoFFmpeg"
    assert b.source_video_widget == "video"
    # The scale drives the computed upscale size (through the MS_MAX_SIZE
    # cap), the denoise the scheduler.
    size = next(n for n in wf.values() if n["class_type"] == "H3UpscaleSizeLD")
    capped = wf[size["inputs"]["multiple"][0]]
    assert capped["inputs"]["values.a"] == [b.scale, 0]
    sched = next(n for n in wf.values() if n["class_type"] == "BasicScheduler")
    assert sched["inputs"]["denoise"] == [b.denoise, 0]
    assert baked_model(wf, b) == "minmax\\minimax_h3_fl2va_int8_convrot.safetensors"
    out = apply_overrides(
        wf,
        b,
        params(
            positive="a woman turns to the camera",
            seed=7,
            scale=2.0,
            denoise=0.25,
            model="minmax\\minimax_h3_fl2va_pruned_bf16.safetensors",
            loras=[{"name": "minimax\\turbo.safetensors", "strength": 1.0}],
            source_video="in.mp4",
        ),
    )
    assert out[b.seed]["inputs"]["noise_seed"] == 7
    assert out[b.denoise]["inputs"]["value"] == 0.25
    assert out[b.scale]["inputs"]["value"] == 2.0
    assert out[b.source_video]["inputs"]["video"] == "in.mp4"
    assert out[b.positive]["inputs"]["text"] == "a woman turns to the camera"
    assert out[b.lora_stack]["inputs"]["lora_1"]["lora"] == "minimax\\turbo.safetensors"
    for node_id, n in out.items():
        for key, value in n["inputs"].items():
            if isinstance(value, list):
                assert value[0] in out, (node_id, key)


# ---- MS_STEPS and MS_MAX_SIZE: a video refine's cost controls ---------------


def test_max_size_binds_a_primitive_value():
    wf = graph(**{"3": node("PrimitiveInt", "MS_MAX_SIZE", value=2560)})
    b = resolve_bindings(wf, "upscale_image")
    assert b.max_size == "3"
    out = apply_overrides(wf, b, params(max_size=1920))
    assert out["3"]["inputs"]["value"] == 1920
    # 0 is "no limit", not a refusal
    assert apply_overrides(wf, b, params(max_size=0))["3"]["inputs"]["value"] == 0
    with pytest.raises(BindingError, match="negative"):
        apply_overrides(wf, b, params(max_size=-1))


def test_max_size_and_steps_without_a_binding_are_refused():
    wf = graph()
    b = resolve_bindings(wf, "upscale_image")
    with pytest.raises(BindingError, match="MS_MAX_SIZE"):
        apply_overrides(wf, b, params(max_size=1920))
    with pytest.raises(BindingError, match="MS_STEPS"):
        apply_overrides(wf, b, params(steps=4))


def test_max_size_is_known_to_validation():
    wf = graph(**{"3": node("PrimitiveInt", "MS_MAX_SIZE", value=2560)})
    report = validate_workflow(wf, "upscale_image", None, None)
    assert not [f for f in report.findings if f.code == "unknown_title"]


def test_an_older_snapshot_has_no_max_size():
    old = {"positive": None, "seed": None, "seed_widget": None, "latent": None}
    b = Bindings.from_json(json.dumps({**old, "save": "2"}))
    assert (b.max_size, b.steps) == (None, None)


def _eval(expression: str, **values: float) -> float:
    """ComfyMathExpression's own functions, over plain Python."""
    names = {"min": min, "max": max, "floor": math.floor, "sqrt": math.sqrt}
    return float(eval(expression, {"__builtins__": {}}, {**names, **values}))


def _math_inputs(wf: Dict[str, Any], node_id: str) -> Dict[str, Any]:
    inputs = wf[node_id]["inputs"]
    return {k[7:]: v for k, v in inputs.items() if k.startswith("values.")}


def test_the_minimax_refine_caps_its_scale_at_the_max_size():
    wf = json.loads((ROOT / MINIMAX).read_text(encoding="utf-8"))
    b = resolve_bindings(wf, "upscale_video")
    assert b.steps is not None and b.max_size is not None
    assert wf[b.steps]["class_type"] == "BasicScheduler"
    out = apply_overrides(wf, b, params(steps=4, max_size=1920))
    assert out[b.steps]["inputs"]["steps"] == 4
    assert out[b.max_size]["inputs"]["value"] == 1920
    size = next(n for n in wf.values() if n["class_type"] == "H3UpscaleSizeLD")
    capped_id = size["inputs"]["multiple"][0]
    assert wf[capped_id]["class_type"] == "ComfyMathExpression"
    links = _math_inputs(wf, capped_id)
    assert links["a"] == [b.scale, 0] and links["d"] == [b.max_size, 0]
    expr = wf[capped_id]["inputs"]["expression"]
    # a: the scale; b, c: the source width and height; d: the cap.
    assert _eval(expr, a=2, b=1184, c=672, d=0) == 2  # no limit
    assert _eval(expr, a=2, b=1184, c=672, d=2560) == 2  # fits already
    assert _eval(expr, a=2, b=2368, c=1344, d=2560) == pytest.approx(2560 / 2368)
    assert _eval(expr, a=2, b=1344, c=2368, d=2560) == pytest.approx(2560 / 2368)
    assert _eval(expr, a=2, b=2368, c=1344, d=1920) == 1  # never below the source
    # Small tiles: attention grows with the square of the tokens per call, so
    # more, smaller tiles do the same frame in less time.
    tiles = next(n for n in wf.values() if n["class_type"] == "MMH3SpatialSplitParams")
    assert tiles["inputs"]["token_budget"] <= 30000


def test_the_seedvr2_video_upscale_caps_its_long_edge():
    raw = (ROOT / "upscale_video_seedvr2_7b_api.json").read_text(encoding="utf-8")
    wf = json.loads(raw)
    assert not validate_workflow(wf, "upscale_video", None, None).findings
    b = resolve_bindings(wf, "upscale_video")
    assert b.max_size is not None
    # SeedVR2 is a one-step model: there is no step count to choose.
    assert b.steps is None
    upscaler = next(n for n in wf.values() if n["class_type"] == "SeedVR2VideoUpscaler")
    target_id = upscaler["inputs"]["resolution"][0]
    expr = wf[target_id]["inputs"]["expression"]
    assert _math_inputs(wf, target_id)["d"] == [b.max_size, 0]

    def short_edge(scale: float, w: int, h: int, cap: int) -> float:
        return _eval(expr, a=scale, b=w, c=h, d=cap)

    assert short_edge(2, 640, 360, 0) == 720
    assert short_edge(2, 640, 360, 2560) == 720
    assert short_edge(2, 1920, 1080, 2560) == 1440  # long edge 2560
    assert short_edge(2, 1920, 1080, 1280) == 1080  # never below the source
    assert short_edge(4, 1920, 1080, 0) == 1442  # the 3.7 MP cap still holds
