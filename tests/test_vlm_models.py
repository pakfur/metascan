"""Sanity tests for the Qwen3-VL model registry."""

import pytest

from metascan.core.vlm_models import REGISTRY, VlmModelSpec, get_spec, resolve_repo


def test_all_four_sizes_present():
    expected = {"qwen3vl-2b", "qwen3vl-4b", "qwen3vl-8b", "qwen3vl-30b-a3b"}
    assert set(REGISTRY.keys()) == expected


def test_specs_are_well_formed():
    for mid, spec in REGISTRY.items():
        assert isinstance(spec, VlmModelSpec)
        assert spec.model_id == mid
        assert spec.hf_repo
        assert spec.gguf_filename.endswith(".gguf")
        assert spec.mmproj_filename.endswith(".gguf")
        assert spec.quant in {"Q4_K_M", "Q5_K_M"}
        assert spec.min_vram_gb > 0
        assert spec.parallel_slots in (2, 4)


def test_min_vram_is_monotonic_by_size():
    sizes = ["qwen3vl-2b", "qwen3vl-4b", "qwen3vl-8b", "qwen3vl-30b-a3b"]
    vrams = [REGISTRY[s].min_vram_gb for s in sizes]
    assert vrams == sorted(vrams)


def test_get_spec_returns_spec():
    spec = get_spec("qwen3vl-4b")
    assert spec.model_id == "qwen3vl-4b"


def test_get_spec_raises_on_unknown():
    with pytest.raises(KeyError):
        get_spec("qwen3vl-bogus")


def test_resolve_repo_returns_default_when_no_override():
    assert resolve_repo("qwen3vl-2b") == REGISTRY["qwen3vl-2b"].hf_repo


def test_resolve_repo_returns_default_when_override_misses():
    assert (
        resolve_repo("qwen3vl-2b", {"qwen3vl-99b": "x"})
        == REGISTRY["qwen3vl-2b"].hf_repo
    )


def test_resolve_repo_applies_override_when_key_matches():
    assert (
        resolve_repo("qwen3vl-2b", {"qwen3vl-2b": "user/custom-repo"})
        == "user/custom-repo"
    )


def test_resolve_repo_raises_on_unknown_id_without_override():
    with pytest.raises(KeyError):
        resolve_repo("qwen3vl-bogus")
