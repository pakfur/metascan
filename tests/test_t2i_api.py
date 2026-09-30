"""REST tests for /api/t2i with a fake runner installed.

The runner's own behaviour is covered in test_t2i_runner.py; here the fake
returns or raises what the routes must translate. The caption store and the
wildcard library are real (over files this test writes, hand-written rows
only) because the routes read them directly. The DB is a temp
DatabaseManager, so the image routes -- which need no runner -- run for real.
"""

from __future__ import annotations

import csv
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import t2i as t2i_api
from backend.api.t2i import get_t2i_runner, set_t2i_runner
from metascan.core.comfy_bindings import BindingError
from metascan.core.comfy_client import ComfyError, PresetNotFoundError
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from metascan.core.t2i_captions import CaptionFilterError, CaptionStore
from metascan.core.t2i_characters import ResolvedCaption
from metascan.core.t2i_form import SEED_MAX, T2iFormError
from metascan.core.t2i_runner import (
    BatchRequest,
    BatchStarted,
    PromptResult,
    T2iConflictError,
    T2iNotFoundError,
    T2iRequestError,
    T2iUnavailableError,
)
from metascan.core.t2i_wildcards import LibraryCache
from metascan.core.vlm_client import VlmError
from metascan.core.vlm_select import VlmSelectError

COLUMNS = [
    "Caption",
    "Aspect Ratio",
    "Nudity",
    "Artistic Quality",
    "Erotic Score",
    "Pornographic Score",
    "Males",
    "Females",
    "Clothing",
]
ROWS = [
    [
        "__ALICE__ reads by a window.",
        "3:2",
        "none",
        "0.9",
        "0.1",
        "0.0",
        "0",
        "1",
        "['dress']",
    ],
    [
        "__BELLA__ walks a dog.",
        "2:3",
        "none",
        "0.8",
        "0.1",
        "0.0",
        "0",
        "1",
        "['coat']",
    ],
    ["__ADAM__ mends a fence.", "1:1", "partial", "0.7", "0.4", "0.1", "1", "0", "[]"],
]
LISTS = {
    "age.txt": "27-year-old\n34-year-old\n",
    "hair.txt": "auburn hair\njet-black hair\n",
    "body.female.txt": "an athletic build\n",
    "body.male.txt": "a lean build\n",
    "sky.txt": "clear\novercast\n",
}


class FakeRunner:
    """Answers what a test sets up; raises what a test says it should."""

    def __init__(self, captions: CaptionStore, library: LibraryCache) -> None:
        self.captions = captions
        self.library = library
        self.calls: List[Tuple[str, Any]] = []
        self.errors: Dict[str, BaseException] = {}
        self.prompt_result = PromptResult(
            "A calm beach at dawn.", "blurry", "Resolved caption.", ["a warning"]
        )
        self.resolve_result = ResolvedCaption(
            "A 34-year-old woman reads.",
            {"ALICE": {"age": "34-year-old", "hair": "auburn hair"}},
            ["no list for __BREASTS__"],
        )
        self.start_result = BatchStarted("b" * 32, 3, ["negative prompt ignored"])
        self.batches: List[Dict[str, Any]] = [
            {
                "batch_id": "b" * 32,
                "mode": "manual",
                "state": "rendering",
                "total_steps": 1,
                "step": None,
                "images_total": 3,
                "images_done": 1,
                "images_failed": 0,
                "next_seed": 103,
                "started_at": "2026-09-29T10:00:00+00:00",
            }
        ]
        self.cancel_result = True

    def _record(self, name: str, payload: Any) -> None:
        self.calls.append((name, payload))
        error = self.errors.get(name)
        if error is not None:
            raise error

    async def generate_prompt(self, **kwargs: Any) -> PromptResult:
        self._record("generate_prompt", kwargs)
        return self.prompt_result

    async def resolve(self, **kwargs: Any) -> ResolvedCaption:
        self._record("resolve", kwargs)
        return self.resolve_result

    async def start_batch(self, req: BatchRequest) -> BatchStarted:
        self._record("start_batch", req)
        return self.start_result

    def active_batches(self) -> List[Dict[str, Any]]:
        self._record("active_batches", None)
        return self.batches

    async def cancel_batch(self, batch_id: str) -> bool:
        self._record("cancel_batch", batch_id)
        return self.cancel_result


def _seed_media(db: DatabaseManager, paths: List[str]) -> None:
    for path in paths:
        db.save_media(
            Media(
                file_path=Path(path),
                file_size=1,
                width=8,
                height=8,
                format="png",
                created_at=datetime.now(),
                modified_at=datetime.now(),
            )
        )


class T2iApiCase(unittest.TestCase):
    """App + temp DB + fake runner. Holds no tests of its own."""

    tmp: Optional[tempfile.TemporaryDirectory] = None

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("METASCAN_API_KEY", "")

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.db = DatabaseManager(self.data_dir)
        self.csv_path = self.data_dir / "captions.csv"
        self._write_csv()
        lists = self.data_dir / "lists"
        lists.mkdir()
        for name, text in LISTS.items():
            (lists / name).write_text(text, encoding="utf-8")
        self.captions = CaptionStore(self.csv_path)
        self.library = LibraryCache(lists)
        self.runner = FakeRunner(self.captions, self.library)
        set_t2i_runner(self.runner)

        self._patches = [
            patch("backend.dependencies.get_data_dir", return_value=self.data_dir),
            patch("backend.dependencies._db_singleton", None, create=False),
        ]
        for started in self._patches:
            started.start()
        import backend.dependencies as deps

        deps._db_singleton = self.db  # type: ignore[attr-defined]

        self.app_config: Dict[str, Any] = {
            "t2i": {},
            "comfy": {"output_root": str(self.data_dir / "comfy_out")},
        }
        self._config_patch = patch(
            "backend.api.t2i.load_app_config", side_effect=lambda: self.app_config
        )
        self._config_patch.start()

        app = FastAPI()
        app.include_router(t2i_api.router)
        self.app = app
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        set_t2i_runner(None)
        self._config_patch.stop()
        for started in self._patches:
            started.stop()
        import backend.dependencies as deps

        deps._db_singleton = None  # type: ignore[attr-defined]
        assert self.tmp is not None
        self.db.close()
        self.tmp.cleanup()

    def _write_csv(self) -> None:
        with open(self.csv_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            writer.writerows(ROWS)

    def batch_body(self, **over: Any) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "mode": "manual",
            "model": "krea2",
            "preset_id": 1,
            "megapixels": 1.0,
            "seed": 100,
            "seed_policy": "increment",
            "batch_size": 1,
            "count_per_batch": 3,
            "loras": [],
            "caption": "a red kite",
            "prompt": "A red kite over a gray sea.",
            "negative": None,
            "aspect_ratio": "3:2",
        }
        body.update(over)
        return body

    def add_image(self, name: str, *, is_new_media: bool = True, **facts: Any) -> int:
        """A t2i_images row whose file exists on disk and in the library."""
        path = self.data_dir / "images" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"png")
        if is_new_media:
            _seed_media(self.db, [str(path)])
        return self.db.create_t2i_image(file_path=str(path), **facts)


class TestWithoutARunner(T2iApiCase):
    """503 for everything that needs the runner, 200 for the rest."""

    def test_routes_that_need_the_runner_answer_503(self) -> None:
        set_t2i_runner(None)
        self.assertIsNone(get_t2i_runner())
        calls = [
            ("get", "/api/t2i/config", None),
            ("get", "/api/t2i/captions/meta", None),
            ("post", "/api/t2i/captions/count", {"filter": None}),
            ("post", "/api/t2i/captions/random", {"filter": None}),
            (
                "post",
                "/api/t2i/captions/resolve",
                {"caption": "x", "seed": 1, "model": "krea2"},
            ),
            ("post", "/api/t2i/prompt", {"caption": "x", "seed": 1, "model": "krea2"}),
            ("post", "/api/t2i/batches", self.batch_body()),
            ("get", "/api/t2i/batches", None),
            ("post", "/api/t2i/batches/abc/cancel", None),
        ]
        for method, url, body in calls:
            with self.subTest(url=url):
                resp = (
                    self.client.get(url)
                    if method == "get"
                    else self.client.post(url, json=body)
                )
                self.assertEqual(resp.status_code, 503)
                self.assertEqual(resp.json()["detail"], "t2i runner is not running")

    def test_images_paths_and_preview_work_without_a_runner(self) -> None:
        set_t2i_runner(None)
        image_id = self.add_image("a.png", prompt_used="p")
        self.assertEqual(self.client.get("/api/t2i/images").status_code, 200)
        self.assertEqual(self.client.get("/api/t2i/paths").status_code, 200)
        self.assertEqual(self.client.get("/api/t2i/output-preview").status_code, 200)
        patched = self.client.patch(
            f"/api/t2i/images/{image_id}", json={"prompt": "new"}
        )
        self.assertEqual(patched.status_code, 200)
        with patch("metascan.utils.trash.send2trash"):
            self.assertEqual(
                self.client.delete(f"/api/t2i/images/{image_id}").status_code, 200
            )


class TestConfig(T2iApiCase):
    def test_the_config_has_the_documented_shape(self) -> None:
        body = self.client.get("/api/t2i/config").json()
        self.assertEqual(
            sorted(body),
            sorted(
                [
                    "output_root",
                    "output_prefix",
                    "megapixels",
                    "default_megapixels",
                    "default_model",
                    "model_workflows",
                    "content_mode",
                    "identity",
                    "window",
                    "max_batch_size",
                    "max_count_per_batch",
                    "models",
                    "aspect_ratios",
                    "seed_policies",
                    "seed_max",
                    "csv",
                    "wildcards",
                ]
            ),
        )
        self.assertEqual(body["output_root"], "")
        self.assertEqual(body["output_prefix"], "/%Y-%m-%d/t2i_")
        self.assertEqual(body["megapixels"], [0.5, 1.0, 1.5, 2.0])
        self.assertEqual(body["default_megapixels"], 1.0)
        self.assertEqual(body["default_model"], "krea2")
        self.assertEqual(
            body["model_workflows"],
            {"krea2": None, "qwen": None, "sd": None, "zimage": None},
        )
        self.assertEqual(body["content_mode"], "uncensored")
        self.assertEqual(body["identity"], {})
        self.assertEqual(
            (body["window"], body["max_batch_size"], body["max_count_per_batch"]),
            (4, 500, 32),
        )
        self.assertEqual(
            body["aspect_ratios"],
            [
                "1:1",
                "4:3",
                "3:4",
                "3:2",
                "2:3",
                "16:9",
                "9:16",
                "4:5",
                "5:4",
                "21:9",
                "9:21",
            ],
        )
        self.assertEqual(
            body["seed_policies"], ["fixed", "increment", "decrement", "random"]
        )
        self.assertEqual(body["seed_max"], SEED_MAX)

    def test_models_carry_the_effective_identity_style(self) -> None:
        models = {
            m["id"]: m for m in self.client.get("/api/t2i/config").json()["models"]
        }
        self.assertEqual(list(models), ["krea2", "qwen", "sd", "zimage"])
        self.assertEqual(
            models["krea2"],
            {
                "id": "krea2",
                "label": "Krea 2",
                "has_negative": False,
                "identity": "ref",
            },
        )
        self.assertEqual(models["sd"]["identity"], "noun")
        self.assertEqual(
            [models[m]["has_negative"] for m in models], [False, True, True, False]
        )
        # A config override wins over the profile's default...
        self.app_config["t2i"] = {"identity": {"sd": "name", "krea2": "bogus"}}
        body = self.client.get("/api/t2i/config").json()
        models = {m["id"]: m for m in body["models"]}
        self.assertEqual(models["sd"]["identity"], "name")
        self.assertEqual(
            models["krea2"]["identity"], "ref"
        )  # ...an invalid one does not
        self.assertEqual(
            body["identity"], {"sd": "name"}
        )  # the raw (sanitised) overrides

    def test_the_section_is_read_fresh_and_sanitised(self) -> None:
        self.app_config["t2i"] = {
            "output_root": "/mnt/d/Media/t2i",
            "megapixels": [0.25, 4],
            "default_megapixels": 4,
            "default_model": "qwen",
            "content_mode": "sfw",
            "window": 6,
            "model_workflows": {"qwen": 7, "nope": 9},
        }
        body = self.client.get("/api/t2i/config").json()
        self.assertEqual(body["output_root"], "/mnt/d/Media/t2i")
        self.assertEqual(body["megapixels"], [0.25, 4.0])
        self.assertEqual(body["default_model"], "qwen")
        self.assertEqual(body["content_mode"], "sfw")
        self.assertEqual(body["window"], 6)
        self.assertEqual(body["model_workflows"]["qwen"], 7)
        self.assertNotIn("nope", body["model_workflows"])

    def test_csv_state_comes_from_the_runners_caption_store(self) -> None:
        self.assertEqual(
            self.client.get("/api/t2i/config").json()["csv"],
            {"available": True, "total": 3, "error": None},
        )
        self.csv_path.unlink()
        csv_state = self.client.get("/api/t2i/config").json()["csv"]
        self.assertEqual(csv_state["available"], False)
        self.assertEqual(csv_state["total"], 0)
        self.assertIn("not found", csv_state["error"])

    def test_wildcards_list_the_slots_that_have_lists(self) -> None:
        wildcards = self.client.get("/api/t2i/config").json()["wildcards"]
        # Config slots first, in their order, then the rest by name; gendered
        # files count once under the slot.
        self.assertEqual(wildcards["slots"], ["age", "hair", "body", "sky"])
        self.assertEqual(wildcards["warnings"], [])

    def test_wildcard_warnings_are_reported(self) -> None:
        (self.data_dir / "lists" / "eyes.txt").write_text(
            "green eyes\nteen eyes\n", encoding="utf-8"
        )
        wildcards = self.client.get("/api/t2i/config").json()["wildcards"]
        self.assertEqual(len(wildcards["warnings"]), 1)
        self.assertIn("eyes.txt:2", wildcards["warnings"][0])
        self.assertIn("eyes", wildcards["slots"])
        self.assertEqual(self.runner.calls, [])  # the fake was never asked anything


class TestCaptions(T2iApiCase):
    def test_meta_lists_the_filter_columns(self) -> None:
        body = self.client.get("/api/t2i/captions/meta").json()
        self.assertEqual(body["total"], 3)
        keys = [c["key"] for c in body["columns"]]
        self.assertEqual(
            keys,
            [
                "nudity",
                "artistic_quality",
                "erotic_score",
                "pornographic_score",
                "males",
                "females",
                "aspect_ratios",
                "clothing",
            ],
        )
        nudity = body["columns"][0]
        self.assertEqual(nudity["type"], "choice")
        self.assertEqual(
            {o["value"]: o["count"] for o in nudity["options"]},
            {"none": 2, "partial": 1},
        )

    def test_meta_of_a_missing_file_is_empty_not_an_error(self) -> None:
        self.csv_path.unlink()
        self.assertEqual(
            self.client.get("/api/t2i/captions/meta").json(),
            {"total": 0, "columns": []},
        )

    def test_count(self) -> None:
        resp = self.client.post("/api/t2i/captions/count", json={"filter": None})
        self.assertEqual(resp.json(), {"count": 3, "total": 3})
        resp = self.client.post("/api/t2i/captions/count", json={})
        self.assertEqual(resp.json(), {"count": 3, "total": 3})
        resp = self.client.post(
            "/api/t2i/captions/count", json={"filter": {"nudity": ["partial"]}}
        )
        self.assertEqual(resp.json(), {"count": 1, "total": 3})
        resp = self.client.post(
            "/api/t2i/captions/count", json={"filter": {"nudity": ["full"]}}
        )
        self.assertEqual(resp.json(), {"count": 0, "total": 3})

    def test_count_400_on_a_bad_filter(self) -> None:
        resp = self.client.post(
            "/api/t2i/captions/count", json={"filter": {"colour": ["red"]}}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("unknown filter key", resp.json()["detail"])

    def test_count_of_a_missing_file_is_zero(self) -> None:
        self.csv_path.unlink()
        resp = self.client.post("/api/t2i/captions/count", json={"filter": None})
        self.assertEqual(resp.json(), {"count": 0, "total": 0})

    def test_random_returns_a_caption_row(self) -> None:
        resp = self.client.post(
            "/api/t2i/captions/random", json={"filter": {"nudity": ["partial"]}}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "id": 2,
                "caption": "__ADAM__ mends a fence.",
                "aspect_ratio": "1:1",
                "nudity": "partial",
                "artistic_quality": 0.7,
                "erotic_score": 0.4,
                "pornographic_score": 0.1,
                "males": 1,
                "females": 0,
                "clothing": [],
            },
        )

    def test_random_without_a_filter_picks_from_all_rows(self) -> None:
        seen = {
            self.client.post("/api/t2i/captions/random", json={"filter": None}).json()[
                "id"
            ]
            for _ in range(60)
        }
        self.assertTrue(seen <= {0, 1, 2})
        self.assertGreater(len(seen), 1)

    def test_random_404_when_nothing_matches(self) -> None:
        resp = self.client.post(
            "/api/t2i/captions/random", json={"filter": {"nudity": ["full"]}}
        )
        self.assertEqual(resp.status_code, 404)
        self.assertIn("no captions match", resp.json()["detail"])

    def test_random_400_on_a_bad_filter(self) -> None:
        resp = self.client.post(
            "/api/t2i/captions/random", json={"filter": {"males": "many"}}
        )
        self.assertEqual(resp.status_code, 400)

    def test_random_503_when_the_file_is_unavailable(self) -> None:
        self.csv_path.unlink()
        resp = self.client.post("/api/t2i/captions/random", json={"filter": None})
        self.assertEqual(resp.status_code, 503)
        self.assertIn("caption CSV unavailable", resp.json()["detail"])

    def test_resolve(self) -> None:
        resp = self.client.post(
            "/api/t2i/captions/resolve",
            json={"caption": "__ALICE__ reads.", "seed": 5, "model": "qwen"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "resolved_caption": "A 34-year-old woman reads.",
                "characters": {"ALICE": {"age": "34-year-old", "hair": "auburn hair"}},
                "warnings": ["no list for __BREASTS__"],
            },
        )
        self.assertEqual(
            self.runner.calls,
            [("resolve", {"caption": "__ALICE__ reads.", "seed": 5, "model": "qwen"})],
        )

    def test_resolve_400_and_422(self) -> None:
        self.runner.errors["resolve"] = T2iRequestError("Unknown model 'x'")
        resp = self.client.post(
            "/api/t2i/captions/resolve", json={"caption": "c", "seed": 1, "model": "x"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], "Unknown model 'x'")
        resp = self.client.post("/api/t2i/captions/resolve", json={"caption": "c"})
        self.assertEqual(resp.status_code, 422)


class TestPrompt(T2iApiCase):
    BODY = {"caption": "__ALICE__ reads.", "seed": 7, "model": "sd"}

    def test_happy_path(self) -> None:
        resp = self.client.post("/api/t2i/prompt", json=self.BODY)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "prompt": "A calm beach at dawn.",
                "negative": "blurry",
                "resolved_caption": "Resolved caption.",
                "warnings": ["a warning"],
            },
        )
        self.assertEqual(self.runner.calls, [("generate_prompt", self.BODY)])

    def test_error_mapping(self) -> None:
        cases: List[Tuple[BaseException, int]] = [
            (T2iRequestError("Caption is empty"), 400),
            (T2iUnavailableError("gone"), 503),
            (VlmSelectError("no model"), 503),
            (VlmError("bad body"), 502),
            (TimeoutError("slow"), 502),
            (RuntimeError("boom"), 502),
        ]
        for error, status in cases:
            with self.subTest(error=type(error).__name__):
                self.runner.errors["generate_prompt"] = error
                resp = self.client.post("/api/t2i/prompt", json=self.BODY)
                self.assertEqual(resp.status_code, status)
                self.assertEqual(resp.json()["detail"], str(error))

    def test_body_validation_is_a_422(self) -> None:
        for body in (
            {},
            {"caption": "x", "seed": 1},
            {"caption": "x", "seed": "one", "model": "sd"},
        ):
            with self.subTest(body=body):
                self.assertEqual(
                    self.client.post("/api/t2i/prompt", json=body).status_code, 422
                )


class TestBatches(T2iApiCase):
    def test_start_passes_every_field_to_the_runner(self) -> None:
        resp = self.client.post(
            "/api/t2i/batches",
            json=self.batch_body(
                preset_id=4,
                seed=12,
                seed_policy="decrement",
                count_per_batch=5,
                negative="blurry",
                loras=[{"name": "kite.safetensors", "strength": 0.8}],
            ),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "batch_id": "b" * 32,
                "total_images": 3,
                "warnings": ["negative prompt ignored"],
            },
        )
        ((name, req),) = self.runner.calls
        self.assertEqual(name, "start_batch")
        self.assertEqual(
            req,
            BatchRequest(
                mode="manual",
                model="krea2",
                preset_id=4,
                megapixels=1.0,
                seed=12,
                seed_policy="decrement",
                batch_size=1,
                count_per_batch=5,
                loras=[{"name": "kite.safetensors", "strength": 0.8}],
                caption="a red kite",
                prompt="A red kite over a gray sea.",
                negative="blurry",
                aspect_ratio="3:2",
                filter=None,
            ),
        )

    def test_a_random_request_carries_its_filter(self) -> None:
        body = {
            "mode": "random",
            "model": "qwen",
            "preset_id": 2,
            "megapixels": 0.5,
            "seed": 1,
            "seed_policy": "random",
            "batch_size": 4,
            "count_per_batch": 2,
            "filter": {"nudity": ["none"], "males": {"min": 1}},
        }
        self.assertEqual(
            self.client.post("/api/t2i/batches", json=body).status_code, 200
        )
        ((_, req),) = self.runner.calls
        self.assertEqual(req.mode, "random")
        self.assertEqual(req.filter, {"nudity": ["none"], "males": {"min": 1}})
        self.assertEqual((req.batch_size, req.count_per_batch), (4, 2))
        self.assertIsNone(req.caption)
        self.assertEqual(req.loras, [])

    def test_optional_fields_default(self) -> None:
        minimal = {
            "mode": "manual",
            "model": "krea2",
            "preset_id": 1,
            "megapixels": 1,
            "seed": 3,
            "seed_policy": "fixed",
        }
        self.assertEqual(
            self.client.post("/api/t2i/batches", json=minimal).status_code, 200
        )
        ((_, req),) = self.runner.calls
        self.assertEqual((req.batch_size, req.count_per_batch), (1, 1))
        self.assertEqual(req.megapixels, 1.0)
        self.assertEqual(req.loras, [])
        self.assertIsNone(req.prompt)

    def test_error_mapping(self) -> None:
        cases: List[Tuple[BaseException, int]] = [
            (T2iRequestError("Prompt is empty"), 400),
            (BindingError("no MS_LATENT"), 400),
            (PresetNotFoundError("no preset"), 400),
            (T2iFormError("loras[0] needs a name"), 400),
            (CaptionFilterError("no captions match the filter"), 400),
            (T2iConflictError("random_batch_active: one is running"), 409),
            (T2iUnavailableError("gone"), 503),
            (ComfyError("ComfyUI is down"), 502),
            (VlmError("bad body"), 502),
            (TimeoutError("slow"), 502),
            (RuntimeError("boom"), 502),
        ]
        for error, status in cases:
            with self.subTest(error=type(error).__name__):
                self.runner.errors["start_batch"] = error
                resp = self.client.post("/api/t2i/batches", json=self.batch_body())
                self.assertEqual(resp.status_code, status)
                self.assertEqual(resp.json()["detail"], str(error))

    def test_the_conflict_message_reaches_the_client(self) -> None:
        self.runner.errors["start_batch"] = T2iConflictError(
            "random_batch_active: wait"
        )
        resp = self.client.post("/api/t2i/batches", json=self.batch_body())
        self.assertTrue(resp.json()["detail"].startswith("random_batch_active"))

    def test_a_lora_without_a_name_is_a_422_not_a_500(self) -> None:
        # I2V's untyped list blows up on a missing key; this one is typed.
        for loras in (
            [{"strength": 0.5}],
            [{"name": "a"}],
            [{"name": 3, "strength": 1}],
            ["a"],
        ):
            with self.subTest(loras=loras):
                resp = self.client.post(
                    "/api/t2i/batches", json=self.batch_body(loras=loras)
                )
                self.assertEqual(resp.status_code, 422)
        self.assertEqual(self.runner.calls, [])

    def test_wrongly_typed_fields_are_a_422(self) -> None:
        for over in (
            {"preset_id": "one"},
            {"seed": 1.5},
            {"megapixels": "big"},
            {"batch_size": "many"},
            {"filter": "everything"},
        ):
            with self.subTest(over=over):
                resp = self.client.post(
                    "/api/t2i/batches", json=self.batch_body(**over)
                )
                self.assertEqual(resp.status_code, 422)
        missing = self.batch_body()
        del missing["seed_policy"]
        self.assertEqual(
            self.client.post("/api/t2i/batches", json=missing).status_code, 422
        )
        self.assertEqual(self.runner.calls, [])

    def test_list(self) -> None:
        resp = self.client.get("/api/t2i/batches")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), self.runner.batches)
        self.runner.batches = []
        self.assertEqual(self.client.get("/api/t2i/batches").json(), [])

    def test_cancel_is_idempotent(self) -> None:
        for outcome in (True, False):
            with self.subTest(cancelled=outcome):
                self.runner.cancel_result = outcome
                resp = self.client.post("/api/t2i/batches/abc123/cancel")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json(), {"status": "cancelled"})
        self.assertEqual(self.runner.calls, [("cancel_batch", "abc123")] * 2)

    def test_cancel_404_for_an_unknown_batch(self) -> None:
        self.runner.errors["cancel_batch"] = T2iNotFoundError("No t2i batch nope")
        resp = self.client.post("/api/t2i/batches/nope/cancel")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"], "No t2i batch nope")


class TestImages(T2iApiCase):
    def facts(self, **over: Any) -> Dict[str, Any]:
        facts: Dict[str, Any] = dict(
            batch_id="b" * 32,
            model="krea2",
            preset_id=7,
            caption="a red kite",
            prompt_used="A red kite over a gray sea.",
            negative_used=None,
            seed=100,
            prompt_seed=100,
            width=1232,
            height=816,
            megapixels=1.0,
            aspect_ratio="3:2",
            loras=[{"name": "kite.safetensors", "strength": 0.5}],
            render_s=41.5,
            comfy_prompt_id="p-1",
        )
        facts.update(over)
        return facts

    def test_list_is_newest_first_with_complete_form_states(self) -> None:
        stored = {"mode": "random", "filter": {"nudity": ["none"]}, "prompt": "edited"}
        first = self.add_image("a.png", **self.facts(), form_state=stored)
        second = self.add_image(
            "b.png", **self.facts(seed=101)
        )  # after a restart: no form state
        rows = self.client.get("/api/t2i/images").json()
        self.assertEqual([r["id"] for r in rows], [second, first])
        newest, oldest = rows
        self.assertEqual(
            sorted(newest),
            sorted(
                [
                    "id",
                    "file_path",
                    "file_name",
                    "batch_id",
                    "model",
                    "preset_id",
                    "caption",
                    "prompt_used",
                    "negative_used",
                    "seed",
                    "prompt_seed",
                    "width",
                    "height",
                    "megapixels",
                    "aspect_ratio",
                    "loras",
                    "render_s",
                    "comfy_prompt_id",
                    "created_at",
                    "is_favorite",
                    "form_state",
                ]
            ),
        )
        self.assertEqual(newest["file_name"], "b.png")
        self.assertEqual(newest["is_favorite"], False)
        # Filled from the facts...
        self.assertEqual(
            newest["form_state"],
            {
                "mode": "manual",
                "filter": None,
                "caption": "a red kite",
                "model": "krea2",
                "preset_id": 7,
                "megapixels": 1.0,
                "aspect_ratio": "3:2",
                "seed": 101,
                "prompt": "A red kite over a gray sea.",
                "negative": None,
                "loras": [{"name": "kite.safetensors", "strength": 0.5}],
            },
        )
        # ...and a stored one wins field by field, still complete.
        self.assertEqual(oldest["form_state"]["mode"], "random")
        self.assertEqual(oldest["form_state"]["filter"], {"nudity": ["none"]})
        self.assertEqual(oldest["form_state"]["prompt"], "edited")
        self.assertEqual(oldest["form_state"]["seed"], 100)
        self.assertEqual(len(oldest["form_state"]), 11)

    def test_paging(self) -> None:
        ids = [self.add_image(f"{n}.png", **self.facts(seed=n)) for n in range(5)]
        page = self.client.get("/api/t2i/images", params={"limit": 2}).json()
        self.assertEqual([r["id"] for r in page], ids[:-3:-1])
        older = self.client.get(
            "/api/t2i/images", params={"limit": 2, "before_id": page[-1]["id"]}
        ).json()
        self.assertEqual([r["id"] for r in older], [ids[2], ids[1]])
        last = self.client.get(
            "/api/t2i/images", params={"limit": 5, "before_id": ids[1]}
        ).json()
        self.assertEqual([r["id"] for r in last], [ids[0]])

    def test_limit_bounds(self) -> None:
        for limit in (0, -1, 201, "many"):
            with self.subTest(limit=limit):
                resp = self.client.get("/api/t2i/images", params={"limit": limit})
                self.assertEqual(resp.status_code, 422)
        self.assertEqual(
            self.client.get("/api/t2i/images", params={"limit": 200}).status_code, 200
        )

    def test_an_image_whose_media_is_gone_is_pruned(self) -> None:
        kept = self.add_image("a.png", **self.facts())
        self.db.create_t2i_image(file_path=str(self.data_dir / "images" / "ghost.png"))
        rows = self.client.get("/api/t2i/images").json()
        self.assertEqual([r["id"] for r in rows], [kept])

    def test_patch_answers_the_full_updated_row(self) -> None:
        image_id = self.add_image("a.png", **self.facts())
        resp = self.client.patch(
            f"/api/t2i/images/{image_id}",
            json={
                "prompt": "A new prompt.",
                "seed": 555,
                "loras": [{"name": "x", "strength": 2}],
            },
        )
        self.assertEqual(resp.status_code, 200)
        row = resp.json()
        self.assertEqual(row["id"], image_id)
        self.assertEqual(row["file_name"], "a.png")
        self.assertEqual(row["form_state"]["prompt"], "A new prompt.")
        self.assertEqual(row["form_state"]["seed"], 555)
        self.assertEqual(row["form_state"]["loras"], [{"name": "x", "strength": 2.0}])
        self.assertEqual(
            row["form_state"]["caption"], "a red kite"
        )  # untouched fields stay
        self.assertEqual(len(row["form_state"]), 11)
        # The as-rendered facts are not reachable from here.
        self.assertEqual(row["prompt_used"], "A red kite over a gray sea.")
        self.assertEqual(row["seed"], 100)
        self.assertEqual(row["loras"], [{"name": "kite.safetensors", "strength": 0.5}])

    def test_patch_persists_and_merges_successive_edits(self) -> None:
        image_id = self.add_image("a.png", **self.facts())
        self.client.patch(f"/api/t2i/images/{image_id}", json={"prompt": "one"})
        self.client.patch(f"/api/t2i/images/{image_id}", json={"negative": "blurry"})
        row = self.client.get("/api/t2i/images").json()[0]
        self.assertEqual(row["form_state"]["prompt"], "one")
        self.assertEqual(row["form_state"]["negative"], "blurry")
        stored = self.db.get_t2i_image(image_id)
        assert stored is not None
        self.assertEqual(stored["form_state"], row["form_state"])
        self.assertEqual(stored["prompt_used"], "A red kite over a gray sea.")

    def test_patch_a_stored_null_survives_a_reload(self) -> None:
        image_id = self.add_image("a.png", **self.facts(negative_used="blurry"))
        self.client.patch(f"/api/t2i/images/{image_id}", json={"negative": None})
        row = self.client.get("/api/t2i/images").json()[0]
        self.assertIsNone(row["form_state"]["negative"])  # the user cleared the box

    def test_patch_400_on_an_invalid_form(self) -> None:
        image_id = self.add_image("a.png", **self.facts())
        bad = [
            {},
            {"nope": 1},
            {"seed_policy": "fixed"},  # session-only, not form state
            {"seed": -1},
            {"seed": SEED_MAX + 1},
            {"mode": "auto"},
            {"aspect_ratio": "wide"},
            {"loras": [{"strength": 1}]},
            {"megapixels": 0},
        ]
        for body in bad:
            with self.subTest(body=body):
                resp = self.client.patch(f"/api/t2i/images/{image_id}", json=body)
                self.assertEqual(resp.status_code, 400)
                self.assertTrue(resp.json()["detail"])
        stored = self.db.get_t2i_image(image_id)
        assert stored is not None
        self.assertIsNone(stored["form_state"])  # nothing was written

    def test_patch_404_for_an_unknown_image(self) -> None:
        resp = self.client.patch("/api/t2i/images/9999", json={"prompt": "x"})
        self.assertEqual(resp.status_code, 404)
        self.assertIn("9999", resp.json()["detail"])

    def test_patch_validates_before_it_looks_up_the_image(self) -> None:
        self.assertEqual(
            self.client.patch("/api/t2i/images/9999", json={}).status_code, 400
        )

    def test_delete_removes_the_row_the_media_and_trashes_the_file(self) -> None:
        image_id = self.add_image("a.png", **self.facts())
        path = str(self.data_dir / "images" / "a.png")
        with patch("metascan.utils.trash.send2trash") as trash:
            resp = self.client.delete(f"/api/t2i/images/{image_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "deleted"})
        trash.assert_called_once_with(path)
        self.assertIsNone(self.db.get_t2i_image(image_id))
        self.assertEqual(self.client.get("/api/t2i/paths").json(), [])
        self.assertEqual(self.client.get("/api/t2i/images").json(), [])

    def test_delete_404_for_an_unknown_image(self) -> None:
        with patch("metascan.utils.trash.send2trash") as trash:
            resp = self.client.delete("/api/t2i/images/9999")
        self.assertEqual(resp.status_code, 404)
        trash.assert_not_called()

    def test_delete_is_not_a_204(self) -> None:
        image_id = self.add_image("a.png", **self.facts())
        with patch("metascan.utils.trash.send2trash"):
            resp = self.client.delete(f"/api/t2i/images/{image_id}")
        self.assertNotEqual(resp.status_code, 204)
        self.assertTrue(resp.content)  # the frontend's request() parses JSON

    def test_paths_are_native_and_only_for_images_still_in_the_library(self) -> None:
        self.add_image("a.png", **self.facts())
        self.add_image("b.png", **self.facts())
        self.db.create_t2i_image(file_path=str(self.data_dir / "images" / "ghost.png"))
        paths = self.client.get("/api/t2i/paths").json()
        self.assertEqual(
            paths,
            [
                str(self.data_dir / "images" / "a.png"),
                str(self.data_dir / "images" / "b.png"),
            ],
        )


class TestJobsCarryTheBatchId(T2iApiCase):
    def test_comfy_jobs_routes_return_t2i_batch_id(self) -> None:
        from backend.api import comfy as comfy_api

        preset = self.db.create_workflow_preset("p", "t2i", "{}", "{}")
        tagged = self.db.create_generation_job(preset, "{}", t2i_batch_id="batch-1")
        plain = self.db.create_generation_job(preset, "{}")
        app = FastAPI()
        app.include_router(comfy_api.router)
        with patch("backend.api.comfy.get_db", lambda: self.db):
            with TestClient(app) as client:
                listed = {j["id"]: j for j in client.get("/api/comfy/jobs").json()}
                single = client.get(f"/api/comfy/jobs/{tagged}").json()
                other = client.get(f"/api/comfy/jobs/{plain}").json()
        self.assertEqual(listed[tagged]["t2i_batch_id"], "batch-1")
        self.assertIsNone(listed[plain]["t2i_batch_id"])
        self.assertEqual(single["t2i_batch_id"], "batch-1")
        self.assertIsNone(other["t2i_batch_id"])


class TestOutputPreview(T2iApiCase):
    def get(self, **params: str) -> Dict[str, Any]:
        resp = self.client.get("/api/t2i/output-preview", params=params)
        self.assertEqual(resp.status_code, 200)  # always 200
        return resp.json()

    def test_a_blank_root_previews_the_default_root(self) -> None:
        body = self.get(root="", prefix="/%Y-%m-%d/t2i_")
        self.assertIsNone(body["error"])
        self.assertEqual(body["warnings"], [])
        path = Path(body["path"])
        self.assertEqual(path.suffix, ".png")
        self.assertEqual(path.parent.parent, self.data_dir / "comfy_out" / "t2i")
        self.assertRegex(path.parent.name, r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(path.stem, r"^t2i_\d+$")
        # Created on demand, so previewing must not create it.
        self.assertFalse((self.data_dir / "comfy_out").exists())

    def test_no_parameters_at_all_is_the_default_root_with_a_bare_number(self) -> None:
        body = self.get()
        self.assertIsNone(body["error"])
        path = Path(body["path"])
        self.assertEqual(path.parent, self.data_dir / "comfy_out" / "t2i")
        self.assertRegex(path.name, r"^\d+\.png$")

    def test_a_relative_comfy_root_becomes_absolute(self) -> None:
        self.app_config["comfy"] = {"output_root": "data/storyboards"}
        path = Path(self.get(prefix="x_")["path"])
        self.assertTrue(path.is_absolute())
        self.assertEqual(path.parent.parts[-3:], ("data", "storyboards", "t2i"))

    def test_an_existing_directory(self) -> None:
        body = self.get(root=str(self.data_dir), prefix="/shots/%Y/kite_")
        self.assertIsNone(body["error"])
        path = Path(body["path"])
        self.assertEqual(
            path.parent, self.data_dir / "shots" / datetime.now().strftime("%Y")
        )
        self.assertRegex(path.name, r"^kite_\d+\.png$")

    def test_a_directory_that_does_not_exist_is_an_error_not_a_path(self) -> None:
        body = self.get(root=str(self.data_dir / "nowhere"), prefix="x_")
        self.assertIsNone(body["path"])
        self.assertIn("does not exist", body["error"])

    def test_a_prefix_that_climbs_out_is_an_error(self) -> None:
        body = self.get(root=str(self.data_dir), prefix="../evil_")
        self.assertIsNone(body["path"])
        self.assertIn("..", body["error"])
        body = self.get(root="", prefix="/../evil_")  # the default root too
        self.assertIsNone(body["path"])
        self.assertIn("..", body["error"])

    def test_a_relative_configured_root_is_an_error(self) -> None:
        body = self.get(root="relative/dir", prefix="x_")
        self.assertIsNone(body["path"])
        self.assertTrue(body["error"])

    def test_warnings_are_always_returned(self) -> None:
        body = self.get(root=str(self.data_dir), prefix="/%Y-%M-%d/t2i_")
        self.assertEqual(len(body["warnings"]), 1)
        self.assertIn("%m", body["warnings"][0])
        broken = self.get(root=str(self.data_dir / "nowhere"), prefix="/%Y-%M-%d/t2i_")
        self.assertEqual(len(broken["warnings"]), 1)  # even with an error
        blank = self.get(root="", prefix="/%Y-%M-%d/t2i_")
        self.assertEqual(len(blank["warnings"]), 1)  # and for the default root
        climbing = self.get(root=str(self.data_dir), prefix="/../%Y-%M-%d/t2i_")
        self.assertIn("..", climbing["error"])
        self.assertEqual(len(climbing["warnings"]), 1)  # and when the prefix is refused

    def test_it_is_a_plain_sync_route(self) -> None:
        # It stats a possibly slow mount: FastAPI runs a sync def in a
        # thread pool, an async def would block the loop.
        import inspect

        self.assertFalse(inspect.iscoroutinefunction(t2i_api.output_preview))


class TestAppWiring(unittest.TestCase):
    def test_create_app_registers_the_t2i_routes(self) -> None:
        from backend.main import create_app

        app = create_app()
        registered = {
            (method, route.path)
            for route in app.routes
            for method in getattr(route, "methods", None) or ()
        }
        expected = {
            ("GET", "/api/t2i/config"),
            ("GET", "/api/t2i/captions/meta"),
            ("POST", "/api/t2i/captions/count"),
            ("POST", "/api/t2i/captions/random"),
            ("POST", "/api/t2i/captions/resolve"),
            ("POST", "/api/t2i/prompt"),
            ("POST", "/api/t2i/batches"),
            ("GET", "/api/t2i/batches"),
            ("POST", "/api/t2i/batches/{batch_id}/cancel"),
            ("GET", "/api/t2i/images"),
            ("PATCH", "/api/t2i/images/{image_id}"),
            ("DELETE", "/api/t2i/images/{image_id}"),
            ("GET", "/api/t2i/paths"),
            ("GET", "/api/t2i/output-preview"),
        }
        self.assertEqual(expected - registered, set())

    def test_the_t2i_runner_is_wired_into_the_lifespan(self) -> None:
        # Import-level only: the lifespan itself needs a live server.
        import inspect

        import backend.main as main

        source = inspect.getsource(main.lifespan)
        self.assertIn("t2i.set_t2i_runner(t2i_runner)", source)
        self.assertIn("comfy_client.on_job_event(t2i_runner.handle_job_event)", source)
        # The runner is built from the caption files under data/t2i_captions,
        # the live config section and the shared unload setting.
        built = source[source.index("T2iRunner(") : source.index("t2i_runner.on_event")]
        for wanted in (
            'CaptionStore(t2i_dir / "t2i_captions.csv")',
            "LibraryCache(t2i_dir)",
            "get_config=lambda: get_t2i_config(load_app_config())",
            'unload_vlm_during_generation=comfy_cfg["unload_vlm_during_generation"]',
            "comfy=comfy_client",
            "get_vlm=get_vlm_client",
            "vlm_installed=vlm_model_installed",
        ):
            self.assertIn(wanted, built)
        self.assertIn('get_data_dir() / "t2i_captions"', source)
        # Shutdown order: the event source first, then each consumer.
        order = [
            source.index("await comfy_client.shutdown()"),
            source.index("await storyboard_runner.aclose()"),
            source.index("await i2v_runner.aclose()"),
            source.index("await t2i_runner.aclose()"),
        ]
        self.assertEqual(order, sorted(order))


if __name__ == "__main__":
    unittest.main()
