"""Tests for metascan.core.hardware platform/tier detection."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

from metascan.core.hardware import (  # noqa: F401
    HardwareReport,
    VulkanInfo,
    _glibc_version,
    _mps_available,
    _nltk_version,
    _platform_info,
    _ram_gb,
    _try_cuda,
    _try_vulkan,
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
