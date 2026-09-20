"""Image-to-video runner: VLM prompt expansion + ComfyUI submission +
job-output ingest for the i2v flow.

Layering mirrors StoryboardRunner: this is the only module that knows
about i2v_videos; comfy_client stays a generic job driver. Correlation
flows one way -- generate() passes i2v_source_path into
ComfyClient.submit, and handle_job_event ingests job_outputs for jobs
whose row carries it (storyboard jobs carry panel_id/beat_id instead
and are ignored here, and vice versa).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from metascan.core.comfy_bindings import GenerationParams, resolve_bindings
from metascan.core.i2v_compiler import (
    assemble_i2v_prompt,
    build_i2v_user_prompt,
    i2v_dims,
    i2v_grammar,
    i2v_max_tokens,
    lint_i2v_prompt,
    validate_i2v_beats,
)
from metascan.core.i2v_output import I2vOutputError, resolve_output_target
from metascan.core.prompt_store import get_prompt_store
from metascan.core.vlm_select import pick_vlm_model
from metascan.utils.path_utils import to_native_path, to_posix_path

logger = logging.getLogger(__name__)

EventCb = Callable[[str, str, Dict[str, Any]], None]


def render_seconds(
    started_at: Optional[str], finished_at: Optional[str]
) -> Optional[float]:
    """Wall-clock seconds a job spent rendering, from the job row's ISO
    stamps. ``job_outputs`` fires before the job is marked done, so a
    missing ``finished_at`` means "now". ``started_at`` is stamped when
    metascan hands the prompt to ComfyUI, so time spent behind another
    in-flight prompt inside ComfyUI is included. None when the start is
    absent/unparseable or the span is negative."""
    if not started_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        end = (
            datetime.fromisoformat(finished_at)
            if finished_at
            else datetime.now(timezone.utc)
        )
    except (TypeError, ValueError):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    span = (end - start).total_seconds()
    return round(span, 1) if span >= 0 else None


class I2vRequestError(RuntimeError):
    """Caller error -- the route answers 400 with the message."""


class I2vUnavailableError(RuntimeError):
    """The VLM subsystem is not running -- the route answers 503."""


class I2vRunner:
    def __init__(
        self,
        db: Any,
        comfy: Any,
        get_vlm: Callable[[], Any],
        output_root: Path,
    ) -> None:
        self.db = db
        self.comfy = comfy
        self.get_vlm = get_vlm
        self.output_root = Path(output_root)
        self._on_event: List[EventCb] = []
        self._ingest_tasks: Set["asyncio.Task[None]"] = set()
        # quality/idea are dialog metadata, not GenerationParams fields;
        # remembered per job until ingest. In-memory only: after a server
        # restart an ingested row simply has NULL quality/idea.
        self._job_meta: Dict[int, Dict[str, Any]] = {}
        # Last filename number handed out (epoch seconds). Two submits in
        # the same second must not share one -- see _next_output_number.
        self._last_output_number = 0

    def on_event(self, cb: EventCb) -> None:
        self._on_event.append(cb)

    def _emit(self, channel: str, event: str, data: Dict[str, Any]) -> None:
        for cb in self._on_event:
            try:
                cb(channel, event, data)
            except Exception:
                logger.warning("i2v on_event callback failed", exc_info=True)

    # ---- prompt expansion (review-only, writes nothing) ----------------

    async def generate_prompt(
        self, source_path: str, idea: str, duration_s: float
    ) -> Tuple[str, List[str]]:
        vlm = self.get_vlm()
        if vlm is None:
            raise I2vUnavailableError("VLM subsystem is not running")
        path = Path(to_native_path(source_path))
        if not vlm.is_image_path(path):
            raise I2vRequestError(f"Not an image: {source_path}")
        if not path.exists():
            raise I2vRequestError(f"File not found: {source_path}")
        model_id = pick_vlm_model(vlm)
        await vlm.ensure_started(model_id)
        raw = await vlm.generate_text(
            system_prompt=get_prompt_store().get("I2V_BEATS_SYSTEM"),
            user_prompt=build_i2v_user_prompt(idea, duration_s),
            image_path=path,
            grammar=i2v_grammar(duration_s),
            temperature=0.6,
            max_tokens=i2v_max_tokens(duration_s),
            timeout=240.0,
        )
        result = validate_i2v_beats(raw)
        text = assemble_i2v_prompt(result)
        return text, lint_i2v_prompt(text, duration_s)

    async def _source_dims(self, src: Path) -> Tuple[int, int]:
        """Source pixel dimensions: the media row first (already scanned,
        and what the dialog shows), falling back to reading the file's
        header for an image that has not been ingested yet."""
        media = await asyncio.to_thread(self.db.get_media, src)
        if media is not None and media.width and media.height:
            return int(media.width), int(media.height)
        try:
            from PIL import Image

            def _probe() -> Tuple[int, int]:
                with Image.open(src) as im:
                    return (int(im.width), int(im.height))

            return await asyncio.to_thread(_probe)
        except Exception as exc:
            raise I2vRequestError(
                f"Cannot determine the dimensions of {src.name}; "
                "rescan the library so its size is known."
            ) from exc

    def _next_output_number(self) -> int:
        """Epoch seconds, bumped past the previous value so numbers are
        strictly increasing within this process. (Across a restart the
        collector's no-clobber tail is the backstop.)"""
        number = max(int(time.time()), self._last_output_number + 1)
        self._last_output_number = number
        return number

    async def _output_placement(
        self, src: Path, output_root: Optional[str], output_prefix: Optional[str]
    ) -> Dict[str, Any]:
        """submit() kwargs placing the clip. No configured root keeps the
        original layout under comfy.output_root; a configured one must
        already exist (a typo must not silently grow a new tree)."""
        if not (output_root or "").strip():
            return {
                "output_dir": self.output_root / "i2v" / src.stem,
                "output_prefix": f"i2v_{src.stem}",
            }
        root = Path(to_native_path(str(output_root).strip()))
        if not await asyncio.to_thread(root.is_dir):
            raise I2vRequestError(
                f"Video output directory does not exist: {root} -- fix it in "
                "Configuration → Video"
            )
        try:
            target = resolve_output_target(
                str(root), output_prefix, datetime.now(), self._next_output_number()
            )
        except I2vOutputError as exc:
            raise I2vRequestError(str(exc)) from exc
        return {"output_dir": target.directory, "output_name": target.stem}

    # ---- generation ------------------------------------------------------

    async def generate(
        self,
        *,
        source_path: str,
        prompt: str,
        duration_s: float,
        quality: str,
        seed: int,
        megapixels: float,
        loras: List[Dict[str, Any]],
        preset_id: int,
        idea: Optional[str] = None,
        steps: Optional[int] = None,
        output_root: Optional[str] = None,
        output_prefix: Optional[str] = None,
    ) -> int:
        preset = await asyncio.to_thread(self.db.get_workflow_preset, preset_id)
        if preset is None:
            raise I2vRequestError(f"Preset {preset_id} does not exist")
        if preset.get("kind") != "ref2v":
            raise I2vRequestError(
                f"Preset '{preset.get('name')}' is kind "
                f"{preset.get('kind')!r}, not a video (ref2v) workflow"
            )
        tgt = preset.get("video_target")
        mode = preset.get("video_mode")
        if (tgt and tgt != "minimax") or (mode and mode != "i2va"):
            raise I2vRequestError(
                f"Preset '{preset.get('name')}' is tagged {tgt}/{mode}; "
                "the i2v flow needs minimax/i2va (or an untagged preset)"
            )
        if not prompt.strip():
            raise I2vRequestError("Prompt is empty")
        if megapixels <= 0:
            raise I2vRequestError(
                f"Megapixel budget must be positive, got {megapixels}"
            )
        if steps is not None and int(steps) <= 0:
            raise I2vRequestError(f"Steps must be positive, got {steps}")
        src = Path(to_native_path(source_path))
        if not src.exists():
            raise I2vRequestError(f"File not found: {source_path}")

        # Output size tracks the source aspect ratio: the image IS the
        # first frame, so any other ratio letterboxes or crops it.
        src_w, src_h = await self._source_dims(src)
        width, height = i2v_dims(src_w, src_h, float(megapixels))

        workflow = json.loads(preset["workflow_json"])
        bindings = resolve_bindings(workflow, preset["kind"])
        if loras and bindings.lora_stack is None:
            raise I2vRequestError(
                "This preset has no MS_LORA_STACK node; clear the lora "
                "list or register a workflow that has one"
            )

        # Steps drive the High quality preset only: the Fast slot is a
        # step-distilled build whose count must stay baked in. A quality
        # preset with no MS_STEPS node keeps its own count rather than
        # failing the submit -- the MS_DURATION precedent.
        apply_steps = (
            steps is not None and quality == "quality" and bindings.steps is not None
        )

        # Resolved before the upload: a bad output config is a caller
        # error and should cost nothing.
        placement = await self._output_placement(src, output_root, output_prefix)

        first_frame = await self.comfy.upload_file(src)
        params = GenerationParams(
            positive=prompt,
            seed=int(seed),
            width=int(width),
            height=int(height),
            batch_size=1,
            first_frame=first_frame,
            loras=list(loras),
            duration_s=(float(duration_s) if bindings.duration is not None else None),
            steps=(int(steps) if apply_steps and steps is not None else None),
        )
        job_id = int(
            await self.comfy.submit(
                preset_id,
                params,
                i2v_source_path=to_posix_path(source_path),
                **placement,
            )
        )
        self._job_meta[job_id] = {"quality": quality, "idea": idea}
        return job_id

    # ---- ingest ------------------------------------------------------------

    def handle_job_event(self, event: str, payload: Dict[str, Any]) -> None:
        """Registered via comfy_client.on_job_event. Sync; never raises."""
        if event != "job_outputs":
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("i2v handle_job_event: no running loop", exc_info=True)
            return
        task = loop.create_task(self._ingest_outputs(payload))
        self._ingest_tasks.add(task)
        task.add_done_callback(self._ingest_tasks.discard)

    async def _ingest_outputs(self, payload: Dict[str, Any]) -> None:
        job_id = payload.get("job_id")
        if job_id is None:
            return
        job = await asyncio.to_thread(self.db.get_generation_job, job_id)
        if job is None or not job.get("i2v_source_path"):
            return
        try:
            params = json.loads(job["params"])
        except (TypeError, ValueError):
            params = {}
        meta = self._job_meta.pop(int(job_id), {})
        inserted: List[str] = []
        for f in payload.get("files") or []:
            posix = to_posix_path(f)
            try:
                await asyncio.to_thread(
                    self.db.create_i2v_video,
                    source_path=job["i2v_source_path"],
                    file_path=posix,
                    prompt_used=params.get("positive"),
                    idea=meta.get("idea"),
                    seed=params.get("seed"),
                    duration_s=params.get("duration_s"),
                    quality=meta.get("quality"),
                    width=params.get("width"),
                    height=params.get("height"),
                    steps=params.get("steps"),
                    render_s=render_seconds(
                        job.get("started_at"), job.get("finished_at")
                    ),
                    preset_id=job.get("preset_id"),
                    comfy_prompt_id=job.get("comfy_prompt_id"),
                )
                inserted.append(posix)
            except Exception:
                logger.warning(
                    "Could not ingest i2v video %s for job %s",
                    f,
                    job_id,
                    exc_info=True,
                )
        if not inserted:
            return
        self._emit(
            "i2v",
            "i2v_videos_changed",
            {
                "source_path": to_native_path(job["i2v_source_path"]),
                "files": [to_native_path(p) for p in inserted],
            },
        )

    async def aclose(self) -> None:
        tasks = [t for t in self._ingest_tasks if not t.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
