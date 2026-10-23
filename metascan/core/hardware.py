"""Hardware detection + tier classification.

Probes CPU / RAM / CUDA / MPS / Vulkan / glibc / NLTK at startup and exposes a
:class:`HardwareReport`. Used by the ``/api/models/hardware`` endpoint and by
:mod:`metascan.core.embedding_manager` to pick a torch device.

All probes are best-effort: if a probe raises, we log at DEBUG and leave the
field at its default. This module never raises on import or on
:func:`detect_hardware` — callers can rely on a populated dataclass.
"""

from __future__ import annotations

import enum
import json
import logging
import os
import platform
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from metascan.core.vlm_models import REGISTRY as _VLM_REGISTRY

logger = logging.getLogger(__name__)


@dataclass
class CudaInfo:
    name: str
    vram_gb: float
    capability: str  # e.g. "8.6"


@dataclass
class VulkanInfo:
    available: bool
    devices: List[str]  # e.g. ["NVIDIA GeForce RTX 3080", "llvmpipe"]
    has_real_device: bool  # True iff at least one non-llvmpipe device is present


@dataclass
class HardwareReport:
    os: str = ""
    machine: str = ""
    python: str = ""
    is_wsl: bool = False
    cpu_count: Optional[int] = None
    ram_gb: Optional[float] = None
    glibc: Optional[str] = None
    cuda: Optional[CudaInfo] = None
    mps: bool = False
    vulkan: Optional[VulkanInfo] = None
    nltk_version: Optional[str] = None
    torch_version: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


def _platform_info() -> dict:
    is_wsl = False
    try:
        if sys.platform == "linux":
            with open("/proc/version", "rt", encoding="utf-8") as f:
                is_wsl = "microsoft" in f.read().lower()
    except OSError:
        pass
    return {
        "os": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "is_wsl": is_wsl,
        "cpu_count": os.cpu_count(),
    }


def _ram_gb() -> Optional[float]:
    try:
        import psutil

        return round(float(psutil.virtual_memory().total) / (1024**3), 1)
    except Exception as e:
        logger.debug("ram probe failed: %s", e)
    # Linux fallback when psutil isn't installed
    try:
        if sys.platform == "linux":
            with open("/proc/meminfo", "rt", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return round(kb / (1024**2), 1)
    except (OSError, ValueError):
        pass
    return None


def _glibc_version() -> Optional[str]:
    if sys.platform != "linux":
        return None
    try:
        # confstr is Linux-only and python provides it on glibc systems
        val = os.confstr("CS_GNU_LIBC_VERSION")  # type: ignore[arg-type]
        if val:
            # "glibc 2.31" -> "2.31"
            parts = val.strip().split()
            return parts[-1] if parts else None
    except (ValueError, OSError, AttributeError):
        pass
    return None


def _nltk_version() -> Optional[str]:
    try:
        import nltk

        return getattr(nltk, "__version__", None)
    except Exception:
        return None


def _torch_version() -> Optional[str]:
    try:
        import torch

        return torch.__version__
    except Exception:
        return None


def _try_cuda() -> Optional[CudaInfo]:
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        name = torch.cuda.get_device_name(0)
        props = torch.cuda.get_device_properties(0)
        vram_gb = round(props.total_memory / (1024**3), 1)
        cap = f"{props.major}.{props.minor}"
        return CudaInfo(name=name, vram_gb=vram_gb, capability=cap)
    except Exception as e:
        logger.debug("cuda probe failed: %s", e)
        return None


def _mps_available() -> bool:
    try:
        import torch

        return bool(
            getattr(torch.backends, "mps", None)
            and torch.backends.mps.is_available()
            and torch.backends.mps.is_built()
        )
    except Exception:
        return False


_DEVICE_NAME_RE = re.compile(r"deviceName\s*=\s*(.+)")


def _try_vulkan() -> VulkanInfo:
    """Probe Vulkan via ``vulkaninfo --summary``.

    Returns a VulkanInfo with ``available=False`` if vulkaninfo isn't
    installed; ``has_real_device=False`` if only llvmpipe (software) is
    present (typical of misconfigured WSL2). Never raises.
    """
    try:
        proc = subprocess.run(
            ["vulkaninfo", "--summary"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except FileNotFoundError:
        return VulkanInfo(available=False, devices=[], has_real_device=False)
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("vulkan probe failed: %s", e)
        return VulkanInfo(available=False, devices=[], has_real_device=False)

    if proc.returncode != 0:
        return VulkanInfo(available=False, devices=[], has_real_device=False)

    devices: List[str] = []
    for m in _DEVICE_NAME_RE.finditer(proc.stdout):
        name = m.group(1).strip()
        if name:
            devices.append(name)

    has_real = any("llvmpipe" not in d.lower() for d in devices)
    return VulkanInfo(
        available=bool(devices),
        devices=devices,
        has_real_device=has_real,
    )


class Tier(str, enum.Enum):
    """Hardware capability tiers. Drives feature gates and model recommendations.

    Reference: docs/MODEL_HARDWARE_REQUIREMENTS.md "Suggested hardware tier model".
    """

    CPU_ONLY = "cpu_only"
    APPLE_SILICON = "apple_silicon"
    CUDA_ENTRY = "cuda_entry"
    CUDA_MAINSTREAM = "cuda_mainstream"
    CUDA_WORKSTATION = "cuda_workstation"


# VRAM thresholds (GB) — keep in sync with docs/MODEL_HARDWARE_REQUIREMENTS.md
_CUDA_MAINSTREAM_MIN_GB = 6.0
_CUDA_WORKSTATION_MIN_GB = 12.0


def classify_tier(report: HardwareReport) -> Tier:
    """Classify a HardwareReport into one of five tiers.

    Order of precedence: CUDA always wins over MPS (CUDA stack is more
    feature-complete in PyTorch). MPS is only consulted on Apple Silicon
    (arm64 Darwin); MPS on Intel macOS does not exist.
    """
    if report.cuda is not None:
        if report.cuda.vram_gb >= _CUDA_WORKSTATION_MIN_GB:
            return Tier.CUDA_WORKSTATION
        if report.cuda.vram_gb >= _CUDA_MAINSTREAM_MIN_GB:
            return Tier.CUDA_MAINSTREAM
        return Tier.CUDA_ENTRY
    if report.mps and report.os == "Darwin" and report.machine == "arm64":
        return Tier.APPLE_SILICON
    return Tier.CPU_ONLY


@dataclass
class Gate:
    available: bool
    recommended: bool
    reason: str = ""
    # Each missing piece separately (writer_training); ``reason`` joins them.
    reasons: List[str] = field(default_factory=list)


# Per-model VRAM floors (GB) for "available". Below this, the model is
# either unrunnable or unusably slow. Numbers from
# docs/MODEL_HARDWARE_REQUIREMENTS.md. ViT-H/14's bs=1 FP32 footprint is
# ~4 GB but interactive batch search needs ~5 GB headroom, so we set the
# floor at 6 GB — aligning with the CUDA_MAINSTREAM tier threshold.
_CLIP_VRAM_MIN: dict[str, float] = {
    "clip-small": 1.0,
    "clip-medium": 2.0,
    "clip-large": 6.0,
}


def feature_gates(report: HardwareReport) -> "dict[str, Gate]":
    """Return per-model availability + recommendation gates.

    Keys are the model ids surfaced by ``GET /api/models/status``:
    ``clip-{small,medium,large}``, ``resr-{x2,x4,x4-anime}``, ``gfpgan-v1.4``,
    ``rife``, ``nltk-punkt``, ``nltk-punkt-tab``, ``nltk-stopwords``,
    ``qwen3vl-{2b,4b,8b,30b-a3b}``, ``qwen38-27b``, ``llama-server``.
    """
    tier = classify_tier(report)
    gates: dict[str, Gate] = {}

    # ---- CLIP ----
    has_gpu = report.cuda is not None or (report.mps and report.os == "Darwin")
    cuda_vram = report.cuda.vram_gb if report.cuda else 0.0

    for key in ("clip-small", "clip-medium", "clip-large"):
        min_vram = _CLIP_VRAM_MIN[key]
        if report.cuda is not None:
            available = cuda_vram >= min_vram
            reason = (
                ""
                if available
                else f"Requires {min_vram} GB VRAM; detected {cuda_vram} GB."
            )
        elif report.mps:
            # ViT-H/14 on MPS hits allocator pressure at <= 16 GB unified RAM
            available = key != "clip-large" or (report.ram_gb or 0.0) >= 24.0
            reason = (
                ""
                if available
                else "ViT-H/14 unstable on MPS with <24 GB unified memory."
            )
        else:
            # CPU-only: small is fine, medium painful, large impractical
            available = key != "clip-large"
            reason = "" if available else "ViT-H/14 is too slow on CPU."

        # Recommendation: pick the largest model the host can handle well
        if not available:
            recommended = False
        elif tier is Tier.CUDA_WORKSTATION:
            recommended = key == "clip-large"
        elif tier is Tier.CUDA_MAINSTREAM:
            recommended = key == "clip-medium"
        elif tier is Tier.CUDA_ENTRY:
            recommended = key == "clip-medium" and cuda_vram >= 2.0
            if not recommended and key == "clip-small":
                recommended = cuda_vram < 2.0
        elif tier is Tier.APPLE_SILICON:
            recommended = key == "clip-small"
        else:  # CPU_ONLY
            recommended = key == "clip-small"

        gates[key] = Gate(available=available, recommended=recommended, reason=reason)

    # ---- Real-ESRGAN ----
    # x2 / x4-anime are light; x4 needs 4 GB CUDA for interactive 1080p
    gates["resr-x2"] = Gate(
        available=True,
        recommended=has_gpu,
        reason=("" if has_gpu else "Will run on CPU at 30-60 s per 1080p image."),
    )
    gates["resr-x4-anime"] = Gate(
        available=True,
        recommended=has_gpu,
        reason="" if has_gpu else "Will run on CPU at 25-45 s per 1080p image.",
    )
    if report.cuda is not None:
        x4_avail = cuda_vram >= 4.0
        x4_reason = (
            ""
            if x4_avail
            else f"Requires 4 GB VRAM for 1080p; detected {cuda_vram} GB."
        )
    else:
        x4_avail = True  # CPU works, just very slow
        x4_reason = "Will run on CPU at 90-180 s per 1080p image."
    gates["resr-x4"] = Gate(
        available=x4_avail,
        recommended=tier in (Tier.CUDA_MAINSTREAM, Tier.CUDA_WORKSTATION),
        reason=x4_reason,
    )

    # ---- GFPGAN ----
    if report.cuda is not None:
        gfp_avail = cuda_vram >= 3.0
        gfp_reason = (
            "" if gfp_avail else f"Requires 3 GB VRAM; detected {cuda_vram} GB."
        )
    else:
        gfp_avail = True
        gfp_reason = "Will run on CPU at 6-12 s per face."
    gates["gfpgan-v1.4"] = Gate(
        available=gfp_avail,
        recommended=tier in (Tier.CUDA_MAINSTREAM, Tier.CUDA_WORKSTATION),
        reason=gfp_reason,
    )

    # ---- RIFE (Vulkan-required) ----
    vk = report.vulkan
    if vk is None or not vk.has_real_device:
        rife_reason = (
            "vulkaninfo not detected; install Vulkan drivers."
            if vk is None or not vk.available
            else "Only llvmpipe (software) Vulkan device detected. "
            "Install GPU Vulkan drivers."
        )
        gates["rife"] = Gate(available=False, recommended=False, reason=rife_reason)
    else:
        gates["rife"] = Gate(available=True, recommended=True, reason="")

    # ---- NLTK punkt vs punkt_tab (CVE-2024-39705 / NLTK 3.8.2 break) ----
    nltk_v = report.nltk_version or ""
    needs_tab = bool(nltk_v) and _nltk_geq_3_8_2(nltk_v)
    if needs_tab:
        gates["nltk-punkt"] = Gate(
            available=False,
            recommended=False,
            reason=f"NLTK {nltk_v} requires punkt_tab (CVE-2024-39705 fix).",
        )
        gates["nltk-punkt-tab"] = Gate(available=True, recommended=True, reason="")
    else:
        gates["nltk-punkt"] = Gate(available=True, recommended=True, reason="")
        gates["nltk-punkt-tab"] = Gate(
            available=False,
            recommended=False,
            reason=f"NLTK {nltk_v or '<unknown>'} uses legacy punkt.",
        )
    gates["nltk-stopwords"] = Gate(available=True, recommended=True, reason="")

    # ---- VLM tagging (Qwen3-VL + Qwen3.8 Abliterated) ----
    # min_vram_gb is the gate floor; recommended = the tier's default size.
    is_apple = report.mps and report.os == "Darwin" and report.machine == "arm64"
    ram_gb = report.ram_gb or 0.0

    for key, _spec in _VLM_REGISTRY.items():
        min_vram = _spec.min_vram_gb
        if report.cuda is not None:
            floor = _spec.cuda_gate_vram_gb or min_vram
            available = cuda_vram >= floor
            reason = (
                ""
                if available
                else f"Requires {floor:.0f} GB VRAM; detected {cuda_vram} GB."
            )
        elif is_apple:
            available = True  # all sizes offered as opt-in on Apple Silicon
            reason = ""
        else:
            # CPU only: 2B always offered, 4B if >= 16 GB RAM, 8B/30B no.
            if key == "qwen3vl-2b":
                available, reason = True, ""
            elif key == "qwen3vl-4b":
                available = ram_gb >= 16.0
                reason = "" if available else "Requires 16 GB RAM on CPU."
            else:
                available = False
                reason = "Requires GPU acceleration."

        # Recommendation: pick one model per tier as default.
        if not available:
            recommended = False
        elif tier is Tier.CUDA_WORKSTATION:
            recommended = (
                key == "qwen38-27b" if cuda_vram >= 22.0 else key == "qwen3vl-8b"
            )
        elif tier is Tier.CUDA_MAINSTREAM:
            recommended = key == "qwen3vl-4b"
        elif tier is Tier.CUDA_ENTRY:
            recommended = key == "qwen3vl-2b"
        elif tier is Tier.APPLE_SILICON:
            recommended = key == "qwen3vl-4b"
        else:  # CPU_ONLY — CLIP fallback recommended; nothing VLM-recommended
            recommended = False

        gates[key] = Gate(available=available, recommended=recommended, reason=reason)

    # ---- llama-server binary gate ----
    # The binary is downloaded by setup_models.py / Models tab; the gate
    # records presence so the UI can render an actionable prompt.
    # TODO Task 3: hoist this to module-level imports once the full
    # implementation lands and we've confirmed it doesn't pull heavy deps.
    from metascan.utils.llama_server import binary_path

    has_binary = binary_path().exists()
    gates["llama-server"] = Gate(
        available=has_binary,
        recommended=has_binary,
        reason="" if has_binary else "Click to install llama.cpp runtime.",
    )

    # ---- Prompt-writer LoRA training ----
    # Never blocks: the subprocess probes run only from the training
    # endpoints (writer_training_gate(probe=True)); until then their part
    # reads "not checked yet".
    gates["writer_training"] = writer_training_gate(report, probe=False)

    return gates


# ---- writer LoRA training gate --------------------------------------------

# bf16 LoRA on the 8B writer at 2048 tokens; DPO runs chosen, rejected and
# the reference adapter's passes.
WRITER_TRAINING_MIN_VRAM_GB = 32.0
# Imported (for real) by the deps probe with the TRAINING interpreter.
WRITER_TRAINING_DEPS: Tuple[str, ...] = (
    "peft",
    "trl",
    "accelerate",
    "datasets",
    "safetensors",
    "transformers",
    "torch",
)
DEPS_CHECK = """
import importlib, importlib.util, json, sys
names = %r
missing = [n for n in names if importlib.util.find_spec(n) is None]
if missing:
    print(json.dumps({"missing": missing}))
    sys.exit(0)
broken = []
for n in names:
    try:
        importlib.import_module(n)
    except Exception as e:
        broken.append("%%s (%%s)" %% (n, type(e).__name__))
print(json.dumps({"missing": [], "broken": broken}))
""" % (
    list(WRITER_TRAINING_DEPS),
)
_DEPS_HINT = "pip install -r requirements-train.txt"
TRAINING_DEPS_UNCHECKED = "Training dependencies not checked yet."


@dataclass(frozen=True)
class WriterTrainingPaths:
    """Where the training pieces live (resolved from config by the backend:
    ``models.writer_train_python`` / ``writer_train_repo`` /
    ``llama_cpp_dir``)."""

    python: str
    base_dir: Path
    converter_script: Path


_training_paths: Optional[WriterTrainingPaths] = None
# Probe results per training interpreter: (deps_reason, trl_reason); "" = ok.
# Probe results per training interpreter: python -> (deps_reason,
# trl_reason, expires_at). "" = ok. A definitive answer never expires
# (expires_at None); a probe that could not run is kept PROBE_FAILURE_TTL_S.
_training_probe_cache: Dict[str, Tuple[str, str, Optional[float]]] = {}
PROBE_FAILURE_TTL_S = 60.0


def set_writer_training_paths(paths: Optional[WriterTrainingPaths]) -> None:
    """Install the resolved paths (lifespan / training endpoints)."""
    global _training_paths
    _training_paths = paths


def clear_writer_training_probe_cache(python: Optional[str] = None) -> None:
    """Forget the probe results (of one interpreter, or all)."""
    if python is None:
        _training_probe_cache.clear()
    else:
        _training_probe_cache.pop(python, None)


# A probe that could not run (timeout, missing interpreter, crash) says
# nothing definitive: it is reported but never cached.
_PROBE_FAILURE_PREFIXES = (
    "could not run",
    "the training dependency probe failed",
    "the TRL probe failed",
)


def _probe_failed(reason: str) -> bool:
    return reason.startswith(_PROBE_FAILURE_PREFIXES)


def cached_training_probe(python: str) -> Optional[Tuple[str, str]]:
    """The cached ``(deps_reason, trl_reason)`` for ``python``, or None
    when never probed or a cached failure has expired."""
    import time

    hit = _training_probe_cache.get(python)
    if hit is None:
        return None
    deps, trl, expires = hit
    if expires is not None and time.monotonic() >= expires:
        _training_probe_cache.pop(python, None)
        return None
    return deps, trl


def store_training_probe(python: str, deps: str, trl: str) -> None:
    """Cache a probe result: definitive answers for good, probe failures
    (timeouts, crashes) for ``PROBE_FAILURE_TTL_S`` only."""
    import time

    failed = _probe_failed(deps) or _probe_failed(trl)
    expires = time.monotonic() + PROBE_FAILURE_TTL_S if failed else None
    _training_probe_cache[python] = (deps, trl, expires)


def parse_deps_probe(returncode: Optional[int], stdout: str) -> str:
    """The owner-facing reason from the deps probe's exit code + stdout."""
    lines = (stdout or "").strip().splitlines()
    try:
        info = json.loads(lines[-1]) if lines else None
    except ValueError:
        info = None
    if returncode != 0 or not isinstance(info, dict):
        return f"the training dependency probe failed (exit {returncode})"
    if info.get("missing"):
        return (
            "training dependencies not installed: "
            f"{', '.join(info['missing'])} ({_DEPS_HINT})"
        )
    if info.get("broken"):
        return f"training dependencies fail to import: {', '.join(info['broken'])}"
    return ""


def default_writer_training_paths() -> WriterTrainingPaths:
    from metascan.core.writer_adapters import (
        DEFAULT_WRITER_TRAIN_REPO,
        default_converter_script,
        train_base_dir,
    )
    from metascan.utils.app_paths import get_data_dir

    data_dir = get_data_dir()
    return WriterTrainingPaths(
        python=sys.executable,
        base_dir=train_base_dir(data_dir, DEFAULT_WRITER_TRAIN_REPO),
        converter_script=default_converter_script(data_dir),
    )


def probe_training_deps(python: str, timeout: float = 300.0) -> str:
    """Import the training stack in a subprocess with ``python``. "" when
    every module imports, else an owner-facing reason. Blocking: the
    server runs the async form (training_manager) instead."""
    try:
        proc = subprocess.run(
            [python, "-c", DEPS_CHECK],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"could not run the training dependency probe: {e}"
    return parse_deps_probe(proc.returncode, proc.stdout)


def _probe_trl(python: str) -> str:
    from metascan.workers.writer_train_probe import probe_trl_reference_support

    ok, reason = probe_trl_reference_support(python)
    return "" if ok else reason


def _base_weights_present(base_dir: Path) -> bool:
    try:
        return (base_dir / "config.json").is_file() and any(
            base_dir.glob("*.safetensors")
        )
    except OSError:
        return False


def writer_training_reasons(
    report: HardwareReport,
    paths: Optional[WriterTrainingPaths] = None,
    *,
    probe: bool = True,
    deps_probe: Optional[Callable[[str], str]] = None,
    trl_probe: Optional[Callable[[str], str]] = None,
) -> List[str]:
    """Every missing piece for writer LoRA training, one reason each ([] =
    available): a CUDA GPU with >= 32 GB VRAM; the training deps importing
    in a subprocess with the training interpreter; TRL's SFT-adapter DPO
    reference support; the HF base weights; llama.cpp's LoRA converter.

    ``probe=False`` never spawns a subprocess: it uses the cached probe
    results for this interpreter (or reports them as not checked yet)."""
    paths = paths or _training_paths or default_writer_training_paths()
    reasons: List[str] = []
    if report.cuda is None:
        reasons.append(
            f"Requires a CUDA GPU with {WRITER_TRAINING_MIN_VRAM_GB:.0f} GB VRAM; "
            "none detected."
        )
    elif report.cuda.vram_gb < WRITER_TRAINING_MIN_VRAM_GB:
        reasons.append(
            f"Requires {WRITER_TRAINING_MIN_VRAM_GB:.0f} GB VRAM; "
            f"detected {report.cuda.vram_gb} GB."
        )
    cached = cached_training_probe(paths.python)
    if cached is None and probe:
        deps = (deps_probe or probe_training_deps)(paths.python)
        trl = "" if deps else (trl_probe or _probe_trl)(paths.python)
        cached = (deps, trl)
        store_training_probe(paths.python, deps, trl)
    if cached is None:
        reasons.append(TRAINING_DEPS_UNCHECKED)
    else:
        reasons.extend(r for r in cached if r)
    if not _base_weights_present(paths.base_dir):
        reasons.append(
            f"Writer training base weights not downloaded ({paths.base_dir})."
        )
    if not paths.converter_script.is_file():
        reasons.append(
            f"llama.cpp LoRA converter not found ({paths.converter_script}); "
            "set models.llama_cpp_dir to a llama.cpp checkout."
        )
    return reasons


def writer_training_gate(
    report: HardwareReport,
    paths: Optional[WriterTrainingPaths] = None,
    *,
    probe: bool = True,
    deps_probe: Optional[Callable[[str], str]] = None,
    trl_probe: Optional[Callable[[str], str]] = None,
) -> Gate:
    reasons = writer_training_reasons(
        report, paths, probe=probe, deps_probe=deps_probe, trl_probe=trl_probe
    )
    return Gate(
        available=not reasons,
        recommended=not reasons,
        reason=" ".join(reasons),
        reasons=reasons,
    )


def _nltk_geq_3_8_2(version: str) -> bool:
    try:
        parts = [int(p) for p in version.split(".")[:3]]
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts) >= (3, 8, 2)
    except ValueError:
        return False


@lru_cache(maxsize=1)
def detect_hardware() -> HardwareReport:
    """Run all probes once and return a populated HardwareReport.

    Cached at module level — the result is stable across the lifetime of
    the process. If hardware can change at runtime (e.g. plugging in an
    eGPU), call :func:`detect_hardware.cache_clear`.
    """
    pinfo = _platform_info()
    report = HardwareReport(
        os=pinfo["os"],
        machine=pinfo["machine"],
        python=pinfo["python"],
        is_wsl=pinfo["is_wsl"],
        cpu_count=pinfo["cpu_count"],
        ram_gb=_ram_gb(),
        glibc=_glibc_version(),
        cuda=_try_cuda(),
        mps=_mps_available(),
        vulkan=_try_vulkan(),
        nltk_version=_nltk_version(),
        torch_version=_torch_version(),
    )
    # Auto-warnings — surface gotchas the UI should show
    if report.is_wsl and (report.vulkan is None or not report.vulkan.has_real_device):
        report.warnings.append(
            "WSL2 detected without GPU Vulkan; RIFE will not work. "
            "Install GPU drivers in Windows host."
        )
    if report.os == "Linux" and report.glibc:
        try:
            major, minor = (int(x) for x in report.glibc.split(".")[:2])
            if (major, minor) < (2, 29):
                report.warnings.append(
                    f"glibc {report.glibc} below 2.29; rife-ncnn-vulkan will fail to load."
                )
        except ValueError:
            pass
    return report


def report_to_dict(report: HardwareReport) -> dict:
    """JSON-serializable view of HardwareReport for API responses."""
    return asdict(report)


def select_torch_device(preference: str = "auto") -> str:
    """Pick a torch device string given a preference.

    Generalizes ``embedding_manager._resolve_device`` so other PyTorch
    paths (Real-ESRGAN, GFPGAN if/when wired) can share the logic.
    """
    if preference != "auto":
        return preference
    rpt = detect_hardware()
    if rpt.cuda is not None:
        return "cuda"
    if rpt.mps and rpt.os == "Darwin" and rpt.machine == "arm64":
        return "mps"
    return "cpu"
