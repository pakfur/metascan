"""Tests for the prompt-writer LoRA training worker (taste learning project 4).

Everything runs on the ``fake`` backend: no CUDA, no torch / transformers /
peft / trl. The real backend's pure helpers (argument building, chat
rendering, converter command) are tested with stand-ins; one smoke test
needs CUDA and the training deps and is skipped otherwise.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest

from metascan.workers import writer_train_backend as real
from metascan.workers import writer_train_worker as w

SECRET_SYSTEM = "SYSTEM-zebra-quartz"
SECRET_USER = "USER-marmalade-volcano"
SECRET_CHOSEN = "CHOSEN-lighthouse-saffron"
SECRET_REJECTED = "REJECTED-umbrella-tundra"
SECRETS = (SECRET_SYSTEM, SECRET_USER, SECRET_CHOSEN, SECRET_REJECTED)


def _pref(i: int, group: str) -> Dict[str, Any]:
    return {
        "system": SECRET_SYSTEM,
        "user": f"{SECRET_USER} {i}",
        "chosen": f"{SECRET_CHOSEN} {i} with more words",
        "rejected": f"{SECRET_REJECTED} {i}",
        "target_model": "flux",
        "categories": [],
        "source": "variant",
        "group": group,
    }


def _sft(i: int) -> Dict[str, Any]:
    return {
        "system": SECRET_SYSTEM,
        "user": f"{SECRET_USER} {i}",
        "chosen": f"{SECRET_CHOSEN} {i}",
        "target_model": "flux",
        "categories": ["cats"],
        "source": "hindsight",
        "group": f"g{i}",
    }


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _make_task(
    tmp_path: Path,
    *,
    n_sft: int = 4,
    n_pref: int = 6,
    n_hold: int = 2,
    fake: Optional[Dict[str, Any]] = None,
    resume: bool = False,
    hyperparams: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    _write_jsonl(data / "sft.jsonl", [_sft(i) for i in range(n_sft)])
    _write_jsonl(data / "prefs.jsonl", [_pref(i, f"p{i}") for i in range(n_pref)])
    _write_jsonl(data / "holdout.jsonl", [_pref(i, "h") for i in range(n_hold)])
    hp = {"rank": 8, "lr": 1e-4, "epochs": 1, "checkpoint_every": 5}
    hp.update(hyperparams or {})
    return {
        "backend": "fake",
        "base_dir": str(tmp_path / "base"),
        "data_root": str(tmp_path),
        "sft_path": str(data / "sft.jsonl"),
        "pref_path": str(data / "prefs.jsonl"),
        "holdout_path": str(data / "holdout.jsonl"),
        "out_dir": str(tmp_path / "out"),
        "checkpoint_dir": str(tmp_path / "ckpt"),
        "control_dir": str(tmp_path / "control"),
        "resume": resume,
        "hyperparams": hp,
        "converter": {"python": sys.executable, "script": "convert_lora_to_gguf.py"},
        "meta": {"counts": {"pref_pairs": n_pref, "content_mode": "uncensored"}},
        "fake": {"sft_steps": 10, "dpo_steps": 10, "progress_every": 1, **(fake or {})},
    }


def _run(task: Dict[str, Any], tmp_path: Path, name: str = "task.json") -> int:
    path = tmp_path / name
    path.write_text(json.dumps(task))
    return w.main(["writer_train_worker", str(path)])


def _result(task: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = json.loads(
        (Path(task["out_dir"]) / w.RESULT_NAME).read_text()
    )
    return out


def _progress(task: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = json.loads(
        (Path(task["out_dir"]) / w.PROGRESS_NAME).read_text()
    )
    return out


def _trace(task: Dict[str, Any]) -> List[str]:
    p = Path(task["out_dir"]) / "fake_trace.txt"
    return p.read_text().split() if p.exists() else []


# ---- hyperparameters ------------------------------------------------------


class TestHyperparams:
    def test_presets_match_the_spec(self) -> None:
        assert w.EFFORT_PRESETS == {
            "quick": {"rank": 8, "lr": 1e-4, "epochs": 1, "dpo_lr": 1e-5},
            "standard": {"rank": 16, "lr": 1e-4, "epochs": 2, "dpo_lr": 1e-5},
            "thorough": {"rank": 32, "lr": 5e-5, "epochs": 3, "dpo_lr": 1e-5},
        }
        for preset in w.EFFORT_PRESETS.values():
            assert w.validate_hyperparams(preset)["dpo_lr"] == 1e-5

    def test_defaults_filled(self) -> None:
        hp = w.validate_hyperparams({"rank": 16, "lr": 1e-4, "epochs": 2})
        assert hp["lora_alpha"] == 32
        assert hp["beta"] == 0.1
        assert hp["max_seq_len"] == 2048
        assert hp["dpo_lr"] == 1e-5
        assert hp["checkpoint_every"] == 50
        assert hp["seed"] == 0

    @pytest.mark.parametrize(
        "bad",
        [
            {"rank": 2, "lr": 1e-4, "epochs": 1},
            {"rank": 128, "lr": 1e-4, "epochs": 1},
            {"rank": 8, "lr": 1e-2, "epochs": 1},
            {"rank": 8, "lr": 1e-7, "epochs": 1},
            {"rank": 8, "lr": 1e-4, "epochs": 6},
            {"rank": 8, "lr": 1e-4, "epochs": 0},
            {"rank": 8, "lr": 1e-4},
            {"rank": 8.5, "lr": 1e-4, "epochs": 1},
            {"rank": 8, "lr": 1e-4, "epochs": 1, "max_seq_len": 4096},
            {"rank": 8, "lr": 1e-4, "epochs": 1, "max_seq_len": 2049},
            {"rank": 8, "lr": 1e-4, "epochs": 1, "dpo_lr": 1e-3},
            {"rank": 8, "lr": 1e-4, "epochs": 1, "dpo_lr": 1e-7},
            {"rank": 8, "lr": 1e-4, "epochs": 1, "bogus": 1},
            {"rank": True, "lr": 1e-4, "epochs": 1},
        ],
    )
    def test_out_of_clamp_rejected(self, bad: Dict[str, Any]) -> None:
        with pytest.raises(w.TrainWorkerError):
            w.validate_hyperparams(bad)

    def test_steps_per_stage(self) -> None:
        # ceil(ceil(n / bs) / ga) * epochs
        assert w.stage_steps(17, batch_size=2, grad_accum=4, epochs=3) == 9
        assert w.stage_steps(0, batch_size=1, grad_accum=8, epochs=2) == 0


# ---- end to end (fake) ----------------------------------------------------


class TestFakeEndToEnd:
    def test_completes_and_writes_artifacts(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        assert _run(task, tmp_path) == w.EXIT_OK
        res = _result(task)
        assert res["status"] == "ok"
        assert res["error"] is None
        assert set(res) >= {
            "status",
            "adapter_dir",
            "gguf_path",
            "holdout_agreement",
            "base_agreement",
            "error",
        }
        out = Path(task["out_dir"])
        assert Path(res["gguf_path"]) == out / w.GGUF_NAME
        assert Path(res["gguf_path"]).stat().st_size > 0
        assert (Path(res["adapter_dir"]) / "adapter_config.json").exists()
        assert 0.0 <= res["holdout_agreement"] <= 1.0
        assert 0.0 <= res["base_agreement"] <= 1.0
        meta = json.loads((out / w.META_NAME).read_text())
        assert meta["counts"]["content_mode"] == "uncensored"
        assert meta["hyperparams"]["rank"] == 8
        assert meta["holdout_agreement"] == res["holdout_agreement"]
        assert meta["n_holdout"] == 2
        # SFT then DPO, every step exactly once.
        assert _trace(task) == [f"sft:{i}" for i in range(1, 11)] + [
            f"dpo:{i}" for i in range(1, 11)
        ]
        # Checkpoints are cleaned on success.
        assert not Path(task["checkpoint_dir"]).exists()
        prog = _progress(task)
        assert prog["stage"] == "convert"
        assert prog["step"] == prog["total_steps"] == 20

    def test_no_sft_examples_skips_sft(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, n_sft=0)
        task["fake"].pop("sft_steps")
        assert _run(task, tmp_path) == w.EXIT_OK
        assert all(s.startswith("dpo:") for s in _trace(task))

    def test_empty_holdout_gives_null_agreement(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, n_hold=0)
        assert _run(task, tmp_path) == w.EXIT_OK
        res = _result(task)
        assert res["holdout_agreement"] is None
        assert res["base_agreement"] is None

    def test_default_steps_follow_dataset_size(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, n_sft=9, n_pref=17)
        task["fake"] = {"progress_every": 1}
        task["hyperparams"].update({"batch_size": 1, "grad_accum": 8, "epochs": 2})
        assert _run(task, tmp_path) == w.EXIT_OK
        trace = _trace(task)
        assert sum(s.startswith("sft:") for s in trace) == 4  # ceil(9/8)*2
        assert sum(s.startswith("dpo:") for s in trace) == 6  # ceil(17/8)*2

    def test_progress_is_well_formed_json_at_every_write(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        seen: List[Dict[str, Any]] = []
        real_replace = os.replace

        def spy(src: Any, dst: Any) -> None:
            real_replace(src, dst)
            if Path(dst).name == w.PROGRESS_NAME:
                seen.append(json.loads(Path(dst).read_text()))

        with mock.patch.object(w.os, "replace", side_effect=spy):
            assert _run(task, tmp_path) == w.EXIT_OK
        assert len(seen) >= 20
        keys = {
            "stage",
            "step",
            "total_steps",
            "loss",
            "holdout_agreement",
            "base_agreement",
            "eta_s",
        }
        for p in seen:
            assert keys <= set(p)
            assert p["stage"] in ("sft", "dpo", "eval", "convert")
            assert 0 <= p["step"] <= p["total_steps"]
        steps = [p["step"] for p in seen]
        assert steps == sorted(steps)
        assert {p["stage"] for p in seen} == {"sft", "dpo", "eval", "convert"}
        # No temp files are left behind.
        assert not [f for f in os.listdir(task["out_dir"]) if f.endswith(".tmp")]

    def test_never_logs_example_text(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        tp = tmp_path / "task.json"
        tp.write_text(json.dumps(task))
        proc = subprocess.run(
            [sys.executable, "-m", "metascan.workers.writer_train_worker", str(tp)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        assert proc.returncode == w.EXIT_OK, proc.stderr
        blob = proc.stdout + proc.stderr
        blob += (Path(task["out_dir"]) / w.PROGRESS_NAME).read_text()
        blob += (Path(task["out_dir"]) / w.RESULT_NAME).read_text()
        blob += (Path(task["out_dir"]) / w.META_NAME).read_text()
        for s in SECRETS:
            assert s not in blob
        assert "writer train" in proc.stderr  # it does log (counts only)

    def test_worker_module_never_imports_training_deps(self) -> None:
        code = (
            "import sys, metascan.workers.writer_train_worker, "
            "metascan.workers.writer_train_backend, "
            "metascan.workers.writer_train_fake, "
            "metascan.workers.writer_train_probe\n"
            "bad = [m for m in ('torch', 'transformers', 'peft', 'trl', "
            "'datasets') if m in sys.modules]\n"
            "print(','.join(bad))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == ""


# ---- pause / resume / cancel ---------------------------------------------


class TestControl:
    def test_pause_then_resume_continues_from_saved_step(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, fake={"signal_at": {"stage": "sft", "step": 7}})
        assert _run(task, tmp_path) == w.EXIT_PAUSED
        res = _result(task)
        assert res["status"] == "paused"
        assert res["stage"] == "sft"
        assert res["step"] == 7
        ckpt = Path(task["checkpoint_dir"])
        assert (ckpt / "sft" / "checkpoint-7").is_dir()
        assert (ckpt / "sft" / "checkpoint-5").is_dir()
        state = json.loads((ckpt / w.STATE_NAME).read_text())
        assert state["stage"] == "sft"
        # The pause request is consumed, so a resume does not pause again.
        assert not (Path(task["control_dir"]) / w.PAUSE_SIGNAL).exists()
        assert _progress(task)["step"] == 7

        task2 = dict(task, resume=True)
        task2["fake"] = {k: v for k, v in task["fake"].items() if k != "signal_at"}
        assert _run(task2, tmp_path, "task2.json") == w.EXIT_OK
        trace = _trace(task2)
        sft = [s for s in trace if s.startswith("sft:")]
        # steps 1..7 once (first run), 8..10 once (resumed run): no repeats.
        assert sft == [f"sft:{i}" for i in range(1, 11)]
        assert _result(task2)["status"] == "ok"
        assert _result(task2)["resumed_from"] == {"stage": "sft", "step": 7}

    def test_pause_in_dpo_resumes_in_dpo_without_redoing_sft(
        self, tmp_path: Path
    ) -> None:
        task = _make_task(tmp_path, fake={"signal_at": {"stage": "dpo", "step": 3}})
        assert _run(task, tmp_path) == w.EXIT_PAUSED
        assert _result(task)["stage"] == "dpo"
        state = json.loads((Path(task["checkpoint_dir"]) / w.STATE_NAME).read_text())
        assert state["stage"] == "dpo"
        assert _progress(task)["step"] == 13  # overall: 10 SFT + 3 DPO

        task2 = dict(task, resume=True)
        task2["fake"] = {k: v for k, v in task["fake"].items() if k != "signal_at"}
        assert _run(task2, tmp_path, "task2.json") == w.EXIT_OK
        trace = _trace(task2)
        assert trace.count("sft:1") == 1
        assert [s for s in trace if s.startswith("dpo:")] == [
            f"dpo:{i}" for i in range(1, 11)
        ]

    def test_resume_with_different_data_is_refused(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, fake={"signal_at": {"stage": "sft", "step": 2}})
        assert _run(task, tmp_path) == w.EXIT_PAUSED
        _write_jsonl(Path(task["pref_path"]), [_pref(i, "x") for i in range(3)])
        task2 = dict(task, resume=True)
        assert _run(task2, tmp_path, "task2.json") == w.EXIT_ERROR
        assert "resume" in _result(task2)["error"]

    def test_resume_without_state_starts_fresh(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, resume=True)
        assert _run(task, tmp_path) == w.EXIT_OK
        assert _result(task)["resumed_from"] is None

    def test_fresh_run_wipes_stale_checkpoints(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, fake={"signal_at": {"stage": "sft", "step": 7}})
        assert _run(task, tmp_path) == w.EXIT_PAUSED
        task2 = dict(task, resume=False)
        task2["fake"] = {k: v for k, v in task["fake"].items() if k != "signal_at"}
        assert _run(task2, tmp_path, "task2.json") == w.EXIT_OK
        sft = [s for s in _trace(task2) if s.startswith("sft:")]
        assert sft.count("sft:1") == 2  # restarted from scratch

    def test_cancel_cleans_checkpoints(self, tmp_path: Path) -> None:
        task = _make_task(
            tmp_path,
            fake={"signal_at": {"stage": "dpo", "step": 6, "file": "cancel.signal"}},
        )
        assert _run(task, tmp_path) == w.EXIT_CANCELLED
        res = _result(task)
        assert res["status"] == "cancelled"
        assert res["gguf_path"] is None
        assert not Path(task["checkpoint_dir"]).exists()
        out = Path(task["out_dir"])
        assert not (out / w.GGUF_NAME).exists()
        assert not (out / w.PEFT_DIRNAME).exists()

    def test_cancel_after_pause_cleans_on_resume_start(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, fake={"signal_at": {"stage": "sft", "step": 4}})
        assert _run(task, tmp_path) == w.EXIT_PAUSED
        (Path(task["control_dir"]) / w.CANCEL_SIGNAL).write_text("")
        task2 = dict(task, resume=True)
        assert _run(task2, tmp_path, "task2.json") == w.EXIT_CANCELLED
        assert not Path(task["checkpoint_dir"]).exists()

    def test_pause_signal_from_another_process(self, tmp_path: Path) -> None:
        """The manager's path: a separate process drops pause.signal while
        the worker runs; the saved step is the step reported at pause."""
        task = _make_task(
            tmp_path, fake={"sft_steps": 400, "dpo_steps": 5, "step_sleep_s": 0.01}
        )
        tp = tmp_path / "task.json"
        tp.write_text(json.dumps(task))
        proc = subprocess.Popen(
            [sys.executable, "-m", "metascan.workers.writer_train_worker", str(tp)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        try:
            prog = Path(task["out_dir"]) / w.PROGRESS_NAME
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if prog.exists() and json.loads(prog.read_text())["step"] >= 3:
                    break
                time.sleep(0.02)
            Path(task["control_dir"]).mkdir(parents=True, exist_ok=True)
            (Path(task["control_dir"]) / w.PAUSE_SIGNAL).write_text("")
            rc = proc.wait(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
        assert rc == w.EXIT_PAUSED
        res = _result(task)
        assert 3 <= res["step"] < 400
        assert (
            Path(task["checkpoint_dir"]) / "sft" / f"checkpoint-{res['step']}"
        ).is_dir()
        assert _progress(task)["step"] == res["step"]


# ---- errors ---------------------------------------------------------------


class TestErrors:
    @pytest.mark.parametrize(
        "mutate",
        [
            lambda t: t.pop("backend"),
            lambda t: t.update(backend="gpu"),
            lambda t: t.pop("pref_path"),
            lambda t: t.update(pref_path="/nonexistent/prefs.jsonl"),
            lambda t: t.update(hyperparams={"rank": 999, "lr": 1e-4, "epochs": 1}),
            lambda t: t.update(hyperparams="fast"),
            lambda t: t.update(resume="yes"),
            lambda t: t.pop("control_dir"),
        ],
    )
    def test_malformed_task_writes_error_result(
        self, tmp_path: Path, mutate: Any
    ) -> None:
        task = _make_task(tmp_path)
        mutate(task)
        assert _run(task, tmp_path) == w.EXIT_ERROR
        res = _result(task)
        assert res["status"] == "error"
        assert res["error"]
        assert res["gguf_path"] is None

    def test_malformed_jsonl_line(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        with open(task["pref_path"], "a") as fh:
            fh.write('{"system": "x", "user": "y"}\n')
        assert _run(task, tmp_path) == w.EXIT_ERROR
        err = _result(task)["error"]
        assert "prefs.jsonl" in err and "line 7" in err
        for s in SECRETS:
            assert s not in err

    def test_no_preference_pairs_is_an_error(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, n_pref=0)
        assert _run(task, tmp_path) == w.EXIT_ERROR
        assert "preference" in _result(task)["error"]

    def test_real_backend_requires_converter(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        task["backend"] = "real"
        task.pop("converter")
        assert _run(task, tmp_path) == w.EXIT_ERROR
        assert "converter" in _result(task)["error"]

    def test_unreadable_task_exits_1(self, tmp_path: Path) -> None:
        p = tmp_path / "task.json"
        p.write_text("{not json")
        assert w.main(["x", str(p)]) == w.EXIT_ERROR

    def test_usage(self) -> None:
        assert w.main(["x"]) == w.EXIT_USAGE

    def test_an_unexpected_error_never_quotes_its_message(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        # A library error message could quote a dataset row: only the type
        # reaches result.json and the log, never str(e).
        from metascan.workers import writer_train_fake as fake

        row_text = "ROWTEXT a private brief about a lighthouse"

        def boom(*a: Any, **kw: Any) -> Any:
            raise ValueError(f"bad example: {row_text}")

        monkeypatch.setattr(fake.FakeBackend, "train_dpo", boom)
        task = _make_task(tmp_path)
        with caplog.at_level(logging.DEBUG):
            assert _run(task, tmp_path) == w.EXIT_ERROR
        res = _result(task)
        assert res["status"] == "error"
        assert "ValueError" in res["error"]
        assert row_text not in json.dumps(res)
        assert row_text not in caplog.text
        assert "ValueError" in caplog.text

    def test_an_unexpected_error_before_the_run_never_quotes_it(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        row_text = "ROWTEXT from a task file"

        def boom(raw: Any) -> Any:
            raise KeyError(row_text)

        monkeypatch.setattr(w, "parse_task", boom)
        task = _make_task(tmp_path)
        with caplog.at_level(logging.DEBUG):
            assert _run(task, tmp_path) == w.EXIT_ERROR
        res = _result(task)
        assert "KeyError" in res["error"] and row_text not in json.dumps(res)
        assert row_text not in caplog.text

    def test_backend_failure_keeps_checkpoints(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, fake={"fail_at": {"stage": "dpo", "step": 7}})
        assert _run(task, tmp_path) == w.EXIT_ERROR
        res = _result(task)
        assert res["status"] == "error"
        assert "fake failure" in res["error"]
        # A failed run can still be resumed from its last checkpoint.
        assert (Path(task["checkpoint_dir"]) / "dpo" / "checkpoint-5").is_dir()


# ---- converter --------------------------------------------------------------


class TestConverter:
    def test_command(self) -> None:
        cmd = w.converter_command(
            {"python": "/py/python", "script": "/llama/convert_lora_to_gguf.py"},
            "/base",
            "/out/peft",
            "/out/adapter.gguf",
        )
        assert cmd == [
            "/py/python",
            "/llama/convert_lora_to_gguf.py",
            "--base",
            "/base",
            "--outtype",
            "f16",
            "--outfile",
            "/out/adapter.gguf",
            "/out/peft",
        ]

    def test_run_converter_success(self, tmp_path: Path) -> None:
        gguf = tmp_path / "a.gguf"

        def fake_run(cmd: List[str], **kw: Any) -> Any:
            gguf.write_bytes(b"GGUF")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(w.subprocess, "run", side_effect=fake_run) as run:
            w.run_converter({"python": "p", "script": "s"}, "b", "d", str(gguf))
        assert run.call_args.args[0][:2] == ["p", "s"]

    def test_run_converter_failure_reports_tail(self, tmp_path: Path) -> None:
        cp = subprocess.CompletedProcess([], 2, "", "line1\nKeyError: blk.0\n")
        with mock.patch.object(w.subprocess, "run", return_value=cp):
            with pytest.raises(w.TrainWorkerError, match="KeyError: blk.0"):
                w.run_converter(
                    {"python": "p", "script": "s"}, "b", "d", str(tmp_path / "x")
                )

    def test_run_converter_missing_output(self, tmp_path: Path) -> None:
        cp = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(w.subprocess, "run", return_value=cp):
            with pytest.raises(w.TrainWorkerError, match="no output"):
                w.run_converter(
                    {"python": "p", "script": "s"}, "b", "d", str(tmp_path / "x")
                )


# ---- real backend helpers (no deps needed) --------------------------------


class _Tok:
    """Stand-in tokenizer with a ChatML-ish template."""

    eos_token = "<|im_end|>"

    def apply_chat_template(
        self,
        messages: List[Dict[str, str]],
        tokenize: bool = False,
        add_generation_prompt: bool = False,
    ) -> str:
        s = "".join(
            f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in messages
        )
        return s + ("<|im_start|>assistant\n" if add_generation_prompt else "")


class TestRealHelpers:
    def test_messages(self) -> None:
        assert real.prompt_messages({"system": "S", "user": "U"}) == [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "U"},
        ]

    def test_render_pair_splits_at_the_generation_prompt(self) -> None:
        prompt, completion = real.render_pair(_Tok(), "S", "U", "C")
        assert prompt.endswith("<|im_start|>assistant\n")
        assert completion == "C<|im_end|>\n"

    def test_render_pair_falls_back_when_template_reorders(self) -> None:
        class Odd(_Tok):
            def apply_chat_template(
                self,
                messages: List[Dict[str, str]],
                tokenize: bool = False,
                add_generation_prompt: bool = False,
            ) -> str:
                return ("GEN" if add_generation_prompt else "FULL") + str(len(messages))

        prompt, completion = real.render_pair(Odd(), "S", "U", "C")
        assert prompt == "GEN2"
        assert completion == "C<|im_end|>"

    def test_lora_kwargs_restrict_to_language_model_for_vl(self) -> None:
        hp = w.validate_hyperparams({"rank": 16, "lr": 1e-4, "epochs": 1})
        kw = real.lora_config_kwargs(hp, is_vl=True)
        assert kw["r"] == 16 and kw["lora_alpha"] == 32
        assert kw["task_type"] == "CAUSAL_LM"
        import re

        pat = kw["target_modules"]
        assert isinstance(pat, str)
        hits = [
            "model.language_model.layers.0.self_attn.q_proj",
            "model.language_model.layers.35.mlp.down_proj",
            "base_model.model.model.language_model.layers.3.mlp.gate_proj",
        ]
        misses = [
            "model.visual.blocks.0.attn.qkv",
            "model.visual.blocks.0.mlp.linear_fc1",
            "model.visual.merger.linear_fc2",
            "model.visual.blocks.0.attn.proj",
            "lm_head",
            "model.language_model.layers.0.self_attn.q_norm",
        ]
        assert all(re.fullmatch(pat, h) for h in hits)
        assert not any(re.fullmatch(pat, m) for m in misses)

    def test_lora_kwargs_text_model_uses_list(self) -> None:
        hp = w.validate_hyperparams({"rank": 8, "lr": 1e-4, "epochs": 1})
        kw = real.lora_config_kwargs(hp, is_vl=False)
        assert kw["target_modules"] == list(real.LORA_TARGETS)

    def test_training_args(self, tmp_path: Path) -> None:
        hp = w.validate_hyperparams(
            {"rank": 8, "lr": 2e-4, "epochs": 3, "checkpoint_every": 50}
        )
        kw = real.training_args_kwargs(hp, tmp_path / "sft", total_steps=100)
        assert kw["output_dir"] == str(tmp_path / "sft")
        assert kw["learning_rate"] == 2e-4
        assert kw["num_train_epochs"] == 3
        assert kw["bf16"] is True and kw.get("fp16") is not True
        assert kw["save_strategy"] == "steps" and kw["save_steps"] == 50
        assert kw["seed"] == 0 and kw["data_seed"] == 0
        # Never truncate: over-long examples are dropped beforehand.
        assert kw["max_length"] is None
        assert kw["report_to"] == "none"
        assert kw["warmup_steps"] == 3

    def test_requires_cuda(self) -> None:
        fake_torch = mock.Mock()
        fake_torch.cuda.is_available.return_value = False
        with pytest.raises(w.TrainWorkerError, match="CUDA"):
            real.require_cuda(fake_torch)

    def test_missing_deps_reported(self) -> None:
        with mock.patch.object(
            real.importlib, "import_module", side_effect=ImportError("No module trl")
        ):
            with pytest.raises(w.TrainWorkerError, match="requirements-train.txt"):
                real.import_deps()


# ---- fix round 1: length cap, GGUF verification, DPO reference ------------

LONG = " ".join(["word"] * 100)  # > max_seq_len 64 in the fake's word count


def _meta(task: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = json.loads((Path(task["out_dir"]) / w.META_NAME).read_text())
    return out


class TestLengthDrops:
    def _task(self, tmp_path: Path, **kw: Any) -> Dict[str, Any]:
        return _make_task(tmp_path, hyperparams={"max_seq_len": 64}, **kw)

    def test_drops_are_counted_in_result_and_meta(self, tmp_path: Path) -> None:
        task = self._task(tmp_path)
        rows = [_sft(i) for i in range(4)]
        rows[0]["chosen"] = LONG
        _write_jsonl(Path(task["sft_path"]), rows)
        prefs = [_pref(i, f"p{i}") for i in range(6)]
        prefs[1]["rejected"] = LONG
        prefs[2]["user"] = LONG
        _write_jsonl(Path(task["pref_path"]), prefs)
        hold = [_pref(i, "h") for i in range(2)]
        hold[0]["system"] = LONG
        _write_jsonl(Path(task["holdout_path"]), hold)
        assert _run(task, tmp_path) == w.EXIT_OK
        want = {"sft": 1, "dpo": 2, "holdout": 1}
        assert _result(task)["dropped_too_long"] == want
        assert _meta(task)["dropped_too_long"] == want

    def test_all_sft_too_long_skips_sft(self, tmp_path: Path) -> None:
        task = self._task(tmp_path)
        _write_jsonl(
            Path(task["sft_path"]), [dict(_sft(i), chosen=LONG) for i in range(3)]
        )
        assert _run(task, tmp_path) == w.EXIT_OK
        assert not any(t.startswith("sft:") for t in _trace(task))
        assert _result(task)["dropped_too_long"]["sft"] == 3
        assert _meta(task)["dpo_reference"] == "base"
        assert _progress(task)["total_steps"] == 10

    def test_all_prefs_too_long_keeps_the_sft_adapter(self, tmp_path: Path) -> None:
        task = self._task(tmp_path)
        _write_jsonl(
            Path(task["pref_path"]),
            [dict(_pref(i, f"p{i}"), chosen=LONG) for i in range(6)],
        )
        assert _run(task, tmp_path) == w.EXIT_OK
        assert not any(t.startswith("dpo:") for t in _trace(task))
        res = _result(task)
        assert (Path(res["adapter_dir"]) / "adapter_config.json").exists()
        assert res["dropped_too_long"]["dpo"] == 6
        assert _meta(task)["dpo_reference"] is None

    def test_nothing_fits_is_an_error(self, tmp_path: Path) -> None:
        task = self._task(tmp_path)
        _write_jsonl(Path(task["sft_path"]), [dict(_sft(0), chosen=LONG)])
        _write_jsonl(
            Path(task["pref_path"]),
            [dict(_pref(i, f"p{i}"), chosen=LONG) for i in range(2)],
        )
        assert _run(task, tmp_path) == w.EXIT_ERROR
        res = _result(task)
        assert "longer than 64 tokens" in res["error"]
        assert res["dropped_too_long"] == {"sft": 1, "dpo": 2, "holdout": 0}


class TestGgufVerification:
    def test_meta_records_count_and_path(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, fake={"converter_path": "remapped"})
        assert _run(task, tmp_path) == w.EXIT_OK
        meta = _meta(task)
        assert meta["gguf_lora_tensors"] == 36 * 7 * 2
        assert meta["converter_path"] == "remapped"

    def test_too_few_lora_tensors_fails_and_keeps_peft(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, fake={"gguf_layers": 35})
        assert _run(task, tmp_path) == w.EXIT_ERROR
        res = _result(task)
        assert "490 LoRA tensor(s), expected 504" in res["error"]
        assert res["stage"] == "convert"
        assert (Path(task["out_dir"]) / w.PEFT_DIRNAME / "adapter_config.json").exists()
        # Resumable: the checkpoint state still says convert.
        state = json.loads((Path(task["checkpoint_dir"]) / w.STATE_NAME).read_text())
        assert state["stage"] == "convert"

    def test_vision_tensors_fail(self, tmp_path: Path) -> None:
        task = _make_task(
            tmp_path, fake={"gguf_extra_names": ["v.blk.0.attn_q.weight.lora_a"]}
        )
        assert _run(task, tmp_path) == w.EXIT_ERROR
        assert "vision tensor" in _result(task)["error"]

    @pytest.mark.parametrize(
        "name",
        [
            "v.blk.0.ffn_up.weight.lora_b",
            "mm.0.weight.lora_a",
            "mmproj.x",
            "blk.0.visual.lora_a",
        ],
    )
    def test_verify_rejects_vision_names(self, name: str) -> None:
        with pytest.raises(w.TrainWorkerError, match="vision"):
            w.verify_gguf_names(["blk.0.attn_q.weight.lora_a", name], 1)

    def test_verify_counts(self) -> None:
        names = [
            "blk.0.attn_q.weight.lora_a",
            "blk.0.attn_q.weight.lora_b",
            "token_embd",
        ]
        assert w.verify_gguf_names(names, 2) == 2
        with pytest.raises(w.TrainWorkerError, match="expected 3"):
            w.verify_gguf_names(names, 3)

    def test_expected_from_text_config(self, tmp_path: Path) -> None:
        (tmp_path / "config.json").write_text(
            json.dumps(
                {
                    "model_type": "qwen3_vl",
                    "text_config": {"num_hidden_layers": 36},
                    "vision_config": {"depth": 27},
                }
            )
        )
        assert w.expected_lora_tensors(str(tmp_path)) == 504
        (tmp_path / "config.json").write_text(json.dumps({"num_hidden_layers": 2}))
        assert w.expected_lora_tensors(str(tmp_path)) == 28
        (tmp_path / "config.json").write_text("{}")
        with pytest.raises(w.TrainWorkerError):
            w.expected_lora_tensors(str(tmp_path))

    @pytest.mark.skipif(
        importlib.util.find_spec("gguf") is not None, reason="gguf installed"
    )
    def test_missing_gguf_package_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(w.TrainWorkerError, match="gguf package"):
            w.read_gguf_tensor_names(tmp_path / "x.gguf")


class TestConverterFallback:
    MAP_ERR = (
        "converter failed (exit 1): ValueError: Can not map tensor "
        "'model.language_model.layers.0.self_attn.q_proj.weight'"
    )

    def test_key_remap(self) -> None:
        keys = {
            "base_model.model.model.language_model.layers.0.self_attn.q_proj.lora_A.weight": 1,
            "base_model.model.model.language_model.layers.3.mlp.down_proj.lora_B.weight": 2,
            "base_model.model.lm_head.weight": 3,
        }
        assert {w.remap_language_model_key(k): v for k, v in keys.items()} == {
            "base_model.model.model.layers.0.self_attn.q_proj.lora_A.weight": 1,
            "base_model.model.model.layers.3.mlp.down_proj.lora_B.weight": 2,
            "base_model.model.lm_head.weight": 3,
        }

    def test_detects_the_mapping_error(self) -> None:
        assert w.is_unmapped_language_model_error(self.MAP_ERR)
        assert not w.is_unmapped_language_model_error("converter failed: out of memory")

    def _peft(self, tmp_path: Path) -> Path:
        np = pytest.importorskip("numpy")
        st = pytest.importorskip("safetensors.numpy")
        peft = tmp_path / "out" / "peft"
        peft.mkdir(parents=True)
        st.save_file(
            {
                "base_model.model.model.language_model.layers.0.self_attn.q_proj.lora_A.weight": np.ones(
                    (4, 8), dtype=np.float32
                ),
                "base_model.model.model.language_model.layers.0.self_attn.q_proj.lora_B.weight": np.zeros(
                    (8, 4), dtype=np.float32
                ),
            },
            str(peft / "adapter_model.safetensors"),
            metadata={"format": "pt"},
        )
        (peft / "adapter_config.json").write_text(
            json.dumps(
                {"r": 4, "lora_alpha": 8, "target_modules": real.VL_TARGET_REGEX}
            )
        )
        return peft

    def test_retries_once_on_a_remapped_copy(self, tmp_path: Path) -> None:
        peft = self._peft(tmp_path)
        before = (peft / "adapter_model.safetensors").read_bytes()
        scratch = tmp_path / "ckpt"
        scratch.mkdir()
        calls: List[str] = []

        def fake_run(converter: Any, base: str, peft_dir: str, gguf: str) -> None:
            calls.append(peft_dir)
            if len(calls) == 1:
                raise w.TrainWorkerError(self.MAP_ERR)

        with mock.patch.object(w, "run_converter", side_effect=fake_run):
            path = w.convert_with_fallback(
                {"python": "p", "script": "s"},
                "/base",
                peft,
                tmp_path / "a.gguf",
                scratch,
            )
        assert path == "remapped"
        remapped = scratch / w.REMAP_DIRNAME
        assert calls == [str(peft), str(remapped)]
        from safetensors.numpy import load_file

        keys = sorted(load_file(str(remapped / "adapter_model.safetensors")))
        assert keys == [
            "base_model.model.model.layers.0.self_attn.q_proj.lora_A.weight",
            "base_model.model.model.layers.0.self_attn.q_proj.lora_B.weight",
        ]
        cfg = json.loads((remapped / "adapter_config.json").read_text())
        assert cfg["target_modules"] == list(w.LORA_TARGETS)
        assert cfg["r"] == 4
        # The original PEFT dir ("Continue" uses it) is untouched.
        assert (peft / "adapter_model.safetensors").read_bytes() == before
        assert json.loads((peft / "adapter_config.json").read_text())[
            "target_modules"
        ] == (real.VL_TARGET_REGEX)

    def test_direct_success_does_not_retry(self, tmp_path: Path) -> None:
        with mock.patch.object(w, "run_converter") as run:
            path = w.convert_with_fallback(
                {"python": "p", "script": "s"},
                "/b",
                tmp_path,
                tmp_path / "a.gguf",
                tmp_path,
            )
        assert path == "direct" and run.call_count == 1

    def test_other_errors_are_not_retried(self, tmp_path: Path) -> None:
        err = w.TrainWorkerError("converter failed (exit 1): MemoryError")
        with mock.patch.object(w, "run_converter", side_effect=err) as run:
            with pytest.raises(w.TrainWorkerError, match="MemoryError"):
                w.convert_with_fallback(
                    {"python": "p", "script": "s"},
                    "/b",
                    tmp_path,
                    tmp_path / "a.gguf",
                    tmp_path,
                )
        assert run.call_count == 1


class TestDpoReference:
    FIELDS = ["beta", "learning_rate", "model_adapter_name", "ref_adapter_name"]

    def test_mode(self) -> None:
        assert real.dpo_reference_mode(None, self.FIELDS) == "disabled"
        assert real.dpo_reference_mode("/sft", self.FIELDS) == "adapter"
        assert real.dpo_reference_mode("/sft", ["beta"]) == "copy"

    def test_dpo_args(self, tmp_path: Path) -> None:
        hp = w.validate_hyperparams(
            {"rank": 8, "lr": 1e-4, "epochs": 1, "dpo_lr": 2e-5}
        )
        kw = real.dpo_args_kwargs(hp, tmp_path, 10, "adapter")
        assert kw["learning_rate"] == 2e-5
        assert kw["beta"] == 0.1
        assert kw["model_adapter_name"] == "default"
        assert kw["ref_adapter_name"] == "reference"
        assert kw["max_length"] is None
        plain = real.dpo_args_kwargs(hp, tmp_path, 10, "disabled")
        assert "ref_adapter_name" not in plain and "model_adapter_name" not in plain

    def test_reference_recorded(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        assert _run(task, tmp_path) == w.EXIT_OK
        assert _meta(task)["dpo_reference"] == "sft_adapter"

    def test_reference_without_sft(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, n_sft=0)
        task["fake"].pop("sft_steps")
        assert _run(task, tmp_path) == w.EXIT_OK
        assert _meta(task)["dpo_reference"] == "base"

    def test_continue_adapter_records_its_lora(self, tmp_path: Path) -> None:
        init = tmp_path / "adapters" / "1"
        init.mkdir(parents=True)
        (init / "adapter_config.json").write_text(json.dumps({"r": 4, "lora_alpha": 4}))
        task = _make_task(tmp_path, n_sft=0)
        task["fake"].pop("sft_steps")
        task["init_adapter_dir"] = str(init)
        assert _run(task, tmp_path) == w.EXIT_OK
        meta = _meta(task)
        assert meta["dpo_reference"] == "init_adapter"
        assert meta["init_adapter_lora"] == {"r": 4, "lora_alpha": 4}
        assert meta["adapter_lora"] == {"r": 4, "lora_alpha": 4}
        assert meta["hyperparams"]["rank"] == 8

    def test_fresh_adapter_lora_in_meta(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        assert _run(task, tmp_path) == w.EXIT_OK
        assert _meta(task)["adapter_lora"] == {"r": 8, "lora_alpha": 16}
        assert _meta(task)["init_adapter_lora"] is None

    def test_adapter_keys_check(self) -> None:
        ok = [
            "base_model.model.model.language_model.layers.0.self_attn.q_proj.lora_A.weight"
        ]
        assert real.check_adapter_keys(ok) == 1
        with pytest.raises(w.TrainWorkerError, match="vision"):
            real.check_adapter_keys(
                ok + ["base_model.model.model.visual.blocks.0.attn.qkv.lora_A.weight"]
            )
        with pytest.raises(w.TrainWorkerError, match="no LoRA"):
            real.check_adapter_keys(["base_model.model.lm_head.weight"])


class TestGuardsAndErrors:
    def test_dirs_must_be_inside_data_root(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path / "root")
        task["checkpoint_dir"] = str(tmp_path / "elsewhere")
        assert _run(task, tmp_path) == w.EXIT_ERROR
        assert "data_root" in _result(task)["error"]
        task2 = _make_task(tmp_path / "root2")
        task2["out_dir"] = task2["data_root"]
        assert _run(task2, tmp_path, "t2.json") == w.EXIT_ERROR

    def test_refuses_to_wipe_unexpected_checkpoint_dir(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        ck = Path(task["checkpoint_dir"])
        ck.mkdir()
        (ck / "precious.txt").write_text("keep me")
        assert _run(task, tmp_path) == w.EXIT_ERROR
        assert "refusing to delete" in _result(task)["error"]
        assert (ck / "precious.txt").read_text() == "keep me"

    def test_cancel_refuses_unexpected_peft_dir(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        peft = Path(task["out_dir"]) / w.PEFT_DIRNAME
        peft.mkdir(parents=True)
        (peft / "notes").mkdir()
        Path(task["control_dir"]).mkdir()
        (Path(task["control_dir"]) / w.CANCEL_SIGNAL).write_text("")
        assert _run(task, tmp_path) == w.EXIT_ERROR
        assert (peft / "notes").is_dir()

    def test_error_result_carries_stage_step_and_counts(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, fake={"fail_at": {"stage": "dpo", "step": 7}})
        assert _run(task, tmp_path) == w.EXIT_ERROR
        res = _result(task)
        assert res["stage"] == "dpo" and res["step"] == 6
        assert (res["n_sft"], res["n_pref"], res["n_holdout"]) == (4, 6, 2)

    def test_eta_clock_starts_at_the_first_step(self, tmp_path: Path) -> None:
        ctx = w.StageContext(tmp_path, w.Control(tmp_path), {"sft": 10, "dpo": 0}, 1)
        clock = [100.0]
        with mock.patch.object(w.time, "monotonic", side_effect=lambda: clock[0]):
            ctx.begin("sft", 10)
            clock[0] = 400.0  # model load: must not count
            ctx.report("sft", 1)
            assert json.loads((tmp_path / w.PROGRESS_NAME).read_text())["eta_s"] is None
            clock[0] = 402.0
            ctx.report("sft", 2)
            assert json.loads((tmp_path / w.PROGRESS_NAME).read_text())["eta_s"] == 16.0
            ctx.begin("dpo", 0)  # a new stage restarts the clock
            assert json.loads((tmp_path / w.PROGRESS_NAME).read_text())["eta_s"] is None


# ---- fix round 2: TRL probe, empty-pairs messages, leftover checkpoints ---

from metascan.workers import writer_train_probe as probe  # noqa: E402


def _cp(stdout: str, rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout, "")


class TestTrlProbe:
    def test_ok(self) -> None:
        out = json.dumps({"installed": True, "version": "0.25.1", "missing": []})
        with mock.patch.object(probe.subprocess, "run", return_value=_cp(out)) as run:
            assert probe.probe_trl_reference_support("/py") == (True, "")
        assert run.call_args.args[0][0] == "/py"

    def test_missing_fields(self) -> None:
        out = json.dumps(
            {"installed": True, "version": "1.16.0", "missing": ["ref_adapter_name"]}
        )
        with mock.patch.object(probe.subprocess, "run", return_value=_cp(out)):
            ok, reason = probe.probe_trl_reference_support()
        assert not ok
        assert "trl 1.16.0 lacks DPOConfig.ref_adapter_name" in reason
        assert "requirements-train.txt" in reason

    def test_not_installed_or_broken(self) -> None:
        out = json.dumps({"installed": False, "error": "ModuleNotFoundError"})
        with mock.patch.object(probe.subprocess, "run", return_value=_cp(out)):
            assert probe.probe_trl_reference_support()[1].startswith(
                "trl is not installed"
            )
        with mock.patch.object(probe.subprocess, "run", return_value=_cp("junk", 1)):
            assert probe.probe_trl_reference_support() == (
                False,
                "the TRL probe failed (exit 1)",
            )
        with mock.patch.object(probe.subprocess, "run", side_effect=OSError("nope")):
            assert not probe.probe_trl_reference_support()[0]

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX wrapper script")
    def test_real_subprocess_with_a_stand_in_trl(self, tmp_path: Path) -> None:
        pkg = tmp_path / "site" / "trl"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(
            "import dataclasses\n__version__ = '0.25.0'\n"
            "@dataclasses.dataclass\nclass DPOConfig:\n"
            "    beta: float = 0.1\n    model_adapter_name: str = ''\n"
            "    ref_adapter_name: str = ''\n"
        )
        wrapper = tmp_path / "py"
        wrapper.write_text(
            f'#!/bin/sh\nPYTHONPATH="{tmp_path / "site"}" exec "{sys.executable}" "$@"\n'
        )
        wrapper.chmod(0o755)
        assert probe.probe_trl_reference_support(str(wrapper)) == (True, "")
        (pkg / "__init__.py").write_text(
            "import dataclasses\n__version__ = '1.0.0'\n"
            "@dataclasses.dataclass\nclass DPOConfig:\n    beta: float = 0.1\n"
        )
        ok, reason = probe.probe_trl_reference_support(str(wrapper))
        assert not ok and "model_adapter_name" in reason


class TestRound2:
    def test_nothing_at_all(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, n_sft=0, n_pref=0)
        assert _run(task, tmp_path) == w.EXIT_ERROR
        err = _result(task)["error"]
        assert "no training examples" in err and "longer" not in err

    def _patched_data(self, task: Dict[str, Any], sft: List[Dict[str, Any]]) -> Any:
        data = w.TrainData(sft=sft, prefs=[], holdout=[])
        return mock.patch.object(w, "load_data", return_value=data)

    def test_no_pairs_with_usable_sft_keeps_the_sft_adapter(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        task = _make_task(tmp_path)
        with self._patched_data(task, [_sft(i) for i in range(3)]):
            assert _run(task, tmp_path) == w.EXIT_OK
        assert "no preference pairs; DPO skipped" in caplog.text
        assert "longer than" not in caplog.text

    def test_no_pairs_and_all_sft_too_long(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path, hyperparams={"max_seq_len": 64})
        with self._patched_data(task, [dict(_sft(0), chosen=LONG)]):
            assert _run(task, tmp_path) == w.EXIT_ERROR
        err = _result(task)["error"]
        assert err.startswith("no preference pairs and no usable SFT example")

    def test_refused_final_wipe_keeps_the_run_ok(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        original = w.safe_rmtree
        gguf = Path(task["out_dir"]) / w.GGUF_NAME

        def refuse_late(path: Path, root: Path, allowed: Any) -> None:
            if Path(path) == Path(task["checkpoint_dir"]) and gguf.exists():
                raise w.TrainWorkerError(
                    f"refusing to delete {path}: unexpected entries"
                )
            original(path, root, allowed)

        with mock.patch.object(w, "safe_rmtree", side_effect=refuse_late):
            assert _run(task, tmp_path) == w.EXIT_OK
        res = _result(task)
        assert res["status"] == "ok" and res["error"] is None
        assert res["leftover_checkpoint_dir"] == task["checkpoint_dir"]
        assert Path(task["checkpoint_dir"]).is_dir()
        assert (Path(task["out_dir"]) / w.META_NAME).exists()

    def test_clean_run_has_no_leftover(self, tmp_path: Path) -> None:
        task = _make_task(tmp_path)
        assert _run(task, tmp_path) == w.EXIT_OK
        assert _result(task)["leftover_checkpoint_dir"] is None


def _cuda_and_deps() -> bool:
    for mod in ("torch", "transformers", "peft", "trl", "datasets"):
        if importlib.util.find_spec(mod) is None:
            return False
    import torch

    return bool(torch.cuda.is_available())


@pytest.mark.skipif(not _cuda_and_deps(), reason="needs CUDA + requirements-train.txt")
def test_real_backend_smoke(tmp_path: Path) -> None:  # pragma: no cover - CUDA only
    """A tiny random text model trained for a couple of steps (offline when
    cached; the owner runs this on the CUDA PC). Conversion is mocked."""
    from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer

    base = tmp_path / "base"
    try:
        tok = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-0.5B-Instruct")
    except Exception:
        pytest.skip("tokenizer not cached")
    cfg = AutoConfig.from_pretrained("Qwen/Qwen2.5-0.5B-Instruct")
    cfg.num_hidden_layers = 2
    cfg.hidden_size = 64
    cfg.intermediate_size = 128
    cfg.num_attention_heads = 4
    cfg.num_key_value_heads = 2
    AutoModelForCausalLM.from_config(cfg).save_pretrained(base)
    tok.save_pretrained(base)
    task = _make_task(tmp_path, hyperparams={"grad_accum": 1, "checkpoint_every": 2})
    task.update(backend="real", base_dir=str(base))
    task.pop("fake")

    def fake_convert(converter: Any, base_dir: str, peft: str, gguf: str) -> None:
        Path(gguf).write_bytes(b"GGUF")

    names = [
        f"blk.{i}.t{k}.weight.lora_{ab}"
        for i in range(2)
        for k in range(7)
        for ab in "ab"
    ]
    with mock.patch.object(
        w, "run_converter", side_effect=fake_convert
    ), mock.patch.object(w, "read_gguf_tensor_names", return_value=names):
        assert _run(task, tmp_path) == w.EXIT_OK
    res = _result(task)
    assert res["status"] == "ok"
    assert (Path(res["adapter_dir"]) / "adapter_config.json").exists()
    assert res["holdout_agreement"] is not None
    assert _meta(task)["dpo_reference"] == "sft_adapter"
    assert _meta(task)["gguf_lora_tensors"] == 28
