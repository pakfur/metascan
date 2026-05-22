"""Path resolution for the bundled llama-server binary.

Download is exercised in test_setup_models_qwen3vl (Task 23); this file only
covers the path/build-name logic which is pure and easy to assert."""

import pytest
from unittest.mock import patch

from metascan.utils.llama_server import (
    LLAMA_CPP_RELEASE,
    binary_filename,
    binary_path,
    pick_release_asset,
    release_url,
)


def _report(*, os_="Linux", machine="x86_64", cuda=False, has_real_vk=False):
    from metascan.core.hardware import CudaInfo, HardwareReport, VulkanInfo

    return HardwareReport(
        os=os_,
        machine=machine,
        python="3.11",
        cuda=CudaInfo(name="t", vram_gb=8.0, capability="8.6") if cuda else None,
        vulkan=VulkanInfo(
            available=has_real_vk, devices=[], has_real_device=has_real_vk
        ),
    )


def test_binary_filename_linux():
    with patch("metascan.utils.llama_server.detect_hardware", return_value=_report()):
        assert binary_filename() == "llama-server"


def test_binary_filename_windows():
    with patch(
        "metascan.utils.llama_server.detect_hardware",
        return_value=_report(os_="Windows", machine="AMD64"),
    ):
        assert binary_filename() == "llama-server.exe"


def test_pick_release_asset_linux_cuda_falls_back_to_vulkan():
    # Upstream ships no Linux CUDA prebuilt, so Linux+CUDA hosts with a real
    # Vulkan device get the Vulkan build.
    rpt = _report(cuda=True, has_real_vk=True)
    asset = pick_release_asset(rpt)
    assert "ubuntu-vulkan" in asset.lower()
    assert "cuda" not in asset.lower()


def test_pick_release_asset_linux_cuda_no_vulkan_falls_back_to_cpu():
    # Linux+CUDA without Vulkan (e.g. WSL2 with no real Vulkan device) falls
    # back to the CPU ubuntu build.
    rpt = _report(cuda=True, has_real_vk=False)
    asset = pick_release_asset(rpt)
    assert "ubuntu" in asset.lower()
    assert "cuda" not in asset.lower()
    assert "vulkan" not in asset.lower()


def test_pick_release_asset_macos_arm64():
    rpt = _report(os_="Darwin", machine="arm64")
    asset = pick_release_asset(rpt)
    assert "macos" in asset.lower() or "darwin" in asset.lower()
    assert "arm64" in asset.lower()


def test_pick_release_asset_linux_vulkan_no_cuda():
    rpt = _report(has_real_vk=True)
    asset = pick_release_asset(rpt)
    assert "vulkan" in asset.lower()


def test_pick_release_asset_linux_cpu_fallback():
    rpt = _report()
    asset = pick_release_asset(rpt)
    assert "ubuntu" in asset.lower()
    # CPU build identifier varies; just assert it's the non-cuda non-vulkan path
    assert "cuda" not in asset.lower()
    assert "vulkan" not in asset.lower()


def test_binary_path_lives_in_data_dir():
    p = binary_path()
    assert "bin" in p.parts


def test_binary_path_prefers_local_override(tmp_path, monkeypatch):
    # When data/bin/local/<binary> exists, binary_path() returns that
    # path instead of the bundled data/bin/<binary>. This is how a
    # user-built llama-server (via scripts/build_llama_server.sh) takes
    # precedence over the upstream prebuilt.
    monkeypatch.setattr("metascan.utils.llama_server.get_data_dir", lambda: tmp_path)
    bundled = tmp_path / "bin" / "llama-server"
    local = tmp_path / "bin" / "local" / "llama-server"
    bundled.parent.mkdir(parents=True, exist_ok=True)
    bundled.write_bytes(b"bundled")

    with patch(
        "metascan.utils.llama_server.detect_hardware",
        return_value=_report(),
    ):
        # Bundled-only: returns bundled path.
        assert binary_path() == bundled

        # With local override present, returns local.
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(b"local")
        assert binary_path() == local


def test_pick_release_asset_macos_intel_raises():
    rpt = _report(os_="Darwin", machine="x86_64")
    with pytest.raises(NotImplementedError, match="Intel"):
        pick_release_asset(rpt)


def test_release_url_format():
    url = release_url("llama-x-bin-ubuntu-x64.zip")
    assert url.startswith(
        f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_CPP_RELEASE}/"
    )
    assert url.endswith("llama-x-bin-ubuntu-x64.zip")
