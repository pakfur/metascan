"""Tests for the prompt-writer LoRA training worker (taste learning project 4).

Everything runs on the ``fake`` backend: no CUDA, no torch / transformers /
peft / trl. The real backend's pure helpers (argument building, chat
rendering, converter command) are tested with stand-ins; one smoke test
needs CUDA and the training deps and is skipped otherwise.
"""

from __future__ import annotations

import importlib.util
import json
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
    data.mkdir(exist_ok=True)
    _write_jsonl(data / "sft.jsonl", [_sft(i) for i in range(n_sft)])
    _write_jsonl(data / "prefs.jsonl", [_pref(i, f"p{i}") for i in range(n_pref)])
    _write_jsonl(data / "holdout.jsonl", [_pref(i, "h") for i in range(n_hold)])
    hp = {"rank": 8, "lr": 1e-4, "epochs": 1, "checkpoint_every": 5}
    hp.update(hyperparams or {})
    return {
        "backend": "fake",
        "base_dir": str(tmp_path / "base"),
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
            "quick": {"rank": 8, "lr": 1e-4, "epochs": 1},
            "standard": {"rank": 16, "lr": 1e-4, "epochs": 2},
            "thorough": {"rank": 32, "lr": 5e-5, "epochs": 3},
        }

    def test_defaults_filled(self) -> None:
        hp = w.validate_hyperparams({"rank": 16, "lr": 1e-4, "epochs": 2})
        assert hp["lora_alpha"] == 32
        assert hp["beta"] == 0.1
        assert hp["max_seq_len"] == 1024
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
            "metascan.workers.writer_train_fake\n"
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
        assert kw["max_length"] == 1024
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

    with mock.patch.object(w, "run_converter", side_effect=fake_convert):
        assert _run(task, tmp_path) == w.EXIT_OK
    res = _result(task)
    assert res["status"] == "ok"
    assert (Path(res["adapter_dir"]) / "adapter_config.json").exists()
    assert res["holdout_agreement"] is not None
