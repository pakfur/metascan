"""Scan dispatch sets tag_with_vlm based on hardware gates."""

from metascan.core.hardware import CudaInfo, HardwareReport


def _report(cuda_gb=None):
    cuda = CudaInfo(name="t", vram_gb=cuda_gb, capability="8.6") if cuda_gb else None
    return HardwareReport(
        os="Linux",
        machine="x86_64",
        python="3.11",
        cpu_count=8,
        ram_gb=16.0,
        cuda=cuda,
    )


def test_workstation_returns_true_only_with_binary_and_weights_present():
    """Without the binary or weights on disk, should_tag_with_vlm is False
    even on a workstation. Real-world precondition: setup_models.py hasn't
    run yet."""
    from backend.services.scan_dispatch import should_tag_with_vlm

    assert should_tag_with_vlm(_report(cuda_gb=16.0)) is False


def test_cpu_only_routes_tag_with_vlm_false():
    from backend.services.scan_dispatch import should_tag_with_vlm

    assert should_tag_with_vlm(_report()) is False


def test_cuda_entry_routes_tag_with_vlm_false_when_uninstalled():
    from backend.services.scan_dispatch import should_tag_with_vlm

    assert should_tag_with_vlm(_report(cuda_gb=4.0)) is False


def test_recommended_model_id_for_workstation():
    from backend.services.scan_dispatch import recommended_vlm_model_id

    assert recommended_vlm_model_id(_report(cuda_gb=16.0)) == "qwen3vl-8b"


def test_recommended_model_id_for_cuda_entry():
    from backend.services.scan_dispatch import recommended_vlm_model_id

    assert recommended_vlm_model_id(_report(cuda_gb=4.0)) == "qwen3vl-2b"


def test_recommended_model_id_for_cpu_only_is_none():
    from backend.services.scan_dispatch import recommended_vlm_model_id

    assert recommended_vlm_model_id(_report()) is None
