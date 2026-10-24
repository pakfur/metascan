"""Writer training profiles (VRAM + performance evidence), worker side.

Every worker invocation ("attempt") writes ``<out_dir>/profile.json``: what
it used (memory per stage, time, throughput, data shape, environment,
config). Everything runs on the ``fake`` backend (no torch, no CUDA): it
feeds deterministic numbers through the same recorder the real backend
uses. The profile is NUMBERS ONLY -- no prompt / brief / example text.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest

from metascan.workers import writer_train_backend as real
from metascan.workers import writer_train_profile as wp
from metascan.workers import writer_train_worker as w
from tests.test_writer_train_worker import SECRETS, _make_task, _progress, _result, _run


def _profile(task: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = json.loads(
        (Path(task["out_dir"]) / wp.PROFILE_NAME).read_text()
    )
    return out


def _stage(profile: Dict[str, Any], name: str) -> Dict[str, Any]:
    found = [s for s in profile["stages"] if s["name"] == name]
    assert found, f"no {name} stage in {[s['name'] for s in profile['stages']]}"
    return found[-1]


# ---- pure helpers -------------------------------------------------------------


class TestHelpers:
    def test_token_stats_nearest_rank(self) -> None:
        stats = wp.token_stats(list(range(100, 0, -1)))
        assert stats == {
            "n": 100,
            "min": 1,
            "p50": 50,
            "p90": 90,
            "p95": 95,
            "p99": 99,
            "max": 100,
        }
        assert all(isinstance(v, int) for v in stats.values())

    def test_token_stats_small_and_empty(self) -> None:
        assert wp.token_stats([7]) == {
            "n": 1,
            "min": 7,
            "p50": 7,
            "p90": 7,
            "p95": 7,
            "p99": 7,
            "max": 7,
        }
        empty = wp.token_stats([])
        assert empty["n"] == 0 and empty["max"] is None

    def test_timeline_is_bounded_and_keeps_first_and_last(self) -> None:
        tl = wp.Timeline()
        for step in range(1, 10_001):
            tl.add({"step": step, "t_s": step * 0.1})
        tl.close()
        samples = tl.to_list()
        assert len(samples) <= wp.TIMELINE_MAX
        assert samples[0]["step"] == 1
        assert samples[-1]["step"] == 10_000
        steps = [s["step"] for s in samples]
        assert steps == sorted(set(steps))
        # Whole-run shape: evenly spread, not just the tail.
        assert steps[len(steps) // 2] < 6_000

    def test_timeline_short_run_keeps_everything(self) -> None:
        tl = wp.Timeline()
        for step in range(1, 51):
            tl.add({"step": step})
        tl.close()
        assert [s["step"] for s in tl.to_list()] == list(range(1, 51))

    @pytest.mark.parametrize(
        "total,nominal",
        [(31.84, 32), (23.99, 24), (23.64, 24), (15.6, 16), (79.15, 80), (47.5, 48)],
    )
    def test_nominal_vram(self, total: float, nominal: int) -> None:
        assert wp.nominal_vram_gb(total) == nominal

    def test_vram_cap_fraction(self) -> None:
        assert wp.vram_cap_fraction(None, 31.84) is None
        assert wp.vram_cap_fraction(24, 31.84) == pytest.approx(24 / 31.84)
        assert wp.vram_cap_fraction(32, 31.84) is None  # not below the card
        assert wp.vram_cap_fraction(24, None) is None

    def test_is_oom_error(self) -> None:
        class OutOfMemoryError(RuntimeError):
            pass

        assert wp.is_oom_error(OutOfMemoryError("anything"))
        assert wp.is_oom_error(RuntimeError("CUDA out of memory. Tried to allocate"))
        assert not wp.is_oom_error(RuntimeError("shape mismatch"))
        assert not wp.is_oom_error(ValueError("CUDA out of memory"))

    def test_is_oom_error_walks_the_cause_chain(self) -> None:
        class OutOfMemoryError(RuntimeError):
            pass

        def chain(depth: int, implicit: bool = False) -> BaseException:
            exc: BaseException = OutOfMemoryError("x")
            for i in range(depth):
                outer = ValueError(f"wrapper {i}")
                if implicit:
                    outer.__context__ = exc
                else:
                    outer.__cause__ = exc
                exc = outer
            return exc

        assert wp.is_oom_error(chain(1))
        assert wp.is_oom_error(chain(5))
        assert wp.is_oom_error(chain(3, implicit=True))
        assert not wp.is_oom_error(chain(6))  # deeper than 5 is not followed
        loop = ValueError("loop")
        loop.__cause__ = loop
        assert not wp.is_oom_error(loop)

    def test_oom_message(self) -> None:
        assert wp.oom_message("dpo", 12, 30.512, 31.84) == (
            "Ran out of GPU memory during dpo at step 12 (peak 30.51 GiB of 31.84)."
        )
        assert "peak unknown" in wp.oom_message("sft", 0, None, None)

    def test_library_versions_never_raise(self) -> None:
        versions = wp.library_versions(("surely-not-installed-pkg-xyz", "pytest"))
        assert versions["surely-not-installed-pkg-xyz"] is None
        assert isinstance(versions["pytest"], str)

    def test_torch_memory_probe(self) -> None:
        torch = mock.Mock()
        torch.cuda.memory_allocated.return_value = 2 * wp.GIB
        torch.cuda.memory_reserved.return_value = 3 * wp.GIB
        torch.cuda.max_memory_allocated.return_value = 4 * wp.GIB
        torch.cuda.max_memory_reserved.return_value = 5 * wp.GIB
        probe = real.TorchMemoryProbe(torch)
        probe.reset_peak()
        torch.cuda.reset_peak_memory_stats.assert_called_once()
        assert (probe.allocated(), probe.reserved()) == (2 * wp.GIB, 3 * wp.GIB)
        assert probe.peak_allocated() == 4 * wp.GIB
        assert probe.peak_reserved() == 5 * wp.GIB
        torch.cuda.memory_allocated.side_effect = RuntimeError("no CUDA")
        assert probe.allocated() is None  # never raises

    def test_cuda_environment(self) -> None:
        torch = mock.Mock()
        props = mock.Mock(total_memory=int(31.84 * wp.GIB), major=12, minor=0)
        props.name = "NVIDIA GeForce RTX 5090"
        torch.cuda.get_device_properties.return_value = props
        torch.version.cuda = "12.8"
        with mock.patch.object(real, "_driver_version", return_value="580.1"):
            env = real.cuda_environment(torch)
        assert env == {
            "gpu_name": "NVIDIA GeForce RTX 5090",
            "total_vram_gib": 31.84,
            "nominal_vram_gb": 32,
            "compute_capability": "12.0",
            "cuda_runtime": "12.8",
            "driver_version": "580.1",
        }

    def test_apply_vram_cap(self) -> None:
        torch = mock.Mock()
        assert real.apply_vram_cap(torch, 24.0, 31.84) == pytest.approx(24 / 31.84)
        torch.cuda.set_per_process_memory_fraction.assert_called_once()
        frac, device = torch.cuda.set_per_process_memory_fraction.call_args[0]
        assert frac == pytest.approx(24 / 31.84) and device == 0
        torch.reset_mock()
        assert real.apply_vram_cap(torch, None, 31.84) is None
        assert real.apply_vram_cap(torch, 40.0, 31.84) is None
        torch.cuda.set_per_process_memory_fraction.assert_not_called()


# ---- the VRAM cap in the task --------------------------------------------------


class TestVramCapTask:
    @pytest.mark.parametrize("bad", [4, 7.9, -1, "24", True, float("inf"), 5000])
    def test_out_of_range_cap_is_refused(self, tmp_path: Path, bad: Any) -> None:
        task = _make_task(tmp_path)
        task["vram_cap_gb"] = bad
        with pytest.raises(w.TrainWorkerError, match="vram_cap_gb"):
            w.parse_task(task)

    def test_cap_parses(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        assert w.parse_task(task).vram_cap_gb is None
        task["vram_cap_gb"] = 24
        assert w.parse_task(task).vram_cap_gb == 24.0
        task["vram_cap_gb"] = None
        assert w.parse_task(task).vram_cap_gb is None

    def test_the_fake_records_an_applied_cap(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        task["vram_cap_gb"] = 24
        assert _run(task, tmp_path) == w.EXIT_OK
        cfg = _profile(task)["config"]
        assert cfg["vram_cap_gb"] == 24.0
        assert cfg["vram_cap_applied"] is True
        assert cfg["vram_cap_fraction"] == pytest.approx(24 / 31.84, abs=1e-4)
        assert _progress(task)["vram_cap_gb"] == 24.0

    def test_a_cap_at_or_above_the_card_is_not_applied(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        task["vram_cap_gb"] = 40
        assert _run(task, tmp_path) == w.EXIT_OK
        cfg = _profile(task)["config"]
        assert cfg["vram_cap_gb"] == 40.0 and cfg["vram_cap_applied"] is False
        assert _progress(task)["vram_cap_gb"] is None
        assert _progress(task)["vram_cap_ignored_total_gib"] == 31.84

    def test_an_applied_cap_is_not_reported_ignored(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        task["vram_cap_gb"] = 24
        assert _run(task, tmp_path) == w.EXIT_OK
        assert _progress(task)["vram_cap_ignored_total_gib"] is None

    def test_no_cap(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        assert _run(task, tmp_path) == w.EXIT_OK
        cfg = _profile(task)["config"]
        assert cfg["vram_cap_gb"] is None and cfg["vram_cap_applied"] is False

    def test_the_cap_is_not_part_of_the_resume_fingerprint(
        self, tmp_path: Path
    ) -> None:
        task = _make_task(tmp_path, fake={"signal_at": {"stage": "sft", "step": 4}})
        assert _run(task, tmp_path) == w.EXIT_PAUSED
        task2 = dict(task, resume=True, vram_cap_gb=20)
        task2["fake"] = {k: v for k, v in task["fake"].items() if k != "signal_at"}
        assert _run(task2, tmp_path, "task2.json") == w.EXIT_OK
        assert _profile(task2)["config"]["vram_cap_gb"] == 20.0


# ---- per-attempt profile.json ---------------------------------------------------


class TestProfile:
    def test_a_completed_attempt(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        task["meta"].update(run_id=7, effort="quick")
        assert _run(task, tmp_path) == w.EXIT_OK
        p = _profile(task)
        assert p["schema"] == wp.PROFILE_SCHEMA == 1
        a = p["attempt"]
        assert a["run_id"] == 7
        assert a["outcome"] == "completed" and a["exit_code"] == 0
        assert a["resumed"] is False and a["resumed_from_step"] is None
        assert a["started_at"] and a["ended_at"] and a["wall_s"] >= 0

        env = p["environment"]
        for key in ("os", "python", "gpu_name", "total_vram_gib", "compute_capability"):
            assert key in env
        assert env["total_vram_gib"] == 31.84 and env["nominal_vram_gb"] == 32
        assert set(env["libraries"]) == set(wp.LIBRARIES)

        cfg = p["config"]
        assert (cfg["rank"], cfg["alpha"], cfg["epochs"]) == (8, 16, 1)
        assert cfg["lr"] == 1e-4 and cfg["dpo_lr"] == 1e-5 and cfg["beta"] == 0.1
        assert (cfg["batch_size"], cfg["grad_accum"]) == (1, 8)
        assert cfg["max_seq_len"] == 2048 and cfg["dtype"] == "bfloat16"
        assert cfg["gradient_checkpointing"] is True
        assert cfg["attn_implementation"] == "sdpa"
        assert cfg["lora_target_regex"] == real.VL_TARGET_REGEX
        assert cfg["preset"] == "quick"

        ds = p["dataset"]
        assert ds["counts"] == {"sft": 4, "pref_train": 6, "holdout": 2}
        assert ds["dropped_too_long"] == {"sft": 0, "dpo": 0, "holdout": 0}
        assert ds["token_lengths"]["sft"]["n"] == 4
        assert ds["token_lengths"]["dpo"]["n"] == 6

        names = [s["name"] for s in p["stages"]]
        assert names == ["load", "sft", "dpo", "eval", "convert"]
        for s in p["stages"]:
            assert s["completed"] is True
            assert s["wall_s"] >= 0
            assert isinstance(s["peak_allocated_gib"], float)
            assert isinstance(s["peak_reserved_gib"], float)
            assert s["peak_reserved_gib"] >= s["peak_allocated_gib"]
        load = _stage(p, "load")
        assert load["allocated_after_load_gib"] == pytest.approx(16.4)
        sft = _stage(p, "sft")
        assert sft["steps_done"] == 10
        assert sft["loss_first"] > sft["loss_last"]
        assert sft["peak_allocated_gib"] > load["allocated_after_load_gib"]
        dpo = _stage(p, "dpo")
        assert dpo["steps_done"] == 10
        # DPO's three passes use more memory than SFT.
        assert dpo["peak_allocated_gib"] > sft["peak_allocated_gib"]
        ev = _stage(p, "eval")
        assert ev["holdout_agreement"] == _result(task)["holdout_agreement"]
        assert ev["base_agreement"] == _result(task)["base_agreement"]

        tl = p["timeline"]
        assert [s["step"] for s in tl["sft"]] == list(range(1, 11))
        for s in tl["sft"]:
            assert set(s) == {"step", "t_s", "loss", "alloc_gib", "reserved_gib"}
        assert p["oom"] is None
        assert _result(task)["error_code"] is None

    def test_the_profile_exists_while_running(self, tmp_path: Path) -> None:
        """Written at the start and at every stage end (the live endpoint)."""
        task = _make_task(tmp_path)
        seen: List[Dict[str, Any]] = []
        real_replace = w.os.replace

        def spy(src: Any, dst: Any) -> None:
            real_replace(src, dst)
            if Path(dst).name == wp.PROFILE_NAME:
                seen.append(json.loads(Path(dst).read_text()))

        with mock.patch.object(wp.os, "replace", side_effect=spy):
            assert _run(task, tmp_path) == w.EXIT_OK
        assert seen[0]["attempt"]["outcome"] is None
        assert len(seen) >= 6  # start + 5 stage ends + exit
        assert seen[-1]["attempt"]["outcome"] == "completed"

    def test_throughput_numbers(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        task["fake"]["step_sleep_s"] = 0.002
        assert _run(task, tmp_path) == w.EXIT_OK
        sft = _stage(_profile(task), "sft")
        assert sft["samples_per_s"] > 0 and sft["tokens_per_s"] > 0
        assert _stage(_profile(task), "convert")["tokens_per_s"] is None

    def test_a_paused_attempt_and_its_resume(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, fake={"signal_at": {"stage": "sft", "step": 4}})
        assert _run(task, tmp_path) == w.EXIT_PAUSED
        p = _profile(task)
        assert p["attempt"]["outcome"] == "paused" and p["attempt"]["exit_code"] == 3
        sft = _stage(p, "sft")
        assert sft["completed"] is False and sft["steps_done"] == 4

        task2 = dict(task, resume=True)
        task2["fake"] = {k: v for k, v in task["fake"].items() if k != "signal_at"}
        assert _run(task2, tmp_path, "task2.json") == w.EXIT_OK
        p2 = _profile(task2)
        assert p2["attempt"]["resumed"] is True
        assert p2["attempt"]["resumed_from_step"] == 4
        assert p2["attempt"]["resumed_from_stage"] == "sft"
        assert p2["attempt"]["outcome"] == "completed"
        sft2 = _stage(p2, "sft")
        assert sft2["start_step"] == 4 and sft2["steps_done"] == 6

    def test_a_cancelled_attempt(self, tmp_path: Path) -> None:
        task = _make_task(
            tmp_path,
            fake={"signal_at": {"stage": "dpo", "step": 3, "file": "cancel.signal"}},
        )
        assert _run(task, tmp_path) == w.EXIT_CANCELLED
        p = _profile(task)
        assert p["attempt"]["outcome"] == "cancelled" and p["attempt"]["exit_code"] == 4
        assert _stage(p, "dpo")["completed"] is False

    def test_a_cancel_before_start(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        Path(task["control_dir"]).mkdir(parents=True)
        (Path(task["control_dir"]) / w.CANCEL_SIGNAL).write_text("")
        assert _run(task, tmp_path) == w.EXIT_CANCELLED
        assert _profile(task)["attempt"]["outcome"] == "cancelled"

    def test_a_failed_attempt(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, fake={"fail_at": {"stage": "dpo", "step": 2}})
        assert _run(task, tmp_path) == w.EXIT_ERROR
        p = _profile(task)
        assert p["attempt"]["outcome"] == "failed" and p["attempt"]["exit_code"] == 1
        assert p["oom"] is None
        assert _stage(p, "dpo")["completed"] is False
        assert _result(task)["error_code"] is None

    def test_an_oom_attempt(self, tmp_path: Path) -> None:
        task = _make_task(
            tmp_path,
            fake={"oom_at": {"stage": "dpo", "step": 3, "batch_tokens": 1900}},
        )
        task["vram_cap_gb"] = 24
        assert _run(task, tmp_path) == w.EXIT_ERROR
        res = _result(task)
        assert res["status"] == "error" and res["error_code"] == "oom"
        assert res["error"].startswith(
            "Ran out of GPU memory during dpo at step 3 (peak "
        )
        assert res["error"].endswith(" GiB of 24.00).")
        p = _profile(task)
        assert p["attempt"]["outcome"] == "oom" and p["attempt"]["exit_code"] == 1
        oom = p["oom"]
        assert (oom["stage"], oom["step"], oom["batch_tokens"]) == ("dpo", 3, 1900)
        assert oom["peak_allocated_gib"] > 0 and oom["peak_reserved_gib"] > 0
        assert oom["reserved_gib"] > 0
        assert oom["vram_cap_gb"] == 24.0
        assert f"{oom['peak_allocated_gib']:.2f}" in res["error"]

    def test_an_oom_without_batch_tokens(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, fake={"oom_at": {"stage": "sft", "step": 1}})
        assert _run(task, tmp_path) == w.EXIT_ERROR
        assert _profile(task)["oom"]["batch_tokens"] is None
        assert "of 31.84" in _result(task)["error"]

    def test_an_oom_while_loading_the_model(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, fake={"oom_at": {"stage": "load"}})
        assert _run(task, tmp_path) == w.EXIT_ERROR
        assert _result(task)["error"].startswith(
            "Ran out of GPU memory during load at step 0 (peak 17.00 GiB of 31.84"
        )
        p = _profile(task)
        assert p["oom"]["stage"] == "load"
        load = _stage(p, "load")
        assert load["completed"] is False

    def test_a_wrapped_oom_is_an_oom(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        task = _make_task(
            tmp_path,
            fake={"oom_at": {"stage": "sft", "step": 2, "wrapped": True}},
        )
        with caplog.at_level(logging.DEBUG, logger=w.logger.name):
            assert _run(task, tmp_path) == w.EXIT_ERROR
        res = _result(task)
        assert res["error_code"] == "oom"
        assert res["error"].startswith("Ran out of GPU memory during sft at step 2")
        assert _profile(task)["attempt"]["outcome"] == "oom"
        frames = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.DEBUG and " at: " in r.getMessage()
        ]
        assert frames and ".py:" in frames[0]

    def test_progress_carries_live_memory(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        seen: List[Dict[str, Any]] = []
        real_replace = w.os.replace

        def spy(src: Any, dst: Any) -> None:
            real_replace(src, dst)
            if Path(dst).name == w.PROGRESS_NAME:
                seen.append(json.loads(Path(dst).read_text()))

        with mock.patch.object(w.os, "replace", side_effect=spy):
            assert _run(task, tmp_path) == w.EXIT_OK
        training = [p for p in seen if p["stage"] in ("sft", "dpo") and p["step"]]
        assert training
        for p in training:
            assert isinstance(p["alloc_gib"], float) and p["alloc_gib"] > 16
            assert p["peak_gib"] >= p["alloc_gib"]
        assert all("alloc_gib" in p and "peak_gib" in p for p in seen)

    def test_a_long_stage_has_a_bounded_timeline(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, fake={"sft_steps": 900, "progress_every": 300})
        task["hyperparams"]["checkpoint_every"] = 1000
        assert _run(task, tmp_path) == w.EXIT_OK
        tl = _profile(task)["timeline"]["sft"]
        assert len(tl) <= wp.TIMELINE_MAX
        assert tl[0]["step"] == 1 and tl[-1]["step"] == 900

    def test_dropped_examples_are_counted(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, hyperparams={"max_seq_len": 64})
        long_one = {
            "system": "x " * 70,
            "user": "u",
            "chosen": "c",
            "target_model": "flux",
            "categories": [],
            "source": "hindsight",
            "group": "g",
        }
        with open(task["sft_path"], "a") as fh:
            fh.write(json.dumps(long_one) + "\n")
        assert _run(task, tmp_path) == w.EXIT_OK
        ds = _profile(task)["dataset"]
        assert ds["dropped_too_long"]["sft"] == 1
        assert ds["counts"]["sft"] == 5
        assert ds["token_lengths"]["sft"]["n"] == 4  # only what was trained
        assert ds["token_lengths"]["sft"]["max"] <= 64

    def test_a_profile_write_failure_never_fails_the_run(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        with mock.patch.object(wp, "_write_json_atomic", side_effect=OSError("disk")):
            assert _run(task, tmp_path) == w.EXIT_OK
        assert _result(task)["status"] == "ok"


# ---- privacy ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "fake",
    [
        {},
        {"fail_at": {"stage": "dpo", "step": 2}},
        {"oom_at": {"stage": "sft", "step": 2, "batch_tokens": 33}},
        {"signal_at": {"stage": "sft", "step": 3}},
        {"oom_at": {"stage": "sft", "step": 2, "message": "SECRETS"}},
        {"oom_at": {"stage": "dpo", "step": 2, "message": "SECRETS", "wrapped": True}},
        {"oom_at": {"stage": "load", "message": "SECRETS"}},
    ],
)
def test_no_example_text_in_any_output(
    tmp_path: Path, fake: Optional[Dict], caplog: pytest.LogCaptureFixture
) -> None:
    spec = (fake or {}).get("oom_at")
    if isinstance(spec, dict) and spec.get("message") == "SECRETS":
        # A library OOM whose message quotes training data.
        fake = {"oom_at": {**spec, "message": f"CUDA out of memory: {SECRETS}"}}
    task = _make_task(tmp_path, fake=fake)
    with caplog.at_level(logging.DEBUG, logger=w.logger.name):
        _run(task, tmp_path)
    out = Path(task["out_dir"])
    blob = "".join(
        (out / name).read_text()
        for name in (wp.PROFILE_NAME, w.RESULT_NAME, w.PROGRESS_NAME)
    )
    # Every other file the attempt produced too, and every log line.
    for f in out.rglob("*"):
        if f.is_file():
            blob += f.read_text(errors="replace")
    blob += caplog.text
    for s in SECRETS:
        assert s not in blob
