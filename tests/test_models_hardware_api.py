"""Tests for the /api/models/hardware and /api/models/status gates payload."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# tests/conftest.py sets KMP_DUPLICATE_LIB_OK=TRUE for the whole session;
# no per-file env setup needed.

from backend.api import models as models_api
from metascan.core.hardware import (
    CudaInfo,
    HardwareReport,
    Tier,
    VulkanInfo,
)


@pytest.fixture()
def client():
    """Minimal FastAPI app with just the models router mounted."""
    app = FastAPI()
    app.include_router(models_api.router)
    return TestClient(app)


def _fake_report(**kwargs) -> HardwareReport:
    base = HardwareReport(
        os="Linux",
        machine="x86_64",
        python="3.11.7",
        cpu_count=8,
        ram_gb=16.0,
    )
    for k, v in kwargs.items():
        setattr(base, k, v)
    return base


def test_hardware_endpoint_returns_tier_and_report(client) -> None:
    fake = _fake_report(
        cuda=CudaInfo(name="RTX 3060", vram_gb=8.0, capability="8.6"),
        vulkan=VulkanInfo(
            available=True, devices=["NVIDIA GeForce RTX 3060"], has_real_device=True
        ),
    )
    with patch("backend.api.models.detect_hardware", return_value=fake):
        r = client.get("/api/models/hardware")
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == Tier.CUDA_MAINSTREAM.value
    assert body["report"]["os"] == "Linux"
    assert body["report"]["cuda"]["name"] == "RTX 3060"
    assert body["report"]["cuda"]["vram_gb"] == 8.0
    assert body["report"]["vulkan"]["has_real_device"] is True


def test_hardware_endpoint_cpu_only_host(client) -> None:
    fake = _fake_report()
    with patch("backend.api.models.detect_hardware", return_value=fake):
        r = client.get("/api/models/hardware")
    body = r.json()
    assert body["tier"] == Tier.CPU_ONLY.value
    assert body["report"]["cuda"] is None
    assert body["report"]["mps"] is False


def test_status_endpoint_includes_gates(client) -> None:
    fake = _fake_report(
        cuda=CudaInfo(name="RTX 3060", vram_gb=8.0, capability="8.6"),
        vulkan=VulkanInfo(available=True, devices=["RTX 3060"], has_real_device=True),
    )
    with patch("backend.api.models.detect_hardware", return_value=fake):
        r = client.get("/api/models/status")
    assert r.status_code == 200
    body = r.json()
    assert "gates" in body
    assert "tier" in body
    # Legacy keys must remain — the gates/tier additions are additive.
    assert {
        "models",
        "hf_token_set",
        "current_clip_model",
        "current_clip_dim",
    } <= body.keys()
    g = body["gates"]
    assert g["clip-medium"]["available"] is True
    assert g["rife"]["available"] is True
    assert "reason" in g["clip-large"]


def test_status_endpoint_gates_block_rife_on_llvmpipe(client) -> None:
    fake = _fake_report(
        vulkan=VulkanInfo(
            available=True,
            devices=["llvmpipe (LLVM 15)"],
            has_real_device=False,
        ),
    )
    with patch("backend.api.models.detect_hardware", return_value=fake):
        r = client.get("/api/models/status")
    body = r.json()
    assert body["gates"]["rife"]["available"] is False
    assert "llvmpipe" in body["gates"]["rife"]["reason"]
