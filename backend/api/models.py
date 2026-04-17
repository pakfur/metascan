"""Model management endpoints.

Exposes the full catalogue of AI assets the app depends on (CLIP, Real-ESRGAN,
GFPGAN, RIFE, NLTK) with per-model status, plus HuggingFace-token and preload
configuration. Live-inference state comes from the ``InferenceClient`` that the
``similarity`` router also uses.

All per-model download progress and inference-client state transitions are
broadcast on the ``models`` WS channel.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.api import similarity
from backend.config import get_models_config, load_app_config, save_app_config
from backend.ws.manager import ws_manager
from metascan.core.embedding_manager import CLIP_MODELS, EmbeddingManager
from metascan.utils.app_paths import get_data_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])


# ----------------------------------------------------------------------
# Upscaling model catalogue
# ----------------------------------------------------------------------

# Kept here rather than importing MediaUpscaler to avoid pulling the heavy
# torchvision/realesrgan dependency graph into the API process. The filenames
# and URLs are the authoritative list mirrored from ``media_upscaler.py``.
_UPSCALE_MODELS: List[Dict[str, Any]] = [
    {
        "id": "resr-x2",
        "filename": "RealESRGAN_x2plus.pth",
        "name": "Real-ESRGAN x2",
        "group": "Upscaling",
        "size_mb_estimate": 64,
        "description": "General-purpose 2x upscaler.",
    },
    {
        "id": "resr-x4",
        "filename": "RealESRGAN_x4plus.pth",
        "name": "Real-ESRGAN x4",
        "group": "Upscaling",
        "size_mb_estimate": 64,
        "description": "General-purpose 4x upscaler.",
    },
    {
        "id": "resr-x4-anime",
        "filename": "RealESRGAN_x4plus_anime_6B.pth",
        "name": "Real-ESRGAN x4 (anime)",
        "group": "Upscaling",
        "size_mb_estimate": 17,
        "description": "4x upscaler tuned for illustration / anime content.",
    },
    {
        "id": "gfpgan-v1.4",
        "filename": "GFPGANv1.4.pth",
        "name": "GFPGAN v1.4",
        "group": "Upscaling",
        "size_mb_estimate": 333,
        "description": "Face restoration used after upscaling.",
    },
    {
        "id": "rife",
        "filename": "rife",  # directory, not a single file
        "name": "RIFE (frame interpolation)",
        "group": "Upscaling",
        "size_mb_estimate": 437,
        "description": (
            "Frame interpolation binary used for smoother video upscaling."
        ),
    },
]

_NLTK_MODELS: List[Dict[str, Any]] = [
    {
        "id": "nltk-punkt",
        "name": "NLTK punkt",
        "group": "NLP",
        "description": "Sentence tokenizer used for prompt keyword extraction.",
    },
    {
        "id": "nltk-stopwords",
        "name": "NLTK stopwords",
        "group": "NLP",
        "description": "Stopword list used for prompt keyword extraction.",
    },
]


def _models_dir() -> Path:
    return get_data_dir() / "models"


def _nltk_data_dir() -> Path:
    return Path.home() / ".metascan" / "nltk_data"


# ----------------------------------------------------------------------
# Status helpers
# ----------------------------------------------------------------------


def _dir_size_bytes(path: Path) -> int:
    total = 0
    try:
        for child in path.rglob("*"):
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def _clip_status_rows(preload: List[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for key, cfg in CLIP_MODELS.items():
        model_id = f"clip-{key}"
        # Reuse the same cache-detection logic the model loader uses so the
        # UI and the server agree on "cached" vs "will download".
        try:
            needs_download = EmbeddingManager._check_model_needs_download(
                cfg["name"], cfg["pretrained"]
            )
        except Exception:  # pragma: no cover — defensive
            needs_download = True
        rows.append(
            {
                "id": model_id,
                "group": "Embedding",
                "name": f"CLIP {cfg['name']}",
                "description": cfg["description"],
                "status": "missing" if needs_download else "available",
                "size_bytes": None,  # unknown until HF hub surfaces it
                "cache_path": None,
                "required_vram_mb": cfg["vram_mb"],
                "embedding_dim": cfg["embedding_dim"],
                "preload_at_startup": model_id in preload,
            }
        )
    return rows


def _upscale_status_rows(preload: List[str]) -> List[Dict[str, Any]]:
    md = _models_dir()
    rows: List[Dict[str, Any]] = []
    for m in _UPSCALE_MODELS:
        if m["id"] == "rife":
            rife_root = md / "rife"
            present = rife_root.exists() and any(rife_root.rglob("rife-ncnn-vulkan*"))
            size = _dir_size_bytes(rife_root) if present else 0
            cache = str(rife_root) if present else None
        else:
            path = md / m["filename"]
            present = path.exists()
            size = path.stat().st_size if present else 0
            cache = str(path) if present else None
        rows.append(
            {
                "id": m["id"],
                "group": m["group"],
                "name": m["name"],
                "description": m["description"],
                "status": "available" if present else "missing",
                "size_bytes": size or None,
                "cache_path": cache,
                "required_vram_mb": None,
                "preload_at_startup": m["id"] in preload,
            }
        )
    return rows


def _nltk_status_rows(preload: List[str]) -> List[Dict[str, Any]]:
    data_dir = _nltk_data_dir()
    rows: List[Dict[str, Any]] = []
    for m in _NLTK_MODELS:
        token = m["id"].removeprefix("nltk-")
        corpora_dir = data_dir / "corpora" / token
        tokenizers_dir = data_dir / "tokenizers" / token
        present = corpora_dir.exists() or tokenizers_dir.exists()
        hit = next(
            (p for p in (corpora_dir, tokenizers_dir) if p.exists()),
            None,
        )
        rows.append(
            {
                "id": m["id"],
                "group": m["group"],
                "name": m["name"],
                "description": m["description"],
                "status": "available" if present else "missing",
                "size_bytes": _dir_size_bytes(hit) if hit else None,
                "cache_path": str(hit) if hit else None,
                "required_vram_mb": None,
                "preload_at_startup": m["id"] in preload,
            }
        )
    return rows


def _hardware_info() -> Dict[str, Any]:
    """Inspect CUDA / GPU availability. Import torch lazily so the endpoint
    doesn't pay the import cost on servers that never hit it."""
    info: Dict[str, Any] = {
        "platform": platform.system(),
        "cpu_count": None,
        "cuda_available": False,
        "gpu_name": None,
        "vram_gb": None,
    }
    try:
        import os

        info["cpu_count"] = os.cpu_count()
    except Exception:
        pass
    try:
        import torch

        info["cuda_available"] = bool(torch.cuda.is_available())
        if info["cuda_available"]:
            info["gpu_name"] = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            info["vram_gb"] = round(props.total_memory / (1024**3), 1)
    except Exception as e:
        logger.debug("hardware probe failed: %s", e)
    return info


# ----------------------------------------------------------------------
# Pydantic payloads
# ----------------------------------------------------------------------


class PreloadUpdate(BaseModel):
    id: str
    enabled: bool


class HFTokenBody(BaseModel):
    token: str


class ModelIdBody(BaseModel):
    id: str


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------


@router.get("/hardware")
async def get_hardware() -> Dict[str, Any]:
    return await asyncio.to_thread(_hardware_info)


@router.get("/inference-status")
async def get_inference_status() -> Dict[str, Any]:
    client = similarity._inference_client  # may be None pre-lifespan
    if client is None:
        return {"state": "idle", "model_key": None, "device": None, "dim": None}
    return client.snapshot()


@router.post("/inference/start")
async def start_inference() -> Dict[str, Any]:
    """Spawn the inference worker in the background using the
    currently-configured CLIP model. The UI calls this when the user
    submits a content search while the worker is idle — without this
    nothing else would trigger a spawn (the UI coalesces pending queries
    until the ``models`` WS channel reports ``state=ready``)."""
    client = similarity._inference_client
    if client is None:
        raise HTTPException(status_code=503, detail="inference client not initialized")
    sim_cfg = load_app_config().get("similarity", {}) or {}
    model_key = str(sim_cfg.get("clip_model") or "small")
    device = str(sim_cfg.get("device") or "auto")
    await client.ensure_started(model_key=model_key, device=device)
    return client.snapshot()


def _build_status_payload() -> Dict[str, Any]:
    """Synchronous worker that builds the models-status snapshot.

    Runs on a thread because the helpers below do non-trivial sync work:
    ``_clip_status_rows`` imports ``open_clip`` (which transitively loads
    torch on first call — tens of seconds cold on WSL /mnt/c), and both
    ``_upscale_status_rows`` / ``_nltk_status_rows`` do ``Path.rglob``
    over cache dirs that live on slow 9P-backed storage.

    Previously this was inlined into the async handler below, which
    pinned the asyncio event loop for ~30 s on first request and
    blocked every other incoming request from being dispatched during
    that window — including the startup ``GET /api/media`` and
    ``GET /api/filters`` that the page load waits on.
    """
    config = load_app_config()
    models_cfg = get_models_config(config)
    preload = models_cfg["preload_at_startup"]
    sim_cfg = config.get("similarity", {}) or {}

    rows = (
        _clip_status_rows(preload)
        + _upscale_status_rows(preload)
        + _nltk_status_rows(preload)
    )

    # Surface the currently-selected CLIP model + dim so the UI can render
    # the dim-mismatch banner alongside the model table.
    current_clip = str(sim_cfg.get("clip_model") or "small")
    current_dim = int(CLIP_MODELS.get(current_clip, {}).get("embedding_dim") or 0)

    return {
        "models": rows,
        "hf_token_set": bool(models_cfg["huggingface_token"]),
        "current_clip_model": current_clip,
        "current_clip_dim": current_dim,
    }


@router.get("/status")
async def get_status() -> Dict[str, Any]:
    return await asyncio.to_thread(_build_status_payload)


@router.post("/preload")
async def set_preload(body: PreloadUpdate) -> Dict[str, Any]:
    config = load_app_config()
    models_cfg = config.setdefault("models", {})
    preload = list(models_cfg.get("preload_at_startup") or [])
    if body.enabled and body.id not in preload:
        preload.append(body.id)
    elif not body.enabled and body.id in preload:
        preload.remove(body.id)
    models_cfg["preload_at_startup"] = preload
    await asyncio.to_thread(save_app_config, config)
    return {"preload_at_startup": preload}


@router.get("/hf-token")
async def get_hf_token_status() -> Dict[str, Any]:
    models_cfg = get_models_config(load_app_config())
    return {"set": bool(models_cfg["huggingface_token"])}


@router.post("/hf-token")
async def set_hf_token(body: HFTokenBody) -> Dict[str, Any]:
    config = load_app_config()
    models_cfg = config.setdefault("models", {})
    models_cfg["huggingface_token"] = body.token or ""
    await asyncio.to_thread(save_app_config, config)

    # Inject into the current process env so the inference worker
    # respawned for a model reload picks it up without a server restart.
    import os

    if body.token:
        os.environ["HF_TOKEN"] = body.token
        os.environ["HUGGING_FACE_HUB_TOKEN"] = body.token
    else:
        os.environ.pop("HF_TOKEN", None)
        os.environ.pop("HUGGING_FACE_HUB_TOKEN", None)
    return {"set": bool(body.token)}


@router.delete("/hf-token")
async def clear_hf_token() -> Dict[str, Any]:
    return await set_hf_token(HFTokenBody(token=""))


@router.post("/hf-token/test")
async def test_hf_token() -> Dict[str, Any]:
    token = get_models_config(load_app_config())["huggingface_token"]
    if not token:
        raise HTTPException(status_code=400, detail="no HuggingFace token configured")

    def _whoami() -> Dict[str, Any]:
        import urllib.request
        import urllib.error
        import json

        req = urllib.request.Request(
            "https://huggingface.co/api/whoami-v2",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return {"ok": False, "status": e.code, "error": e.reason}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {
            "ok": True,
            "name": body.get("name"),
            "fullname": body.get("fullname"),
            "type": body.get("type"),
        }

    return await asyncio.to_thread(_whoami)


@router.post("/download")
async def download_model(body: ModelIdBody) -> Dict[str, Any]:
    """Trigger an asynchronous download for the given model id.

    CLIP downloads are done by instantiating a one-shot ``EmbeddingManager``
    on a worker thread — the same code path the inference worker uses — so
    cache layout and auth stay consistent.

    Upscaling / NLTK downloads call their existing installers.
    """
    mid = body.id
    if mid.startswith("clip-"):
        key = mid.removeprefix("clip-")
        if key not in CLIP_MODELS:
            raise HTTPException(status_code=404, detail=f"unknown CLIP model: {mid}")

        asyncio.create_task(_download_clip(mid, key))
        return {"status": "started", "id": mid}

    if mid in {m["id"] for m in _UPSCALE_MODELS}:
        asyncio.create_task(_download_upscaler(mid))
        return {"status": "started", "id": mid}

    if mid in {m["id"] for m in _NLTK_MODELS}:
        asyncio.create_task(_download_nltk(mid))
        return {"status": "started", "id": mid}

    raise HTTPException(status_code=404, detail=f"unknown model id: {mid}")


@router.delete("/{model_id}")
async def delete_model(model_id: str) -> Dict[str, Any]:
    """Delete cached weights for a non-CLIP model. CLIP weights live in the
    shared huggingface_hub cache and are not deleted here — that cache is
    managed by the HF tooling."""
    if model_id.startswith("clip-"):
        raise HTTPException(
            status_code=400,
            detail=(
                "CLIP weights live in the shared huggingface_hub cache. "
                "Use the `huggingface-cli delete-cache` tool to remove them."
            ),
        )
    for m in _UPSCALE_MODELS:
        if m["id"] == model_id:
            if m["id"] == "rife":
                target = _models_dir() / "rife"
                if target.exists():
                    await asyncio.to_thread(shutil.rmtree, target, True)
                return {"ok": True}
            path = _models_dir() / m["filename"]
            if path.exists():
                await asyncio.to_thread(path.unlink)
            return {"ok": True}
    raise HTTPException(status_code=404, detail=f"unknown model id: {model_id}")


@router.post("/rebuild-index")
async def rebuild_index() -> Dict[str, Any]:
    """Convenience wrapper that forwards to the existing similarity index
    rebuild endpoint so the Models tab can surface a 'Rebuild index' button
    next to the dim-mismatch banner."""
    return await similarity.build_index(rebuild=True)


# ----------------------------------------------------------------------
# Background download helpers
# ----------------------------------------------------------------------


def _broadcast_download(mid: str, event: str, **data: Any) -> None:
    ws_manager.broadcast_sync("models", event, {"id": mid, **data})


async def _download_clip(mid: str, key: str) -> None:
    _broadcast_download(mid, "download_progress", stage="starting", percent=0.0)

    def _load() -> None:
        mgr = EmbeddingManager(model_key=key, device="cpu")
        mgr._ensure_model_loaded()

    try:
        await asyncio.to_thread(_load)
    except Exception as e:
        logger.exception("CLIP download for %s failed", mid)
        _broadcast_download(mid, "download_error", error=str(e))
        return
    _broadcast_download(mid, "download_complete")


async def _download_upscaler(mid: str) -> None:
    _broadcast_download(mid, "download_progress", stage="starting", percent=0.0)

    def _setup() -> None:
        from metascan.core.media_upscaler import MediaUpscaler

        upscaler = MediaUpscaler(
            models_dir=_models_dir(), device="auto", tile_size=512, debug=False
        )

        def cb(message: str, pct: float) -> None:
            ws_manager.broadcast_sync(
                "models",
                "download_progress",
                {"id": mid, "stage": message, "percent": pct / 100.0},
            )

        upscaler.setup_models(cb)

    try:
        await asyncio.to_thread(_setup)
    except Exception as e:
        logger.exception("Upscaler download for %s failed", mid)
        _broadcast_download(mid, "download_error", error=str(e))
        return
    _broadcast_download(mid, "download_complete")


async def _download_nltk(mid: str) -> None:
    _broadcast_download(mid, "download_progress", stage="starting", percent=0.0)

    def _download() -> None:
        import nltk

        name = mid.removeprefix("nltk-")
        data_dir = _nltk_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        nltk.download(name, download_dir=str(data_dir), quiet=True)

    try:
        await asyncio.to_thread(_download)
    except Exception as e:
        logger.exception("NLTK download for %s failed", mid)
        _broadcast_download(mid, "download_error", error=str(e))
        return
    _broadcast_download(mid, "download_complete")
