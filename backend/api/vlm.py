"""REST endpoints for Qwen3-VL tagging and the VLM model roles.

The lifespan installs a ``VlmRoles`` via ``set_vlm_roles(...)``: the
``writer`` role (T2I / I2V prompt writing) and the ``general`` role
(tagging, retag, chat, prompt playground, storyboard), sharing one client
when both name the same model. ``set_vlm_client(...)`` installs a single
client for both roles (tests). Endpoints fail fast with 503 if neither is
installed (misconfigured deployment).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.dependencies import get_db
from backend.services.vlm_roles import Role, VlmRoles, WriterModelNotAllowed
from metascan.core.vlm_select import vlm_model_installed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vlm", tags=["vlm"])


_vlm_client: Optional[Any] = None
_vlm_roles: Optional[VlmRoles] = None


def set_vlm_client(client: Any) -> None:
    """Install one client that serves both roles (clears any roles).

    Pass ``None`` to clear (used by tests)."""
    global _vlm_client, _vlm_roles
    _vlm_client = client
    _vlm_roles = None


def set_vlm_roles(roles: Optional[VlmRoles]) -> None:
    """Install the role map. Called from the FastAPI lifespan."""
    global _vlm_client, _vlm_roles
    _vlm_roles = roles
    _vlm_client = None


def get_vlm_roles() -> Optional[VlmRoles]:
    return _vlm_roles


def get_vlm_client(role: Role = "general") -> Any:
    """The client serving ``role`` (default: general), or None."""
    if _vlm_roles is not None:
        return _vlm_roles.client(role)
    return _vlm_client


def get_writer_vlm() -> Any:
    """The prompt-writer client (T2I / I2V)."""
    return get_vlm_client("writer")


def get_writer_adapter_path(adapter_id: int) -> Optional[Path]:
    """The GGUF of adapter ``adapter_id`` when the writer serves it (see
    ``VlmRoles.adapter_path``); None without roles or when it does not."""
    if _vlm_roles is None:
        return None
    return _vlm_roles.adapter_path(adapter_id)


async def refresh_writer_adapters() -> None:
    """Apply an adapter registry change to the writer (no-op without roles)."""
    if _vlm_roles is not None:
        await _vlm_roles.refresh_adapters()


async def withdraw_writer_adapter(adapter_id: int) -> bool:
    """Stop serving ``adapter_id`` before its files are deleted; True when
    no running server still has its file loaded."""
    if _vlm_roles is None:
        return True
    return await _vlm_roles.withdraw_adapter(adapter_id)


def get_all_vlm_clients() -> List[Any]:
    """Every distinct client once -- what unloading for ComfyUI shuts down."""
    if _vlm_roles is not None:
        return _vlm_roles.distinct_clients()
    return [_vlm_client] if _vlm_client is not None else []


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


class TagRequest(BaseModel):
    path: str


@router.get("/status")
async def status() -> Dict[str, Any]:
    """Return the general role's VlmClient state snapshot."""
    client = get_vlm_client()
    if client is None:
        return {
            "state": "idle",
            "model_id": None,
            "base_url": None,
            "progress": {},
            "error": None,
        }
    return client.snapshot()


@router.post("/tag")
async def tag_one(body: TagRequest) -> Dict[str, List[str]]:
    """Tag a single image file using the active VLM model.

    Raises 503 if the VlmClient is not initialized, 404 if the file does
    not exist on disk.
    """
    client = get_vlm_client()
    if client is None:
        raise HTTPException(status_code=503, detail="vlm client not initialized")

    p = Path(body.path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {body.path}")

    preferred = getattr(client, "preferred_model_id", None)
    if client.model_id is None and isinstance(preferred, str) and preferred:
        await client.ensure_started(preferred)
    elif client.model_id is None:
        from metascan.core.hardware import detect_hardware, feature_gates
        from metascan.core.vlm_models import REGISTRY

        gates = feature_gates(detect_hardware())
        candidates = [
            mid for mid, g in gates.items() if mid in REGISTRY and g.recommended
        ]
        if not candidates:
            raise HTTPException(
                status_code=503,
                detail="no recommended VLM model on this hardware",
            )
        await client.ensure_started(candidates[0])
    else:
        await client.ensure_started(client.model_id)

    tags: List[str] = await client.generate_tags(p)

    db = get_db()
    try:
        db.add_tag_indices(p, tags, source="vlm")
    except Exception:
        logger.exception("Failed to persist VLM tags for %s", p)

    return {"tags": tags}


# ---------------------------------------------------------------------------
# Retag background job
# ---------------------------------------------------------------------------


@dataclass
class _RetagJob:
    job_id: str
    paths: List[str]
    cancelled: bool = False
    current: int = 0
    total: int = 0


_jobs: Dict[str, _RetagJob] = {}


class RetagRequest(BaseModel):
    scope: Literal["paths", "all_clip"] = "paths"
    paths: Optional[List[str]] = None
    force: bool = False


async def _run_retag_job(job: "_RetagJob") -> None:
    """Background VLM work: when the roles take turns it waits for a busy
    prompt writer instead of evicting it, and yields to interactive work."""
    from metascan.core.vlm_client import background_vlm_work

    with background_vlm_work():
        await _retag(job)


async def _retag(job: "_RetagJob") -> None:
    import time

    from metascan.core.hardware import detect_hardware
    from metascan.core.vlm_client import VlmClient
    from metascan.core.vlm_models import REGISTRY
    from backend.services.scan_dispatch import preferred_vlm_model_id

    client = get_vlm_client()
    db = get_db()
    if client is None:
        return

    # Drop non-images upfront so the progress totals reflect only what the
    # model can actually process. ``client.generate_tags`` would skip them
    # safely too, but counting them as "done" inflates the total and makes
    # the ETA misleading.
    skipped_non_image = 0
    filtered: List[str] = []
    for ps in job.paths:
        if VlmClient.is_image_path(Path(ps)):
            filtered.append(ps)
        else:
            skipped_non_image += 1
    job.paths = filtered
    job.total = len(filtered)

    preferred = getattr(client, "preferred_model_id", None)
    model_id = (
        client.model_id
        or (preferred if isinstance(preferred, str) else None)
        or preferred_vlm_model_id(detect_hardware())
        or "qwen3vl-4b"
    )
    # ensure_started once up front; the per-request path no longer needs to
    # re-acquire the start lock for every image.
    await client.ensure_started(model_id)

    # Match per-request concurrency to the model spec's parallel slots so
    # GPU prefill/decode pipelines stay full instead of stalling between
    # CPU-side image prep, HTTP marshalling, and DB writes. Falls back to
    # 2 if the registry doesn't list a value.
    spec = REGISTRY.get(model_id)
    concurrency = spec.parallel_slots if spec is not None else 2
    sem = asyncio.Semaphore(concurrency)

    SUMMARY_EVERY_N = 25
    SUMMARY_INTERVAL_S = 30.0

    ok = 0
    empty = 0
    fail = 0
    done = 0
    start = time.monotonic()
    last_summary = start
    progress_lock = asyncio.Lock()

    logger.info(
        "VLM retag start: %d images, model=%s, concurrency=%d "
        "(skipped %d non-images)",
        job.total,
        model_id,
        concurrency,
        skipped_non_image,
    )

    async def _process_one(path_str: str) -> None:
        nonlocal ok, empty, fail, done, last_summary
        if job.cancelled:
            return
        async with sem:
            if job.cancelled:
                return
            p = Path(path_str)
            if not p.is_file():
                _ok = False
                _empty = False
            else:
                try:
                    tags = await client.generate_tags(p)
                    # Move the sync DB write off the event loop. On WSL2's
                    # /mnt/<drive> mounts (9P over Hyper-V) every SQLite
                    # fsync can take 100ms+; running it here would stall
                    # the heartbeat and serialize concurrent retag tasks.
                    await asyncio.to_thread(db.add_tag_indices, p, tags, "vlm")
                    _ok = True
                    _empty = not tags
                except Exception as e:
                    logger.warning("retag failed for %s: %s", path_str, e)
                    _ok = False
                    _empty = False

        async with progress_lock:
            if _ok:
                ok += 1
                if _empty:
                    empty += 1
            else:
                fail += 1
            done = ok + fail
            job.current = done

            try:
                from backend.ws.manager import ws_manager

                ws_manager.broadcast_sync(
                    "models",
                    "vlm_progress",
                    {"job_id": job.job_id, "current": done, "total": job.total},
                )
            except Exception:
                pass

            now = time.monotonic()
            if (
                done % SUMMARY_EVERY_N == 0
                or (now - last_summary) >= SUMMARY_INTERVAL_S
            ):
                last_summary = now
                elapsed = now - start
                rate = done / elapsed if elapsed > 0 else 0.0
                remaining = job.total - done
                eta_s = remaining / rate if rate > 0 else float("inf")
                logger.info(
                    "VLM retag [%d/%d] ok=%d (empty=%d) fail=%d  "
                    "rate=%.2f img/s  ETA=%s",
                    done,
                    job.total,
                    ok,
                    empty,
                    fail,
                    rate,
                    _format_eta(eta_s),
                )

    await asyncio.gather(*[_process_one(p) for p in job.paths])

    job.current = job.total
    elapsed = time.monotonic() - start
    rate = job.total / elapsed if elapsed > 0 else 0.0
    logger.info(
        "VLM retag done: %d images in %ds (%.2f img/s) — "
        "ok=%d (empty=%d) fail=%d  cancelled=%s",
        job.total,
        int(elapsed),
        rate,
        ok,
        empty,
        fail,
        job.cancelled,
    )


def _format_eta(seconds: float) -> str:
    """HH:MM:SS, or ``--:--:--`` when the rate is zero/inf."""
    if seconds == float("inf") or seconds != seconds:
        return "--:--:--"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


@router.post("/retag", status_code=202)
async def retag(body: RetagRequest) -> Dict[str, Any]:
    if get_vlm_client() is None:
        raise HTTPException(status_code=503, detail="vlm client not initialized")
    if body.scope == "paths":
        if not body.paths:
            raise HTTPException(
                status_code=400, detail="paths required for scope=paths"
            )
        targets = list(body.paths)
    elif body.scope == "all_clip":
        db = get_db()
        targets = _list_paths_for_retag(db, force=body.force)
    else:
        raise HTTPException(status_code=400, detail=f"unknown scope: {body.scope}")

    job = _RetagJob(job_id=str(uuid.uuid4()), paths=targets, total=len(targets))
    _jobs[job.job_id] = job
    asyncio.create_task(_run_retag_job(job))
    return {"job_id": job.job_id, "total": job.total}


@router.delete("/retag/{job_id}")
async def cancel_retag(job_id: str) -> Dict[str, str]:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    job.cancelled = True
    return {"status": "cancelled"}


class ActiveBody(BaseModel):
    model_id: str
    role: Role = "general"


def _cancel_retag_jobs() -> None:
    for job in _jobs.values():
        job.cancelled = True


async def _point_role(roles: VlmRoles, role: Role, model_id: str) -> None:
    """Re-point ``role``; retag jobs run on the general client, so stop
    them when that client changes."""
    general_before = roles.client("general")
    await roles.set_model(role, model_id)
    if roles.client("general") is not general_before:
        _cancel_retag_jobs()


@router.post("/active")
async def set_active(body: ActiveBody) -> Dict[str, Any]:
    """Load ``model_id`` for ``role`` (default general, the pre-roles
    meaning) and make it that role's model."""
    from metascan.core.vlm_models import REGISTRY, writer_allowed

    if get_vlm_client(body.role) is None:
        raise HTTPException(status_code=503, detail="vlm client not initialized")
    if body.model_id not in REGISTRY:
        raise HTTPException(status_code=400, detail=f"unknown model: {body.model_id}")
    if body.role == "writer" and not writer_allowed(body.model_id):
        raise _error(
            422,
            "writer_model_not_allowed",
            f"{body.model_id} cannot be the prompt writer",
        )
    if not await asyncio.to_thread(vlm_model_installed, body.model_id):
        raise _error(409, "model_not_installed", f"{body.model_id} is not installed")
    roles = _vlm_roles
    if body.role == "general":
        _cancel_retag_jobs()
    if roles is None:
        client = get_vlm_client(body.role)
        await client.swap_model(body.model_id)
        return dict(client.snapshot())

    async def _swap(c: Any) -> None:
        await c.swap_model(body.model_id)

    # The role is persisted only once the model actually loaded.
    general_before = roles.client("general")
    await roles.activate(body.role, body.model_id, _swap)
    if roles.client("general") is not general_before:
        _cancel_retag_jobs()
    return dict(roles.client(body.role).snapshot())


class RoleBody(BaseModel):
    role: Role
    model_id: str


def _require_roles() -> VlmRoles:
    if _vlm_roles is None:
        raise HTTPException(status_code=503, detail="vlm roles not initialized")
    return _vlm_roles


@router.get("/roles")
async def get_roles() -> Dict[str, Any]:
    """Which model each role uses, its client's state, whether the two
    models stay loaded together, and notes for the Models tab."""
    roles = _require_roles()
    await roles.refresh()  # a writer download takes effect without restart
    return roles.snapshot()


@router.post("/roles")
async def set_role(body: RoleBody) -> Dict[str, Any]:
    """Point a role at a model and persist it. Does not load the model:
    the next request in that role starts it."""
    from metascan.core.vlm_models import REGISTRY

    roles = _require_roles()
    if body.model_id not in REGISTRY:
        raise _error(404, "unknown_model", f"unknown model: {body.model_id}")
    if body.role == "writer":
        from metascan.core.vlm_models import writer_allowed

        if not writer_allowed(body.model_id):
            raise _error(
                422,
                "writer_model_not_allowed",
                f"{body.model_id} cannot be the prompt writer",
            )
    if not await asyncio.to_thread(vlm_model_installed, body.model_id):
        raise _error(409, "model_not_installed", f"{body.model_id} is not installed")
    try:
        await _point_role(roles, body.role, body.model_id)
    except WriterModelNotAllowed:  # pragma: no cover - checked above
        raise _error(
            422,
            "writer_model_not_allowed",
            f"{body.model_id} cannot be the prompt writer",
        )
    return roles.snapshot()


@router.post("/unload")
async def unload(body: ActiveBody) -> Dict[str, Any]:
    """Unload the active VLM and free its VRAM.

    The body's ``model_id`` is required for symmetry with ``/active`` and to
    guard against unloading a model the caller didn't intend (e.g. another
    tab swapped models in between). If ``model_id`` doesn't match the
    currently-loaded model, the request is a no-op.
    """
    general = get_vlm_client()
    if general is None:
        raise HTTPException(status_code=503, detail="vlm client not initialized")
    # Whichever role's client holds the model: the general one first.
    client = next(
        (c for c in [general, *get_all_vlm_clients()] if c.model_id == body.model_id),
        None,
    )
    if client is None:
        return dict(general.snapshot())
    if client is general:
        _cancel_retag_jobs()
    await client.shutdown()
    return dict(client.snapshot())


def _list_paths_for_retag(db: Any, *, force: bool) -> List[str]:
    """SELECT paths whose tag rows are pure clip/both. With force=True also
    include vlm-tagged files."""
    sql = (
        "SELECT DISTINCT file_path FROM indices "
        "WHERE index_type='tag' AND source IN ('clip', 'both')"
    )
    if force:
        sql = "SELECT DISTINCT file_path FROM indices WHERE index_type='tag'"
    with db.lock:
        with db._get_connection() as conn:
            rows = conn.execute(sql).fetchall()
    return [r["file_path"] for r in rows]
