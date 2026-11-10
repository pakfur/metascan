"""The upscale routes, the swap's server rules, and the cache headers a swap
under the same URL depends on."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import media as media_api
from backend.api import upscale as upscale_api
from backend.services.upscale_service import UpscaleService
from metascan.cache.thumbnail import ThumbnailCache
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.scanner import Scanner
from metascan.core.upscale_store import UpscaleError, UpscaleStore
from metascan.utils.path_utils import to_native_path, to_posix_path


class Env:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.db = DatabaseManager(tmp / "db")
        self.thumbs = ThumbnailCache(tmp / "thumbs")
        self.scanner = Scanner(self.db, self.thumbs)
        self.events: List[Any] = []
        self.moved: List[int] = []
        self.kicks: List[int] = []
        self.indexing = False
        self.store = UpscaleStore(
            self.db,
            lambda: tmp / "originals",
            self.scanner.read_technical,
            supported=Scanner.SUPPORTED_EXTENSIONS,
            trash=lambda p: p.unlink(missing_ok=True),
        )

        async def after_move() -> None:
            self.moved.append(1)

        async def kick() -> None:
            self.kicks.append(1)

        self.service = UpscaleService(
            self.db,
            self.store,
            self.thumbs,
            tmp / "data",
            lambda ch, ev, data: self.events.append((ch, ev, data)),
            lambda: self.indexing,
            after_move,
            kick,
        )
        (tmp / "data").mkdir()

    def media(self, name: str = "a.png") -> Path:
        path = self.tmp / "library" / name
        path.parent.mkdir(exist_ok=True)
        Image.new("RGB", (32, 32), "red").save(path)
        self.scanner.ingest_file(path)
        return path

    def candidate(self, source: Path, suffix: str = ".png") -> int:
        path = self.tmp / "staging" / f"{len(self.events)}c{suffix}"
        path.parent.mkdir(exist_ok=True)
        Image.new("RGB", (128, 128), "blue").save(path)
        return self.db.add_upscale_candidate(
            source_path=to_posix_path(source),
            file_path=to_posix_path(path),
            kind="upscale_image",
        )


@pytest.fixture
def env(tmp_path, monkeypatch) -> Any:
    e = Env(tmp_path)
    upscale_api.install(SimpleNamespace(), e.service, None)
    app = FastAPI()
    app.include_router(upscale_api.router)
    e.client = TestClient(app)
    yield e
    upscale_api.install(None, None, None)


def test_state_of_a_file_never_upscaled(env):
    path = env.media()
    candidate = env.candidate(path)
    r = env.client.get("/api/upscale/state", params={"path": str(path)})
    body = r.json()
    assert body["record"] is None
    assert [c["id"] for c in body["candidates"]] == [candidate]
    assert body["candidates"][0]["source_path"] == str(path)


def test_accept_then_state_then_revert(env):
    path = env.media()
    r = env.client.post(f"/api/upscale/candidates/{env.candidate(path)}/accept")
    assert r.status_code == 200 and Path(r.json()["file_path"]) == path
    assert env.moved == [1] and env.kicks == [1]
    assert ("upscale", "media_swapped", {"path": str(path), "new_path": str(path)}) in (
        env.events
    )

    body = env.client.get("/api/upscale/state", params={"path": str(path)}).json()
    assert body["record"]["original_name"] == "a.png"
    assert Path(body["record"]["original_path"]).parent == env.tmp / "originals"

    r = env.client.post("/api/upscale/revert", json={"file_path": str(path)})
    assert r.status_code == 200
    assert env.db.upscale_record(to_posix_path(path)) is None


def test_a_swap_refreshes_the_phash_and_thumbnail(env):
    path = env.media()
    env.client.post(f"/api/upscale/candidates/{env.candidate(path)}/accept")
    with env.db._get_connection() as conn:
        row = conn.execute(
            "SELECT phash FROM media_hashes WHERE file_path = ?",
            (to_posix_path(path),),
        ).fetchone()
    assert row["phash"]
    assert env.thumbs.get_thumbnail_path(path).exists()


def test_a_missing_candidate_is_a_404(env):
    r = env.client.post("/api/upscale/candidates/99/accept")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"


def test_revert_of_a_plain_file_is_a_404(env):
    r = env.client.post("/api/upscale/revert", json={"file_path": str(env.media())})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_upscaled"


def test_discard(env):
    path = env.media()
    candidate = env.candidate(path)
    row = env.db.get_upscale_candidate(candidate)
    r = env.client.delete(f"/api/upscale/candidates/{candidate}")
    assert r.json() == {"status": "deleted"}
    assert env.db.get_upscale_candidate(candidate) is None
    assert not Path(to_native_path(row["file_path"])).exists()
    assert env.client.delete(f"/api/upscale/candidates/{candidate}").status_code == 404


def test_a_swap_during_a_scan_is_refused_and_changes_nothing(env, monkeypatch):
    from backend.api import scan

    path = env.media()
    candidate = env.candidate(path)
    before = path.read_bytes()
    monkeypatch.setattr(scan, "scan_running", lambda: True)
    r = env.client.post(f"/api/upscale/candidates/{candidate}/accept")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "library_busy"
    assert path.read_bytes() == before
    assert env.db.list_upscale_journal() == []
    assert env.db.get_upscale_candidate(candidate) is not None
    assert list((env.tmp / "originals").glob("*")) == []


def test_a_swap_during_an_unfinished_full_clean_is_refused(env):
    from backend.services import taste_snapshot

    path = env.media()
    taste_snapshot.write_full_clean_marker(env.tmp / "data")
    r = env.client.post(f"/api/upscale/candidates/{env.candidate(path)}/accept")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "library_busy"


def test_a_held_gate_times_out_as_busy(env, monkeypatch):
    from backend.api import scan

    monkeypatch.setattr(scan, "MOVE_GATE_WAIT_S", 0.05)
    path = env.media()
    candidate = env.candidate(path)
    assert scan.move_gate.acquire(blocking=False)
    try:
        r = env.client.post(f"/api/upscale/candidates/{candidate}/accept")
    finally:
        scan.move_gate.release()
    assert r.status_code == 409
    assert env.db.list_upscale_journal() == []


def test_a_swap_during_an_originals_move_is_refused(env):
    env.service.originals_moving = lambda: True
    path = env.media()
    r = env.client.post(f"/api/upscale/candidates/{env.candidate(path)}/accept")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "originals_moving"


def test_a_swap_during_a_build_is_embedded_again_after_it(env):
    path = env.media()
    env.indexing = True
    env.client.post(f"/api/upscale/candidates/{env.candidate(path)}/accept")
    assert env.kicks == []
    posix = to_posix_path(path)
    with env.db._get_connection() as conn:
        conn.execute(
            "UPDATE media_hashes SET has_embedding = 1 WHERE file_path = ?", (posix,)
        )
        conn.commit()

    env.service.embedding_run_finished()

    with env.db._get_connection() as conn:
        row = conn.execute(
            "SELECT has_embedding FROM media_hashes WHERE file_path = ?", (posix,)
        ).fetchone()
    assert row["has_embedding"] == 0


def test_the_error_type_carries_its_code():
    assert UpscaleError("x", "y").code == "x"


# ---- config and the folder route -------------------------------------------


@pytest.fixture
def config_env(tmp_path, monkeypatch) -> Any:
    from backend.api import upscale

    stored: Dict[str, Any] = {
        "directories": [{"filepath": str(tmp_path / "lib")}],
        "comfy": {"output_root": str(tmp_path / "gen")},
    }

    class Originals:
        moving: List[str] = []

        def active(self) -> bool:
            return bool(self.moving)

        async def begin(self, target: str) -> None:
            self.moving.append(target)

    db = DatabaseManager(tmp_path / "db")
    originals = Originals()
    monkeypatch.setattr(upscale, "load_app_config", lambda: stored)
    monkeypatch.setattr(upscale, "save_app_config", stored.update)
    monkeypatch.setattr(upscale, "get_db", lambda: db)
    upscale.install(None, None, originals)
    app = FastAPI()
    app.include_router(upscale.router)
    yield SimpleNamespace(
        client=TestClient(app),
        stored=stored,
        db=db,
        originals=originals,
        tmp=tmp_path,
    )
    upscale.install(None, None, None)


def preset(db: DatabaseManager, name: str, kind: str, bindings: str) -> int:
    with db._get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO workflow_presets (name, kind, workflow_json, bindings) "
            "VALUES (?, ?, '{}', ?)",
            (name, kind, bindings),
        )
        conn.commit()
        return int(cur.lastrowid)


def test_config_lists_workflows_by_type_and_says_what_each_takes(config_env):
    preset(config_env.db, "i", "upscale_image", '{"seed": "3", "scale": "4"}')
    preset(config_env.db, "v", "upscale_video", '{"positive": "2"}')
    preset(config_env.db, "t", "t2i", "{}")
    body = config_env.client.get("/api/upscale/config").json()
    assert [w["name"] for w in body["image_workflows"]] == ["i"]
    assert [w["name"] for w in body["video_workflows"]] == ["v"]
    image = body["image_workflows"][0]
    assert (image["has_seed"], image["has_scale"], image["has_prompt"]) == (
        True,
        True,
        False,
    )
    assert body["resolved_original_root"].endswith("_original")


def test_a_folder_with_no_originals_just_saves(config_env):
    target = str(config_env.tmp / "keep")
    r = config_env.client.post("/api/upscale/original-folder", json={"path": target})
    assert r.status_code == 202 and r.json() == {"status": "saved"}
    assert config_env.stored["upscale"]["original_root"] == target


def test_a_folder_in_the_library_is_refused(config_env):
    target = str(config_env.tmp / "lib" / "keep")
    r = config_env.client.post("/api/upscale/original-folder", json={"path": target})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "original_folder_in_library"


def test_a_folder_with_originals_moves_them(config_env):
    with config_env.db._get_connection() as conn:
        conn.execute(
            "INSERT INTO upscale_records (file_path, original_path, original_name, "
            "upscaled_size) VALUES ('/l/a.png', '/o/a.png', 'a.png', 1)"
        )
        conn.commit()
    target = config_env.tmp / "keep"
    r = config_env.client.post(
        "/api/upscale/original-folder", json={"path": str(target)}
    )
    assert r.json() == {"status": "moving"}
    assert config_env.originals.moving == [str(target)]
    assert "upscale" not in config_env.stored  # the process saves it last

    r = config_env.client.post(
        "/api/upscale/original-folder", json={"path": str(config_env.tmp / "other")}
    )
    assert r.status_code == 409
    config_env.originals.moving.clear()


# ---- cache headers -----------------------------------------------------------


@pytest.fixture
def stream(tmp_path) -> Any:
    app = FastAPI()
    app.include_router(media_api.router)
    path = tmp_path / "a.bin"
    path.write_bytes(b"0123456789")
    return SimpleNamespace(client=TestClient(app), path=path)


def url(path: Path) -> str:
    return f"/api/stream/{path}"


def test_a_stream_asks_the_browser_to_revalidate(stream):
    r = stream.client.get(url(stream.path))
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert r.headers["etag"]


def test_an_unchanged_file_is_a_304(stream):
    etag = stream.client.get(url(stream.path)).headers["etag"]
    r = stream.client.get(url(stream.path), headers={"If-None-Match": etag})
    assert r.status_code == 304 and r.content == b""


def test_a_swapped_file_is_a_200(stream):
    etag = stream.client.get(url(stream.path)).headers["etag"]
    stream.path.write_bytes(b"a longer file than before")
    r = stream.client.get(url(stream.path), headers={"If-None-Match": etag})
    assert r.status_code == 200


def test_a_range_request_still_works_and_carries_the_etag(stream):
    r = stream.client.get(url(stream.path), headers={"Range": "bytes=2-4"})
    assert r.status_code == 206 and r.content == b"234"
    assert r.headers["etag"] and r.headers["cache-control"] == "no-cache"


def _generated(env: Any, path: Path) -> None:
    """Give the ingested file the metadata a generated image carries."""
    from metascan.core.media import LoRA

    media = env.db.get_media(path)
    media.prompt = "a lake at dusk"
    media.negative_prompt = "blurry"
    media.seed = 42
    media.model = ["moss/dirtyMoss_bf16.safetensors"]
    media.loras = [LoRA(lora_name="moss/film", lora_weight=0.8)]
    assert env.db.save_media(media)


def test_state_carries_what_the_image_was_made_with(env):
    path = env.media()
    _generated(env, path)
    body = env.client.get("/api/upscale/state", params={"path": str(path)}).json()
    assert body["generation"] == {
        "prompt": "a lake at dusk",
        "negative": "blurry",
        "seed": 42,
        "model": "moss/dirtyMoss_bf16.safetensors",
        "loras": [{"name": "moss/film.safetensors", "strength": 0.8}],
        "blind": False,
        # "a lake at dusk": no people, no illustration tag.
        "refine_prompt": "photo_scene",
    }


def test_state_guesses_the_refine_prompt_from_the_tags(env):
    people = env.media("p.png")
    env.db.add_tag_indices(people, ["woman"], source="clip")
    art = env.media("i.png")
    # A medium from VLM / CLIP tags wins over people.
    env.db.add_tag_indices(art, ["woman", "digital art"], source="clip")
    got = {
        p.name: env.client.get("/api/upscale/state", params={"path": str(p)}).json()[
            "generation"
        ]["refine_prompt"]
        for p in (people, art)
    }
    assert got == {"p.png": "photo_people", "i.png": "illustration"}


def test_config_carries_the_refine_prompts(config_env):
    body = config_env.client.get("/api/upscale/config").json()
    assert [p["id"] for p in body["refine_prompts"]] == [
        "photo_people",
        "photo_scene",
        "illustration",
        "gentle",
    ]
    first = body["refine_prompts"][0]
    assert set(first) == {"id", "label", "prompt", "negative", "subject"}
    assert body["refine_prompt_max_denoise"] == 0.3


def test_a_t2i_images_own_lora_stack_wins(env):
    path = env.media()
    _generated(env, path)
    with env.db._get_connection() as conn:
        conn.execute(
            "INSERT INTO t2i_images (file_path, loras) VALUES (?, ?)",
            (
                to_posix_path(path),
                '[{"name": "moss/film.ckpt", "strength": 0.5}, {"bad": 1}]',
            ),
        )
        conn.commit()
    assert env.db.t2i_loras_for_path(to_posix_path(path)) == [
        {"name": "moss/film.ckpt", "strength": 0.5}
    ]
    body = env.client.get("/api/upscale/state", params={"path": str(path)}).json()
    assert body["generation"]["loras"] == [{"name": "moss/film.ckpt", "strength": 0.5}]
    assert env.db.t2i_loras_for_path("not/a/t2i.png") is None


def test_a_blind_scorecard_image_keeps_its_prompt_hidden(env, monkeypatch):
    path = env.media()
    _generated(env, path)
    monkeypatch.setattr(
        env.db,
        "scorecard_for_path",
        lambda p: {"scorecard_id": "s", "adapter_id": 1, "blind": True},
    )
    gen = env.client.get("/api/upscale/state", params={"path": str(path)}).json()[
        "generation"
    ]
    assert gen["prompt"] is None and gen["negative"] is None and gen["blind"] is True


def test_config_says_which_refine_values_a_workflow_takes(config_env):
    import json as _json

    graph = {
        "1": {
            "class_type": "LoadImage",
            "_meta": {"title": "MS_SOURCE_IMAGE"},
            "inputs": {"image": "x.png"},
        },
        "2": {
            "class_type": "UNETLoader",
            "_meta": {"title": "MS_MODEL"},
            "inputs": {"unet_name": "moss/sft.safetensors"},
        },
        "3": {"class_type": "SaveImage", "_meta": {"title": "MS_SAVE"}, "inputs": {}},
    }
    bindings = _json.dumps(
        {
            "negative": "4",
            "lora_stack": "5",
            "denoise": "6",
            "denoise_widget": "value",
            "model": "2",
            "model_widget": "unet_name",
            "save": "3",
        }
    )
    with config_env.db._get_connection() as conn:
        conn.execute(
            "INSERT INTO workflow_presets (name, kind, workflow_json, bindings) "
            "VALUES ('refine', 'upscale_image', ?, ?)",
            (_json.dumps(graph), bindings),
        )
        conn.commit()
    preset(config_env.db, "plain", "upscale_image", '{"seed": "3"}')
    body = config_env.client.get("/api/upscale/config").json()
    refine, plain = body["image_workflows"]
    assert (
        refine["has_negative"],
        refine["has_loras"],
        refine["has_denoise"],
        refine["has_model"],
        refine["model"],
    ) == (True, True, True, True, "moss/sft.safetensors")
    assert (plain["has_denoise"], plain["has_model"], plain["model"]) == (
        False,
        False,
        None,
    )
    assert body["denoise_levels"] == [0.2, 0.3, 0.4, 0.5]
    assert body["default_denoise"] == 0.3


def test_a_run_passes_the_refine_values_on(tmp_path, monkeypatch):
    from backend.api import upscale

    seen: List[Any] = []

    class Runner:
        async def start_run(self, req: Any, window: int = 2) -> Any:
            seen.append(req)
            return SimpleNamespace(batch_id="b", total=1, next_seed=None)

        def active_batches(self) -> List[Any]:
            return []

    monkeypatch.setattr(upscale, "load_app_config", lambda: {})
    monkeypatch.setattr(upscale.upscale_folders, "staging_conflict", lambda c: None)
    monkeypatch.setattr(upscale, "get_generation_process", lambda name: None)
    upscale.install(Runner(), None, None)
    app = FastAPI()
    app.include_router(upscale.router)
    client = TestClient(app)
    try:
        body = {
            "source_path": str(tmp_path / "a.png"),
            "preset_id": 1,
            "negative": "blurry",
            "loras": [{"name": "moss/film.safetensors", "strength": 0.7}],
            "denoise": 0.35,
            "model": "moss/dirty.safetensors",
        }
        assert client.post("/api/upscale/runs", json=body).status_code == 200
        req = seen[-1]
        assert (req.negative, req.denoise, req.model) == (
            "blurry",
            0.35,
            "moss/dirty.safetensors",
        )
        assert req.loras == [{"name": "moss/film.safetensors", "strength": 0.7}]
        # No loras key is no list (the stack node is then left empty).
        del body["loras"]
        client.post("/api/upscale/runs", json=body)
        assert seen[-1].loras is None
        for bad in ({"denoise": 1.5}, {"loras": [{"name": "", "strength": 1}]}):
            r = client.post("/api/upscale/runs", json={**body, **bad})
            assert r.status_code == 422
    finally:
        upscale.install(None, None, None)


class _CaptionRunner:
    """Stands in for T2iRunner: records what the route asked for."""

    def __init__(self, error: Any = None) -> None:
        self.captioned: List[Path] = []
        self.asked: List[Dict[str, Any]] = []
        self.error = error

    async def caption_image(self, path: Path) -> Any:
        if self.error is not None:
            raise self.error
        self.captioned.append(path)
        return "A lake at dusk.", "qwen3vl-8b"

    async def generate_prompt(self, **kw: Any) -> Any:
        self.asked.append(kw)
        draft = SimpleNamespace(writer_id="base", writer_model="qwen3vl-8b")
        return SimpleNamespace(
            prompt="A photograph of a lake at dusk.",
            negative="worst quality",
            warnings=["a note"],
            drafts=[draft],
        )


@pytest.fixture
def caption_env(env, monkeypatch):
    from backend.api import t2i as t2i_api

    runner = _CaptionRunner()
    monkeypatch.setattr(t2i_api, "get_t2i_runner", lambda: runner)
    monkeypatch.setattr(upscale_api, "get_db", lambda: env.db)
    env.caption_runner = runner
    return env


def test_a_prompt_is_written_from_the_images_caption(caption_env):
    path = caption_env.media()
    r = caption_env.client.post(
        "/api/upscale/prompt-from-image",
        json={"source_path": str(path), "model": "moss"},
    )
    assert r.status_code == 200
    assert r.json() == {
        "caption": "A lake at dusk.",
        "caption_model": "qwen3vl-8b",
        "prompt": "A photograph of a lake at dusk.",
        "negative": "worst quality",
        "warnings": ["a note"],
        "writer_id": "base",
        "writer_model": "qwen3vl-8b",
    }
    runner = caption_env.caption_runner
    assert runner.captioned == [Path(to_native_path(to_posix_path(path)))]
    assert runner.asked == [
        {
            "caption": "A lake at dusk.",
            "seed": 0,
            "model": "moss",
            "directions": False,
            "variants": 1,
            "writer": "base",
        }
    ]


def test_an_upscaled_item_is_captioned_from_its_original(caption_env, monkeypatch):
    path = caption_env.media()
    original = caption_env.tmp / "originals" / "kept.png"
    monkeypatch.setattr(
        caption_env.db,
        "upscale_record",
        lambda p: {"original_path": to_posix_path(original)},
    )
    caption_env.client.post(
        "/api/upscale/prompt-from-image",
        json={"source_path": str(path), "model": "moss"},
    )
    assert caption_env.caption_runner.captioned == [
        Path(to_native_path(to_posix_path(original)))
    ]


def test_an_unknown_model_or_no_runner_is_refused(caption_env, monkeypatch):
    from backend.api import t2i as t2i_api

    path = str(caption_env.media())
    r = caption_env.client.post(
        "/api/upscale/prompt-from-image", json={"source_path": path, "model": "nope"}
    )
    assert r.status_code == 400
    monkeypatch.setattr(t2i_api, "get_t2i_runner", lambda: None)
    r = caption_env.client.post(
        "/api/upscale/prompt-from-image", json={"source_path": path, "model": "moss"}
    )
    assert r.status_code == 503


def test_runner_errors_keep_their_meaning(caption_env, monkeypatch):
    from backend.api import t2i as t2i_api
    from metascan.core.t2i_runner import T2iRequestError, T2iUnavailableError
    from metascan.core.vlm_client import VlmError

    path = str(caption_env.media())
    for error, status in (
        (T2iUnavailableError("no writer"), 503),
        (T2iRequestError("not an image"), 400),
        (VlmError("it failed"), 502),
    ):
        monkeypatch.setattr(t2i_api, "get_t2i_runner", lambda: _CaptionRunner(error))
        r = caption_env.client.post(
            "/api/upscale/prompt-from-image",
            json={"source_path": path, "model": "moss"},
        )
        assert r.status_code == status, (error, r.status_code)


def test_config_names_the_prompt_style_of_each_workflow(config_env):
    import json as _json

    def add(name: str, unet: str, positive: bool) -> None:
        graph = {
            "2": {
                "class_type": "UNETLoader",
                "_meta": {"title": "MS_MODEL"},
                "inputs": {"unet_name": unet},
            }
        }
        bindings = {"model": "2", "model_widget": "unet_name", "save": "3"}
        if positive:
            bindings["positive"] = "4"
        with config_env.db._get_connection() as conn:
            conn.execute(
                "INSERT INTO workflow_presets (name, kind, workflow_json, bindings) "
                "VALUES (?, 'upscale_image', ?, ?)",
                (name, _json.dumps(graph), _json.dumps(bindings)),
            )
            conn.commit()

    add("moss", "moss/sft.safetensors", True)
    add("moss-no-prompt", "moss/sft.safetensors", False)
    add("other", "flux/f.safetensors", True)
    rows = {
        w["name"]: w
        for w in config_env.client.get("/api/upscale/config").json()["image_workflows"]
    }
    assert (rows["moss"]["prompt_model"], rows["moss"]["prompt_model_label"]) == (
        "moss",
        "MOSS-Image",
    )
    assert rows["moss-no-prompt"]["prompt_model"] is None
    assert rows["other"]["prompt_model"] is None
