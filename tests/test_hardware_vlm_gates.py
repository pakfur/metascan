"""Gate tests for Qwen3-VL VLM tagging across all hardware tiers.

Each test constructs a HardwareReport directly and calls feature_gates()
so no real hardware probes or detect_hardware() caching are involved.
"""

from metascan.core.hardware import (
    CudaInfo,
    HardwareReport,
    feature_gates,
)


def _report(*, cuda_gb=None, mps=False, ram_gb=16.0, machine="x86_64", os_="Linux"):
    cuda = CudaInfo(name="test", vram_gb=cuda_gb, capability="8.6") if cuda_gb else None
    return HardwareReport(
        os=os_,
        machine=machine,
        python="3.11",
        cpu_count=8,
        ram_gb=ram_gb,
        cuda=cuda,
        mps=mps,
    )


def test_cpu_only_recommends_clip_offers_2b_4b():
    g = feature_gates(_report(ram_gb=32.0))
    assert g["qwen3vl-2b"].available is True
    assert g["qwen3vl-2b"].recommended is False
    assert g["qwen3vl-4b"].available is True  # ram_gb >= 16
    assert g["qwen3vl-4b"].recommended is False
    assert g["qwen3vl-8b"].available is False
    assert g["qwen3vl-30b-a3b"].available is False


def test_cpu_only_low_ram_only_2b():
    g = feature_gates(_report(ram_gb=8.0))
    assert g["qwen3vl-2b"].available is True
    assert g["qwen3vl-4b"].available is False


def test_cuda_entry_recommends_2b():
    g = feature_gates(_report(cuda_gb=4.0))
    assert g["qwen3vl-2b"].recommended is True
    assert g["qwen3vl-4b"].available is False
    assert g["qwen3vl-8b"].available is False
    assert g["qwen3vl-30b-a3b"].available is False


def test_cuda_mainstream_low_band_recommends_4b():
    g = feature_gates(_report(cuda_gb=8.0))
    assert g["qwen3vl-4b"].recommended is True
    assert g["qwen3vl-8b"].available is False  # below 10 GB sub-band


def test_cuda_mainstream_high_band_offers_8b():
    g = feature_gates(_report(cuda_gb=11.0))
    assert g["qwen3vl-4b"].recommended is True
    assert g["qwen3vl-8b"].available is True
    assert g["qwen3vl-8b"].recommended is False
    assert g["qwen3vl-30b-a3b"].available is False


def test_cuda_workstation_recommends_8b():
    g = feature_gates(_report(cuda_gb=16.0))
    assert g["qwen3vl-8b"].recommended is True
    assert g["qwen3vl-30b-a3b"].available is False  # below 24 GB


def test_cuda_workstation_high_recommends_30b():
    g = feature_gates(_report(cuda_gb=24.0))
    assert g["qwen3vl-30b-a3b"].available is True
    assert g["qwen3vl-30b-a3b"].recommended is True
    assert g["qwen3vl-8b"].available is True
    assert g["qwen3vl-8b"].recommended is False


def test_apple_silicon_recommends_4b_offers_all():
    g = feature_gates(_report(mps=True, machine="arm64", os_="Darwin", ram_gb=64.0))
    assert g["qwen3vl-2b"].available is True
    assert g["qwen3vl-4b"].available is True
    assert g["qwen3vl-4b"].recommended is True
    assert g["qwen3vl-8b"].available is True
    assert g["qwen3vl-30b-a3b"].available is True


def test_llama_server_gate_present():
    g = feature_gates(_report(cuda_gb=16.0))
    assert "llama-server" in g
