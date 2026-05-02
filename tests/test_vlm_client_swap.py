"""Model-swap behavior for VlmClient."""

from metascan.core.vlm_client import STATE_READY, VlmClient
from tests._fake_llama_server import FakeLlamaServer


async def test_swap_model_reaches_ready_for_new_model():
    async with FakeLlamaServer() as fake_a, FakeLlamaServer() as fake_b:
        urls = {"qwen3vl-2b": fake_a.base_url, "qwen3vl-4b": fake_b.base_url}
        client = VlmClient(spawn_override=lambda mid: urls[mid])
        try:
            await client.start("qwen3vl-2b")
            assert client.model_id == "qwen3vl-2b"
            await client.swap_model("qwen3vl-4b")
            assert client.state == STATE_READY
            assert client.model_id == "qwen3vl-4b"
        finally:
            await client.shutdown()
