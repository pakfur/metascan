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
