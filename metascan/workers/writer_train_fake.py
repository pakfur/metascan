"""Fake training backend for the writer LoRA worker: no torch, no GPU.

A deterministic step loop that honours the same control files, checkpoint
layout (``<checkpoint_dir>/<stage>/checkpoint-<step>/``) and state.json as
the real backend, computes a dummy agreement and writes a dummy PEFT dir and
``.gguf``. Used by the worker's tests and by the training manager's tests.

Knobs (``task.fake``, all optional)::

    {"sft_steps": int, "dpo_steps": int,   # default: from dataset size
     "step_sleep_s": float,                # per step, default 0
     "progress_every": int,                # progress.json cadence, default 2
     "signal_at": {"stage", "step", "file": "pause.signal"},
                                           # drop a control file at that step
     "fail_at": {"stage", "step"},         # raise at that step
     "text_layers": int,                   # base text layers, default 36
     "gguf_layers": int,                   # layers the dummy GGUF carries
     "gguf_extra_names": [str],            # extra GGUF tensor names
     "converter_path": "direct" | "remapped"}

Length filter: an example "fits" when the whitespace-separated words of
system + user + completion number at most ``max_seq_len`` (a stand-in for
tokens); the rest are dropped and counted like the real backend does.

Every executed step is appended to ``<out_dir>/fake_trace.txt`` as
``<stage>:<step>`` so tests can see what ran (and what a resume skipped).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from metascan.workers import writer_train_worker as w


class FakeBackend:
    def __init__(self, task: "w.TrainTask") -> None:
        self.task = task
        self.knobs: Dict[str, Any] = dict(task.fake)
        self.sleep = float(self.knobs.get("step_sleep_s") or 0.0)
        self.trace = task.out_dir / "fake_trace.txt"

    # -- helpers --------------------------------------------------------

    def _match(self, key: str, stage: str, step: int) -> Optional[Dict[str, Any]]:
        spec = self.knobs.get(key)
        if (
            isinstance(spec, dict)
            and spec.get("stage") == stage
            and spec.get("step") == step
        ):
            return spec
        return None

    def _save_checkpoint(
        self, ctx: "w.StageContext", stage: str, step: int, loss: float
    ) -> None:
        ck = self.task.checkpoint_dir / stage / f"checkpoint-{step}"
        ck.mkdir(parents=True, exist_ok=True)
        (ck / "fake_state.json").write_text(json.dumps({"step": step, "loss": loss}))
        ctx.note_checkpoint(stage, step)

    def _write_adapter(self, path: Path, init_adapter: Optional[str]) -> None:
        """A dummy PEFT dir; continuing an adapter keeps its rank/alpha."""
        hp = self.task.hyperparams
        cfg: Dict[str, Any] = {
            "peft_type": "LORA",
            "r": hp["rank"],
            "lora_alpha": hp["lora_alpha"],
            "fake": True,
        }
        if init_adapter:
            src = json.loads((Path(init_adapter) / "adapter_config.json").read_text())
            cfg.update(r=src.get("r"), lora_alpha=src.get("lora_alpha"))
        path.mkdir(parents=True, exist_ok=True)
        (path / "adapter_config.json").write_text(json.dumps(cfg))
        (path / "adapter_model.safetensors").write_bytes(b"\0" * 16)

    def _fits(self, ex: Dict[str, Any], key: str) -> bool:
        words = len(f"{ex['system']} {ex['user']} {ex[key]}".split())
        return words <= int(self.task.hyperparams["max_seq_len"])

    def _keep(
        self,
        ctx: "w.StageContext",
        examples: List[Dict[str, Any]],
        keys: Tuple[str, ...],
        kind: str,
    ) -> List[Dict[str, Any]]:
        kept = [ex for ex in examples if all(self._fits(ex, k) for k in keys)]
        ctx.dropped[kind] = len(examples) - len(kept)
        return kept

    def _loop(
        self,
        ctx: "w.StageContext",
        stage: str,
        resume_from: Optional[Path],
    ) -> None:
        total = ctx.totals.get(stage, 0)
        every = int(self.task.hyperparams["checkpoint_every"])
        start = w.checkpoint_step(resume_from)
        for step in range(start + 1, total + 1):
            if self.sleep:
                time.sleep(self.sleep)
            with open(self.trace, "a") as fh:
                fh.write(f"{stage}:{step}\n")
            if self._match("fail_at", stage, step):
                raise w.TrainWorkerError(f"fake failure at {stage} step {step}")
            loss = round(2.0 / (1.0 + 0.1 * step), 4)
            if step % every == 0:
                self._save_checkpoint(ctx, stage, step, loss)
            ctx.report(stage, step, loss)
            signal = self._match("signal_at", stage, step)
            if signal:
                ctx.control.dir.mkdir(parents=True, exist_ok=True)
                (
                    ctx.control.dir / str(signal.get("file") or w.PAUSE_SIGNAL)
                ).write_text("")
            action = ctx.check()
            if action == "cancel":
                raise w.Cancelled()
            if action == "pause":
                if step % every:
                    self._save_checkpoint(ctx, stage, step, loss)
                ctx.report(stage, step, force=True)
                raise w.Paused(stage, step)

    # -- Backend protocol -----------------------------------------------

    def train_sft(
        self,
        ctx: "w.StageContext",
        examples: List[Dict[str, Any]],
        init_adapter: Optional[str],
        resume_from: Optional[Path],
        save_to: Path,
    ) -> bool:
        if not self._keep(ctx, examples, ("chosen",), "sft"):
            return False
        self._loop(ctx, "sft", resume_from)
        self._write_adapter(save_to, init_adapter)
        return True

    def train_dpo(
        self,
        ctx: "w.StageContext",
        examples: List[Dict[str, Any]],
        init_adapter: Optional[str],
        resume_from: Optional[Path],
        save_to: Path,
    ) -> bool:
        if not self._keep(ctx, examples, ("chosen", "rejected"), "dpo"):
            return False
        self._loop(ctx, "dpo", resume_from)
        self._write_adapter(save_to, init_adapter)
        return True

    def evaluate(
        self,
        ctx: "w.StageContext",
        holdout: List[Dict[str, Any]],
        adapter_dir: Path,
    ) -> Tuple[Optional[float], Optional[float]]:
        holdout = self._keep(ctx, holdout, ("chosen", "rejected"), "holdout")
        if not holdout:
            return None, None
        # Dummy but deterministic: "base" prefers the shorter completion,
        # "adapter" the longer one.
        base = adapter = 0
        for i, ex in enumerate(holdout, start=1):
            longer = len(ex["chosen"]) > len(ex["rejected"])
            adapter += int(longer)
            base += int(not longer)
            ctx.report("eval", i)
        n = len(holdout)
        return adapter / n, base / n

    def convert(self, ctx: "w.StageContext", peft_dir: Path, gguf_path: Path) -> str:
        gguf_path.write_bytes(b"GGUF\x03\x00\x00\x00fake-lora")
        return str(self.knobs.get("converter_path") or "direct")

    def gguf_tensor_names(self, gguf_path: Path) -> List[str]:
        layers = int(self.knobs.get("gguf_layers", self.knobs.get("text_layers", 36)))
        kinds = (
            "attn_q",
            "attn_k",
            "attn_v",
            "attn_output",
            "ffn_gate",
            "ffn_up",
            "ffn_down",
        )
        names = [
            f"blk.{i}.{k}.weight.lora_{ab}"
            for i in range(layers)
            for k in kinds
            for ab in "ab"
        ]
        return names + [str(n) for n in self.knobs.get("gguf_extra_names") or []]

    def expected_lora_tensors(self) -> int:
        return int(self.knobs.get("text_layers", 36)) * len(w.LORA_TARGETS) * 2
