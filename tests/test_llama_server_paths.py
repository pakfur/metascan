"""Path resolution for the bundled llama-server binary.

Download is exercised in test_setup_models (Task 23); this file only covers
the path/build-name logic which is pure and easy to assert."""

from unittest.mock import patch

from metascan.utils.llama_server import (
    binary_filename,
    pick_release_asset,
    binary_path,
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


def test_pick_release_asset_linux_cuda():
    rpt = _report(cuda=True)
    asset = pick_release_asset(rpt)
    assert "linux" in asset.lower()
    assert "cuda" in asset.lower()


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
    assert "linux" in asset.lower()
    # CPU build identifier varies; just assert it's the non-cuda non-vulkan path
    assert "cuda" not in asset.lower()
    assert "vulkan" not in asset.lower()


def test_binary_path_lives_in_data_dir():
    p = binary_path()
    assert "bin" in p.parts
