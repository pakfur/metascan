"""Test FFmpeg last-frame extraction."""

import shutil
import subprocess

import pytest

from metascan.utils.ffmpeg_utils import extract_last_frame


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_extract_last_frame_produces_png(tmp_path):
    """Generate a 1-second test clip and extract its last frame as PNG."""
    # Generate a test clip using ffmpeg's color source
    test_clip = tmp_path / "test.mp4"
    result = subprocess.run(
        [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64:d=1",
            "-y",
            str(test_clip),
        ],
        capture_output=True,
        timeout=10,
    )
    assert (
        result.returncode == 0
    ), f"Failed to create test clip: {result.stderr.decode()}"
    assert test_clip.exists(), "Test clip was not created"

    # Extract the last frame
    out_png = tmp_path / "last_frame.png"
    extract_last_frame(test_clip, out_png)

    # Verify the PNG was created and has content
    assert out_png.exists(), "Output PNG was not created"
    assert out_png.stat().st_size > 0, "Output PNG is empty"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_extract_last_frame_bad_input_raises(tmp_path):
    """Verify RuntimeError is raised for invalid input video."""
    nonexistent = tmp_path / "nope.mp4"
    out_png = tmp_path / "output.png"

    with pytest.raises(RuntimeError):
        extract_last_frame(nonexistent, out_png)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_extract_last_frame_creates_parent_dirs(tmp_path):
    """Verify parent directories are created if they don't exist."""
    # Generate a test clip
    test_clip = tmp_path / "test.mp4"
    result = subprocess.run(
        [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:d=1",
            "-y",
            str(test_clip),
        ],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0

    # Extract to a path with nested nonexistent dirs
    out_png = tmp_path / "nested" / "deep" / "dir" / "frame.png"
    extract_last_frame(test_clip, out_png)

    # Verify the PNG was created in the nested path
    assert out_png.exists(), "Output PNG was not created in nested dir"
    assert out_png.stat().st_size > 0, "Output PNG is empty"


def test_extract_last_frame_timeout_raises_runtime_error(tmp_path, monkeypatch):
    """A hung ffmpeg process (e.g. on a malformed AI-generated video) must
    raise RuntimeError, not block the request thread forever."""

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "metascan.utils.ffmpeg_utils.get_ffmpeg_path", lambda: "/usr/bin/ffmpeg"
    )

    video_path = tmp_path / "malformed.mp4"
    video_path.write_bytes(b"not a real video")
    out_png = tmp_path / "frame.png"

    with pytest.raises(RuntimeError):
        extract_last_frame(video_path, out_png)
