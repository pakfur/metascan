"""Tests for the embedding-manager device-resolution path."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# tests/conftest.py sets KMP_DUPLICATE_LIB_OK=TRUE for the full session
# before any test imports torch/faiss; no per-file env setup needed.

from metascan.core.embedding_manager import EmbeddingManager
from metascan.core.hardware import CudaInfo, HardwareReport


def _fake_report(**kwargs) -> HardwareReport:
    base = HardwareReport(
        os="Linux",
        machine="x86_64",
        python="3.11.7",
        cpu_count=8,
        ram_gb=16.0,
    )
    for k, v in kwargs.items():
        setattr(base, k, v)
    return base


@pytest.fixture(autouse=True)
def stub_torch():
    """The device-resolution path must not actually touch torch."""
    fake_torch = MagicMock()
    fake_torch.__version__ = "2.3.0"
    fake_torch.version.cuda = "12.1"
    fake_torch.cuda.is_available.return_value = False
    fake_torch.backends.mps.is_available.return_value = False
    fake_torch.backends.mps.is_built.return_value = False
    with patch("metascan.core.embedding_manager._torch", fake_torch):
        with patch(
            "metascan.core.embedding_manager._ensure_heavy_imports",
            return_value=None,
        ):
            yield fake_torch


def test_resolve_device_picks_cuda_when_available(stub_torch) -> None:
    fake = _fake_report(cuda=CudaInfo(name="X", vram_gb=8.0, capability="8.6"))
    with patch("metascan.core.hardware.detect_hardware", return_value=fake):
        em = EmbeddingManager(model_key="small", device="auto")
        assert em._resolve_device() == "cuda"


def test_resolve_device_picks_mps_on_apple_silicon(stub_torch) -> None:
    fake = _fake_report(os="Darwin", machine="arm64", mps=True)
    with patch("metascan.core.hardware.detect_hardware", return_value=fake):
        em = EmbeddingManager(model_key="small", device="auto")
        assert em._resolve_device() == "mps"


def test_resolve_device_falls_back_to_cpu(stub_torch) -> None:
    fake = _fake_report()
    with patch("metascan.core.hardware.detect_hardware", return_value=fake):
        em = EmbeddingManager(model_key="small", device="auto")
        assert em._resolve_device() == "cpu"


def test_resolve_device_explicit_preference_wins(stub_torch) -> None:
    fake = _fake_report(cuda=CudaInfo(name="X", vram_gb=8.0, capability="8.6"))
    with patch("metascan.core.hardware.detect_hardware", return_value=fake):
        em = EmbeddingManager(model_key="small", device="cpu")
        assert em._resolve_device() == "cpu"
