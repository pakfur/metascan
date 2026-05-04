"""Verify VlmClient is constructed and registered during lifespan."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.api import vlm as vlm_api


def test_lifespan_installs_vlm_client():
    # Pin config to a deterministic empty preload list so the test does not
    # depend on the host config.json (a dev box may have qwen3vl-* in
    # `preload_at_startup`, which schedules a VlmClient.start task during
    # lifespan and races the state assertion to "loading").
    fake_config = {
        "models": {"preload_at_startup": [], "huggingface_token": ""},
        "similarity": {},
    }
    with patch("backend.main.load_app_config", return_value=fake_config):
        app = create_app()
        with TestClient(app):
            client = vlm_api.get_vlm_client()
            assert client is not None
            assert client.state == "idle"
