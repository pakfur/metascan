"""Writer and general VLM model roles.

Two roles share the llama-server pool:

* ``writer`` -- T2I prompt writing (and its variants) and I2V prompt
  writing. Defaults to ``qwen3vl-8b`` (``WRITER_DEFAULT_MODEL``): the taste
  adapters of a later project are trained on it, so the base-writer
  baseline must be measured with it. Never a model whose spec says
  ``writer_allowed=False`` (``qwen38-27b``).
* ``general`` -- tagging, retag, chat, the prompt playground and
  storyboards. Defaults to today's pick: the hardware recommendation.

Both roles naming the same model share ONE ``VlmClient`` (one
llama-server). Two ids mean two clients, each with its own port, stderr
drainer and supervision -- the ``VlmClient`` rules apply per instance.
Config keys: ``models.writer_vlm`` / ``models.general_vlm``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from metascan.core.vlm_models import REGISTRY, WRITER_DEFAULT_MODEL, writer_allowed

logger = logging.getLogger(__name__)

Role = Literal["writer", "general"]
ROLES: Tuple[Role, Role] = ("writer", "general")


class WriterModelNotAllowed(ValueError):
    """The model may not serve the writer role (``writer_allowed=False``)."""


@dataclass(frozen=True)
class RoleModels:
    writer: Optional[str]
    general: Optional[str]
    # True when the writer default (qwen3vl-8b) is not installed and the
    # writer uses the general model instead.
    writer_falls_back: bool = False


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
    general model (when the writer may use it), else ``WRITER_DEFAULT_MODEL``
    anyway -- the consumers' installed check then falls back without a VLM.
    """
    general = general_cfg if general_cfg in REGISTRY else None
    if general_cfg is not None and general is None:
        logger.warning("Ignoring unknown models.general_vlm %r", general_cfg)
    if general is None:
        general = general_default()

    if writer_cfg is not None:
        if writer_cfg in REGISTRY and writer_allowed(writer_cfg):
            return RoleModels(writer=writer_cfg, general=general)
        logger.warning(
            "Ignoring models.writer_vlm %r: not a model the prompt writer can use",
            writer_cfg,
        )

    if installed(WRITER_DEFAULT_MODEL):
        return RoleModels(writer=WRITER_DEFAULT_MODEL, general=general)
    if general is not None and writer_allowed(general):
        logger.warning(
            "Prompt writer model %s is not installed; the writer falls back "
            "to the general model %s",
            WRITER_DEFAULT_MODEL,
            general,
        )
        return RoleModels(writer=general, general=general, writer_falls_back=True)
    return RoleModels(writer=WRITER_DEFAULT_MODEL, general=general)


def default_general_model() -> Optional[str]:
    """Today's pick for the general role: the installed hardware
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
    """Maps each role to a client; equal model ids share one client."""

    def __init__(
        self,
        *,
        writer: Optional[str],
        general: Optional[str],
        client_factory: Callable[[], Any],
        save_model: Optional[Callable[[Role, str], None]] = None,
        writer_falls_back: bool = False,
    ) -> None:
        self._factory = client_factory
        self._save = save_model
        self._models: Dict[Role, Optional[str]] = {
            "writer": writer,
            "general": general,
        }
        self._writer_falls_back = writer_falls_back
        self._clients: Dict[Optional[str], Any] = {}
        self._lock = asyncio.Lock()
        self._rebuild()

    @classmethod
    def from_config(
        cls,
        config: Dict[str, Any],
        *,
        client_factory: Callable[[], Any],
        save_model: Optional[Callable[[Role, str], None]] = persist_role_model,
    ) -> "VlmRoles":
        """Build from the app config (sync: probes hardware and disk).

        An unset general role takes the first VLM in
        ``models.preload_at_startup`` -- before roles that was the loaded
        model ``pick_vlm_model`` chose -- else the hardware pick. A failing
        hardware probe leaves it unset rather than failing startup."""
        from backend.config import get_models_config, get_vlm_roles_config
        from metascan.core.vlm_select import vlm_model_installed

        preloaded = [
            mid
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
        models = resolve_role_models(
            raw["writer_vlm"],
            raw["general_vlm"],
            installed=vlm_model_installed,
            general_default=_general_default,
        )
        return cls(
            writer=models.writer,
            general=models.general,
            client_factory=client_factory,
            save_model=save_model,
            writer_falls_back=models.writer_falls_back,
        )

    # ---- lookups -------------------------------------------------------

    def client(self, role: Role) -> Any:
        return self._clients[self._models[role]]

    def model_id(self, role: Role) -> Optional[str]:
        return self._models[role]

    @property
    def shared(self) -> bool:
        return self.client("writer") is self.client("general")

    @property
    def writer_falls_back(self) -> bool:
        return self._writer_falls_back

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
            "writer_falls_back": self._writer_falls_back,
        }

    # ---- changes -------------------------------------------------------

    def _rebuild(self) -> List[Any]:
        """Align the clients with the role models; return the clients no
        role uses any more (the caller shuts them down)."""
        needed = [self._models[r] for r in ROLES]
        stale = [c for mid, c in self._clients.items() if mid not in needed]
        self._clients = {mid: c for mid, c in self._clients.items() if mid in needed}
        for mid in needed:
            if mid not in self._clients:
                c = self._factory()
                c.preferred_model_id = mid
                self._clients[mid] = c
        return stale

    async def set_model(self, role: Role, model_id: str) -> None:
        """Point ``role`` at ``model_id``, persist it, and rebuild the
        clients so equal ids share one. A client no role needs any more is
        shut down. Raises KeyError for an unknown id and
        WriterModelNotAllowed for a model the writer may not use."""
        if role not in ROLES:
            raise ValueError(f"unknown role: {role}")
        if model_id not in REGISTRY:
            raise KeyError(model_id)
        if role == "writer" and not writer_allowed(model_id):
            raise WriterModelNotAllowed(model_id)
        async with self._lock:
            if self._save is not None:
                await asyncio.to_thread(self._save, role, model_id)
            self._models[role] = model_id
            if role == "writer":
                self._writer_falls_back = False
            elif self._writer_falls_back and writer_allowed(model_id):
                # The unset writer keeps following the general model.
                self._models["writer"] = model_id
            stale = self._rebuild()
        for c in stale:
            try:
                await c.shutdown()
            except Exception:
                logger.exception("Shutting down a replaced VLM client raised")

    async def shutdown_all(self) -> None:
        """Shut each distinct client down once."""
        for c in self.distinct_clients():
            try:
                await c.shutdown()
            except Exception:
                logger.exception("VLM client shutdown raised")
