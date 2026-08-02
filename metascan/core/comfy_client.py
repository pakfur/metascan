"""Asyncio driver for a ComfyUI server.

Modeled on metascan.core.inference_client.InferenceClient: constructed
once in the FastAPI lifespan, installed as a singleton, and owning all
network state for the subsystem.

This module talks to a ComfyUI that already exists — it never spawns
one. All job bookkeeping lives in the generation_jobs table so state
survives a restart.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

import httpx

from metascan.core.comfy_bindings import (
    Bindings,
    GenerationParams,
    apply_overrides,
    resolve_bindings,
)

logger = logging.getLogger(__name__)


class ComfyError(RuntimeError):
    """A ComfyUI request failed, or was made against unusable state."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ComfyClient:
    def __init__(
        self,
        base_url: str,
        output_root: Path,
        db: Any,
        scanner: Any = None,
        in_flight: int = 2,
        request_timeout_s: float = 30.0,
        client_id: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.output_root = Path(output_root)
        self.db = db
        self.scanner = scanner
        self.in_flight = max(1, int(in_flight))
        self.client_id = client_id or str(uuid4())
        self._http = httpx.AsyncClient(timeout=request_timeout_s)
        # ComfyUI's prompt_id -> our generation_jobs.id. Populated at
        # dispatch and rehydrated on reconnect (Task 7). Exists so the
        # event reader never touches SQLite: ComfyUI emits `progress`
        # many times per second per job.
        self._prompt_to_job: Dict[str, int] = {}

    # ---- lifecycle ---------------------------------------------------

    async def aclose(self) -> None:
        await self._http.aclose()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "base_url": self.base_url,
            "client_id": self.client_id,
            "in_flight": self.in_flight,
        }

    # ---- presets -----------------------------------------------------

    async def register_preset(
        self, name: str, kind: str, workflow: Dict[str, Any]
    ) -> int:
        """Resolve MS_* bindings and persist the preset.

        Raises BindingError (from comfy_bindings) before anything is
        written, so an unusable workflow never reaches the database.
        """
        bindings = resolve_bindings(workflow, kind)
        preset_id = await asyncio.to_thread(
            self.db.create_workflow_preset,
            name,
            kind,
            json.dumps(workflow),
            bindings.to_json(),
        )
        return int(preset_id)

    async def _load_preset(self, preset_id: int) -> tuple:
        row = await asyncio.to_thread(self.db.get_workflow_preset, preset_id)
        if row is None:
            raise ComfyError(f"No workflow preset with id {preset_id}")
        return json.loads(row["workflow_json"]), Bindings.from_json(row["bindings"])

    # ---- submission --------------------------------------------------

    async def submit_now(
        self,
        preset_id: int,
        params: GenerationParams,
        panel_id: Optional[int] = None,
    ) -> int:
        """Bypass the queue and hand a job straight to ComfyUI.

        Returns the generation_jobs row id.
        """
        workflow, bindings = await self._load_preset(preset_id)
        graph = apply_overrides(workflow, bindings, params)

        job_id = int(
            await asyncio.to_thread(
                self.db.create_generation_job,
                preset_id,
                params.to_json(),
                panel_id,
            )
        )
        try:
            resp = await self._http.post(
                f"{self.base_url}/prompt",
                json={"prompt": graph, "client_id": self.client_id},
            )
            resp.raise_for_status()
            prompt_id = resp.json().get("prompt_id")
            if not prompt_id:
                raise ComfyError("ComfyUI accepted the prompt but returned no id")
        except ComfyError:
            await asyncio.to_thread(
                self.db.update_generation_job,
                job_id,
                state="failed",
                error="no prompt_id in response",
                finished_at=_now(),
            )
            raise
        except Exception as exc:
            message = f"ComfyUI at {self.base_url} rejected the job: {exc}"
            logger.warning(message)
            await asyncio.to_thread(
                self.db.update_generation_job,
                job_id,
                state="failed",
                error=message,
                finished_at=_now(),
            )
            raise ComfyError(message) from exc

        self._prompt_to_job[str(prompt_id)] = job_id
        await asyncio.to_thread(
            self.db.update_generation_job,
            job_id,
            state="running",
            comfy_prompt_id=prompt_id,
            started_at=_now(),
        )
        return job_id

    # ---- history -----------------------------------------------------

    async def fetch_history(self, prompt_id: str) -> Dict[str, Any]:
        """Return ComfyUI's history entry for a prompt, or {} if absent."""
        try:
            resp = await self._http.get(f"{self.base_url}/history/{prompt_id}")
            resp.raise_for_status()
        except Exception as exc:
            raise ComfyError(
                f"Could not read history from {self.base_url}: {exc}"
            ) from exc
        payload = resp.json()
        entry = payload.get(prompt_id)
        return entry if isinstance(entry, dict) else {}


__all__ = ["ComfyClient", "ComfyError"]
