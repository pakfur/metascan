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
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from metascan.core.comfy_bindings import Bindings, GenerationParams
from metascan.core import h3_compiler as h3
from metascan.core import storyboard_story as story
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
from metascan.core.video_targets import shot_cap
from metascan.core.vlm_client import VlmError
from metascan.core.vlm_models import REGISTRY
from metascan.utils.path_utils import to_native_path, to_posix_path

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
        # Guards the folder-ensure read-check-create-update sequence in
        # generate(). Two concurrent generate() calls for the same
        # storyboard would otherwise both observe folder_id is None, both
        # create a folder (distinct uuid4 ids, so no DB collision), and
        # race the update_storyboard(folder_id=...) write -- the loser's
        # folder becomes a permanent orphan nothing ever references again.
        # Folder creation is rare (once per storyboard's lifetime), so one
        # runner-wide lock is fine; a per-storyboard lock map would be
        # over-engineering for this.
        self._folder_lock = asyncio.Lock()

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
        from metascan.core.vlm_select import VlmSelectError, pick_vlm_model

        try:
            return pick_vlm_model(vlm)
        except VlmSelectError as e:
            raise StoryboardError(str(e)) from e

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

    # ---- compose (story engine) --------------------------------------

    async def check_compose_gates(
        self,
        storyboard_id: int,
        stages: Sequence[str],
        scene_ids: Optional[List[int]],
        confirm: bool,
    ) -> None:
        """Synchronous-shaped gate check so the route can 409 before the
        202 fire-and-forget task starts. Small TOCTOU window accepted.

        Every raise stamps ``exc._compose_stage`` with the stage the gate
        failure actually concerns, so a direct ``compose_story(...,
        stages=(...))`` call (which never enters the per-stage loop in
        ``_compose_locked`` before this gate fires) still reports the
        correct stage on the ``story_error`` event -- not the "outline"
        fallback ``compose_story`` initializes ``stage`` to.
        """
        unknown = set(stages) - set(story.STAGES)
        if unknown:
            raise StoryboardError(f"unknown stages: {', '.join(sorted(unknown))}")
        tree = await asyncio.to_thread(self.db.get_storyboard_tree, storyboard_id)
        if tree is None:
            raise StoryboardError(f"no storyboard with id {storyboard_id}")
        if "outline" in stages and not (tree.get("source_text") or "").strip():
            exc: StoryboardError = StoryboardError(
                "storyboard has no premise (source_text)"
            )
            exc._compose_stage = "outline"  # type: ignore[attr-defined]
            raise exc
        if confirm:
            return
        if "outline" in stages and tree.get("outline"):
            exc = ConfirmRequiredError(
                "storyboard already has an outline — pass confirm=true"
            )
            exc._compose_stage = "outline"  # type: ignore[attr-defined]
            raise exc
        if "scenes" in stages and tree["scenes"]:
            exc = ConfirmRequiredError(
                "storyboard already has scenes; rebuilding destroys panel "
                "identity — pass confirm=true"
            )
            exc._compose_stage = "scenes"  # type: ignore[attr-defined]
            raise exc
        if "shots" in stages:
            targets = [
                s for s in tree["scenes"] if scene_ids is None or s["id"] in scene_ids
            ]
            if any(s["panels"] for s in targets):
                exc = ConfirmRequiredError(
                    "target scenes already have shots — pass confirm=true"
                )
                exc._compose_stage = "shots"  # type: ignore[attr-defined]
                raise exc

    async def compose_story(
        self,
        storyboard_id: int,
        *,
        stages: Sequence[str] = story.STAGES,
        scene_ids: Optional[List[int]] = None,
        panel_ids: Optional[List[int]] = None,
        confirm: bool = False,
    ) -> Dict[str, int]:
        stage = "outline"
        try:
            counts = await self._compose_locked(
                storyboard_id,
                stages=stages,
                scene_ids=scene_ids,
                panel_ids=panel_ids,
                confirm=confirm,
            )
        except Exception as exc:
            stage = getattr(exc, "_compose_stage", stage)
            self._emit(
                "storyboard",
                "story_error",
                {"storyboard_id": storyboard_id, "stage": stage, "error": str(exc)},
            )
            raise
        self._emit(
            "storyboard",
            "story_complete",
            {"storyboard_id": storyboard_id, "counts": counts},
        )
        return counts

    async def _compose_locked(
        self,
        storyboard_id: int,
        *,
        stages: Sequence[str],
        scene_ids: Optional[List[int]],
        panel_ids: Optional[List[int]],
        confirm: bool,
    ) -> Dict[str, int]:
        vlm = self.get_vlm()
        if vlm is None:
            raise StoryboardError("no VLM client — composing requires a VLM")
        await self.check_compose_gates(storyboard_id, stages, scene_ids, confirm)

        run_stages = [s for s in story.STAGES if s in set(stages)]
        counts: Dict[str, int] = {}
        current = "outline"
        try:
            async with self._synth_lock:
                model_id = self._pick_vlm_model(vlm)
                await vlm.ensure_started(model_id)
                from metascan.core.vlm_models import REGISTRY

                slots = 2
                spec = REGISTRY.get(model_id)
                if spec is not None:
                    slots = spec.parallel_slots
                sem = asyncio.Semaphore(slots)

                for current in run_stages:
                    n = await self._run_stage(
                        current, vlm, sem, storyboard_id, scene_ids, panel_ids
                    )
                    counts[current] = n
                    self._emit(
                        "storyboard",
                        "story_stage_complete",
                        {"storyboard_id": storyboard_id, "stage": current},
                    )
        except Exception as exc:
            exc._compose_stage = current  # type: ignore[attr-defined]
            raise
        return counts

    async def _run_stage(
        self,
        stage: str,
        vlm: Any,
        sem: asyncio.Semaphore,
        storyboard_id: int,
        scene_ids: Optional[List[int]],
        panel_ids: Optional[List[int]],
    ) -> int:
        tree = await asyncio.to_thread(self.db.get_storyboard_tree, storyboard_id)
        assert tree is not None
        roster = {s["name"].strip().lower(): int(s["id"]) for s in tree["subjects"]}

        def progress(done: int, total: int) -> None:
            self._emit(
                "storyboard",
                "story_progress",
                {
                    "storyboard_id": storyboard_id,
                    "stage": stage,
                    "done": done,
                    "total": total,
                },
            )

        if stage == "outline":
            progress(0, 1)
            raw = await vlm.generate_text(
                system_prompt=story.STORY_OUTLINE_SYSTEM,
                user_prompt=story.build_outline_user_prompt(
                    tree["source_text"], tree["subjects"]
                ),
                grammar=story.OUTLINE_GRAMMAR,
                temperature=0.7,
                max_tokens=1500,
                timeout=600.0,
            )
            outline = story.validate_outline_response(raw)
            await asyncio.to_thread(
                self.db.update_storyboard,
                storyboard_id,
                outline=json.dumps(outline),
            )
            created = 0
            for i, subj in enumerate(outline["subjects"]):
                if subj["name"].strip().lower() in roster:
                    continue  # user's existing description wins
                await asyncio.to_thread(
                    self.db.create_subject,
                    storyboard_id,
                    name=subj["name"],
                    description=subj["description"],
                    voice=subj.get("voice"),
                    sort_order=len(roster) + created,
                )
                created += 1
            progress(1, 1)
            return 1

        outline_json = tree.get("outline") or ""
        if not outline_json:
            raise StoryboardError(f"stage {stage!r} needs an outline first")

        if stage == "scenes":
            progress(0, 1)
            raw = await vlm.generate_text(
                system_prompt=story.STORY_SCENES_SYSTEM,
                user_prompt=story.build_scenes_user_prompt(outline_json),
                grammar=story.SCENES_GRAMMAR,
                temperature=0.7,
                max_tokens=1200,
                timeout=300.0,
            )
            scenes = story.validate_scenes_response(raw)
            await asyncio.to_thread(
                self.db.replace_storyboard_scenes, storyboard_id, scenes
            )
            progress(1, 1)
            return len(scenes)

        if stage == "shots":
            # spec §10.3: the shots stage's per-shot duration guidance is
            # coupled to the storyboard's video target so H3 (max ~15s per
            # clip) and future dialects with a different cap both steer the
            # LLM toward shots the compiler can actually render as one clip.
            cap = shot_cap(tree.get("video_target"))
            targets = [
                (i, s)
                for i, s in enumerate(tree["scenes"])
                if scene_ids is None or s["id"] in scene_ids
            ]
            total = len(targets)
            done = 0
            lock = asyncio.Lock()
            made = 0

            async def _shots_for(idx: int, scene: Dict[str, Any]) -> int:
                nonlocal done
                prev_name = tree["scenes"][idx - 1]["name"] if idx > 0 else None
                next_name = (
                    tree["scenes"][idx + 1]["name"]
                    if idx + 1 < len(tree["scenes"])
                    else None
                )
                async with sem:
                    raw = await vlm.generate_text(
                        system_prompt=story.STORY_SHOTS_SYSTEM,
                        user_prompt=story.build_shots_user_prompt(
                            outline_json,
                            scene,
                            tree["subjects"],
                            prev_name,
                            next_name,
                            max_shot_s=cap,
                        ),
                        grammar=story.SHOTS_GRAMMAR,
                        temperature=0.6,
                        max_tokens=800,
                        timeout=300.0,
                    )
                panels, warnings = story.validate_shots_response(raw, roster)
                for w in warnings:
                    logger.warning("compose shots (%s): %s", scene["name"], w)
                await asyncio.to_thread(
                    self.db.replace_scene_panels, scene["id"], panels
                )
                async with lock:
                    done += 1
                    progress(done, total)
                return len(panels)

            results = await asyncio.gather(*(_shots_for(i, s) for i, s in targets))
            made = sum(results)
            return made

        # stage == "beats"
        work = [
            (scene, panel)
            for scene in tree["scenes"]
            for panel in scene["panels"]
            if panel_ids is None or panel["id"] in panel_ids
        ]
        logline = ""
        try:
            logline = json.loads(outline_json).get("logline", "")
        except (TypeError, ValueError):
            pass
        total = len(work)
        done = 0
        lock = asyncio.Lock()

        async def _beats_for(scene: Dict[str, Any], panel: Dict[str, Any]) -> int:
            nonlocal done
            subjects = [s for s in tree["subjects"] if s["id"] in panel["subject_ids"]]
            async with sem:
                raw = await vlm.generate_text(
                    system_prompt=story.STORY_BEATS_SYSTEM,
                    user_prompt=story.build_beats_user_prompt(
                        logline, scene, panel, subjects
                    ),
                    grammar=story.BEATS_GRAMMAR,
                    temperature=0.6,
                    max_tokens=900,
                    timeout=300.0,
                )
            beats = story.validate_beats_response(raw, roster)
            story.rescale_beat_durations(beats, float(panel.get("duration_s") or 12.0))
            await asyncio.to_thread(self.db.replace_panel_beats, panel["id"], beats)
            async with lock:
                done += 1
                progress(done, total)
            return len(beats)

        results = await asyncio.gather(*(_beats_for(s, p) for s, p in work))
        return sum(results)

    # ---- compile (H3 video-prompt) --------------------------------------

    async def compile_video(
        self,
        storyboard_id: int,
        panel_ids: Optional[List[int]] = None,
        force: bool = False,
        deterministic_only: bool = False,
    ) -> Dict[str, int]:
        """Compile per-panel H3 video prompts, emitting a terminal WS event.

        Mirrors ``synthesize``'s contract: fires ``compile_complete`` with
        the same counts this returns on success, or ``compile_error`` (and
        re-raises, for a direct caller not going through a fire-and-forget
        route) on failure. Unlike ``compose_story``, compile has no
        sub-stages, so the error event stamps nothing beyond the message.
        """
        try:
            counts = await self._compile_locked(
                storyboard_id, panel_ids, force, deterministic_only
            )
        except Exception as exc:
            self._emit(
                "storyboard",
                "compile_error",
                {"storyboard_id": storyboard_id, "error": str(exc)},
            )
            raise
        self._emit(
            "storyboard",
            "compile_complete",
            {"storyboard_id": storyboard_id, **counts},
        )
        return counts

    async def _compile_locked(
        self,
        storyboard_id: int,
        panel_ids: Optional[List[int]],
        force: bool,
        deterministic_only: bool,
    ) -> Dict[str, int]:
        async with self._synth_lock:
            tree = await asyncio.to_thread(self.db.get_storyboard_tree, storyboard_id)
            if tree is None:
                raise StoryboardError(f"no storyboard with id {storyboard_id}")
            if tree.get("video_target") != "minimax":
                raise StoryboardError(
                    f"storyboard {storyboard_id} has video_target "
                    f"{tree.get('video_target')!r} -- the H3 compiler only "
                    "supports 'minimax'"
                )

            vlm = None if deterministic_only else self.get_vlm()
            if vlm is None and not deterministic_only:
                raise StoryboardError(
                    "no VLM client — compiling requires a VLM unless "
                    "deterministic_only=True"
                )

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
                if panel["video_prompt_locked"] and not forced_override:
                    skipped_locked += 1
                    continue
                work.append((scene, panel))

            counts: Dict[str, int] = {
                "compiled": 0,
                "failed": 0,
                "skipped_locked": skipped_locked,
            }
            total = len(work)
            if total == 0:
                return counts

            mode = tree.get("video_mode") or "ref2va"

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
                # Beat dialog subject_ids are picked from the whole board
                # roster (BeatForm's picker + validate_beats_response), not
                # just this panel's subject_ids -- so an off-panel speaker
                # still needs a real <Subject N> definition/refplan entry.
                # Union the two sets, preserving tree["subjects"]' board
                # sort_order (already ORDER BY sort_order, id from the DB).
                dialog_subject_ids = {
                    d.get("subject_id")
                    for beat in (panel.get("beats") or [])
                    for d in (beat.get("dialog") or [])
                    if d.get("subject_id") is not None
                }
                wanted_ids = set(panel["subject_ids"]) | dialog_subject_ids
                subjects = [s for s in tree["subjects"] if s["id"] in wanted_ids]
                try:
                    if sem is not None:
                        async with sem:
                            doc, issues = await self._compile_panel(
                                vlm,
                                tree,
                                scene,
                                panel,
                                subjects,
                                mode,
                                deterministic_only,
                            )
                    else:
                        doc, issues = await self._compile_panel(
                            vlm, tree, scene, panel, subjects, mode, deterministic_only
                        )
                except Exception as exc:
                    if not isinstance(
                        exc, (VlmError, TimeoutError, RuntimeError, h3.H3Error)
                    ):
                        logger.warning(
                            "compile_video: panel %s failed with an unexpected "
                            "error: %s",
                            panel["id"],
                            exc,
                        )
                    await asyncio.to_thread(
                        self.db.update_panel,
                        panel["id"],
                        video_prompt_source="compiled",
                        video_prompt_locked=0,
                        video_prompt_warnings=json.dumps([str(exc)]),
                    )
                    async with progress_lock:
                        done += 1
                        counts["failed"] += 1
                        done_snapshot = done
                    self._emit(
                        "storyboard",
                        "compile_progress",
                        {
                            "storyboard_id": storyboard_id,
                            "panel_id": panel["id"],
                            "done": done_snapshot,
                            "total": total,
                        },
                    )
                    return

                has_error = any(i.severity == "error" for i in issues)
                await asyncio.to_thread(
                    self.db.update_panel,
                    panel["id"],
                    video_prompt=doc,
                    video_prompt_source="compiled",
                    video_prompt_locked=0,
                    video_prompt_warnings=json.dumps([i.message for i in issues]),
                )
                async with progress_lock:
                    done += 1
                    counts["failed" if has_error else "compiled"] += 1
                    done_snapshot = done
                self._emit(
                    "storyboard",
                    "compile_progress",
                    {
                        "storyboard_id": storyboard_id,
                        "panel_id": panel["id"],
                        "done": done_snapshot,
                        "total": total,
                    },
                )

            await asyncio.gather(*(_one(scene, panel) for scene, panel in work))
            return counts

    async def _compile_panel(
        self,
        vlm: Optional[Any],
        tree: Dict[str, Any],
        scene: Dict[str, Any],
        panel: Dict[str, Any],
        subjects: List[Dict[str, Any]],
        mode: str,
        deterministic_only: bool,
    ) -> Tuple[str, List[h3.LintError]]:
        """Compile one panel's H3 video prompt: scaffold -> body/sound LLM
        calls (or a deterministic fallback) -> assemble -> lint -> (one
        retry on lint errors).

        This is the ONLY place in the runner that imports/uses
        ``h3_compiler`` symbols (spec §8 dialect seam) -- a future ``ltx``
        dialect adds one dispatch branch inside this method; the rest of
        the runner, the API, storage, and UI stay untouched.
        """
        beats: List[Dict[str, Any]] = list(panel.get("beats") or [])
        if not beats:
            # No beats yet: fall back to a single beat-equivalent group
            # built straight from the panel's own action/duration_s.
            beats = [
                {
                    "duration_s": panel.get("duration_s") or 12.0,
                    "action": panel.get("action") or "",
                    "camera_motion": None,
                    "camera_amplitude": None,
                    "camera_speed": None,
                    "is_cut": 0,
                    "sound": None,
                    "dialog": [],
                }
            ]

        refplan = h3.assign_reference_labels(subjects, scene)
        speakers = h3.assign_speakers(beats, subjects, refplan)
        duration_s = float(
            panel.get("duration_s")
            or sum(float(b.get("duration_s") or 0) for b in beats)
            or 12.0
        )
        timeline = h3.compute_timeline(beats, duration_s, mode, refplan)

        scaffold_panel = dict(panel)
        scaffold_panel["beats"] = beats
        scaffold = h3.build_scaffold(
            scaffold_panel, scene, tree, subjects, refplan, speakers, timeline
        )

        subject_definitions = h3.render_subject_definitions(refplan, subjects, scene)
        summary = h3.render_summary(refplan, panel, subjects, mode)
        retention_analysis = h3.render_retention_analysis(
            refplan, subjects, scene, timeline
        )
        expect = h3.build_expectations(refplan, speakers, timeline, mode, beats=beats)

        def _fallback_body() -> str:
            style = tree.get("style_block") or "cinematic, live-action"
            lines_by_beat: Dict[int, List[Any]] = {}
            for sl in speakers.lines:
                lines_by_beat.setdefault(sl.beat_index, []).append(sl)
            parts = [f"The target video is in a {style} style."]
            for shot in timeline.shots:
                header = (
                    "[Shot 1]"
                    if shot.number == 1
                    else f"[Shot {shot.number}] At "
                    f"{h3.format_timecode(shot.start_s)}, the shot cuts to"
                )
                sentences: List[str] = []
                for bi in shot.beat_indices:
                    beat = beats[bi] if bi < len(beats) else {}
                    action = beat.get("action") or ""
                    if action:
                        sentences.append(f"{action}.")
                    camera = h3.render_camera(
                        beat.get("camera_motion"),
                        beat.get("camera_amplitude"),
                        beat.get("camera_speed"),
                    )
                    if camera:
                        sentences.append(f"The camera {camera}.")
                    for sl in sorted(
                        lines_by_beat.get(bi, []), key=lambda x: x.line_index
                    ):
                        speaker_part = (
                            f"{sl.subject_label} ({sl.speaker_id})"
                            if sl.subject_label
                            else f"the {sl.voice or 'voice'} ({sl.speaker_id})"
                        )
                        sentences.append(
                            f"{speaker_part} says, <d>[{sl.language}] {sl.text}</d>"
                        )
                parts.append(f"{header} {' '.join(sentences)}".strip())
            return "\n".join(parts)

        def _fallback_sound() -> Tuple[str, str]:
            events = [str(b["sound"]) for b in beats if b.get("sound")]
            if events:
                soundscape = " ".join(f"{e}." for e in events)
            else:
                soundscape = "The scene carries quiet ambient room tone throughout."
            return soundscape, "N/A"

        if deterministic_only or vlm is None:
            detailed_description = _fallback_body()
            overall_soundscape, non_diegetic_music = _fallback_sound()
        else:
            detailed_description = await vlm.generate_text(
                system_prompt=h3.H3_BODY_SYSTEM,
                user_prompt=h3.build_body_user_prompt(scaffold),
                temperature=0.5,
                max_tokens=1200,
                timeout=300.0,
            )
            sound_raw = await vlm.generate_text(
                system_prompt=h3.H3_SOUND_SYSTEM,
                user_prompt=h3.build_sound_user_prompt(beats, scene.get("mood")),
                grammar=h3.SOUND_GRAMMAR,
                temperature=0.4,
                max_tokens=250,
                timeout=120.0,
            )
            sound = h3.validate_sound_response(sound_raw)
            overall_soundscape = sound["overall_soundscape"]
            non_diegetic_music = sound["non_diegetic_music"]

        def _assemble(dd: str) -> str:
            return h3.assemble(
                timeline.alignment_line,
                subject_definitions,
                summary,
                retention_analysis,
                dd,
                overall_soundscape,
                non_diegetic_music,
            )

        doc = _assemble(detailed_description)
        issues = h3.lint_h3_prompt(doc, expect)

        if (
            not deterministic_only
            and vlm is not None
            and any(i.severity == "error" for i in issues)
        ):
            detailed_description = await vlm.generate_text(
                system_prompt=h3.H3_BODY_SYSTEM,
                user_prompt=h3.build_retry_user_prompt(scaffold, issues),
                temperature=0.5,
                max_tokens=1200,
                timeout=300.0,
            )
            doc = _assemble(detailed_description)
            issues = h3.lint_h3_prompt(doc, expect)

        return doc, issues

    # ---- synthesize ----------------------------------------------------

    async def synthesize(
        self,
        storyboard_id: int,
        panel_ids: Optional[List[int]] = None,
        force: bool = False,
    ) -> Dict[str, int]:
        """Compose per-panel prompts, emitting a terminal WS event.

        The route fires this as a fire-and-forget background task (202
        response), so a failure here (e.g. the VLM won't load) would
        otherwise be completely silent to the client. Emits
        ``synthesis_complete`` with the same counts this returns on
        success, or ``synthesis_error`` (and re-raises, for any direct
        caller that isn't going through the fire-and-forget route) on
        failure.
        """
        try:
            counts = await self._synthesize_locked(storyboard_id, panel_ids, force)
        except Exception as exc:
            self._emit(
                "storyboard",
                "synthesis_error",
                {"storyboard_id": storyboard_id, "error": str(exc)},
            )
            raise
        self._emit(
            "storyboard",
            "synthesis_complete",
            {"storyboard_id": storyboard_id, **counts},
        )
        return counts

    async def _synthesize_locked(
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
            async with self._folder_lock:
                # Re-read inside the lock: another generate() call for this
                # storyboard may have created (and published) the folder
                # while this one was waiting to acquire it.
                current = await asyncio.to_thread(self.db.get_storyboard, storyboard_id)
                if current is None:
                    raise StoryboardError(
                        f"storyboard {storyboard_id} vanished during generate"
                    )
                folder_id = current.get("folder_id")
                if folder_id is None:
                    folder = await asyncio.to_thread(
                        self.db.create_folder,
                        str(uuid4()),
                        "manual",
                        f"Storyboard: {tree['name']}",
                    )
                    folder_id = folder["id"] if folder else None
                    await asyncio.to_thread(
                        self.db.update_storyboard,
                        storyboard_id,
                        folder_id=folder_id,
                    )
                    if folder is not None:
                        self._emit("folders", "folder_created", {"folder": folder})
                tree["folder_id"] = folder_id

        if self.unload_vlm_during_generation:
            # Hold the synth lock only around the unload check+call itself
            # -- never across the submit loop below -- so a synthesize()
            # in progress finishes (or at least isn't torn out from under
            # itself) before generate() rips the VLM out from under it.
            # synthesize() acquires the same lock for its whole run, so
            # this simply waits for it to finish rather than racing.
            async with self._synth_lock:
                vlm = self.get_vlm()
                if vlm is not None and vlm.model_id:
                    await vlm.shutdown()

        width, height = bucket_dims(tree["aspect_ratio"], tree["target_model"])
        slug = storyboard_slug(storyboard_id, tree["name"])
        priority = panel_ids is not None and len(panel_ids) == 1

        job_ids: List[int] = []
        for scene, panel in targets:
            committed = await asyncio.to_thread(self.db.count_panel_images, panel["id"])
            # count_panel_images only sees rows already ingested from a
            # finished job. Two generate() calls for the same panel before
            # the first has ingested (e.g. two rerolls back-to-back) would
            # otherwise both read the same committed count and submit an
            # identical seed. Fold in batch_size for every still-pending
            # (queued/running) job against this panel so a second reroll
            # advances the seed even before the first one's outputs land.
            pending_jobs = await asyncio.to_thread(
                self.db.list_generation_jobs,
                states=["queued", "running"],
                panel_ids=[panel["id"]],
                limit=10000,
            )
            variant_base = committed + tree["batch_size"] * len(pending_jobs)
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

        # POSIX-normalized paths, used for every internal DB write
        # (set_media_hidden / create_panel_image / add_folder_items all
        # expect -- and themselves normalize to -- the stored form). Kept
        # separate from the WS-facing list below so a to_native_path
        # conversion never leaks into an FK lookup.
        inserted: List[str] = []
        for i, f in enumerate(payload.get("files") or []):
            posix_path = to_posix_path(f)
            try:
                await asyncio.to_thread(self.db.set_media_hidden, posix_path, True)
                await asyncio.to_thread(
                    self.db.create_panel_image,
                    panel_id,
                    file_path=posix_path,
                    seed=params.get("seed"),
                    variant_index=base + i,
                    prompt_used=params.get("positive"),
                    preset_id=job.get("preset_id"),
                    comfy_prompt_id=job.get("comfy_prompt_id"),
                )
                inserted.append(posix_path)
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

        # The WS payload reflects the SAME normalized values that were
        # inserted, converted with to_native_path -- mirroring get_folder's
        # precedent -- rather than the raw, pre-conversion strings from the
        # comfy job_outputs event.
        self._emit(
            "storyboard",
            "panel_images_changed",
            {
                "storyboard_id": storyboard_id,
                "panel_id": panel_id,
                "files": [to_native_path(p) for p in inserted],
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
            # list_generation_jobs defaults to limit=100 -- a storyboard
            # with more in-flight jobs than that would silently under-cancel.
            # Mirrors ComfyClient._rehydrate_jobs's precedent.
            limit=10000,
        )
        for job in jobs:
            await self.comfy.cancel(job["id"])
        return len(jobs)

    # ---- lifecycle ---------------------------------------------------

    async def aclose(self) -> None:
        if self._ingest_tasks:
            await asyncio.gather(*self._ingest_tasks, return_exceptions=True)


__all__ = ["StoryboardRunner", "StoryboardError", "ConfirmRequiredError"]
