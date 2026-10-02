"""End-to-end tests for the classify CLI against a fake llama-server."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import time

import httpx
import pytest

from scripts.caption_classifier import classify
from scripts.caption_classifier.prompt import PROMPT_VERSION
from scripts.caption_classifier.results import results_path
from scripts.caption_classifier.server import free_port
from tests._caption_classifier_helpers import answer
from tests._fake_caption_llama import FAKE_SCRIPT

HEADER = [
    "Caption",
    "Aspect Ratio",
    "Nudity",
    "Artistic Quality",
    "Erotic Score",
    "Pornographic Score",
    "Males",
    "Females",
    "Clothing",
]


def _write_csv(path, captions):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for text in captions:
            w.writerow([text, "1:1", "none", "0.5", "0.1", "0.0", "0", "1", "[]"])


@pytest.fixture
def fake(tmp_path):
    content, tokens = answer()
    response = tmp_path / "response.json"
    response.write_text(json.dumps({"content": content, "tokens": tokens}))
    log = tmp_path / "requests.jsonl"
    port = free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            str(FAKE_SCRIPT),
            "--port",
            str(port),
            "--response",
            str(response),
            "--requests-log",
            str(log),
        ],
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{url}/health").status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.05)
    yield url, log
    proc.terminate()
    proc.wait(timeout=10)


def _args(tmp_path, url, *extra):
    return [
        "--csv",
        str(tmp_path / "c.csv"),
        "--out",
        str(tmp_path / "out"),
        "--server-url",
        url,
        "--log-file",
        str(tmp_path / "cls.log"),
        *extra,
    ]


def _requested(log):
    if not log.exists():
        return 0
    return len(log.read_text().splitlines())


def _ok_rows(tmp_path):
    path = results_path(tmp_path / "out", PROMPT_VERSION)
    return sorted(json.loads(l)["row_id"] for l in path.read_text().splitlines())


def test_count_limits_to_the_first_rows_and_rerun_skips_them(tmp_path, fake):
    url, log = fake
    _write_csv(tmp_path / "c.csv", [f"__ALICE__ pose {i}." for i in range(5)])
    assert classify.main(_args(tmp_path, url, "--count", "2")) == 0
    assert _ok_rows(tmp_path) == [0, 1]
    assert classify.main(_args(tmp_path, url, "--count", "3")) == 0
    assert _ok_rows(tmp_path) == [0, 1, 2]
    assert _requested(log) == 3


def test_edited_caption_is_classified_again(tmp_path, fake):
    url, log = fake
    _write_csv(tmp_path / "c.csv", ["__ALICE__ one.", "__ALICE__ two."])
    assert classify.main(_args(tmp_path, url)) == 0
    _write_csv(tmp_path / "c.csv", ["__ALICE__ one, edited.", "__ALICE__ two."])
    assert classify.main(_args(tmp_path, url)) == 0
    assert _requested(log) == 3


def test_results_from_another_version_need_new_run(tmp_path, fake):
    url, _ = fake
    _write_csv(tmp_path / "c.csv", ["__ALICE__ one."])
    (tmp_path / "out").mkdir()
    results_path(tmp_path / "out", "000000000000").write_text("")
    assert classify.main(_args(tmp_path, url)) == 2
    assert classify.main(_args(tmp_path, url, "--new-run")) == 0


def test_missing_csv_exits_2(tmp_path, fake):
    url, _ = fake
    assert classify.main(_args(tmp_path, url)) == 2


def test_count_and_sample_are_mutually_exclusive(tmp_path, fake):
    url, _ = fake
    with pytest.raises(SystemExit) as exc:
        classify.main(_args(tmp_path, url, "--count", "1", "--sample", "1"))
    assert exc.value.code == 2
