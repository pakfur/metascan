"""What the Upscale dialog pre-fills: ``upscale_defaults``."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from metascan.core.upscale_defaults import (
    generation_defaults,
    lora_file_name,
    model_family,
    same_family,
)


def media(**kw: Any) -> SimpleNamespace:
    values = dict(
        prompt="a lake at dusk",
        negative_prompt="blurry",
        seed=123,
        model=["moss\\dirtyMoss_bf16.safetensors"],
        loras=[SimpleNamespace(lora_name="moss\\film", lora_weight=0.8)],
    )
    values.update(kw)
    return SimpleNamespace(**values)


def test_metadata_fills_every_field_and_names_the_lora_file():
    out = generation_defaults(media(), None, blind=False)
    assert out == {
        "prompt": "a lake at dusk",
        "negative": "blurry",
        "seed": 123,
        "model": "moss\\dirtyMoss_bf16.safetensors",
        "loras": [{"name": "moss\\film.safetensors", "strength": 0.8}],
        "blind": False,
    }


def test_the_t2i_stack_wins_over_parsed_metadata():
    stack = [{"name": "moss\\film.ckpt", "strength": 0.5}]
    assert generation_defaults(media(), stack, blind=False)["loras"] == stack
    # A T2I image made with no LoRAs has an empty stack, not the metadata's.
    assert generation_defaults(media(), [], blind=False)["loras"] == []


def test_a_blind_scorecard_image_gives_no_prompt():
    out = generation_defaults(media(), None, blind=True)
    assert out["prompt"] is None and out["negative"] is None
    assert out["seed"] == 123 and out["blind"] is True


def test_a_seed_the_dialog_cannot_take_is_left_out():
    assert generation_defaults(media(seed=2**40), None, False)["seed"] is None
    assert generation_defaults(media(seed=-1), None, False)["seed"] is None


def test_no_media_record_gives_nothing():
    out = generation_defaults(None, None, blind=False)
    assert out["prompt"] is None and out["loras"] == [] and out["model"] is None


def test_lora_file_name_keeps_a_known_extension():
    assert lora_file_name("a\\b") == "a\\b.safetensors"
    assert lora_file_name("a\\b.SafeTensors") == "a\\b.SafeTensors"
    assert lora_file_name("b.gguf") == "b.gguf"


def test_model_families():
    assert model_family("moss\\dirtyMoss_bf16.safetensors") == "moss"
    assert model_family("Moss/sft.safetensors") == "moss"
    assert model_family("bare.safetensors") is None
    assert same_family("moss\\a.safetensors", "moss/b.safetensors")
    assert same_family("bare.safetensors", "bare.safetensors")
    assert not same_family("krea2\\a.safetensors", "moss\\b.safetensors")
    assert not same_family("a.safetensors", "b.safetensors")
    assert not same_family(None, "moss\\b.safetensors")
