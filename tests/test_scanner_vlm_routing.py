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


def test_workstation_returns_true_only_with_binary_and_weights_present(
    monkeypatch, tmp_path
):
    """Without the binary or weights on disk, should_tag_with_vlm is False
    even on a workstation. Real-world precondition: setup_models.py hasn't
    run yet.

    Point all data-dir lookups at an empty tmp_path so this test is
    independent of host state (a dev box may have llama-server +
    GGUF weights installed from prior `setup_models.py` runs).
    """
    from backend.services import scan_dispatch
    from backend.services.scan_dispatch import should_tag_with_vlm

    monkeypatch.setattr("metascan.utils.llama_server.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(scan_dispatch, "get_data_dir", lambda: tmp_path)

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


def test_recommended_model_id_for_workstation_high_is_qwen38():
    from backend.services.scan_dispatch import recommended_vlm_model_id

    assert recommended_vlm_model_id(_report(cuda_gb=32.0)) == "qwen38-27b"


def _touch_weights(vlm_dir, model_id):
    from metascan.core.vlm_models import REGISTRY

    spec = REGISTRY[model_id]
    vlm_dir.mkdir(parents=True, exist_ok=True)
    (vlm_dir / spec.gguf_filename).touch()
    (vlm_dir / spec.mmproj_filename).touch()


def _touch_binary(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "llama-server").touch()


def test_preferred_falls_back_to_installed_30b_when_qwen38_recommended_but_missing(
    monkeypatch, tmp_path
):
    """Workstation-high recommends qwen38-27b, but only 30b-a3b weights are
    installed (host hasn't run setup_models.py for the new model yet). The
    scan must still VLM-tag using the model it actually has, not silently
    fall back to CLIP.
    """
    from backend.services import scan_dispatch
    from backend.services.scan_dispatch import (
        preferred_vlm_model_id,
        should_tag_with_vlm,
    )

    monkeypatch.setattr("metascan.utils.llama_server.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(scan_dispatch, "get_data_dir", lambda: tmp_path)
    _touch_binary(tmp_path)
    _touch_weights(tmp_path / "models" / "vlm", "qwen3vl-30b-a3b")

    report = _report(cuda_gb=32.0)
    assert preferred_vlm_model_id(report) == "qwen3vl-30b-a3b"
    assert should_tag_with_vlm(report) is True


def test_preferred_prefers_recommended_qwen38_when_both_installed(
    monkeypatch, tmp_path
):
    """Once qwen38-27b's weights are also on disk, it wins over the older
    30b-a3b install (the hardware-recommended model always takes priority
    when its weights are present).
    """
    from backend.services import scan_dispatch
    from backend.services.scan_dispatch import preferred_vlm_model_id

    monkeypatch.setattr("metascan.utils.llama_server.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(scan_dispatch, "get_data_dir", lambda: tmp_path)
    _touch_binary(tmp_path)
    vlm_dir = tmp_path / "models" / "vlm"
    _touch_weights(vlm_dir, "qwen3vl-30b-a3b")
    _touch_weights(vlm_dir, "qwen38-27b")

    assert preferred_vlm_model_id(_report(cuda_gb=32.0)) == "qwen38-27b"


def test_preferred_none_when_no_weights_installed(monkeypatch, tmp_path):
    from backend.services import scan_dispatch
    from backend.services.scan_dispatch import should_tag_with_vlm

    monkeypatch.setattr("metascan.utils.llama_server.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(scan_dispatch, "get_data_dir", lambda: tmp_path)
    _touch_binary(tmp_path)

    assert should_tag_with_vlm(_report(cuda_gb=32.0)) is False


def test_preferred_none_on_cpu_only_even_with_weights_installed(monkeypatch, tmp_path):
    """cpu_only has no recommended VLM model at all, so preferred_vlm_model_id
    must stay None regardless of what weights happen to be on disk — mirrors
    the pre-existing "never VLM-tag on cpu_only" behavior.
    """
    from backend.services import scan_dispatch
    from backend.services.scan_dispatch import preferred_vlm_model_id

    monkeypatch.setattr("metascan.utils.llama_server.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(scan_dispatch, "get_data_dir", lambda: tmp_path)
    _touch_binary(tmp_path)
    _touch_weights(tmp_path / "models" / "vlm", "qwen3vl-2b")

    assert preferred_vlm_model_id(_report()) is None
