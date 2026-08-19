"""Tests for StoryboardRunner.generate_video: upfront validation, RefPlan-
ordered uploads, anchor handling (keeper / prev_last), seed accounting,
only_failed filtering, and per-panel extraction/upload failure isolation.

Uses a real temp DatabaseManager (FK cascades and the storyboard schema
matter) plus a FakeComfy stub recording upload_file/submit calls -- mirrors
tests/test_storyboard_runner.py's structure for generate().
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from metascan.core.comfy_bindings import apply_overrides, resolve_bindings
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from metascan.core.storyboard_runner import StoryboardError, StoryboardRunner

# ---- fixtures / stubs -------------------------------------------------


def _node(class_type: str, title: str, inputs: dict) -> dict:
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}


def _ref2v_workflow(
    n_ref: int = 0,
    n_audio: int = 0,
    first_frame: bool = False,
    duration: bool = False,
    lora_stack: bool = False,
) -> dict:
    wf = {
        "1": _node("KSamplerAdvanced", "MS_SEED", {"noise_seed": 0}),
        "2": _node("CLIPTextEncode", "MS_POSITIVE", {"text": ""}),
        "3": _node("VHS_VideoCombine", "MS_SAVE", {"filename_prefix": "ms"}),
    }
    if lora_stack:
        wf["stack"] = _node(
            "Power Lora Loader (rgthree)", "MS_LORA_STACK", {"model": ["4", 0]}
        )
    ref_titles = ["MS_REF_IMAGE", "MS_REF_IMAGE_2", "MS_REF_IMAGE_3"]
    for i in range(n_ref):
        wf[f"ref{i}"] = _node("LoadImage", ref_titles[i], {"image": ""})
    audio_titles = ["MS_AUDIO", "MS_AUDIO_2"]
    for i in range(n_audio):
        wf[f"audio{i}"] = _node("LoadAudio", audio_titles[i], {"audio": ""})
    if first_frame:
        wf["ff"] = _node("LoadImage", "MS_FIRST_FRAME", {"image": ""})
    if duration:
        wf["dur"] = _node("PrimitiveFloat", "MS_DURATION", {"value": 0})
    return wf


def _media(path: str) -> Media:
    return Media(
        file_path=Path(path),
        file_size=1,
        width=8,
        height=8,
        format="png",
        created_at=datetime.now(),
        modified_at=datetime.now(),
    )


class FakeComfy:
    """Mirrors the parts of ComfyClient generate_video touches. upload_file
    never reads bytes off disk (unlike the real ComfyClient), so tests
    don't need real files to exist for reference/keeper pictures -- only
    the "voice reference exists on disk" validation needs real files,
    handled per-test via tmp_path.

    ``submit`` mirrors ComfyClient.submit's real behavior of resolving the
    preset's bindings and running ``apply_overrides`` synchronously before
    recording the job -- a stub that skipped this (as this class used to)
    would never surface a BindingError the real client raises, which is
    exactly the bug class this file's binding-integration tests exist to
    catch."""

    def __init__(self, db=None):
        self.db = db
        self.uploaded: list = []  # Path objects passed to upload_file, in order
        self.submitted: list = []  # (preset_id, params, panel_id, output_dir)
        self.applied_graphs: list = []  # apply_overrides() results, submit order
        self._n = 0

    async def upload_file(self, path):
        path = Path(path)
        self.uploaded.append(path)
        self._n += 1
        return f"uploaded-{self._n}-{path.name}"

    async def submit(
        self,
        preset_id,
        params,
        panel_id=None,
        priority=False,
        output_dir=None,
        output_prefix=None,
    ):
        preset = self.db.get_workflow_preset(preset_id)
        workflow = json.loads(preset["workflow_json"])
        bindings = resolve_bindings(workflow, preset["kind"])
        graph = apply_overrides(workflow, bindings, params)  # raises BindingError
        self.applied_graphs.append(graph)
        self.submitted.append((preset_id, params, panel_id, output_dir))
        return int(
            self.db.create_generation_job(
                preset_id,
                params.to_json(),
                panel_id,
                str(output_dir) if output_dir else None,
            )
        )

    async def cancel(self, job_id):
        pass


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


@pytest.fixture
def events():
    return []


@pytest.fixture
def comfy(db):
    return FakeComfy(db)


def make_runner(db, comfy, events, tmp_path, unload_vlm=False) -> StoryboardRunner:
    runner = StoryboardRunner(
        db,
        comfy,
        (lambda: None),
        output_root=tmp_path / "out",
        unload_vlm_during_generation=unload_vlm,
    )
    runner.on_event(lambda ch, ev, data: events.append((ch, ev, data)))
    return runner


def _preset(
    db, n_ref=0, n_audio=0, first_frame=False, duration=False, lora_stack=False
) -> int:
    workflow = _ref2v_workflow(
        n_ref=n_ref,
        n_audio=n_audio,
        first_frame=first_frame,
        duration=duration,
        lora_stack=lora_stack,
    )
    bindings = resolve_bindings(workflow, "ref2v")
    return db.create_workflow_preset(
        f"h3-{n_ref}-{n_audio}-{int(duration)}-{int(lora_stack)}",
        "ref2v",
        json.dumps(workflow),
        bindings.to_json(),
    )


def _storyboard(
    db,
    video_preset_id,
    *,
    video_target="minimax",
    video_mode=None,
    base_seed=1000,
    batch_size=1,
) -> int:
    sb_id = db.create_storyboard(
        name="Vid Board",
        target_model="sd",
        architecture="t2i",
        aspect_ratio="16:9",
        base_seed=base_seed,
        batch_size=batch_size,
    )
    db.update_storyboard(
        sb_id,
        video_target=video_target,
        video_preset_id=video_preset_id,
        video_mode=video_mode,
    )
    return sb_id


def _bare_panel(
    db,
    sb_id,
    *,
    scene_id=None,
    scene_sort_order=0,
    panel_sort_order=0,
    subject_ids=None,
    video_prompt="ALIGNMENT: n/a\nSUBJECT DEFINITIONS: none.\nSUMMARY: a shot.",
    video_anchor=None,
    duration_s=6.0,
) -> "tuple[int, int]":
    """Create (or reuse) a scene + one panel with a valid video_prompt.

    ``subject_ids`` moved to beats -- passing it here creates a single bare
    beat carrying them (needed by refplan/ref-slot tests). Omitting it
    leaves the panel beat-less, which some anchor-validation tests rely on
    (no beat -> no first-beat keeper -> the expected failure)."""
    if scene_id is None:
        scene_id = db.create_scene(sb_id, name="Scene", sort_order=scene_sort_order)
    panel_id = db.create_panel(
        scene_id,
        action="something happens",
        sort_order=panel_sort_order,
        duration_s=duration_s,
    )
    db.update_panel(panel_id, video_prompt=video_prompt, video_anchor=video_anchor)
    if subject_ids:
        db.create_beat(panel_id, action="", sort_order=0, subject_ids=list(subject_ids))
    return scene_id, panel_id


def _video_image(db, panel_id, path, *, selected=True) -> int:
    """Attach a keeper candidate to the panel's first beat (creating a bare
    one if the panel has none yet) -- keeper resolution lives on the
    shot's first beat since keyframes moved to beats."""
    db.save_media(_media(path))
    beats = db.list_beats(panel_id)
    beat_id = (
        beats[0]["id"] if beats else db.create_beat(panel_id, action="", sort_order=0)
    )
    image_id = db.create_beat_image(beat_id, file_path=path, variant_index=0)
    if selected:
        db.select_beat_image(beat_id, image_id)
    return image_id


# ---- validation --------------------------------------------------------


async def test_validation_lists_all_failures(db, comfy, events, tmp_path):
    preset_id = _preset(db)
    sb_id = _storyboard(db, preset_id)

    _, panel_a = _bare_panel(db, sb_id, panel_sort_order=0, video_prompt="")
    scene_id, panel_b = _bare_panel(
        db, sb_id, panel_sort_order=1, video_anchor="keeper"
    )

    runner = make_runner(db, comfy, events, tmp_path)

    with pytest.raises(StoryboardError) as exc_info:
        await runner.generate_video(sb_id)

    message = str(exc_info.value)
    assert f"panel {panel_a}" in message
    assert f"panel {panel_b}" in message
    assert comfy.submitted == []


async def test_keeper_anchor_fails_when_first_beat_has_no_keeper(
    db, comfy, events, tmp_path
):
    """A panel whose first beat exists but has no selected keeper (as
    opposed to no beat at all) must fail validation with a message naming
    the shot's first beat, and must not reach comfy.submit."""
    preset_id = _preset(db, first_frame=True)
    sb_id = _storyboard(db, preset_id)

    _, panel_id = _bare_panel(db, sb_id, video_anchor="keeper")
    db.create_beat(panel_id, action="a shot", sort_order=0)

    runner = make_runner(db, comfy, events, tmp_path)

    with pytest.raises(StoryboardError) as exc_info:
        await runner.generate_video(sb_id)

    message = str(exc_info.value)
    assert f"panel {panel_id}" in message
    assert "first beat" in message
    assert comfy.submitted == []


async def test_ref_slot_arithmetic_validated_upfront(db, comfy, events, tmp_path):
    preset_id = _preset(db, n_ref=1)  # only one MS_REF_IMAGE slot
    sb_id = _storyboard(db, preset_id)

    db.save_media(_media("/pics/subj1.png"))
    db.save_media(_media("/pics/subj2.png"))
    subject_id = db.create_subject(
        sb_id,
        name="Maya",
        description="a woman",
        reference_path="/pics/subj1.png",
        reference_path_2="/pics/subj2.png",  # 2 pictures, only 1 slot
        sort_order=0,
    )
    _, panel_id = _bare_panel(db, sb_id, subject_ids=[subject_id])

    runner = make_runner(db, comfy, events, tmp_path)

    with pytest.raises(StoryboardError) as exc_info:
        await runner.generate_video(sb_id)

    assert f"panel {panel_id}" in str(exc_info.value)
    assert comfy.submitted == []


# ---- uploads / params shape --------------------------------------------


async def test_uploads_follow_refplan_order_and_params_shape(
    db, comfy, events, tmp_path
):
    preset_id = _preset(db, n_ref=3, n_audio=2)
    sb_id = _storyboard(db, preset_id, base_seed=1000)

    voice_path = tmp_path / "maya.wav"
    voice_path.write_bytes(b"fake-audio")

    db.save_media(_media("/pics/subj1.png"))
    db.save_media(_media("/pics/subj2.png"))
    db.save_media(_media("/pics/scene.png"))
    subject_id = db.create_subject(
        sb_id,
        name="Maya",
        description="a woman",
        reference_path="/pics/subj1.png",
        reference_path_2="/pics/subj2.png",
        voice_ref_path=str(voice_path),
        sort_order=0,
    )
    scene_id = db.create_scene(
        sb_id, name="Yard", sort_order=0, reference_path="/pics/scene.png"
    )
    panel_id = db.create_panel(
        scene_id,
        action="Maya speaks",
        sort_order=0,
        duration_s=6.0,
    )
    db.update_panel(panel_id, video_prompt="a compiled prompt")
    beat_ids = db.replace_panel_beats(
        panel_id,
        [
            {
                "duration_s": 6.0,
                "action": "Maya speaks",
                "camera_motion": None,
                "camera_amplitude": None,
                "camera_speed": None,
                "is_cut": 0,
                "subject_ids": [subject_id],
                "dialog": [
                    {
                        "subject_id": subject_id,
                        "voice": None,
                        "delivery": "soft",
                        "language": "English",
                        "text": "Hello.",
                    }
                ],
                "sound": None,
            }
        ],
    )

    # One already-ingested image on the first beat (committed=1) + one
    # still-pending job (queued) -- exercises variant_base = committed +
    # 1*pending.
    db.save_media(_media("/pics/existing.mp4"))
    db.create_beat_image(beat_ids[0], file_path="/pics/existing.mp4", variant_index=0)
    db.create_generation_job(preset_id, "{}", panel_id=panel_id)

    runner = make_runner(db, comfy, events, tmp_path)
    result = await runner.generate_video(sb_id)

    assert result["skipped"] == []
    assert len(result["jobs"]) == 1

    # Upload order: Picture 1 (subj1), Picture 2 (subj2), Picture 3 (scene),
    # then Audio 1 (voice ref) -- refplan order, pictures before audio.
    assert [p.name for p in comfy.uploaded] == [
        "subj1.png",
        "subj2.png",
        "scene.png",
        "maya.wav",
    ]

    preset_id_arg, params, panel_id_arg, output_dir = comfy.submitted[0]
    assert panel_id_arg == panel_id
    assert params.ref_images == [
        "uploaded-1-subj1.png",
        "uploaded-2-subj2.png",
        "uploaded-3-scene.png",
    ]
    assert params.audio_refs == ["uploaded-4-maya.wav"]
    assert params.positive == "a compiled prompt"
    assert params.batch_size == 1

    from metascan.core.storyboard_brief import beat_seed

    # beat_sort_order is hardcoded 0 in generate_video's seed call pending
    # real beat_sort_order plumbing (TODO in storyboard_runner.py).
    assert params.seed == beat_seed(1000, 0, 0, 1 + 1 * 1)


async def test_generate_video_carries_video_loras_through(db, comfy, events, tmp_path):
    preset_id = _preset(db, lora_stack=True)
    sb_id = _storyboard(db, preset_id)
    _, panel_id = _bare_panel(db, sb_id)
    video_loras = [{"name": "motion.safetensors", "strength": 0.8}]
    db.update_panel(panel_id, video_loras=video_loras)
    # image_loras must NOT leak into video jobs
    db.update_panel(
        panel_id, image_loras=[{"name": "style.safetensors", "strength": 1.0}]
    )

    runner = make_runner(db, comfy, events, tmp_path)
    result = await runner.generate_video(sb_id)

    assert result["skipped"] == []
    _, params, _, _ = comfy.submitted[0]
    assert params.loras == video_loras


async def test_generate_video_loras_without_stack_is_validation_failure(
    db, comfy, events, tmp_path
):
    preset_id = _preset(db)
    sb_id = _storyboard(db, preset_id)
    _, panel_id = _bare_panel(db, sb_id)
    db.update_panel(
        panel_id, video_loras=[{"name": "motion.safetensors", "strength": 1.0}]
    )

    runner = make_runner(db, comfy, events, tmp_path)
    with pytest.raises(StoryboardError, match="MS_LORA_STACK"):
        await runner.generate_video(sb_id)

    assert comfy.submitted == []


# ---- anchors -------------------------------------------------------------


async def test_keeper_anchor_uploads_selected_still(db, comfy, events, tmp_path):
    preset_id = _preset(db, first_frame=True)
    sb_id = _storyboard(db, preset_id)

    _, panel_id = _bare_panel(db, sb_id, video_anchor="keeper")
    _video_image(db, panel_id, "/pics/keeper.png", selected=True)

    runner = make_runner(db, comfy, events, tmp_path)
    result = await runner.generate_video(sb_id)

    assert result["skipped"] == []
    assert len(result["jobs"]) == 1
    assert any(p.name == "keeper.png" for p in comfy.uploaded)
    _, params, _, _ = comfy.submitted[0]
    assert params.first_frame == "uploaded-1-keeper.png"


async def test_prev_last_uses_extracted_frame(db, comfy, events, tmp_path, monkeypatch):
    preset_id = _preset(db, first_frame=True)
    sb_id = _storyboard(db, preset_id)

    scene_id, panel0 = _bare_panel(db, sb_id, panel_sort_order=0)
    _video_image(db, panel0, "/pics/prev.mp4", selected=True)

    _, panel1 = _bare_panel(
        db, sb_id, scene_id=scene_id, panel_sort_order=1, video_anchor="prev_last"
    )

    calls = []

    def fake_extract_last_frame(video_path, out_png):
        calls.append((Path(video_path), Path(out_png)))
        out_png.parent.mkdir(parents=True, exist_ok=True)
        out_png.write_bytes(b"stub-png")

    monkeypatch.setattr(
        "metascan.core.storyboard_runner.extract_last_frame", fake_extract_last_frame
    )

    runner = make_runner(db, comfy, events, tmp_path)
    result = await runner.generate_video(sb_id, panel_ids=[panel1])

    assert result["skipped"] == []
    assert len(result["jobs"]) == 1
    assert len(calls) == 1
    assert calls[0][0] == Path("/pics/prev.mp4")
    _, params, panel_id_arg, _ = comfy.submitted[0]
    assert panel_id_arg == panel1
    assert params.first_frame is not None


async def test_prev_last_crosses_scene_boundary(
    db, comfy, events, tmp_path, monkeypatch
):
    """Board order for 'prev_last' is scene sort_order then panel
    sort_order across the WHOLE board, not scene-local -- the "previous
    panel" of the first panel in scene 1 is the last panel of scene 0, not
    None."""
    preset_id = _preset(db, first_frame=True)
    sb_id = _storyboard(db, preset_id)

    scene0_id, panel_a = _bare_panel(db, sb_id, scene_sort_order=0, panel_sort_order=0)
    _video_image(db, panel_a, "/pics/scene0-last.mp4", selected=True)

    scene1_id, panel_b = _bare_panel(
        db, sb_id, scene_sort_order=1, panel_sort_order=0, video_anchor="prev_last"
    )
    assert scene1_id != scene0_id

    calls = []

    def fake_extract_last_frame(video_path, out_png):
        calls.append((Path(video_path), Path(out_png)))
        out_png.parent.mkdir(parents=True, exist_ok=True)
        out_png.write_bytes(b"stub-png")

    monkeypatch.setattr(
        "metascan.core.storyboard_runner.extract_last_frame", fake_extract_last_frame
    )

    runner = make_runner(db, comfy, events, tmp_path)
    result = await runner.generate_video(sb_id, panel_ids=[panel_b])

    assert result["skipped"] == []
    assert len(result["jobs"]) == 1
    assert len(calls) == 1
    assert calls[0][0] == Path("/pics/scene0-last.mp4")


# ---- only_failed -----------------------------------------------------


async def test_only_failed_filters(db, comfy, events, tmp_path):
    preset_id = _preset(db)
    sb_id = _storyboard(db, preset_id)

    scene_id, panel0 = _bare_panel(db, sb_id, panel_sort_order=0)
    _, panel1 = _bare_panel(db, sb_id, scene_id=scene_id, panel_sort_order=1)

    j0 = db.create_generation_job(preset_id, "{}", panel_id=panel0)
    db.update_generation_job(j0, state="failed")
    j1 = db.create_generation_job(preset_id, "{}", panel_id=panel1)
    db.update_generation_job(j1, state="done")

    runner = make_runner(db, comfy, events, tmp_path)
    result = await runner.generate_video(sb_id, only_failed=True)

    assert [pid for _, _, pid, _ in comfy.submitted] == [panel0]
    assert result["jobs"]


# ---- per-panel failure isolation ---------------------------------------


def _node_by_title(graph: dict, title: str) -> dict:
    for node in graph.values():
        if isinstance(node, dict) and (node.get("_meta") or {}).get("title") == title:
            return node
    raise AssertionError(f"no node titled {title!r} in graph")


# ---- binding integration (real apply_overrides, not stubbed) -----------
#
# generate_video ALWAYS sets duration_s (panels.duration_s is NOT NULL) and
# always sets first_frame when an anchor is used, but a minimal spec-legal
# ref2v preset (only MS_POSITIVE/MS_SEED/MS_SAVE) has neither MS_DURATION
# nor MS_FIRST_FRAME. Since FakeComfy.submit now runs the REAL
# resolve_bindings + apply_overrides (see FakeComfy's docstring), these
# tests exercise the exact BindingError the real ComfyClient.submit raises
# -- this class of bug must never hide behind a stub again.


async def test_minimal_preset_no_binding_error_duration_omitted(
    db, comfy, events, tmp_path
):
    """A minimal spec-legal ref2v preset (only the 3 required titles) has
    no MS_DURATION node. generate_video must not crash with a BindingError
    on a plain (non-anchor) panel, and must not ask apply_overrides to
    bind a duration this preset can't accept."""
    preset_id = _preset(db)  # no ref/audio/first_frame/duration bindings
    sb_id = _storyboard(db, preset_id)
    _, panel_id = _bare_panel(db, sb_id, duration_s=6.0)

    runner = make_runner(db, comfy, events, tmp_path)
    result = await runner.generate_video(sb_id)

    assert result["skipped"] == []
    assert len(result["jobs"]) == 1
    assert len(comfy.applied_graphs) == 1  # apply_overrides succeeded

    _, params, panel_id_arg, _ = comfy.submitted[0]
    assert panel_id_arg == panel_id
    assert params.duration_s is None
    assert params.first_frame is None


async def test_preset_with_duration_binding_writes_duration(
    db, comfy, events, tmp_path
):
    """A preset that DOES have MS_DURATION gets the panel's duration_s
    both on the submitted params and written into the rendered graph."""
    preset_id = _preset(db, duration=True)
    sb_id = _storyboard(db, preset_id)
    _, panel_id = _bare_panel(db, sb_id, duration_s=6.0)

    runner = make_runner(db, comfy, events, tmp_path)
    result = await runner.generate_video(sb_id)

    assert result["skipped"] == []
    assert len(result["jobs"]) == 1

    _, params, panel_id_arg, _ = comfy.submitted[0]
    assert panel_id_arg == panel_id
    assert params.duration_s == 6.0

    graph = comfy.applied_graphs[0]
    dur_node = _node_by_title(graph, "MS_DURATION")
    assert dur_node["inputs"]["value"] == 6.0


async def test_anchor_without_first_frame_binding_fails_upfront(
    db, comfy, events, tmp_path
):
    """A preset with no MS_FIRST_FRAME node, targeted by a panel with an
    anchor set, must fail upfront validation (naming the panel) rather
    than reach comfy.submit and blow up with a raw BindingError."""
    preset_id = _preset(db)  # no MS_FIRST_FRAME binding
    sb_id = _storyboard(db, preset_id)
    _, panel_id = _bare_panel(db, sb_id, video_anchor="keeper")
    _video_image(db, panel_id, "/pics/keeper.png", selected=True)

    runner = make_runner(db, comfy, events, tmp_path)

    with pytest.raises(StoryboardError) as exc_info:
        await runner.generate_video(sb_id)

    message = str(exc_info.value)
    assert f"panel {panel_id}" in message
    assert "MS_FIRST_FRAME" in message
    assert comfy.submitted == []
    assert comfy.applied_graphs == []


async def test_extraction_failure_isolates_panel(
    db, comfy, events, tmp_path, monkeypatch
):
    preset_id = _preset(db, first_frame=True)
    sb_id = _storyboard(db, preset_id)

    scene_id, panel0 = _bare_panel(db, sb_id, panel_sort_order=0)
    _video_image(db, panel0, "/pics/prev.mp4", selected=True)

    _, panel1 = _bare_panel(
        db, sb_id, scene_id=scene_id, panel_sort_order=1, video_anchor="prev_last"
    )
    _, panel2 = _bare_panel(db, sb_id, scene_id=scene_id, panel_sort_order=2)

    def failing_extract_last_frame(video_path, out_png):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(
        "metascan.core.storyboard_runner.extract_last_frame",
        failing_extract_last_frame,
    )

    runner = make_runner(db, comfy, events, tmp_path)
    result = await runner.generate_video(sb_id, panel_ids=[panel1, panel2])

    assert result["skipped"] == [{"panel_id": panel1, "error": "ffmpeg exploded"}]
    assert [pid for _, _, pid, _ in comfy.submitted] == [panel2]
    assert len(result["jobs"]) == 1
