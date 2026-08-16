"""Task 3: `ComfyClient.upload_file` + video-aware `collect_outputs`.

Driven the same way as `tests/test_comfy_client.py`: an in-process fake
ComfyUI (`tests/_fake_comfy_server.py`) plus a real `ComfyClient` wired
against a temp SQLite db and output directory. Ingestion is stubbed with
`RecordingScanner` rather than a real `Scanner` -- the fake server's
`/view` always serves PNG bytes regardless of requested filename, which
is fine for exercising download/collection/filtering but isn't a real
video a `Scanner` could probe with ffprobe.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from metascan.core.comfy_bindings import GenerationParams
from metascan.core.comfy_client import ComfyClient, ComfyError
from metascan.core.database_sqlite import DatabaseManager
from tests._fake_comfy_server import fake_comfy  # noqa: F401


def _node(class_type: str, title: str, inputs: dict) -> dict:
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}


def t2i_workflow() -> dict:
    return {
        "3": _node("KSampler", "MS_SEED", {"seed": 0, "steps": 20}),
        "5": _node(
            "EmptyLatentImage",
            "MS_LATENT",
            {"width": 512, "height": 512, "batch_size": 1},
        ),
        "6": _node("CLIPTextEncode", "MS_POSITIVE", {"text": ""}),
        "9": _node("SaveImage", "MS_SAVE", {"filename_prefix": "ms"}),
    }


def params(**kw: Any) -> GenerationParams:
    base = dict(positive="a cat", seed=42, width=1024, height=576, batch_size=2)
    base.update(kw)
    return GenerationParams(**base)


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


class RecordingScanner:
    """Stand-in for `Scanner`: records ingested paths without touching
    ffprobe/Pillow -- the fake server's `/view` bytes aren't valid media
    for the extensions exercised here."""

    def __init__(self) -> None:
        self.ingested: List[Path] = []

    def ingest_file(self, file_path: Path) -> None:
        self.ingested.append(Path(file_path))
        return None


@pytest.fixture
async def started_client(fake_comfy, workspace):  # noqa: F811
    db = DatabaseManager(workspace / "db")
    c = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=db,
        scanner=RecordingScanner(),
        in_flight=2,
    )
    await c.start()
    try:
        yield c
    finally:
        await c.shutdown()


async def _run_job(client: ComfyClient) -> Tuple[int, Dict[str, Any]]:
    pid = await client.register_preset("wan", "t2i", t2i_workflow())
    job_id = await client.submit(pid, params())
    job = await client.wait_for_job(job_id, timeout=10.0)
    return job_id, job


# ---- upload_file -------------------------------------------------------


async def test_upload_file_mime_and_cache(
    started_client, workspace, fake_comfy  # noqa: F811
):
    src = workspace / "refs" / "clip.wav"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"RIFF0000WAVEfmt ")

    first = await started_client.upload_file(src)
    second = await started_client.upload_file(src)

    assert first == second
    # Identical bytes -> uploaded once (SHA-256 cache hit on the second
    # call), and the uploaded name keeps the real .wav suffix.
    assert len(fake_comfy.uploaded) == 1
    assert fake_comfy.uploaded[0].endswith(".wav")


async def test_upload_file_missing_path_raises(started_client, workspace):
    with pytest.raises(ComfyError) as exc:
        await started_client.upload_file(workspace / "refs" / "nope.wav")
    assert "nope.wav" in str(exc.value)


# ---- collect_outputs: video-aware --------------------------------------


async def test_collect_outputs_gifs_key_mp4_ingested(
    started_client, fake_comfy  # noqa: F811
):
    fake_comfy.output_override = {
        "gifs": [{"filename": "clip_00001.mp4", "subfolder": "", "type": "output"}]
    }
    seen: List[Tuple[str, Dict[str, Any]]] = []
    started_client.on_job_event(lambda event, payload: seen.append((event, payload)))

    job_id, job = await _run_job(started_client)
    assert job["state"] == "done"

    files = list(started_client.output_dir_for(job_id).glob("*.mp4"))
    assert len(files) == 1
    assert files[0].stat().st_size > 0
    assert files[0] in started_client.scanner.ingested

    outputs = [p for e, p in seen if e == "job_outputs"]
    assert len(outputs) == 1
    assert [Path(f).name for f in outputs[0]["files"]] == ["clip_00001.mp4"]


async def test_collect_outputs_mixed_keys_and_sidecar_filtered(
    started_client, fake_comfy  # noqa: F811
):
    fake_comfy.output_override = {
        "images": [{"filename": "frame_00001_.png", "subfolder": "", "type": "output"}],
        "videos": [
            {"filename": "clip_00001.mp4", "subfolder": "", "type": "output"},
            {"filename": "clip_00001.json", "subfolder": "", "type": "output"},
        ],
    }
    seen: List[Tuple[str, Dict[str, Any]]] = []
    started_client.on_job_event(lambda event, payload: seen.append((event, payload)))

    job_id, job = await _run_job(started_client)
    assert job["state"] == "done"

    out_dir = started_client.output_dir_for(job_id)
    on_disk = {f.name for f in out_dir.iterdir()}
    # The sidecar .json is downloaded to disk -- nothing is silently lost.
    assert on_disk == {"frame_00001_.png", "clip_00001.mp4", "clip_00001.json"}

    outputs = [p for e, p in seen if e == "job_outputs"]
    assert len(outputs) == 1
    returned_names = {Path(f).name for f in outputs[0]["files"]}
    # ... but it's excluded from the returned/ingested set.
    assert returned_names == {"frame_00001_.png", "clip_00001.mp4"}

    ingested_names = {p.name for p in started_client.scanner.ingested}
    assert ingested_names == {"frame_00001_.png", "clip_00001.mp4"}


async def test_collect_outputs_images_only_unchanged(
    started_client, fake_comfy  # noqa: F811
):
    """Regression guard: a plain image-only workflow (no output_override)
    must still behave exactly as before Task 3."""
    job_id, job = await _run_job(started_client)
    assert job["state"] == "done"

    files = sorted(started_client.output_dir_for(job_id).glob("*.png"))
    assert len(files) == fake_comfy.images_per_job == 2
    assert all(f.stat().st_size > 0 for f in files)

    ingested_names = {p.name for p in started_client.scanner.ingested}
    assert ingested_names == {f.name for f in files}
