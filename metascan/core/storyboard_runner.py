"""Storyboard orchestration: parse, synthesize, generate, ingest.

Composes VlmClient + ComfyClient + DatabaseManager. Knows nothing about
FastAPI: events go out through on_event callbacks as
(channel, event, data) tuples that the lifespan bridges onto the
multiplexed WebSocket.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from metascan.core.comfy_bindings import Bindings, GenerationParams
from metascan.core.storyboard_brief import (
    bucket_dims,
    compose_brief,
    panel_seed,
    storyboard_slug,
)
from metascan.core.storyboard_parse import (
    PARSE_GRAMMAR,
    PARSE_SYSTEM_PROMPT,
    build_parse_user_prompt,
    validate_parse_response,
)
from metascan.core.storyboard_synthesis import build_render_messages, finalize_prompt
from metascan.core.vlm_client import VlmError
from metascan.core.vlm_models import REGISTRY

logger = logging.getLogger(__name__)

EventCb = Callable[[str, str, Dict[str, Any]], None]


class StoryboardError(RuntimeError):
    """A storyboard operation could not be completed."""


class ConfirmRequiredError(StoryboardError):
    """A destructive operation needs explicit confirmation from the caller."""


class StoryboardRunner:
    """Orchestrates the storyboard lifecycle: parse -> synthesize -> generate.

    ``db`` and ``comfy`` are typed ``Any`` — runtime duck-typing, mirroring
    ``ComfyClient``'s ``db: Any``. ``get_vlm`` is called fresh at every use
    site (never cached) so the caller can swap/tear down the VlmClient
    between calls without this object going stale.
    """

    def __init__(
        self,
        db: Any,
        comfy: Any,
        get_vlm: Callable[[], Optional[Any]],
        output_root: Path,
        unload_vlm_during_generation: bool = True,
    ) -> None:
        self.db = db
        self.comfy = comfy
        self.get_vlm = get_vlm
        self.output_root = Path(output_root)
        self.unload_vlm_during_generation = unload_vlm_during_generation
        self._listeners: List[EventCb] = []
        # Output-ingest tasks spawned by handle_job_event. A bare
        # asyncio.create_task result is only weakly referenced by the event
        # loop and can be garbage-collected mid-flight -- holding a strong
        # reference here (and self-evicting once done) keeps them alive
        # until aclose() drains them. Mirrors ComfyClient._collect_tasks.
        self._ingest_tasks: "set[asyncio.Task[None]]" = set()
        # One synthesis run at a time -- a second concurrent run against
        # the same (or another) storyboard would double-write prompts if
        # interleaved with itself; simplest safe answer is a single lock.
        self._synth_lock = asyncio.Lock()

    # ---- events ----------------------------------------------------------

    def on_event(self, cb: EventCb) -> None:
        self._listeners.append(cb)

    def _emit(self, channel: str, event: str, data: Dict[str, Any]) -> None:
        for cb in list(self._listeners):
            try:
                cb(channel, event, data)
            except Exception:
                logger.debug("storyboard event listener raised", exc_info=True)

    # ---- VLM model selection -----------------------------------------

    def _pick_vlm_model(self, vlm: Any) -> str:
        """Pick a model id to pass to ``vlm.ensure_started``.

        Prefers whatever the client already has loaded (idempotent restart
        of the same model); otherwise picks the first hardware-recommended
        ``qwen3vl-*`` gate.
        """
        model_id = getattr(vlm, "model_id", None)
        if model_id:
            return str(model_id)
        from metascan.core.hardware import detect_hardware, feature_gates

        gates = feature_gates(detect_hardware())
        for mid, gate in gates.items():
            if mid.startswith("qwen3vl-") and gate.recommended:
                return mid
        raise StoryboardError("no VLM model available on this hardware")

    # ---- parse -------------------------------------------------------

    async def parse(
        self, storyboard_id: int, text: str, confirm: bool = False
    ) -> Dict[str, Any]:
        vlm = self.get_vlm()
        if vlm is None:
            raise StoryboardError("no VLM client — parsing requires a VLM")

        tree = await asyncio.to_thread(self.db.get_storyboard_tree, storyboard_id)
        if tree is None:
            raise StoryboardError(f"no storyboard with id {storyboard_id}")
        if tree["scenes"] and not confirm:
            raise ConfirmRequiredError(
                "storyboard already has scenes; re-parsing destroys panel "
                "identity — pass confirm=true"
            )

        model_id = self._pick_vlm_model(vlm)
        await vlm.ensure_started(model_id)
        raw = await vlm.generate_text(
            system_prompt=PARSE_SYSTEM_PROMPT,
            user_prompt=build_parse_user_prompt(text),
            grammar=PARSE_GRAMMAR,
            temperature=0.2,
            max_tokens=4096,
            timeout=600.0,
        )
        # ParseError propagates to the caller (the route maps it to a 4xx).
        parsed = validate_parse_response(raw)

        await asyncio.to_thread(
            self.db.replace_storyboard_structure, storyboard_id, parsed
        )
        await asyncio.to_thread(
            self.db.update_storyboard, storyboard_id, source_text=text
        )
        fresh: Optional[Dict[str, Any]] = await asyncio.to_thread(
            self.db.get_storyboard_tree, storyboard_id
        )
        if fresh is None:
            raise StoryboardError(f"storyboard {storyboard_id} vanished during parse")
        return fresh

    # ---- synthesize ----------------------------------------------------

    async def synthesize(
        self,
        storyboard_id: int,
        panel_ids: Optional[List[int]] = None,
        force: bool = False,
    ) -> Dict[str, int]:
        async with self._synth_lock:
            tree = await asyncio.to_thread(self.db.get_storyboard_tree, storyboard_id)
            if tree is None:
                raise StoryboardError(f"no storyboard with id {storyboard_id}")
            subjects_by_id = {s["id"]: s for s in tree["subjects"]}

            explicit_ids = set(panel_ids) if panel_ids is not None else None
            candidates: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
            for scene in tree["scenes"]:
                for panel in scene["panels"]:
                    if explicit_ids is not None and panel["id"] not in explicit_ids:
                        continue
                    candidates.append((scene, panel))

            work: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
            skipped_locked = 0
            for scene, panel in candidates:
                forced_override = (
                    force and explicit_ids is not None and panel["id"] in explicit_ids
                )
                if panel["prompt_locked"] and not forced_override:
                    skipped_locked += 1
                    continue
                work.append((scene, panel))

            counts: Dict[str, int] = {
                "synthesized": 0,
                "fallback": 0,
                "skipped_locked": skipped_locked,
            }
            total = len(work)
            if total == 0:
                return counts

            vlm = self.get_vlm()
            sem: Optional[asyncio.Semaphore] = None
            if vlm is not None:
                model_id = self._pick_vlm_model(vlm)
                await vlm.ensure_started(model_id)
                slots = REGISTRY[model_id].parallel_slots if model_id in REGISTRY else 2
                sem = asyncio.Semaphore(slots)

            progress_lock = asyncio.Lock()
            done = 0

            async def _one(scene: Dict[str, Any], panel: Dict[str, Any]) -> None:
                nonlocal done
                subjects = [
                    subjects_by_id[sid]
                    for sid in panel["subject_ids"]
                    if sid in subjects_by_id
                ]
                brief = compose_brief(tree, scene, panel, subjects)
                text = brief
                source = "brief"
                if vlm is not None:
                    assert sem is not None
                    system, user = build_render_messages(brief, tree["target_model"])
                    async with sem:
                        try:
                            text = await vlm.generate_text(
                                system_prompt=system,
                                user_prompt=user,
                                temperature=0.6,
                                max_tokens=400,
                                timeout=180.0,
                            )
                            source = "llm"
                        except VlmError:
                            text = brief
                            source = "brief"
                prompt = finalize_prompt(text, tree.get("style_block"))
                await asyncio.to_thread(
                    self.db.update_panel,
                    panel["id"],
                    brief=brief,
                    prompt=prompt,
                    prompt_source=source,
                    prompt_locked=0,
                )
                async with progress_lock:
                    done += 1
                    counts["synthesized" if source == "llm" else "fallback"] += 1
                    done_snapshot = done
                self._emit(
                    "storyboard",
                    "synthesis_progress",
                    {
                        "storyboard_id": storyboard_id,
                        "panel_id": panel["id"],
                        "done": done_snapshot,
                        "total": total,
                        "prompt_source": source,
                    },
                )

            await asyncio.gather(*(_one(scene, panel) for scene, panel in work))
            return counts

    # ---- generate ------------------------------------------------------

    async def generate(
        self,
        storyboard_id: int,
        panel_ids: Optional[List[int]] = None,
        only_failed: bool = False,
    ) -> List[int]:
        tree = await asyncio.to_thread(self.db.get_storyboard_tree, storyboard_id)
        if tree is None:
            raise StoryboardError(f"no storyboard with id {storyboard_id}")
        preset_id = tree.get("preset_id")
        if preset_id is None:
            raise StoryboardError("storyboard has no workflow preset")
        preset = await asyncio.to_thread(self.db.get_workflow_preset, preset_id)
        if preset is None:
            raise StoryboardError(f"no workflow preset with id {preset_id}")
        bindings = Bindings.from_json(preset["bindings"])
        kind = preset["kind"]

        subjects_by_id = {s["id"]: s for s in tree["subjects"]}
        all_panels: List[Tuple[Dict[str, Any], Dict[str, Any]]] = [
            (scene, panel) for scene in tree["scenes"] for panel in scene["panels"]
        ]
        if panel_ids is not None:
            wanted = set(panel_ids)
            all_panels = [(s, p) for s, p in all_panels if p["id"] in wanted]
        targets = [(s, p) for s, p in all_panels if p.get("prompt")]

        if only_failed:
            ids = [p["id"] for _, p in targets]
            latest = await asyncio.to_thread(self.db.latest_jobs_for_panels, ids)
            targets = [
                (s, p)
                for s, p in targets
                if (latest.get(p["id"]) or {}).get("state") == "failed"
            ]

        if not targets:
            return []

        def _primary_subject(panel: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            subject_ids = panel.get("subject_ids") or []
            if not subject_ids:
                return None
            return subjects_by_id.get(subject_ids[0])

        # Validate everything before submitting anything (spec §9: half-run
        # avoidance). A bad binding discovered mid-loop would leave earlier
        # panels already queued in ComfyUI with no way to undo that.
        for scene, panel in targets:
            effective_negative = panel.get("negative") or tree.get("negative")
            if effective_negative is not None and bindings.negative is None:
                raise StoryboardError(
                    f"storyboard {storyboard_id} has a negative prompt but "
                    f"preset {preset_id} has no MS_NEGATIVE node"
                )
            primary = _primary_subject(panel)
            if (
                primary is not None
                and primary.get("lora_name")
                and bindings.lora is None
            ):
                raise StoryboardError(
                    f"panel {panel['id']}'s subject {primary.get('name')!r} has "
                    f"a LoRA but preset {preset_id} has no MS_LORA node"
                )
            if kind == "ref" and (primary is None or not primary.get("reference_path")):
                raise StoryboardError(
                    f"panel {panel['id']} has no subject reference image for "
                    f"a 'ref' kind preset"
                )

        if tree.get("folder_id") is None:
            folder = await asyncio.to_thread(
                self.db.create_folder,
                str(uuid4()),
                "manual",
                f"Storyboard: {tree['name']}",
            )
            folder_id = folder["id"] if folder else None
            await asyncio.to_thread(
                self.db.update_storyboard, storyboard_id, folder_id=folder_id
            )
            tree["folder_id"] = folder_id

        if self.unload_vlm_during_generation:
            vlm = self.get_vlm()
            if vlm is not None and vlm.model_id:
                await vlm.shutdown()

        width, height = bucket_dims(tree["aspect_ratio"], tree["target_model"])
        slug = storyboard_slug(storyboard_id, tree["name"])
        priority = panel_ids is not None and len(panel_ids) == 1

        job_ids: List[int] = []
        for scene, panel in targets:
            variant_base = await asyncio.to_thread(
                self.db.count_panel_images, panel["id"]
            )
            seed = panel_seed(tree["base_seed"], panel["sort_order"], variant_base)
            effective_negative = panel.get("negative") or tree.get("negative")
            primary = _primary_subject(panel)

            ref_name: Optional[str] = None
            if kind == "ref" and primary is not None:
                ref_path = primary.get("reference_path")
                if ref_path:
                    ref_name = await self.comfy.upload_image(Path(ref_path))

            params = GenerationParams(
                positive=panel["prompt"],
                seed=seed,
                width=width,
                height=height,
                batch_size=tree["batch_size"],
                negative=effective_negative,
                lora_name=primary.get("lora_name") if primary is not None else None,
                lora_strength=(
                    primary.get("lora_strength") if primary is not None else None
                ),
                ref_image=ref_name,
            )
            output_dir = (
                self.output_root
                / slug
                / f"scene_{scene['sort_order']:02d}"
                / f"panel_{panel['sort_order']:02d}"
            )
            job_id = await self.comfy.submit(
                preset_id,
                params,
                panel_id=panel["id"],
                priority=priority,
                output_dir=output_dir,
            )
            job_ids.append(job_id)
        return job_ids

    # ---- job-output ingest ----------------------------------------------

    def handle_job_event(self, event: str, payload: Dict[str, Any]) -> None:
        """Registered via ``comfy_client.on_job_event``. Sync; schedules an
        ingest task. Never raises -- mirrors ComfyClient._emit's contract
        that a listener error must not break the event source."""
        if event != "job_outputs":
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("handle_job_event: no running event loop", exc_info=True)
            return
        task = loop.create_task(self._ingest_outputs(payload))
        self._ingest_tasks.add(task)
        task.add_done_callback(self._ingest_tasks.discard)

    async def _ingest_outputs(self, payload: Dict[str, Any]) -> None:
        job_id = payload.get("job_id")
        if job_id is None:
            return
        job = await asyncio.to_thread(self.db.get_generation_job, job_id)
        if job is None or job.get("panel_id") is None:
            return
        panel_id = job["panel_id"]
        panel = await asyncio.to_thread(self.db.get_panel, panel_id)
        if panel is None:
            return

        storyboard_id = await asyncio.to_thread(
            self.db.storyboard_id_for_panel, panel_id
        )
        folder_id: Optional[str] = None
        if storyboard_id is not None:
            storyboard = await asyncio.to_thread(self.db.get_storyboard, storyboard_id)
            if storyboard is not None:
                folder_id = storyboard.get("folder_id")

        try:
            params = json.loads(job["params"])
        except (TypeError, ValueError):
            params = {}
        base = await asyncio.to_thread(self.db.count_panel_images, panel_id)

        inserted: List[str] = []
        for i, f in enumerate(payload.get("files") or []):
            try:
                await asyncio.to_thread(self.db.set_media_hidden, f, True)
                await asyncio.to_thread(
                    self.db.create_panel_image,
                    panel_id,
                    file_path=f,
                    seed=params.get("seed"),
                    variant_index=base + i,
                    prompt_used=params.get("positive"),
                    preset_id=job.get("preset_id"),
                    comfy_prompt_id=job.get("comfy_prompt_id"),
                )
                inserted.append(f)
            except Exception:
                logger.warning(
                    "Could not ingest panel image %s for job %s",
                    f,
                    job_id,
                    exc_info=True,
                )

        if not inserted:
            return

        if folder_id is not None:
            await asyncio.to_thread(self.db.add_folder_items, folder_id, inserted)
            self._emit("folders", "folder_items_changed", {"folder_id": folder_id})

        self._emit(
            "storyboard",
            "panel_images_changed",
            {
                "storyboard_id": storyboard_id,
                "panel_id": panel_id,
                "files": inserted,
            },
        )

    # ---- cancel ----------------------------------------------------------

    async def cancel(self, storyboard_id: int) -> int:
        tree = await asyncio.to_thread(self.db.get_storyboard_tree, storyboard_id)
        if tree is None:
            raise StoryboardError(f"no storyboard with id {storyboard_id}")
        panel_ids = [
            panel["id"] for scene in tree["scenes"] for panel in scene["panels"]
        ]
        if not panel_ids:
            return 0
        jobs = await asyncio.to_thread(
            self.db.list_generation_jobs,
            states=["queued", "running"],
            panel_ids=panel_ids,
        )
        for job in jobs:
            await self.comfy.cancel(job["id"])
        return len(jobs)

    # ---- lifecycle ---------------------------------------------------

    async def aclose(self) -> None:
        if self._ingest_tasks:
            await asyncio.gather(*self._ingest_tasks, return_exceptions=True)


__all__ = ["StoryboardRunner", "StoryboardError", "ConfirmRequiredError"]
