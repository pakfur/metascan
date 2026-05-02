"""Verify VlmClient is constructed and registered during lifespan."""

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.api import vlm as vlm_api


def test_lifespan_installs_vlm_client():
    app = create_app()
    with TestClient(app):
        client = vlm_api.get_vlm_client()
        assert client is not None
        assert client.state == "idle"
