"""Writer and general VLM model roles.

Two roles share the llama-server pool:

* ``writer`` -- T2I prompt writing (and its variants) and I2V prompt
  writing. Defaults to ``qwen3vl-8b`` (``WRITER_DEFAULT_MODEL``): the taste
  adapters of a later project are trained on it, so the base-writer
  baseline must be measured with it. Never a model whose spec says
  ``writer_allowed=False`` (``qwen38-27b``).
* ``general`` -- tagging, retag, chat, the prompt playground and
  storyboards. Defaults to the preloaded VLM, else the hardware pick.

Both roles naming the same model share ONE ``VlmClient`` (one
llama-server). Two ids mean two clients, each with its own port, stderr
drainer and supervision -- the ``VlmClient`` rules apply per instance.
Two distinct models stay loaded together only when their
``approx_vram_gb`` fit in the detected VRAM minus ``VRAM_HEADROOM_GB``;
otherwise they take turns: before one spawns, the other is shut down
(after a bounded wait for its in-flight requests). Neither role changes
model -- only which one is resident.

A client no role uses any more is *retired*: it refuses to start again,
so a caller still holding it cannot respawn the old model untracked.

Prompt-writer adapters (LoRA fine-tunes of the writer model) are served
by the writer's llama-server: it spawns with ``--lora <file>`` for every
servable registry adapter whose base model is the writer model, and a
request picks one by position. ``refresh_adapters()`` re-reads the
registry; a client whose running server has a different list is restarted
when it is idle (no request in flight or waiting, no hold, not loading),
otherwise ``restart_pending`` is set and the restart happens after its next
idle moment. A stopped client just spawns with the new list next time.

Config keys: ``models.writer_vlm`` / ``models.general_vlm``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from metascan.core.vlm_client import is_background_vlm_work
from metascan.core.writer_adapters import AdapterFile
from metascan.core.vlm_models import (
    REGISTRY,
    WRITER_DEFAULT_MODEL,
    writer_allowed,
    writer_missing_hint,
)

logger = logging.getLogger(__name__)

Role = Literal["writer", "general"]
ROLES: Tuple[Role, Role] = ("writer", "general")

# VRAM kept free when deciding whether two role models fit together.
VRAM_HEADROOM_GB = 2.0
# How long a swap waits for the outgoing client's in-flight requests.
IDLE_TIMEOUT_S = 30.0
# A client whose last request ended less than this ago counts as busy (a
# tagging batch between two items), so the roles do not swap per item.
SWAP_GRACE_S = 0.5
# How long a swap waits for the other client's model load to finish
# (matches VlmClient.ensure_started's wait for a load in progress).
LOAD_TIMEOUT_S = 600.0
# Background work (tag drain, retag) only takes the GPU back from the other
# role once that role has been idle this long: a writer between two
# Generate Prompt clicks, or between captions, is not evicted.
BACKGROUND_GRACE_S = 30.0
# Poll interval of a background swap's wait.
_BACKGROUND_POLL_S = 0.05

NOTE_TAKE_TURNS = (
    "writer and tagging models take turns — they don't fit in VRAM together"
)
NOTE_WRITER_FALLS_BACK = "writer falls back to general"

_RESIDENT_STATES = ("spawning", "loading", "ready")


class WriterModelNotAllowed(ValueError):
    """The model may not serve the writer role (``writer_allowed=False``)."""


@dataclass(frozen=True)
class AdapterSlot:
    """An adapter the writer client loads: ``position`` is its index in the
    ``--lora`` list the writer's next spawn uses."""

    adapter_id: int
    position: int
    path: Path


# Poll interval of a deferred adapter restart's wait for an idle client.
_ADAPTER_RESTART_POLL_S = 0.05


@dataclass(frozen=True)
class RoleModels:
    writer: Optional[str]
    general: Optional[str]
    # The writer is unset and uses the general model because the writer
    # default is not installed.
    writer_falls_back: bool = False
    # The writer's model is not installed (consumers fall back / refuse).
    writer_missing: bool = False


def _writer_for(
    writer_cfg: Optional[str],
    general: Optional[str],
    installed: Callable[[str], bool],
) -> Tuple[str, bool]:
    """(writer model, falls back to general) for a valid-or-None writer_cfg."""
    if writer_cfg is not None:
        return writer_cfg, False
    if installed(WRITER_DEFAULT_MODEL):
        return WRITER_DEFAULT_MODEL, False
    if general is not None and writer_allowed(general):
        return general, True
    return WRITER_DEFAULT_MODEL, False


def _valid_writer_cfg(writer_cfg: Optional[str]) -> Optional[str]:
    if writer_cfg is None:
        return None
    if writer_cfg in REGISTRY and writer_allowed(writer_cfg):
        return writer_cfg
    logger.warning(
        "Ignoring models.writer_vlm %r: not a model the prompt writer can use",
        writer_cfg,
    )
    return None


def resolve_role_models(
    writer_cfg: Optional[str],
    general_cfg: Optional[str],
    *,
    installed: Callable[[str], bool],
    general_default: Callable[[], Optional[str]],
) -> RoleModels:
    """Turn the configured ids (``None`` = unset) into the models to use.

    A configured id the registry does not know, or a configured writer the
    writer may not use, is ignored with a warning and the default applies.
    Writer default: ``WRITER_DEFAULT_MODEL`` when installed, else the
    general model when the writer may use it, else ``WRITER_DEFAULT_MODEL``
    anyway (flagged missing: T2I falls back, I2V says to download it).
    """
    general = general_cfg if general_cfg in REGISTRY else None
    if general_cfg is not None and general is None:
        logger.warning("Ignoring unknown models.general_vlm %r", general_cfg)
    if general is None:
        general = general_default()
    writer, falls_back = _writer_for(_valid_writer_cfg(writer_cfg), general, installed)
    return RoleModels(
        writer=writer,
        general=general,
        writer_falls_back=falls_back,
        writer_missing=not installed(writer),
    )


def vram_budget_gb(report: Any) -> Optional[float]:
    """VRAM the role models may share: CUDA VRAM, Apple unified memory
    (``ram_gb``) on MPS, None (unknown / CPU only) otherwise."""
    cuda = getattr(report, "cuda", None)
    if cuda is not None:
        return float(cuda.vram_gb)
    if getattr(report, "mps", False) and getattr(report, "ram_gb", None):
        return float(report.ram_gb)
    return None


def default_general_model() -> Optional[str]:
    """The hardware pick for the general role: the installed
    recommendation, else the bare recommendation (None on cpu_only)."""
    from backend.services.scan_dispatch import (
        preferred_vlm_model_id,
        recommended_vlm_model_id,
    )
    from metascan.core.hardware import detect_hardware

    report = detect_hardware()
    return preferred_vlm_model_id(report) or recommended_vlm_model_id(report)


def persist_role_model(role: Role, model_id: str) -> None:
    """Write ``models.<role>_vlm`` to config.json, keeping every other key."""
    from backend import config as app_config

    cfg = app_config.load_app_config()
    cfg.setdefault("models", {})[f"{role}_vlm"] = model_id
    app_config.save_app_config(cfg)


class VlmRoles:
    """Maps each role to a client; equal model ids share one client.

    ``writer=None`` means the writer is unset: it follows the default rule
    (``WRITER_DEFAULT_MODEL`` when installed, else the general model) and
    ``refresh()`` re-applies the rule after a download or a general change.
    """

    def __init__(
        self,
        *,
        writer: Optional[str],
        general: Optional[str],
        client_factory: Callable[[], Any],
        save_model: Optional[Callable[[Role, str], None]] = None,
        installed: Optional[Callable[[str], bool]] = None,
        vram_gb: Optional[float] = None,
        idle_timeout_s: float = IDLE_TIMEOUT_S,
        swap_grace_s: float = SWAP_GRACE_S,
        load_timeout_s: float = LOAD_TIMEOUT_S,
        background_grace_s: float = BACKGROUND_GRACE_S,
        adapter_source: Optional[Callable[[], Sequence[AdapterFile]]] = None,
    ) -> None:
        self._factory = client_factory
        # Reads the servable adapters off the registry (sync: DB + disk);
        # None means no adapters are served.
        self._adapter_source = adapter_source
        self._adapter_files: List[AdapterFile] = []
        # Adapters being deleted: never loaded again by this process, even
        # while their registry row survives a failed file removal.
        self._withdrawn: Set[int] = set()
        self._restart_task: Optional["asyncio.Task[None]"] = None
        self._save = save_model
        self._installed: Callable[[str], bool] = installed or (lambda _mid: True)
        self._vram_gb = vram_gb
        self._idle_timeout_s = idle_timeout_s
        self._swap_grace_s = swap_grace_s
        self._load_timeout_s = load_timeout_s
        self._background_grace_s = background_grace_s
        self._writer_cfg: Optional[str] = writer
        self._models: Dict[Role, Optional[str]] = {"writer": None, "general": general}
        self._models["writer"] = self._effective_writer()
        if self._writer_cfg is None and self.writer_missing:
            logger.warning(
                "%s; the general model %s cannot write prompts",
                writer_missing_hint(str(self._models["writer"])),
                general,
            )
        self._clients: Dict[Optional[str], Any] = {}
        self._lock = asyncio.Lock()
        self._residency_lock = asyncio.Lock()
        self._rebuild()

    @classmethod
    def from_config(
        cls,
        config: Dict[str, Any],
        *,
        client_factory: Callable[[], Any],
        save_model: Optional[Callable[[Role, str], None]] = persist_role_model,
        adapter_source: Optional[Callable[[], Sequence[AdapterFile]]] = None,
    ) -> "VlmRoles":
        """Build from the app config (sync: probes hardware and disk).

        An unset general role takes the first VLM in
        ``models.preload_at_startup`` -- before roles that was the loaded
        model ``pick_vlm_model`` chose -- else the hardware pick. A failing
        hardware probe leaves it unset (and the VRAM unknown) rather than
        failing startup."""
        from backend.config import get_models_config, get_vlm_roles_config
        from metascan.core.vlm_select import vlm_model_installed

        preloaded: List[str] = [
            str(mid)
            for mid in get_models_config(config)["preload_at_startup"]
            if mid in REGISTRY
        ]

        def _general_default() -> Optional[str]:
            if preloaded:
                return preloaded[0]
            try:
                return default_general_model()
            except Exception:
                logger.exception("Could not pick a default general VLM model")
                return None

        raw = get_vlm_roles_config(config)
        general = raw["general_vlm"] if raw["general_vlm"] in REGISTRY else None
        if raw["general_vlm"] is not None and general is None:
            logger.warning("Ignoring unknown models.general_vlm %r", raw["general_vlm"])
        if general is None:
            general = _general_default()

        vram: Optional[float] = None
        try:
            from metascan.core.hardware import detect_hardware

            vram = vram_budget_gb(detect_hardware())
        except Exception:
            logger.exception("Could not read the VRAM budget for the VLM roles")

        return cls(
            writer=_valid_writer_cfg(raw["writer_vlm"]),
            general=general,
            client_factory=client_factory,
            save_model=save_model,
            installed=vlm_model_installed,
            vram_gb=vram,
            adapter_source=adapter_source,
        )

    # ---- lookups -------------------------------------------------------

    def client(self, role: Role) -> Any:
        return self._clients[self._models[role]]

    def model_id(self, role: Role) -> Optional[str]:
        return self._models[role]

    @property
    def shared(self) -> bool:
        return self.client("writer") is self.client("general")

    def _effective_writer(self) -> Optional[str]:
        writer, _ = _writer_for(
            self._writer_cfg, self._models["general"], self._installed
        )
        return writer

    @property
    def writer_falls_back(self) -> bool:
        """Computed from the current state: unset writer, default missing,
        general used instead."""
        _, falls_back = _writer_for(
            self._writer_cfg, self._models["general"], self._installed
        )
        return falls_back

    @property
    def writer_missing(self) -> bool:
        mid = self._models["writer"]
        return mid is not None and not self._installed(mid)

    @property
    def co_resident(self) -> bool:
        """True when both role models may stay loaded at once: one shared
        model, a role with no model, or both fit in VRAM with headroom."""
        if self.shared:
            return True
        writer, general = self._models["writer"], self._models["general"]
        if writer is None or general is None:
            return True
        if self._vram_gb is None:
            return False
        need = REGISTRY[writer].approx_vram_gb + REGISTRY[general].approx_vram_gb
        return need <= self._vram_gb - VRAM_HEADROOM_GB

    def distinct_clients(self) -> List[Any]:
        """Each client once, writer first."""
        out: List[Any] = []
        for role in ROLES:
            c = self.client(role)
            if not any(c is seen for seen in out):
                out.append(c)
        return out

    def roles_of(self, client: Any) -> List[Role]:
        return [role for role in ROLES if self.client(role) is client]

    def notes(self) -> List[str]:
        out: List[str] = []
        if self.writer_missing:
            out.append(writer_missing_hint(str(self._models["writer"])))
        if self.writer_falls_back:
            out.append(NOTE_WRITER_FALLS_BACK)
        if not self.co_resident:
            out.append(NOTE_TAKE_TURNS)
        return out

    def snapshot(self) -> Dict[str, Any]:
        def _row(role: Role) -> Dict[str, Any]:
            c = self.client(role)
            return {
                "model_id": self._models[role],
                "state": getattr(c, "state", None),
                "loaded_model_id": getattr(c, "model_id", None),
            }

        return {
            "writer": _row("writer"),
            "general": _row("general"),
            "shared": self.shared,
            "co_resident": self.co_resident,
            "writer_falls_back": self.writer_falls_back,
            "writer_missing": self.writer_missing,
            "notes": self.notes(),
        }

    # ---- residency -----------------------------------------------------

    async def _wait_for_background_turn(self, other: Any, client: Any) -> None:
        """A background spawn's wait: until ``other`` is idle for
        background work (``VlmClient.busy_for_background``: nothing in
        flight or waiting, no hold, idle for the background grace) or no
        longer resident -- no time cap, so background work never
        force-evicts the other role. If an interactive caller starts
        waiting for ``client`` meanwhile, the wait becomes the bounded
        interactive one from that moment."""
        busy = getattr(other, "busy_for_background", None)
        if busy is None:  # a duck-typed client: wait for in-flight work
            await other.wait_idle(self._idle_timeout_s, grace=self._swap_grace_s)
            return
        deadline: Optional[float] = None
        loop = asyncio.get_running_loop()
        while getattr(other, "state", None) in _RESIDENT_STATES and busy(
            self._background_grace_s
        ):
            if deadline is None and getattr(client, "interactive_waiters", 0) > 0:
                deadline = loop.time() + self._idle_timeout_s
            if deadline is not None and loop.time() >= deadline:
                logger.warning(
                    "VLM %s still busy after %.0fs; shutting it down to make "
                    "room for %s",
                    other.model_id,
                    self._idle_timeout_s,
                    client.preferred_model_id,
                )
                return
            await asyncio.sleep(_BACKGROUND_POLL_S)

    async def _wait_for_interactive_turn(self, other: Any, client: Any) -> None:
        """An interactive spawn's wait: bounded (``idle_timeout_s``) for
        ``other``'s requests to end, cut short as soon as all ``other`` is
        doing is background work (preempted: retried after the eviction)."""
        preemptible = getattr(other, "background_only", None)
        if preemptible is None:  # a duck-typed client
            idle = await other.wait_idle(self._idle_timeout_s, grace=self._swap_grace_s)
        else:
            idle = False
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._idle_timeout_s
            while True:
                if preemptible():
                    logger.info(
                        "VLM %s is only doing background work; preempting it " "for %s",
                        other.model_id,
                        client.preferred_model_id,
                    )
                    return
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                slice_s = min(_BACKGROUND_POLL_S, remaining)
                if await other.wait_idle(slice_s, grace=self._swap_grace_s):
                    idle = True
                    break
        if not idle:
            logger.warning(
                "VLM %s still busy after %.0fs; shutting it down to make room "
                "for %s",
                other.model_id,
                self._idle_timeout_s,
                client.preferred_model_id,
            )

    async def _make_room(self, client: Any) -> None:
        """``before_spawn`` hook: when the two role models do not fit
        together, shut the other client down before ``client`` spawns.

        An interactive spawn (prompt writing, chat, playground, storyboard)
        waits (bounded) for the other client's in-flight requests, then
        evicts it -- at once when all it is doing is background work, which
        is retried after an eviction. A background spawn (``background_vlm_
        work``: the tag drain, retag) never force-evicts: it waits, with no
        time cap, until the other client is idle for background work."""
        if self.co_resident:
            return
        background = is_background_vlm_work()
        async with self._residency_lock:
            for other in self.distinct_clients():
                if other is client:
                    continue
                if getattr(other, "state", None) not in _RESIDENT_STATES:
                    continue
                # An explicit shutdown (ComfyUI unload, /unload) landing
                # during the waits below wins: it is re-checked afterwards.
                stops_before = getattr(other, "_explicit_stops", None)
                # Never evict a load in progress: wait for it (bounded by
                # the load timeout); the swap grace then lets the requests
                # that waited for it run before it can go.
                if getattr(other, "state", None) in ("spawning", "loading"):
                    if not await other.wait_loaded(self._load_timeout_s):
                        logger.warning(
                            "VLM %s still loading after %.0fs; evicting it",
                            other.model_id,
                            self._load_timeout_s,
                        )
                if background:
                    await self._wait_for_background_turn(other, client)
                else:
                    await self._wait_for_interactive_turn(other, client)
                if getattr(other, "state", None) not in _RESIDENT_STATES:
                    # Stopped (or failed) meanwhile -- nothing to evict, and
                    # evict() must not mark it swapped_out behind an
                    # explicit unload's back.
                    continue
                if getattr(other, "_explicit_stops", None) != stops_before:
                    # Explicitly unloaded and started again while we waited:
                    # a deliberate load, leave it alone (co-resident for
                    # now) rather than undo it behind the caller's back.
                    logger.warning(
                        "VLM %s was explicitly reloaded while %s waited for "
                        "it; not evicting it",
                        other.model_id,
                        client.preferred_model_id,
                    )
                    continue
                logger.info(
                    "VLM roles take turns: unloading %s for %s",
                    other.model_id,
                    client.preferred_model_id,
                )
                # evict(): its next request brings the model back.
                await other.evict()

    # ---- changes -------------------------------------------------------

    def _rebuild(self) -> List[Any]:
        """Align the clients with the role models; retire and return the
        clients no role uses any more (the caller shuts them down)."""
        needed = [self._models[r] for r in ROLES]
        stale = [c for mid, c in self._clients.items() if mid not in needed]
        self._clients = {mid: c for mid, c in self._clients.items() if mid in needed}
        for mid in needed:
            if mid not in self._clients:
                c = self._factory()
                c.preferred_model_id = mid
                c.retired = False
                c.before_spawn = self._make_room
                self._clients[mid] = c
        for c in stale:
            c.retired = True
        self._apply_loras()
        return stale

    # ---- writer adapters -----------------------------------------------

    def _writer_adapter_files(self) -> List[AdapterFile]:
        """The adapters the writer loads: servable, built on the writer's
        model, not being deleted; in registry id order."""
        writer = self._models["writer"]
        return [
            f
            for f in self._adapter_files
            if f.base_model == writer and f.adapter_id not in self._withdrawn
        ]

    def _apply_loras(self) -> None:
        """Give each client the adapter list its next spawn should load:
        the writer's adapters for the writer client (shared or not), none
        for a general-only client."""
        writer_paths = tuple(f.path for f in self._writer_adapter_files())
        writer_client = self._clients.get(self._models["writer"])
        for c in self._clients.values():
            c.lora_paths = writer_paths if c is writer_client else ()

    def writer_adapters(self) -> List[AdapterSlot]:
        """The writer's adapters with their ``--lora`` positions."""
        return [
            AdapterSlot(f.adapter_id, position, f.path)
            for position, f in enumerate(self._writer_adapter_files())
        ]

    def adapter_path(self, adapter_id: int) -> Optional[Path]:
        """The GGUF of ``adapter_id`` when the writer serves it, else None
        (not servable, not on disk, another base model, being deleted)."""
        for slot in self.writer_adapters():
            if slot.adapter_id == adapter_id:
                return slot.path
        return None

    @property
    def restart_pending(self) -> bool:
        """True while a client waits to go idle for an adapter restart."""
        return self._restart_task is not None and not self._restart_task.done()

    def _needs_adapter_restart(self, client: Any) -> bool:
        return bool(getattr(client, "loras_stale", False)) and (
            getattr(client, "state", None) in _RESIDENT_STATES
        )

    def _idle_for_restart(self, client: Any) -> bool:
        """Nothing in flight or waiting, no hold (a T2I Random batch's
        prompting phase), not loading, and past the swap grace."""
        busy = getattr(client, "busy_for_background", None)
        if busy is None:
            return int(getattr(client, "in_flight", 0)) == 0
        return not busy(self._swap_grace_s)

    async def _restart_idle_stale(self) -> bool:
        """Restart every idle client whose server has a stale adapter list.
        True when some client still waits (busy)."""
        waiting = False
        for c in self.distinct_clients():
            if not self._needs_adapter_restart(c):
                continue
            if self._idle_for_restart(c):
                try:
                    await c.restart_for_loras()
                except Exception:
                    logger.exception("Restarting a VLM for its adapters raised")
            else:
                waiting = True
        return waiting

    async def _deferred_restart(self) -> None:
        while await self._restart_idle_stale():
            await asyncio.sleep(_ADAPTER_RESTART_POLL_S)

    async def refresh_adapters(self) -> None:
        """Re-read the adapter registry and apply it: an idle writer whose
        server has a different list is restarted now (it comes back with
        the new list on its next request), a busy one after its next idle
        moment (``restart_pending``), a stopped one at its next start."""
        if self._adapter_source is not None:
            try:
                files = list(await asyncio.to_thread(self._adapter_source))
            except Exception:
                logger.exception("Could not read the writer adapter registry")
                files = list(self._adapter_files)
            self._adapter_files = files
            # A withdrawn adapter the registry no longer lists was deleted:
            # nothing left to keep out.
            listed = {f.adapter_id for f in files}
            self._withdrawn &= listed
        self._apply_loras()
        if await self._restart_idle_stale() and not self.restart_pending:
            self._restart_task = asyncio.create_task(
                self._deferred_restart(), name="vlm-adapter-restart"
            )

    async def withdraw_adapter(self, adapter_id: int) -> bool:
        """Stop serving ``adapter_id`` (it is being deleted): drop it from
        the writer's list and restart the writer without it when idle. True
        when no running server still has its file loaded (on Windows the
        file can only be removed then)."""
        self._withdrawn.add(int(adapter_id))
        await self.refresh_adapters()
        paths = {f.path for f in self._adapter_files if f.adapter_id == adapter_id}
        return not any(
            p in tuple(getattr(c, "loaded_lora_paths", ()))
            and getattr(c, "state", None) in _RESIDENT_STATES
            for c in self.distinct_clients()
            for p in paths
        )

    async def _shutdown_stale(self, stale: List[Any]) -> None:
        for c in stale:
            try:
                await c.shutdown()
            except Exception:
                logger.exception("Shutting down a replaced VLM client raised")

    def _check(self, role: Role, model_id: str) -> None:
        if role not in ROLES:
            raise ValueError(f"unknown role: {role}")
        if model_id not in REGISTRY:
            raise KeyError(model_id)
        if role == "writer" and not writer_allowed(model_id):
            raise WriterModelNotAllowed(model_id)

    def _point(self, role: Role, model_id: str) -> List[Any]:
        if role == "writer":
            self._writer_cfg = model_id
        else:
            self._models["general"] = model_id
        self._models["writer"] = self._effective_writer()
        return self._rebuild()

    async def set_model(self, role: Role, model_id: str) -> None:
        """Point ``role`` at ``model_id``, persist it, and rebuild the
        clients so equal ids share one. A client no role needs any more is
        retired and shut down. An unset writer follows the default rule.
        Raises KeyError for an unknown id and WriterModelNotAllowed for a
        model the writer may not use."""
        self._check(role, model_id)
        async with self._lock:
            if self._save is not None:
                await asyncio.to_thread(self._save, role, model_id)
            stale = self._point(role, model_id)
        await self._shutdown_stale(stale)

    async def activate(
        self,
        role: Role,
        model_id: str,
        swap: Callable[[Any], Awaitable[None]],
    ) -> None:
        """Point ``role`` at ``model_id`` and run ``swap(client)`` (load
        it); persist only when that succeeds, else roll the role back."""
        self._check(role, model_id)
        async with self._lock:
            before = (self._writer_cfg, dict(self._models))
            stale = self._point(role, model_id)
        await self._shutdown_stale(stale)
        try:
            await swap(self.client(role))
        except BaseException:
            async with self._lock:
                self._writer_cfg, models = before
                self._models = models
                stale = self._rebuild()
            await self._shutdown_stale(stale)
            raise
        if self._save is not None:
            await asyncio.to_thread(self._save, role, model_id)

    async def refresh(self) -> None:
        """Re-apply the unset-writer rule (e.g. after the writer default
        was downloaded) without a restart."""
        async with self._lock:
            writer = self._effective_writer()
            if writer == self._models["writer"]:
                return
            self._models["writer"] = writer
            stale = self._rebuild()
        await self._shutdown_stale(stale)

    async def shutdown_all(self) -> None:
        """Shut each distinct client down once."""
        task = self._restart_task
        if task is not None and not task.done():
            task.cancel()
        for c in self.distinct_clients():
            try:
                await c.shutdown()
            except Exception:
                logger.exception("VLM client shutdown raised")
