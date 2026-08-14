"""API tests for the light unbounded similarity search endpoints."""

import asyncio
import tempfile
from pathlib import Path

import numpy as np
import pytest

from metascan.core.embedding_manager import FaissIndexManager

DIM = 32  # FAISS test vectors must use dim >= 32 (ARM SIMD alignment)


def _vec_with_cos(c: float) -> np.ndarray:
    """Unit vector whose cosine against the base query vector is exactly c."""
    v = np.zeros(DIM, dtype=np.float32)
    v[0] = c
    v[1] = np.sqrt(max(0.0, 1.0 - c * c))
    return v


BASE = _vec_with_cos(1.0)


class FakeInferenceClient:
    async def encode_text(self, query):
        return BASE

    async def encode_image(self, file_path):
        return BASE

    async def encode_video(self, file_path, num_keyframes=4):
        return BASE


@pytest.fixture()
def light_api(monkeypatch):
    from backend.api import similarity as sim_api

    tmp = tempfile.TemporaryDirectory()
    fm = FaissIndexManager(Path(tmp.name))
    fm.create(embedding_dim=DIM, model_key="test")
    fm.add("/lib/high.png", _vec_with_cos(0.9))
    fm.add("/lib/mid.png", _vec_with_cos(0.5))
    fm.add("/lib/low.png", _vec_with_cos(0.1))

    async def noop_ready(client):
        pass

    monkeypatch.setattr(sim_api, "_faiss_manager", fm)
    monkeypatch.setattr(sim_api, "_inference_client", FakeInferenceClient())
    monkeypatch.setattr(sim_api, "_ensure_worker_ready", noop_ready)
    yield sim_api
    tmp.cleanup()


def test_faiss_manager_size_property(light_api):
    from backend.api import similarity as sim_api

    assert sim_api._get_faiss_manager().size == 3


def test_content_search_unbounded_light_shape(light_api):
    body = light_api.ContentSearchRequest(query="anything")
    out = asyncio.run(light_api.content_search(body))
    assert [o["file_path"] for o in out] == [
        "/lib/high.png",
        "/lib/mid.png",
        "/lib/low.png",
    ]
    assert all(set(o) == {"file_path", "similarity_score"} for o in out)
    assert out[0]["similarity_score"] == pytest.approx(0.9, abs=1e-3)


def test_content_search_threshold_filters(light_api):
    body = light_api.ContentSearchRequest(query="anything", threshold=0.4)
    out = asyncio.run(light_api.content_search(body))
    assert [o["file_path"] for o in out] == ["/lib/high.png", "/lib/mid.png"]


def test_content_search_max_results_caps(light_api):
    body = light_api.ContentSearchRequest(query="anything", max_results=1)
    out = asyncio.run(light_api.content_search(body))
    assert [o["file_path"] for o in out] == ["/lib/high.png"]


def test_search_similar_light_shape(light_api):
    body = light_api.SimilaritySearchRequest(file_path="/lib/query.png", threshold=0.0)
    out = asyncio.run(light_api.search_similar(body))
    assert [o["file_path"] for o in out] == [
        "/lib/high.png",
        "/lib/mid.png",
        "/lib/low.png",
    ]
    assert all(set(o) == {"file_path", "similarity_score"} for o in out)
