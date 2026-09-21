"""Tests for ComfyUIExtractor (still images).

The fixture is a reduced copy of a real Qwen-Image graph whose widgets are
wired rather than typed in: the positive CLIPTextEncode's ``text`` and the
KSampler's ``seed`` / ``steps`` / ``cfg`` are all links to other nodes. In
API format a link is a ``[node_id, output_index]`` list, and treating one
as a string crashed the extractor (``'list' object has no attribute
'lower'``), which cost the file ALL of its metadata.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict

from metascan.extractors.comfyui import ComfyUIExtractor

RESOLVED_PROMPT = "In a bedroom at night, a portrait lit by a warm lamp."
WILDCARD_TEMPLATE = "In a {bedroom|kitchen} at night, a portrait lit by __light__."
# Deliberately free of the words the positive/negative heuristic looks for
# ("negative", "bad", "ugly", "worst") -- as the real file's was.
NEGATIVE_PROMPT = "blurry, low quality, noisy, pixelated, grainy, distorted"

LINKED_GRAPH: Dict[str, Any] = {
    "2": {
        "class_type": "KSampler",
        "inputs": {
            "seed": ["79", 0],
            "steps": ["134", 0],
            "cfg": ["135", 0],
            "sampler_name": "euler",
            "scheduler": "beta",
            "denoise": ["292", 0],
            "positive": ["7", 0],
            "negative": ["8", 0],
        },
        "_meta": {"title": "Main KSampler"},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": ["377", 0], "clip": ["6", 1]},
        "_meta": {"title": "Positive (Prompt)"},
    },
    "8": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": NEGATIVE_PROMPT, "clip": ["6", 1]},
        "_meta": {"title": "Negative (Prompt)"},
    },
    "79": {
        "class_type": "PrimitiveInt",
        "inputs": {"value": 572},
        "_meta": {"title": "Seed"},
    },
    "134": {
        "class_type": "ImpactInt",
        "inputs": {"value": 50},
        "_meta": {"title": "Steps"},
    },
    "135": {
        "class_type": "ImpactFloat",
        "inputs": {"value": 3.0},
        "_meta": {"title": "CFG"},
    },
    "292": {
        "class_type": "easy float",
        "inputs": {"value": 1.0},
        "_meta": {"title": "Main Ksampler Denoise"},
    },
    "375": {
        "class_type": "DPRandomGenerator",
        "inputs": {"text": WILDCARD_TEMPLATE, "seed": 256, "autorefresh": "Yes"},
        "_meta": {"title": "Random Prompts"},
    },
    "377": {
        "class_type": "ShowText|pysssss",
        "inputs": {"text_0": RESOLVED_PROMPT, "text": ["375", 0]},
        "_meta": {"title": "Show Text"},
    },
}


def _extract(graph: Dict[str, Any], monkeypatch):
    extractor = ComfyUIExtractor()
    monkeypatch.setattr(
        extractor, "_get_exif_metadata", lambda _path: {"prompt": json.dumps(graph)}
    )
    return extractor.extract(Path("image.png"))


def test_linked_prompt_text_does_not_discard_the_files_metadata(monkeypatch):
    result = _extract(LINKED_GRAPH, monkeypatch)
    assert result is not None
    assert result["source"] == "ComfyUI"
    assert result["sampler"] == "euler"
    assert result["scheduler"] == "beta"


def test_linked_prompt_resolves_to_the_expanded_text_not_the_template(monkeypatch):
    # ShowText's text_0 is what the sampler actually saw; the node upstream
    # of it still holds the unexpanded wildcard template.
    result = _extract(LINKED_GRAPH, monkeypatch)
    assert result["prompt"] == RESOLVED_PROMPT


def test_negative_is_not_promoted_to_prompt_when_positive_is_linked(monkeypatch):
    result = _extract(LINKED_GRAPH, monkeypatch)
    assert result["negative_prompt"] == NEGATIVE_PROMPT
    assert result["prompt"] != NEGATIVE_PROMPT


def test_titles_decide_polarity_regardless_of_node_order(monkeypatch):
    graph = {k: copy.deepcopy(LINKED_GRAPH[k]) for k in ("8", "7", "377", "375")}
    result = _extract(graph, monkeypatch)
    assert result["prompt"] == RESOLVED_PROMPT
    assert result["negative_prompt"] == NEGATIVE_PROMPT


def test_linked_sampler_widgets_resolve_to_their_primitive_values(monkeypatch):
    result = _extract(LINKED_GRAPH, monkeypatch)
    assert result["seed"] == 572
    assert result["steps"] == 50
    assert result["cfg_scale"] == 3.0


def test_unresolvable_link_is_skipped_without_raising(monkeypatch):
    graph = copy.deepcopy(LINKED_GRAPH)
    graph["7"]["inputs"]["text"] = ["999", 0]  # dangling link
    result = _extract(graph, monkeypatch)
    assert result is not None
    assert "prompt" not in result
    assert result["negative_prompt"] == NEGATIVE_PROMPT


def test_link_cycle_terminates(monkeypatch):
    graph = copy.deepcopy(LINKED_GRAPH)
    graph["377"]["inputs"] = {"text": ["375", 0]}
    graph["375"]["inputs"] = {"text": ["377", 0]}
    result = _extract(graph, monkeypatch)
    assert result is not None
    assert "prompt" not in result


def test_untitled_literal_prompts_keep_the_content_heuristic(monkeypatch):
    graph = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a red fox"}},
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "ugly, worst quality"},
        },
    }
    result = _extract(graph, monkeypatch)
    assert result["prompt"] == "a red fox"
    assert result["negative_prompt"] == "ugly, worst quality"


# ---- models and loras: trace the sampler's model wire ------------------
#
# Reduced from the same real graph. Two UNETLoaders sit behind a mode
# switch, so listing every loader would report a model that never ran; the
# one that did is whichever the KSampler's `model` input actually reaches.


def _model_graph() -> Dict[str, Any]:
    return {
        "2": {
            "class_type": "KSampler",
            "inputs": {"sampler_name": "euler", "scheduler": "beta", "model": ["6", 0]},
            "_meta": {"title": "Main KSampler"},
        },
        "6": {
            "class_type": "Power Lora Loader (rgthree)",
            "inputs": {
                "PowerLoraLoaderHeaderWidget": {"type": "PowerLoraLoaderHeaderWidget"},
                "lora_1": {
                    "on": True,
                    "lora": "qwen\\first.safetensors",
                    "strength": 0.8,
                },
                "lora_2": {
                    "on": False,
                    "lora": "qwen\\off.safetensors",
                    "strength": 1.0,
                },
                "lora_3": {
                    "on": True,
                    "lora": "qwen\\second.safetensors",
                    "strength": 0.55,
                },
                "➕ Add Lora": "",
                "model": ["236", 0],
                "clip": ["13", 0],
            },
        },
        "236": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {"shift": 13.0, "model": ["233", 0]},
        },
        "233": {
            "class_type": "ImpactSwitch",
            "inputs": {
                "select": ["138", 0],
                "sel_mode": False,
                "input1": ["12", 0],
                "input2": ["12", 0],
                "input3": ["174", 0],
            },
        },
        "138": {"class_type": "ImpactInt", "inputs": {"value": 1}},
        "12": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": "qwen\\qwen_image.safetensors",
                "weight_dtype": "default",
            },
        },
        "174": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": "qwen\\qwen_image_edit.safetensors",
                "weight_dtype": "default",
            },
        },
    }


def test_unet_loader_model_is_read(monkeypatch):
    result = _extract(_model_graph(), monkeypatch)
    assert result["models"] == ["qwen\\qwen_image.safetensors"]


def test_only_the_switch_branch_that_is_selected_counts(monkeypatch):
    graph = _model_graph()
    graph["138"]["inputs"]["value"] = 3
    result = _extract(graph, monkeypatch)
    assert result["models"] == ["qwen\\qwen_image_edit.safetensors"]


def test_underscore_style_switch_inputs_are_understood(monkeypatch):
    graph = _model_graph()
    graph["233"] = {
        "class_type": "Big Model Switch [Dream]",
        "inputs": {"select": 2, "input_1": ["12", 0], "input_2": ["174", 0]},
    }
    result = _extract(graph, monkeypatch)
    assert result["models"] == ["qwen\\qwen_image_edit.safetensors"]


def test_an_unresolvable_switch_reports_every_branch(monkeypatch):
    graph = _model_graph()
    graph["233"]["inputs"]["select"] = ["999", 0]  # dangling
    result = _extract(graph, monkeypatch)
    assert result["models"] == [
        "qwen\\qwen_image.safetensors",
        "qwen\\qwen_image_edit.safetensors",
    ]


def test_power_lora_loader_reports_enabled_loras_in_order(monkeypatch):
    result = _extract(_model_graph(), monkeypatch)
    assert result["loras"] == [
        {"lora_name": "qwen\\first", "lora_weight": 0.8},
        {"lora_name": "qwen\\second", "lora_weight": 0.55},
    ]


def test_loras_come_out_in_application_order_across_nodes(monkeypatch):
    graph = _model_graph()
    # checkpoint -> LoraLoader(base) -> Power Lora Loader -> KSampler
    graph["236"] = {
        "class_type": "LoraLoader",
        "inputs": {
            "lora_name": "base.safetensors",
            "strength_model": 1.0,
            "strength_clip": 1.0,
            "model": ["233", 0],
        },
    }
    result = _extract(graph, monkeypatch)
    assert [entry["lora_name"] for entry in result["loras"]] == [
        "base",
        "qwen\\first",
        "qwen\\second",
    ]


def test_a_lora_on_an_unselected_branch_is_not_reported(monkeypatch):
    graph = _model_graph()
    graph["174"] = {
        "class_type": "LoraLoader",
        "inputs": {"lora_name": "edit_only.safetensors", "strength_model": 1.0},
    }
    result = _extract(graph, monkeypatch)
    assert "edit_only" not in [entry["lora_name"] for entry in result["loras"]]


def test_several_samplers_sharing_a_model_report_it_once(monkeypatch):
    graph = _model_graph()
    graph["29"] = copy.deepcopy(graph["2"])
    graph["41"] = copy.deepcopy(graph["2"])
    result = _extract(graph, monkeypatch)
    assert result["models"] == ["qwen\\qwen_image.safetensors"]
    assert len(result["loras"]) == 2


def test_a_model_loop_terminates(monkeypatch):
    graph = _model_graph()
    graph["233"]["inputs"] = {"select": 1, "input1": ["236", 0]}
    # The wire never reaches a loader, so this lands in the list-everything
    # fallback; the point is that the walk ends.
    result = _extract(graph, monkeypatch)
    assert result is not None
    assert len(result["models"]) == 2


def test_classic_checkpoint_graph_is_unchanged(monkeypatch):
    graph = {
        "3": {
            "class_type": "KSampler",
            "inputs": {"sampler_name": "euler", "seed": 7, "model": ["10", 0]},
        },
        "10": {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": "detail.safetensors",
                "strength_model": 0.6,
                "model": ["4", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sdxl_base.safetensors"},
        },
    }
    result = _extract(graph, monkeypatch)
    assert result["models"] == ["sdxl_base.safetensors"]
    assert result["loras"] == [{"lora_name": "detail", "lora_weight": 0.6}]


def test_loaders_are_still_found_when_no_sampler_wires_to_them(monkeypatch):
    # No traceable KSampler.model (e.g. a custom sampler this extractor
    # does not know): fall back to listing what the graph contains.
    graph = {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sdxl_base.safetensors"},
        },
        "12": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux.safetensors"}},
        "10": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "detail.safetensors", "strength_model": 0.6},
        },
        "6": {
            "class_type": "Power Lora Loader (rgthree)",
            "inputs": {
                "lora_1": {"on": True, "lora": "p.safetensors", "strength": 0.5}
            },
        },
    }
    result = _extract(graph, monkeypatch)
    assert sorted(result["models"]) == ["flux.safetensors", "sdxl_base.safetensors"]
    assert sorted(entry["lora_name"] for entry in result["loras"]) == ["detail", "p"]
