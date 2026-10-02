"""Tests for the classifier runner against the fake llama-server."""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from metascan.core.t2i_captions import CaptionRow
from scripts.caption_classifier.prompt import PROMPT_VERSION, SYSTEM_PROMPT
from scripts.caption_classifier.runner import FatalServerError, Runner
from scripts.caption_classifier.server import LlamaServer
from tests._caption_classifier_helpers import answer
from tests._fake_caption_llama import fake_command


class Rows:
    def __init__(self, n: int) -> None:
        self._rows = [
            CaptionRow(
                id=i,
                caption=f"__ALICE__ stands in room {i}.",
                aspect_ratio="1:1",
                nudity="none",
                artistic_quality=0.5,
                erotic_score=0.1,
                pornographic_score=0.0,
                males=0,
                females=1,
                clothing=(),
            )
            for i in range(n)
        ]

    def get(self, row_id: int) -> CaptionRow:
        return self._rows[row_id]


class Sink:
    def __init__(self, on_write=None) -> None:
        self.records: List[Dict[str, Any]] = []
        self._on_write = on_write

    def write(self, record: Dict[str, Any]) -> None:
        self.records.append(record)
        if self._on_write:
            self._on_write()


@pytest.fixture
def response_file(tmp_path):
    content, tokens = answer(emotion="B", alts={"emotion": {"B": 0.8, "A": 0.2}})
    path = tmp_path / "response.json"
    path.write_text(json.dumps({"content": content, "tokens": tokens}))
    return path


async def _run(server_flags, n, response_file, tmp_path, workers=1, sink=None, **kw):
    log = tmp_path / "requests.jsonl"
    srv = LlamaServer(
        fake_command(response=response_file, requests_log=log, **server_flags),
        health_timeout=20,
    )
    sink = sink or Sink()
    runner = Runner(
        rows=Rows(n),
        server=srv,
        writer=sink,
        model_id="fake",
        prompt_version=PROMPT_VERSION,
        workers=workers,
        timeout=10,
        progress_every=0.1,
        **kw,
    )
    await srv.start()
    try:
        stats = await runner.run(list(range(n)))
    finally:
        await srv.stop()
    requests = [json.loads(line) for line in log.read_text().splitlines()]
    return runner, stats, sink, requests


async def test_every_row_gets_an_ok_record(response_file, tmp_path):
    _, stats, sink, requests = await _run({}, 3, response_file, tmp_path, workers=2)
    assert stats.ok == 3 and stats.errors == 0
    assert sorted(r["row_id"] for r in sink.records) == [0, 1, 2]
    rec = sink.records[0]
    assert rec["status"] == "ok" and rec["model"] == "fake"
    assert rec["prompt_version"] == PROMPT_VERSION
    assert len(rec["caption_sha1"]) == 40
    assert rec["emotion"] == {"A": 0.2, "B": 0.8, "C": 0.0}
    body = requests[0]
    assert body["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert body["logprobs"] is True and body["top_logprobs"] == 20
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["temperature"] == 0.0 and "grammar" in body


async def test_one_failed_request_is_retried(response_file, tmp_path):
    _, stats, sink, requests = await _run({"fail_first": 1}, 1, response_file, tmp_path)
    assert stats.ok == 1 and len(requests) == 2


async def test_context_overflow_becomes_an_error_row_and_the_run_continues(
    response_file, tmp_path
):
    _, stats, sink, requests = await _run(
        {"fail_first": 3, "fail_status": 400}, 2, response_file, tmp_path
    )
    assert [r["status"] for r in sink.records] == ["error", "ok"]
    assert "400" in sink.records[0]["error"]
    assert "context size" in sink.records[0]["error"]
    assert stats.errors == 1 and len(requests) == 4


async def test_one_crash_restarts_the_server_and_finishes(response_file, tmp_path):
    _, stats, sink, _ = await _run({"exit_after": 2}, 3, response_file, tmp_path)
    assert stats.restarts == 1
    assert [r["status"] for r in sink.records] == ["ok", "ok", "ok"]


async def test_second_crash_stops_the_run_and_keeps_written_rows(
    response_file, tmp_path
):
    sink = Sink()
    with pytest.raises(FatalServerError):
        await _run({"exit_after": 1}, 4, response_file, tmp_path, sink=sink)
    assert [r["row_id"] for r in sink.records] == [0, 1]


async def test_stop_request_finishes_the_current_row_then_stops(
    response_file, tmp_path
):
    holder: Dict[str, Runner] = {}
    sink = Sink(on_write=lambda: holder["runner"].request_stop())
    log = tmp_path / "requests.jsonl"
    srv = LlamaServer(
        fake_command(response=response_file, requests_log=log), health_timeout=20
    )
    runner = Runner(
        rows=Rows(5),
        server=srv,
        writer=sink,
        model_id="fake",
        prompt_version=PROMPT_VERSION,
        workers=1,
        timeout=10,
    )
    holder["runner"] = runner
    await srv.start()
    try:
        stats = await runner.run(list(range(5)))
    finally:
        await srv.stop()
    assert len(sink.records) == 1 and stats.stopped


async def test_malformed_response_becomes_an_error_row_not_a_crash(tmp_path):
    _, tokens = answer()
    path = tmp_path / "null.json"
    path.write_text(json.dumps({"content": None, "tokens": tokens}))
    _, stats, sink, _ = await _run({}, 2, path, tmp_path)
    assert [r["status"] for r in sink.records] == ["error", "error"]
    assert "malformed" in sink.records[0]["error"]


class _DeadExternal:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def alive(self) -> bool:
        return True

    async def restart(self) -> None:
        raise AssertionError("must not restart")


async def test_server_that_never_answers_stops_the_run(tmp_path):
    from scripts.caption_classifier.server import free_port

    sink = Sink()
    runner = Runner(
        rows=Rows(50),
        server=_DeadExternal(f"http://127.0.0.1:{free_port()}"),
        writer=sink,
        model_id="fake",
        prompt_version=PROMPT_VERSION,
        workers=1,
        timeout=5,
        max_transport_failures=4,
    )
    with pytest.raises(FatalServerError, match="in a row"):
        await runner.run(list(range(50)))
    assert len(sink.records) < 50
