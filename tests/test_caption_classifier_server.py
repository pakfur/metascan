"""Tests for the classifier's llama-server supervisor, against a fake server."""

from __future__ import annotations

import sys

import pytest

from scripts.caption_classifier import server as server_mod
from scripts.caption_classifier.server import LlamaServer, ServerError, build_command
from tests._fake_caption_llama import fake_command


async def test_start_waits_for_health_then_stop_kills_it():
    srv = LlamaServer(fake_command(load_ms=300), health_timeout=20)
    await srv.start()
    try:
        assert srv.alive()
        assert srv.base_url.startswith("http://127.0.0.1:")
    finally:
        await srv.stop()
    assert not srv.alive()


async def test_restart_comes_back_on_a_new_process():
    srv = LlamaServer(fake_command(), health_timeout=20)
    await srv.start()
    try:
        await srv.restart()
        assert srv.alive()
    finally:
        await srv.stop()


async def test_process_that_exits_while_loading_raises():
    srv = LlamaServer(
        lambda port: [sys.executable, "-c", "import sys; sys.exit(3)"],
        health_timeout=20,
    )
    with pytest.raises(ServerError, match="code 3"):
        await srv.start()
    assert not srv.alive()


async def test_health_timeout_raises_and_stops_the_process():
    srv = LlamaServer(fake_command(load_ms=100000), health_timeout=1.0)
    with pytest.raises(ServerError, match="not healthy"):
        await srv.start()
    assert not srv.alive()


def test_build_command_is_text_only_and_sizes_context(tmp_path, monkeypatch):
    binary = tmp_path / "llama-server"
    binary.write_text("")
    spec = server_mod.REGISTRY["qwen3vl-30b-a3b"]
    gguf = tmp_path / "models" / "vlm" / spec.gguf_filename
    gguf.parent.mkdir(parents=True)
    gguf.write_text("")
    monkeypatch.setattr(server_mod, "binary_path", lambda: binary)
    monkeypatch.setattr(server_mod, "get_data_dir", lambda: tmp_path)

    cmd = build_command("qwen3vl-30b-a3b", 9999, parallel=16, ctx_per_slot=6144)

    assert cmd[0] == str(binary)
    assert "--mmproj" not in cmd
    assert cmd[cmd.index("--model") + 1] == str(gguf)
    assert cmd[cmd.index("--parallel") + 1] == "16"
    assert cmd[cmd.index("--ctx-size") + 1] == str(16 * 6144)
    assert cmd[cmd.index("--port") + 1] == "9999"
    assert cmd[-len(spec.extra_args) :] == list(spec.extra_args)


def test_build_command_rejects_unknown_model_and_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "binary_path", lambda: tmp_path / "nope")
    monkeypatch.setattr(server_mod, "get_data_dir", lambda: tmp_path)
    with pytest.raises(ServerError, match="unknown model"):
        build_command("no-such-model", 1, 1, 1)
    with pytest.raises(ServerError, match="missing"):
        build_command("qwen3vl-30b-a3b", 1, 1, 1)


async def test_overlong_stderr_line_does_not_stop_the_drain():
    from tests._fake_caption_llama import FAKE_SCRIPT

    def command(port):
        code = (
            "import runpy, sys; sys.stderr.write('x' * 200000 + '\\n'); "
            "sys.stderr.flush(); "
            f"sys.argv = [{str(FAKE_SCRIPT)!r}, '--port', '{port}']; "
            f"runpy.run_path({str(FAKE_SCRIPT)!r}, run_name='__main__')"
        )
        return [sys.executable, "-c", code]

    srv = LlamaServer(command, health_timeout=20)
    await srv.start()
    try:
        assert srv._drain is not None and not srv._drain.done()
    finally:
        await srv.stop()
