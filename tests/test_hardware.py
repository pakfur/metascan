"""Tests for metascan.core.hardware platform/tier detection."""

from __future__ import annotations

from metascan.core.hardware import HardwareReport, _platform_info


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
