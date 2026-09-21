"""Tests for ComfyUIVideoExtractor.

The H3 fixture is the API-format graph metascan's own MiniMax H3 i2va turbo
preset embeds in a rendered clip's ``prompt`` container tag. It uses the
split-sampler topology (RandomNoise / BasicScheduler / KSamplerSelect /
SamplerCustomAdvanced) and feeds its prompt from a plain string node, none
of which the extractor's original KSampler + CLIPTextEncode handlers saw.
"""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.scanner import Scanner
from metascan.extractors.comfyui_video import ComfyUIVideoExtractor

H3_PROMPT_TEXT = (
    "For the target video, at 0.00 seconds into the target video, "
    "<Picture 1> (from [Shot 1]) is fully referenced.\n\n"
    "integrated_multimodal_description: [Shot 1] The camera holds a static shot."
)

H3_I2VA_GRAPH: Dict[str, Any] = {
    "1": {
        "class_type": "LoadImage",
        "inputs": {"image": "metascan_849cd42a1b693d5d.jpg"},
        "_meta": {"title": "MS_FIRST_FRAME"},
    },
    "2": {
        "class_type": "JWStringMultiline",
        "inputs": {"text": H3_PROMPT_TEXT},
        "_meta": {"title": "MS_POSITIVE"},
    },
    "3": {
        "class_type": "CLIPLoader",
        "inputs": {
            "clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
            "type": "minimax",
            "device": "default",
        },
        "_meta": {"title": "Load CLIP"},
    },
    "6": {
        "class_type": "PrimitiveFloat",
        "inputs": {"value": 10.0},
        "_meta": {"title": "MS_DURATION"},
    },
    "7": {
        "class_type": "ComfyMathExpression",
        "inputs": {"expression": "round(a * 24)", "values.a": ["6", 0]},
        "_meta": {"title": "Math Expression (seconds to frames)"},
    },
    "8": {
        "class_type": "MiniMaxH3ImageToVideo",
        "inputs": {
            "prompt": ["2", 0],
            "width": 640,
            "height": 1184,
            "length": ["7", 1],
            "clip": ["3", 0],
            "first_frame": ["1", 0],
        },
        "_meta": {"title": "MS_RESOLUTION"},
    },
    "9": {
        "class_type": "UNETLoader",
        "inputs": {
            "unet_name": "minmax\\minimax_h3_fl2va_pruned_bf16.safetensors",
            "weight_dtype": "default",
        },
        "_meta": {"title": "Load Diffusion Model"},
    },
    "10": {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "model": ["9", 0],
            "lora_name": "minimax\\turbo_4step.safetensors",
            "strength_model": 1.0,
        },
        "_meta": {"title": "Turbo 4-step LoRA"},
    },
    "11": {
        "class_type": "Power Lora Loader (rgthree)",
        "inputs": {
            "PowerLoraLoaderHeaderWidget": {"type": "PowerLoraLoaderHeaderWidget"},
            "➕ Add Lora": "",
            "model": ["10", 0],
        },
        "_meta": {"title": "MS_LORA_STACK"},
    },
    "13": {
        "class_type": "BasicGuider",
        "inputs": {"model": ["11", 0], "conditioning": ["8", 0]},
        "_meta": {"title": "Basic Guider"},
    },
    "14": {
        "class_type": "BasicScheduler",
        "inputs": {
            "model": ["11", 0],
            "scheduler": "simple",
            "steps": 4,
            "denoise": 1.0,
        },
        "_meta": {"title": "BasicScheduler"},
    },
    "15": {
        "class_type": "KSamplerSelect",
        "inputs": {"sampler_name": "res_multistep"},
        "_meta": {"title": "KSamplerSelect"},
    },
    "16": {
        "class_type": "RandomNoise",
        "inputs": {"noise_seed": 1411732318},
        "_meta": {"title": "MS_SEED"},
    },
    "17": {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["16", 0],
            "guider": ["13", 0],
            "sampler": ["15", 0],
            "sigmas": ["14", 0],
            "latent_image": ["8", 1],
        },
        "_meta": {"title": "SamplerCustomAdvanced"},
    },
    "20": {
        "class_type": "CreateVideo",
        "inputs": {"fps": 24.0, "bit_depth": 8, "images": ["18", 0]},
        "_meta": {"title": "Create Video"},
    },
    "21": {
        "class_type": "SaveVideo",
        "inputs": {
            "filename_prefix": "video/metascan_i2va_turbo",
            "format": "auto",
            "codec": "auto",
            "video": ["20", 0],
        },
        "_meta": {"title": "MS_SAVE"},
    },
}

CONTAINER = {"frame_rate": 24.0, "video_length": 243, "duration": 10.125}


def _extract(graph: Dict[str, Any], container=None, monkeypatch=None):
    extractor = ComfyUIVideoExtractor()
    monkeypatch.setattr(
        extractor,
        "_get_video_metadata",
        lambda _path: {"prompt": json.dumps(graph)},
    )
    monkeypatch.setattr(
        extractor, "_probe_container", lambda _path: dict(container or {})
    )
    return extractor.extract(Path("clip.mp4"))


def test_h3_prompt_comes_from_the_ms_positive_string_node(monkeypatch):
    result = _extract(H3_I2VA_GRAPH, monkeypatch=monkeypatch)
    assert result["prompt"] == H3_PROMPT_TEXT


def test_h3_split_sampler_nodes_yield_seed_steps_and_scheduler(monkeypatch):
    result = _extract(H3_I2VA_GRAPH, monkeypatch=monkeypatch)
    assert result["seed"] == 1411732318
    assert result["steps"] == 4
    assert result["scheduler"] == "simple"


def test_core_res_multistep_sampler_is_not_relabelled_res4lyf(monkeypatch):
    result = _extract(H3_I2VA_GRAPH, monkeypatch=monkeypatch)
    assert result["sampler"] == "res_multistep"


def test_res4lyf_category_sampler_names_keep_their_label(monkeypatch):
    graph = copy.deepcopy(H3_I2VA_GRAPH)
    graph["15"]["inputs"]["sampler_name"] = "multistep/res_2m"
    result = _extract(graph, monkeypatch=monkeypatch)
    assert result["sampler"] == "RES4LYF"


def test_create_video_fps_is_the_frame_rate(monkeypatch):
    result = _extract(H3_I2VA_GRAPH, monkeypatch=monkeypatch)
    assert result["frame_rate"] == 24.0


def test_container_supplies_length_and_duration_the_graph_cannot(monkeypatch):
    # ``length`` is a link into a math node, so the frame count is not
    # readable from the graph; the container is the source of truth.
    result = _extract(H3_I2VA_GRAPH, container=CONTAINER, monkeypatch=monkeypatch)
    assert result["video_length"] == 243
    assert result["duration"] == 10.125


def test_graph_values_win_over_the_container(monkeypatch):
    result = _extract(
        H3_I2VA_GRAPH,
        container={"frame_rate": 30.0, "video_length": 243, "duration": 10.125},
        monkeypatch=monkeypatch,
    )
    assert result["frame_rate"] == 24.0


def test_prompt_is_resolved_through_a_link_without_ms_titles(monkeypatch):
    graph = copy.deepcopy(H3_I2VA_GRAPH)
    for node in graph.values():
        node["_meta"] = {"title": node["class_type"]}
    result = _extract(graph, monkeypatch=monkeypatch)
    assert result["prompt"] == H3_PROMPT_TEXT
    assert result["seed"] == 1411732318


def test_models_and_loras_still_extracted(monkeypatch):
    result = _extract(H3_I2VA_GRAPH, monkeypatch=monkeypatch)
    assert result["models"] == ["minmax\\minimax_h3_fl2va_pruned_bf16"]
    assert result["loras"] == [
        {"lora_name": "minimax\\turbo_4step", "lora_weight": 1.0}
    ]


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def test_scanner_maps_extractor_video_fields_onto_media(workspace, monkeypatch):
    scanner = Scanner(DatabaseManager(workspace / "db"))
    clip = workspace / "clip.mp4"
    clip.write_bytes(b"\x00")
    monkeypatch.setattr(scanner, "_get_video_info", lambda _p: (640, 1184, "MP4"))
    monkeypatch.setattr(
        scanner.extractor_manager,
        "extract_metadata",
        lambda _p: {"source": "ComfyUI", "raw_metadata": {}, **CONTAINER},
    )

    media = scanner._process_media_file(clip)

    assert media is not None
    assert media.frame_rate == 24.0
    assert media.duration == 10.125
    assert media.video_length == 243
