"""Pure-unit tests for the ref2v binding kind.

Mirrors the fixture style of tests/test_comfy_bindings.py: minimal
API-format workflow dict literals with ``_meta.title`` + ``inputs``.
"""

from __future__ import annotations

import json

import pytest

from metascan.core.comfy_bindings import (
    BindingError,
    Bindings,
    GenerationParams,
    apply_overrides,
    resolve_bindings,
)


def _node(class_type: str, title: str, inputs: dict) -> dict:
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}


def minimal_ref2v() -> dict:
    """A valid ref2v graph with only the required MS_* titles present."""
    return {
        "3": _node("KSampler", "MS_SEED", {"seed": 0, "steps": 20, "cfg": 7.0}),
        "6": _node("CLIPTextEncode", "MS_POSITIVE", {"text": "", "clip": ["4", 1]}),
        "9": _node(
            "SaveVideo", "MS_SAVE", {"images": ["8", 0], "filename_prefix": "ms"}
        ),
    }


def full_ref2v() -> dict:
    """A ref2v graph with every optional title also present."""
    wf = minimal_ref2v()
    wf["11"] = _node("LoadImage", "MS_REF_IMAGE", {"image": "ref1.png"})
    wf["12"] = _node("LoadImage", "MS_REF_IMAGE_2", {"image": "ref2.png"})
    wf["13"] = _node("LoadImage", "MS_REF_IMAGE_3", {"image": "ref3.png"})
    wf["14"] = _node("LoadImage", "MS_FIRST_FRAME", {"image": "first.png"})
    wf["15"] = _node("LoadImage", "MS_LAST_FRAME", {"image": "last.png"})
    wf["16"] = _node("LoadAudio", "MS_AUDIO", {"audio": "a1.mp3"})
    wf["17"] = _node("LoadAudio", "MS_AUDIO_2", {"audio": "a2.mp3"})
    wf["18"] = _node("PrimitiveFloat", "MS_DURATION", {"value": 5.0})
    return wf


def base_ref2v_params(**kw) -> GenerationParams:
    defaults = dict(
        positive="a cat walking", seed=42, width=1024, height=576, batch_size=1
    )
    defaults.update(kw)
    return GenerationParams(**defaults)


def test_ref2v_requires_only_positive_seed_save():
    b = resolve_bindings(minimal_ref2v(), "ref2v")
    assert b.positive == "6"
    assert b.seed == "3"
    assert b.save == "9"
    assert b.latent is None
    assert b.ref_image is None
    assert b.ref_image_2 is None
    assert b.ref_image_3 is None
    assert b.first_frame is None
    assert b.last_frame is None
    assert b.audio is None
    assert b.audio_2 is None
    assert b.duration is None


def test_ref2v_resolves_all_optional_titles():
    b = resolve_bindings(full_ref2v(), "ref2v")
    assert b.ref_image == "11"
    assert b.ref_image_2 == "12"
    assert b.ref_image_3 == "13"
    assert b.first_frame == "14"
    assert b.last_frame == "15"
    assert b.audio == "16"
    assert b.audio_2 == "17"
    assert b.duration == "18"


def test_ref2v_missing_required_title_raises():
    wf = minimal_ref2v()
    del wf["9"]  # MS_SAVE
    with pytest.raises(BindingError) as exc:
        resolve_bindings(wf, "ref2v")
    assert "MS_SAVE" in str(exc.value)


def test_t2i_still_requires_latent():
    wf = minimal_ref2v()
    # No MS_LATENT present -> t2i must still reject it.
    with pytest.raises(BindingError) as exc:
        resolve_bindings(wf, "t2i")
    assert "MS_LATENT" in str(exc.value)


def test_bindings_from_json_pre_v4_blob():
    # Pre-V4 stored JSON: only the original fields, latent always present.
    raw = json.dumps(
        {
            "positive": "6",
            "seed": "3",
            "seed_widget": "seed",
            "latent": "5",
            "save": "9",
            "negative": None,
            "lora": None,
            "ref_image": None,
        }
    )
    b = Bindings.from_json(raw)
    assert b.latent == "5"
    assert b.ref_image_2 is None
    assert b.ref_image_3 is None
    assert b.first_frame is None
    assert b.last_frame is None
    assert b.audio is None
    assert b.audio_2 is None
    assert b.duration is None


def test_apply_overrides_ref_images_in_order():
    wf = full_ref2v()
    b = resolve_bindings(wf, "ref2v")
    out = apply_overrides(
        wf, b, base_ref2v_params(ref_images=["r1.png", "r2.png", "r3.png"])
    )
    assert out["11"]["inputs"]["image"] == "r1.png"
    assert out["12"]["inputs"]["image"] == "r2.png"
    assert out["13"]["inputs"]["image"] == "r3.png"


def test_apply_overrides_too_many_refs_raises():
    wf = minimal_ref2v()
    wf["11"] = _node("LoadImage", "MS_REF_IMAGE", {"image": "ref1.png"})
    b = resolve_bindings(wf, "ref2v")
    with pytest.raises(BindingError) as exc:
        apply_overrides(wf, b, base_ref2v_params(ref_images=["r1.png", "r2.png"]))
    assert "2 reference images supplied" in str(exc.value)
    assert "1 MS_REF_IMAGE slot" in str(exc.value)


def test_apply_overrides_ref_images_and_singular_ref_image_conflict():
    wf = full_ref2v()
    b = resolve_bindings(wf, "ref2v")
    with pytest.raises(BindingError):
        apply_overrides(
            wf,
            b,
            base_ref2v_params(ref_image="single.png", ref_images=["r1.png"]),
        )


def test_apply_overrides_first_frame_and_audio_and_duration():
    wf = full_ref2v()
    b = resolve_bindings(wf, "ref2v")
    out = apply_overrides(
        wf,
        b,
        base_ref2v_params(
            first_frame="first_ov.png",
            last_frame="last_ov.png",
            audio_refs=["a1_ov.mp3", "a2_ov.mp3"],
            duration_s=8.5,
        ),
    )
    assert out["14"]["inputs"]["image"] == "first_ov.png"
    assert out["15"]["inputs"]["image"] == "last_ov.png"
    assert out["16"]["inputs"]["audio"] == "a1_ov.mp3"
    assert out["17"]["inputs"]["audio"] == "a2_ov.mp3"
    assert out["18"]["inputs"]["value"] == 8.5


@pytest.mark.parametrize(
    "kwargs",
    [
        {"first_frame": "first_ov.png"},
        {"last_frame": "last_ov.png"},
        {"audio_refs": ["a1_ov.mp3"]},
        {"duration_s": 3.0},
    ],
)
def test_apply_overrides_param_without_binding_raises_each(kwargs):
    wf = minimal_ref2v()
    b = resolve_bindings(wf, "ref2v")
    with pytest.raises(BindingError):
        apply_overrides(wf, b, base_ref2v_params(**kwargs))


def test_apply_overrides_no_latent_skips_dimension_writes():
    wf = minimal_ref2v()
    b = resolve_bindings(wf, "ref2v")
    out = apply_overrides(wf, b, base_ref2v_params())
    assert out["6"]["inputs"]["text"] == "a cat walking"
    assert out["3"]["inputs"]["seed"] == 42
    # No MS_LATENT node exists at all -- nothing to assert dimensions on,
    # and apply_overrides must not have raised trying to write to one.


def test_apply_overrides_too_many_audio_refs_raises():
    wf = minimal_ref2v()
    wf["16"] = _node("LoadAudio", "MS_AUDIO", {"audio": "a1.mp3"})
    b = resolve_bindings(wf, "ref2v")
    with pytest.raises(BindingError) as exc:
        apply_overrides(wf, b, base_ref2v_params(audio_refs=["a1_ov.mp3", "a2_ov.mp3"]))
    assert "2 " in str(exc.value)
    assert "1 MS_AUDIO slot" in str(exc.value)
