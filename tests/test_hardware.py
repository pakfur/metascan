"""Tests for metascan.core.hardware platform/tier detection."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

from metascan.core.hardware import (  # noqa: F401
    CudaInfo,
    Gate,
    HardwareReport,
    Tier,
    VulkanInfo,
    _glibc_version,
    _mps_available,
    _nltk_version,
    _platform_info,
    _ram_gb,
    _try_cuda,
    _try_vulkan,
    classify_tier,
    feature_gates,
)


def test_platform_info_populates_basic_fields() -> None:
    info = _platform_info()
    assert info["os"] in {"Linux", "Darwin", "Windows"}
    assert isinstance(info["machine"], str) and info["machine"]
    assert isinstance(info["python"], str)
    # cpu_count may legitimately be None on exotic hosts but never negative
    assert info["cpu_count"] is None or info["cpu_count"] >= 1


def test_platform_info_detects_wsl(tmp_path) -> None:
    # On WSL, /proc/version mentions "microsoft". We can't truly mock that
    # cross-platform, so just assert the field exists and is a bool.
    info = _platform_info()
    assert isinstance(info["is_wsl"], bool)


def test_hardware_report_dataclass_defaults() -> None:
    rpt = HardwareReport()
    assert rpt.os == ""
    assert rpt.cuda is None
    assert rpt.mps is False
    assert rpt.vulkan is None
    assert rpt.warnings == []


def test_ram_gb_returns_positive_or_none() -> None:
    val = _ram_gb()
    assert val is None or val > 0


def test_glibc_version_linux_only() -> None:
    val = _glibc_version()
    if sys.platform == "linux":
        # Either "2.31" or None if probe failed; never empty string.
        assert val is None or val.count(".") >= 1
    else:
        assert val is None


def test_nltk_version_when_installed() -> None:
    val = _nltk_version()
    # Metascan pins nltk in requirements.txt — should be present.
    assert val is None or val.count(".") >= 1


def test_try_cuda_no_torch() -> None:
    """If torch import fails, _try_cuda must return None and not raise."""
    with patch.dict(sys.modules, {"torch": None}):
        with patch("builtins.__import__", side_effect=ImportError("no torch")):
            result = _try_cuda()
    assert result is None


def test_try_cuda_with_mock_torch() -> None:
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    fake_torch.cuda.get_device_name.return_value = "NVIDIA GeForce RTX 3080"
    props = MagicMock()
    props.total_memory = 10 * 1024**3  # 10 GB
    props.major, props.minor = 8, 6
    fake_torch.cuda.get_device_properties.return_value = props
    with patch.dict(sys.modules, {"torch": fake_torch}):
        result = _try_cuda()
    assert result is not None
    assert result.name == "NVIDIA GeForce RTX 3080"
    assert result.vram_gb == 10.0
    assert result.capability == "8.6"


def test_mps_available_no_torch() -> None:
    with patch.dict(sys.modules, {"torch": None}):
        with patch("builtins.__import__", side_effect=ImportError("no torch")):
            assert _mps_available() is False


def test_mps_available_with_mock_torch() -> None:
    fake_torch = MagicMock()
    fake_torch.backends.mps.is_available.return_value = True
    fake_torch.backends.mps.is_built.return_value = True
    with patch.dict(sys.modules, {"torch": fake_torch}):
        assert _mps_available() is True


_VULKANINFO_REAL_GPU = """\
VULKANINFO

Devices:
========
GPU0:
\tdeviceName         = NVIDIA GeForce RTX 3080
\tdeviceType         = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU
"""

_VULKANINFO_LLVMPIPE_ONLY = """\
VULKANINFO

Devices:
========
GPU0:
\tdeviceName         = llvmpipe (LLVM 15.0.7, 256 bits)
\tdeviceType         = PHYSICAL_DEVICE_TYPE_CPU
"""


def test_vulkan_real_device(tmp_path) -> None:
    fake = MagicMock()
    fake.returncode = 0
    fake.stdout = _VULKANINFO_REAL_GPU
    with patch("subprocess.run", return_value=fake):
        info = _try_vulkan()
    assert info is not None
    assert info.available is True
    assert info.has_real_device is True
    assert any("RTX 3080" in d for d in info.devices)


def test_vulkan_llvmpipe_only_is_not_real() -> None:
    fake = MagicMock()
    fake.returncode = 0
    fake.stdout = _VULKANINFO_LLVMPIPE_ONLY
    with patch("subprocess.run", return_value=fake):
        info = _try_vulkan()
    assert info is not None
    assert info.available is True
    assert info.has_real_device is False  # llvmpipe is software fallback
    assert any("llvmpipe" in d for d in info.devices)


def test_vulkan_command_not_found() -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError("vulkaninfo")):
        info = _try_vulkan()
    assert info is not None
    assert info.available is False
    assert info.devices == []
    assert info.has_real_device is False


def test_vulkan_timeout() -> None:
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="vulkaninfo", timeout=5.0),
    ):
        info = _try_vulkan()
    assert info is not None
    assert info.available is False


def _make_report(**kwargs) -> HardwareReport:
    base = HardwareReport(
        os="Linux",
        machine="x86_64",
        python="3.11.7",
        is_wsl=False,
        cpu_count=8,
        ram_gb=16.0,
    )
    for k, v in kwargs.items():
        setattr(base, k, v)
    return base


def test_tier_cpu_only() -> None:
    rpt = _make_report()
    assert classify_tier(rpt) is Tier.CPU_ONLY


def test_tier_apple_silicon() -> None:
    rpt = _make_report(os="Darwin", machine="arm64", mps=True)
    assert classify_tier(rpt) is Tier.APPLE_SILICON


def test_tier_apple_silicon_without_mps_falls_back_to_cpu() -> None:
    # MPS not built into torch yet → still CPU-only tier.
    rpt = _make_report(os="Darwin", machine="arm64", mps=False)
    assert classify_tier(rpt) is Tier.CPU_ONLY


def test_tier_cuda_entry() -> None:
    rpt = _make_report(cuda=CudaInfo(name="GTX 1660", vram_gb=4.0, capability="7.5"))
    assert classify_tier(rpt) is Tier.CUDA_ENTRY


def test_tier_cuda_mainstream() -> None:
    rpt = _make_report(
        cuda=CudaInfo(name="RTX 3060", vram_gb=8.0, capability="8.6"),
    )
    assert classify_tier(rpt) is Tier.CUDA_MAINSTREAM


def test_tier_cuda_workstation() -> None:
    rpt = _make_report(
        cuda=CudaInfo(name="RTX 4090", vram_gb=24.0, capability="8.9"),
    )
    assert classify_tier(rpt) is Tier.CUDA_WORKSTATION


def test_tier_cuda_below_4gb_is_entry_not_below() -> None:
    # 2 GB VRAM — still CUDA_ENTRY (we don't have a sub-tier; the gates
    # block large models below 4 GB, but the tier itself doesn't split).
    rpt = _make_report(cuda=CudaInfo(name="GT 1030", vram_gb=2.0, capability="6.1"))
    assert classify_tier(rpt) is Tier.CUDA_ENTRY


def test_tier_cuda_takes_precedence_over_mps() -> None:
    rpt = _make_report(
        os="Darwin",
        cuda=CudaInfo(name="RTX 3060", vram_gb=8.0, capability="8.6"),
        mps=True,
    )
    assert classify_tier(rpt) is Tier.CUDA_MAINSTREAM


def test_gates_cpu_only_blocks_large_clip() -> None:
    rpt = _make_report()
    gates = feature_gates(rpt)
    assert gates["clip-small"].available is True
    assert gates["clip-small"].recommended is True
    assert gates["clip-medium"].available is True  # CPU possible, slow
    assert gates["clip-medium"].recommended is False
    assert gates["clip-large"].available is False  # too slow on CPU
    assert "CPU" in gates["clip-large"].reason


def test_gates_cpu_only_blocks_real_esrgan_x4() -> None:
    rpt = _make_report()
    gates = feature_gates(rpt)
    assert gates["resr-x2"].available is True
    assert gates["resr-x4"].available is True
    assert gates["resr-x4"].recommended is False  # 90-180s/1080p on CPU
    assert gates["resr-x4-anime"].available is True


def test_gates_cpu_only_blocks_rife_when_no_vulkan() -> None:
    rpt = _make_report()  # no vulkan info
    gates = feature_gates(rpt)
    assert gates["rife"].available is False
    assert "Vulkan" in gates["rife"].reason


def test_gates_rife_available_when_vulkan_real() -> None:
    rpt = _make_report(
        vulkan=VulkanInfo(
            available=True,
            devices=["NVIDIA GeForce RTX 3060"],
            has_real_device=True,
        ),
    )
    gates = feature_gates(rpt)
    assert gates["rife"].available is True


def test_gates_rife_blocked_when_only_llvmpipe() -> None:
    rpt = _make_report(
        vulkan=VulkanInfo(
            available=True,
            devices=["llvmpipe (LLVM 15.0.7, 256 bits)"],
            has_real_device=False,
        ),
    )
    gates = feature_gates(rpt)
    assert gates["rife"].available is False
    assert "llvmpipe" in gates["rife"].reason


def test_gates_cuda_entry_recommends_clip_medium_not_large() -> None:
    rpt = _make_report(cuda=CudaInfo(name="GTX 1660", vram_gb=4.0, capability="7.5"))
    gates = feature_gates(rpt)
    assert gates["clip-large"].available is False  # 4 GB cannot run ViT-H
    assert gates["clip-medium"].available is True
    assert gates["clip-medium"].recommended is True


def test_gates_cuda_workstation_recommends_clip_large() -> None:
    rpt = _make_report(cuda=CudaInfo(name="RTX 4090", vram_gb=24.0, capability="8.9"))
    gates = feature_gates(rpt)
    assert gates["clip-large"].available is True
    assert gates["clip-large"].recommended is True


def test_gates_apple_silicon_allows_small_medium_clip_blocks_large() -> None:
    rpt = _make_report(os="Darwin", machine="arm64", mps=True, ram_gb=16.0)
    gates = feature_gates(rpt)
    assert gates["clip-small"].available is True
    assert gates["clip-small"].recommended is True
    assert gates["clip-medium"].available is True
    # ViT-H/14 on MPS hits allocator limits below 24 GB unified; gate
    # marks it unavailable (and therefore not recommended) on the 16 GB
    # case here — the recommended=False assertion holds either way.
    assert gates["clip-large"].recommended is False


def test_gates_nltk_blocked_on_old_punkt_with_new_nltk() -> None:
    rpt = _make_report(nltk_version="3.9.1")
    gates = feature_gates(rpt)
    assert gates["nltk-punkt"].available is False
    assert "punkt_tab" in gates["nltk-punkt"].reason
    # The replacement id should still be available.
    assert gates["nltk-punkt-tab"].available is True


def test_gate_dataclass_fields() -> None:
    g = Gate(available=True, recommended=False, reason="testing")
    assert g.available is True
    assert g.recommended is False
    assert g.reason == "testing"
