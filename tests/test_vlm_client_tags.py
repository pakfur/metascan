"""generate_tags happy path + parse-error handling."""

from pathlib import Path

import pytest

from metascan.core.vlm_client import VlmClient
from tests._fake_llama_server import FakeLlamaServer


def _write_tiny_jpeg(path: Path) -> None:
    """Write a real, decodable 4×4 JPEG. The encoder now opens images via
    Pillow and resizes them, so test fixtures need to be valid images."""
    from PIL import Image

    Image.new("RGB", (4, 4), color=(128, 64, 200)).save(path, format="JPEG")


async def test_generate_tags_returns_normalized_list(tmp_path: Path):
    img = tmp_path / "x.jpg"
    _write_tiny_jpeg(img)
    async with FakeLlamaServer(
        canned_response='[" Red Dress ", "outdoor", "Outdoor"]'
    ) as fake:
        client = VlmClient(spawn_override=lambda mid: fake.base_url)
        try:
            await client.start("qwen3vl-2b")
            tags = await client.generate_tags(img)
            assert tags == ["red dress", "outdoor"]
        finally:
            await client.shutdown()


async def test_generate_tags_returns_empty_on_garbage_response(tmp_path: Path):
    img = tmp_path / "x.jpg"
    _write_tiny_jpeg(img)
    async with FakeLlamaServer(canned_response="not json at all") as fake:
        client = VlmClient(spawn_override=lambda mid: fake.base_url)
        try:
            await client.start("qwen3vl-2b")
            tags = await client.generate_tags(img)
            assert tags == []
        finally:
            await client.shutdown()


async def test_generate_tags_raises_when_not_started(tmp_path: Path):
    img = tmp_path / "x.jpg"
    _write_tiny_jpeg(img)
    client = VlmClient(spawn_override=lambda mid: "http://127.0.0.1:1")
    with pytest.raises(RuntimeError):
        await client.generate_tags(img)
