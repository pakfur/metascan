"""Pure-unit tests for the MS_LORA_STACK (stackable lora loader) contract.

Same style as test_comfy_bindings.py: dict literals shaped like ComfyUI's
API-format export, no network/DB. The stack node models rgthree's Power
Lora Loader: dynamic ``lora_N`` inputs whose values are dicts like
``{"on": true, "lora": "<file>", "strength": 1.0}``, alongside pass-through
link inputs (model/clip) and UI-only widget entries.
"""

from __future__ import annotations

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


def _power_lora_inputs(**extra: object) -> dict:
    inputs: dict = {
        "model": ["4", 0],
        "clip": ["4", 1],
        "PowerLoraLoaderHeaderWidget": {"type": "PowerLoraLoaderHeaderWidget"},
        "➕ Add Lora": "",
    }
    inputs.update(extra)
    return inputs


def t2i_with_stack(**stack_extra: object) -> dict:
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
        "6": _node("CLIPTextEncode", "MS_POSITIVE", {"text": "", "clip": ["10", 1]}),
        "9": _node(
            "SaveImage", "MS_SAVE", {"images": ["8", 0], "filename_prefix": "ms"}
        ),
        "10": _node(
            "Power Lora Loader (rgthree)",
            "MS_LORA_STACK",
            _power_lora_inputs(**stack_extra),
        ),
    }


def _params(**overrides: object) -> GenerationParams:
    base: dict = dict(positive="p", seed=1, width=512, height=512, batch_size=1)
    base.update(overrides)
    return GenerationParams(**base)


# ---- resolution -------------------------------------------------------------


def test_resolves_lora_stack_title():
    b = resolve_bindings(t2i_with_stack(), "t2i")
    assert b.lora_stack == "10"


def test_lora_stack_absent_is_none():
    wf = t2i_with_stack()
    del wf["10"]
    wf["6"]["inputs"]["clip"] = ["4", 1]
    b = resolve_bindings(wf, "t2i")
    assert b.lora_stack is None


def test_bindings_json_without_lora_stack_deserializes():
    """Presets registered before MS_LORA_STACK existed round-trip fine."""
    wf = t2i_with_stack()
    del wf["10"]
    wf["6"]["inputs"]["clip"] = ["4", 1]
    b = resolve_bindings(wf, "t2i")
    raw = b.to_json().replace('"lora_stack": null, ', "")
    assert "lora_stack" not in raw
    assert Bindings.from_json(raw).lora_stack is None


def test_generation_params_json_without_loras_deserializes():
    raw = _params().to_json().replace('"loras": [], ', "")
    assert "loras" not in raw
    assert GenerationParams.from_json(raw).loras == []


# ---- injection --------------------------------------------------------------


def test_loras_without_stack_node_raises():
    wf = t2i_with_stack()
    del wf["10"]
    wf["6"]["inputs"]["clip"] = ["4", 1]
    b = resolve_bindings(wf, "t2i")
    with pytest.raises(BindingError, match="MS_LORA_STACK"):
        apply_overrides(
            wf, b, _params(loras=[{"name": "style.safetensors", "strength": 1.0}])
        )


def test_loras_written_as_lora_n_entries():
    wf = t2i_with_stack()
    b = resolve_bindings(wf, "t2i")
    out = apply_overrides(
        wf,
        b,
        _params(
            loras=[
                {"name": "style.safetensors", "strength": 0.7},
                {"name": "detail.safetensors", "strength": 1.0},
            ]
        ),
    )
    inputs = out["10"]["inputs"]
    assert inputs["lora_1"] == {
        "on": True,
        "lora": "style.safetensors",
        "strength": 0.7,
    }
    assert inputs["lora_2"] == {
        "on": True,
        "lora": "detail.safetensors",
        "strength": 1.0,
    }


def test_baked_in_stack_entries_are_replaced():
    """Metascan owns MS_LORA_STACK: pre-existing entries are cleared, even
    when fewer loras are supplied than the workflow had baked in."""
    wf = t2i_with_stack(
        lora_1={"on": True, "lora": "old-a.safetensors", "strength": 1.0},
        lora_2={"on": False, "lora": "old-b.safetensors", "strength": 0.5},
        lora_7={"on": True, "lora": "old-c.safetensors", "strength": 0.9},
    )
    b = resolve_bindings(wf, "t2i")
    out = apply_overrides(
        wf, b, _params(loras=[{"name": "new.safetensors", "strength": 1.0}])
    )
    inputs = out["10"]["inputs"]
    assert inputs["lora_1"] == {"on": True, "lora": "new.safetensors", "strength": 1.0}
    assert "lora_2" not in inputs
    assert "lora_7" not in inputs


def test_empty_loras_clears_baked_in_entries():
    wf = t2i_with_stack(
        lora_1={"on": True, "lora": "old.safetensors", "strength": 1.0},
    )
    b = resolve_bindings(wf, "t2i")
    out = apply_overrides(wf, b, _params(loras=[]))
    assert "lora_1" not in out["10"]["inputs"]


def test_non_lora_stack_inputs_are_preserved():
    wf = t2i_with_stack(
        lora_1={"on": True, "lora": "old.safetensors", "strength": 1.0},
    )
    b = resolve_bindings(wf, "t2i")
    out = apply_overrides(
        wf, b, _params(loras=[{"name": "new.safetensors", "strength": 1.0}])
    )
    inputs = out["10"]["inputs"]
    assert inputs["model"] == ["4", 0]
    assert inputs["clip"] == ["4", 1]
    assert inputs["PowerLoraLoaderHeaderWidget"] == {
        "type": "PowerLoraLoaderHeaderWidget"
    }
    assert inputs["➕ Add Lora"] == ""


def test_stack_coexists_with_single_ms_lora():
    wf = t2i_with_stack()
    wf["11"] = _node(
        "LoraLoader",
        "MS_LORA",
        {
            "lora_name": "z.safetensors",
            "strength_model": 1.0,
            "strength_clip": 1.0,
            "model": ["10", 0],
            "clip": ["10", 1],
        },
    )
    b = resolve_bindings(wf, "t2i")
    assert b.lora == "11"
    assert b.lora_stack == "10"
    out = apply_overrides(
        wf,
        b,
        _params(
            lora_name="char.safetensors",
            lora_strength=0.8,
            loras=[{"name": "style.safetensors", "strength": 0.6}],
        ),
    )
    assert out["11"]["inputs"]["lora_name"] == "char.safetensors"
    assert out["10"]["inputs"]["lora_1"] == {
        "on": True,
        "lora": "style.safetensors",
        "strength": 0.6,
    }
