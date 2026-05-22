"""Argument parsing and download-target resolution for setup_models.py.

Real downloads are not exercised here — they need network access and HF
gated repos. We just verify the resolver returns the expected target
shapes so a future user-installed setup_models will compose URLs correctly.
"""

import pytest

from setup_models import resolve_qwen3vl_targets


def test_resolve_targets_for_4b():
    targets = resolve_qwen3vl_targets("qwen3vl-4b")
    # Expect 3 targets: GGUF, mmproj, llama-server binary.
    assert len(targets) == 3
    paths = [str(t.dest) for t in targets]
    assert any(".gguf" in p for p in paths)
    assert any("mmproj" in p.lower() for p in paths)
    # The third target is the binary — its path is platform-specific but
    # always lives under <data_dir>/bin/.
    assert any("/bin/" in p or "\\bin\\" in p for p in paths)


def test_resolve_targets_unknown_size_raises():
    with pytest.raises(KeyError):
        resolve_qwen3vl_targets("qwen3vl-bogus")


def test_resolve_targets_2b_uses_huihui_repo():
    targets = resolve_qwen3vl_targets("qwen3vl-2b")
    # First two targets should reference the abliterated HF repo.
    hf_targets = [t for t in targets if t.repo is not None]
    assert len(hf_targets) == 2
    for t in hf_targets:
        assert "Qwen3-VL-2B" in t.repo
