"""Real training backend for the writer LoRA worker: TRL + PEFT on CUDA.

Imported only by ``writer_train_worker`` for ``backend: "real"``. The
training stack (torch, transformers, peft, trl, datasets -- see
``requirements-train.txt``) is imported lazily inside ``import_deps`` so
importing this module costs nothing and the FastAPI process never loads
it. The pure helpers (``render_pair``, ``lora_config_kwargs``,
``training_args_kwargs``) are unit-tested without the deps.

Method (global constraints): LoRA in bf16 on the language model only --
``q/k/v/o_proj`` and ``gate/up/down_proj`` -- the vision tower frozen and
untouched; SFT (loss on the assistant completion only) then DPO (beta 0.1,
reference = the same model with the adapter disabled); max sequence 1024
tokens; seed 0. Inputs are text-only chats built with the base tokenizer's
chat template: system + user, assistant = the target.
"""

from __future__ import annotations

import gc
import importlib
import logging
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from metascan.workers import writer_train_worker as w

logger = logging.getLogger("writer_train_worker")

LORA_TARGETS = (
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
)
# Full-match on the module name: only Linear layers under the language model
# (``model.language_model.layers.N...`` in transformers' Qwen3-VL), never
# the vision tower (``model.visual...``: qkv/proj/linear_fc*).
VL_TARGET_REGEX = r".*language_model\..*\.(" + "|".join(LORA_TARGETS) + r")"

_DEPS = ("torch", "transformers", "peft", "trl", "datasets")


def import_deps() -> Any:
    mods: Dict[str, Any] = {}
    for name in _DEPS:
        try:
            mods[name] = importlib.import_module(name)
        except ImportError as e:
            raise w.TrainWorkerError(
                f"training dependencies missing ({e}); install them with "
                "pip install -r requirements-train.txt"
            ) from None
    return SimpleNamespace(**mods)


def require_cuda(torch: Any) -> None:
    if not torch.cuda.is_available():
        raise w.TrainWorkerError("writer training needs a CUDA GPU (none available)")
    if not torch.cuda.is_bf16_supported():
        raise w.TrainWorkerError("writer training needs a GPU with bf16 support")


# ---- pure helpers ------------------------------------------------------


def prompt_messages(ex: Dict[str, Any]) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": ex["system"]},
        {"role": "user", "content": ex["user"]},
    ]


def render_pair(tok: Any, system: str, user: str, completion: str) -> Tuple[str, str]:
    """``(prompt_text, completion_text)`` exactly as the chat template lays
    them out: the prompt ends with the assistant generation header and the
    completion carries the template's end-of-turn. Same split TRL makes for
    conversational prompt/completion data. When the template does not
    render the prompt as a prefix of the full chat, falls back to
    ``completion + eos``."""
    msgs = prompt_messages({"system": system, "user": user})
    prompt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    full = tok.apply_chat_template(
        msgs + [{"role": "assistant", "content": completion}], tokenize=False
    )
    if isinstance(full, str) and full.startswith(prompt):
        return prompt, full[len(prompt) :]
    return prompt, completion + (getattr(tok, "eos_token", None) or "")


def lora_config_kwargs(hp: Dict[str, Any], is_vl: bool) -> Dict[str, Any]:
    return {
        "r": int(hp["rank"]),
        "lora_alpha": int(hp["lora_alpha"]),
        "lora_dropout": float(hp["lora_dropout"]),
        "bias": "none",
        "task_type": "CAUSAL_LM",
        "target_modules": VL_TARGET_REGEX if is_vl else list(LORA_TARGETS),
    }


def training_args_kwargs(
    hp: Dict[str, Any], output_dir: Path, total_steps: int
) -> Dict[str, Any]:
    """Arguments shared by ``SFTConfig`` and ``DPOConfig``."""
    return {
        "output_dir": str(output_dir),
        "num_train_epochs": int(hp["epochs"]),
        "learning_rate": float(hp["lr"]),
        "per_device_train_batch_size": int(hp["batch_size"]),
        "gradient_accumulation_steps": int(hp["grad_accum"]),
        "lr_scheduler_type": "cosine",
        "warmup_steps": int(math.ceil(float(hp["warmup_ratio"]) * max(0, total_steps))),
        "bf16": True,
        "max_length": int(hp["max_seq_len"]),
        "gradient_checkpointing": True,
        "gradient_checkpointing_kwargs": {"use_reentrant": False},
        "save_strategy": "steps",
        "save_steps": int(hp["checkpoint_every"]),
        "save_total_limit": 2,
        "logging_steps": 1,
        "report_to": "none",
        "disable_tqdm": True,
        "seed": int(hp["seed"]),
        "data_seed": int(hp["seed"]),
    }


def _config(cls: Any, kwargs: Dict[str, Any], optional: Dict[str, Any]) -> Any:
    """Build a TRL config; ``optional`` keys are passed only when this TRL
    version still has the field (e.g. ``max_prompt_length``, removed in
    newer TRL and defaulting to a truncating 512 in older ones)."""
    import dataclasses

    names = {f.name for f in dataclasses.fields(cls)}
    kw = dict(kwargs)
    kw.update({k: v for k, v in optional.items() if k in names})
    return cls(**kw)


# ---- the backend ----------------------------------------------------------


class RealBackend:
    def __init__(self, task: "w.TrainTask") -> None:
        self.task = task
        self.hp = task.hyperparams
        self.deps: Any = None
        self.model: Any = None
        self.tok: Any = None
        self.is_vl = False
        self._adapter_src: Optional[str] = None
        self._loaded = False

    # -- model --------------------------------------------------------------

    def _ensure(self, init_adapter: Optional[str]) -> None:
        if self.deps is None:
            self.deps = import_deps()
            require_cuda(self.deps.torch)
            self.deps.transformers.set_seed(int(self.hp["seed"]))
        if self._loaded and self._adapter_src == init_adapter:
            return
        self._release()
        d = self.deps
        tf = d.transformers
        base = self.task.base_dir
        logger.info("writer train: loading base model (bf16, CUDA)")
        config = tf.AutoConfig.from_pretrained(base)
        self.is_vl = getattr(config, "vision_config", None) is not None
        if self.is_vl:
            cls = None
            if getattr(config, "model_type", "") == "qwen3_vl":
                cls = getattr(tf, "Qwen3VLForConditionalGeneration", None)
            cls = cls or tf.AutoModelForImageTextToText
        else:
            cls = tf.AutoModelForCausalLM
        model = cls.from_pretrained(
            base,
            dtype=d.torch.bfloat16,
            device_map={"": 0},
            attn_implementation="sdpa",
        )
        model.config.use_cache = False
        for p in model.parameters():  # vision tower and base stay frozen
            p.requires_grad_(False)
        tok = tf.AutoTokenizer.from_pretrained(base)
        if not getattr(tok, "chat_template", None) and self.is_vl:
            # Some VL repos ship the template only as the processor's
            # chat_template.json; training is text-only, so lend it to the
            # tokenizer (TRL gets the tokenizer, never the processor).
            proc = tf.AutoProcessor.from_pretrained(base)
            tok.chat_template = getattr(proc, "chat_template", None)
        if not getattr(tok, "chat_template", None):
            raise w.TrainWorkerError("the base model has no chat template")
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token
        peft = d.peft
        if init_adapter:
            model = peft.PeftModel.from_pretrained(
                model, init_adapter, is_trainable=True
            )
        else:
            model = peft.get_peft_model(
                model, peft.LoraConfig(**lora_config_kwargs(self.hp, self.is_vl))
            )
        trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
        logger.info("writer train: %d trainable LoRA parameter(s)", trainable)
        self.model, self.tok = model, tok
        self._adapter_src = init_adapter
        self._loaded = True

    def _release(self) -> None:
        self.model = None
        self._loaded = False
        gc.collect()
        if self.deps is not None:
            self.deps.torch.cuda.empty_cache()

    # -- data ---------------------------------------------------------------

    def _length(self, prompt: str, completion: str) -> int:
        return len(self.tok(prompt + completion, add_special_tokens=False)["input_ids"])

    def _fits(self, ex: Dict[str, Any], keys: Tuple[str, ...]) -> bool:
        limit = int(self.hp["max_seq_len"])
        for k in keys:
            p, c = render_pair(self.tok, ex["system"], ex["user"], ex[k])
            if self._length(p, c) > limit:
                return False
        return True

    def _filter(
        self, examples: List[Dict[str, Any]], keys: Tuple[str, ...], kind: str
    ) -> List[Dict[str, Any]]:
        kept = [ex for ex in examples if self._fits(ex, keys)]
        dropped = len(examples) - len(kept)
        if dropped:
            logger.warning(
                "writer train: %d %s example(s) longer than %d tokens left out",
                dropped,
                kind,
                self.hp["max_seq_len"],
            )
        if not kept:
            raise w.TrainWorkerError(
                f"every {kind} example is longer than {self.hp['max_seq_len']} tokens"
            )
        return kept

    # -- training -------------------------------------------------------------

    def _callback(self, ctx: "w.StageContext", stage: str) -> Any:
        base_cls = self.deps.transformers.TrainerCallback

        class _ControlCallback(base_cls):  # type: ignore[misc,valid-type]
            def __init__(self) -> None:
                self.action: Optional[str] = None
                self.loss: Optional[float] = None

            def on_train_begin(
                self, args: Any, state: Any, control: Any, **kw: Any
            ) -> Any:
                if state.max_steps:
                    ctx.set_total(stage, int(state.max_steps))
                return control

            def on_log(
                self, args: Any, state: Any, control: Any, logs: Any = None, **kw: Any
            ) -> Any:
                if logs and "loss" in logs:
                    self.loss = float(logs["loss"])
                return control

            def on_step_end(
                self, args: Any, state: Any, control: Any, **kw: Any
            ) -> Any:
                ctx.report(stage, int(state.global_step), self.loss)
                act = ctx.check()
                if act == "pause":
                    self.action = "pause"
                    control.should_save = True  # checkpoint at this boundary
                    control.should_training_stop = True
                elif act == "cancel":
                    self.action = "cancel"
                    control.should_training_stop = True
                return control

            def on_save(self, args: Any, state: Any, control: Any, **kw: Any) -> Any:
                ctx.note_checkpoint(stage, int(state.global_step))
                return control

        return _ControlCallback()

    def _run_trainer(
        self,
        ctx: "w.StageContext",
        stage: str,
        trainer: Any,
        cb: Any,
        resume_from: Optional[Path],
        save_to: Path,
    ) -> None:
        trainer.train(resume_from_checkpoint=str(resume_from) if resume_from else None)
        step = int(trainer.state.global_step)
        del trainer
        if cb.action == "cancel":
            raise w.Cancelled()
        if cb.action == "pause":
            ctx.report(stage, step, force=True)
            raise w.Paused(stage, step)
        save_to.mkdir(parents=True, exist_ok=True)
        self.model.save_pretrained(str(save_to))
        self._adapter_src = str(save_to)
        self.deps.torch.cuda.empty_cache()

    def train_sft(
        self,
        ctx: "w.StageContext",
        examples: List[Dict[str, Any]],
        init_adapter: Optional[str],
        resume_from: Optional[Path],
        save_to: Path,
    ) -> None:
        self._ensure(init_adapter)
        d = self.deps
        kept = self._filter(examples, ("chosen",), "SFT")
        ds = d.datasets.Dataset.from_list(
            [
                {
                    "prompt": prompt_messages(ex),
                    "completion": [{"role": "assistant", "content": ex["chosen"]}],
                }
                for ex in kept
            ]
        )
        total = w.stage_steps(
            len(kept),
            batch_size=self.hp["batch_size"],
            grad_accum=self.hp["grad_accum"],
            epochs=self.hp["epochs"],
        )
        ctx.set_total("sft", total)
        args = _config(
            d.trl.SFTConfig,
            {
                **training_args_kwargs(
                    self.hp, self.task.checkpoint_dir / "sft", total
                ),
                "completion_only_loss": True,
                "packing": False,
            },
            {},
        )
        cb = self._callback(ctx, "sft")
        trainer = d.trl.SFTTrainer(
            model=self.model,
            args=args,
            train_dataset=ds,
            processing_class=self.tok,
            callbacks=[cb],
        )
        self._run_trainer(ctx, "sft", trainer, cb, resume_from, save_to)

    def train_dpo(
        self,
        ctx: "w.StageContext",
        examples: List[Dict[str, Any]],
        init_adapter: Optional[str],
        resume_from: Optional[Path],
        save_to: Path,
    ) -> None:
        self._ensure(init_adapter)
        d = self.deps
        kept = self._filter(examples, ("chosen", "rejected"), "preference")
        ds = d.datasets.Dataset.from_list(
            [
                {
                    "prompt": prompt_messages(ex),
                    "chosen": [{"role": "assistant", "content": ex["chosen"]}],
                    "rejected": [{"role": "assistant", "content": ex["rejected"]}],
                }
                for ex in kept
            ]
        )
        total = w.stage_steps(
            len(kept),
            batch_size=self.hp["batch_size"],
            grad_accum=self.hp["grad_accum"],
            epochs=self.hp["epochs"],
        )
        ctx.set_total("dpo", total)
        args = _config(
            d.trl.DPOConfig,
            {
                **training_args_kwargs(
                    self.hp, self.task.checkpoint_dir / "dpo", total
                ),
                "beta": float(self.hp["beta"]),
            },
            # Older TRL truncates prompts to 512 by default; the META system
            # prompts are long, so turn that off where the field exists.
            {"max_prompt_length": None, "max_completion_length": None},
        )
        cb = self._callback(ctx, "dpo")
        # ref_model=None with a PeftModel: TRL uses the same model with the
        # adapter disabled as the reference -- no second 8B copy in VRAM.
        trainer = d.trl.DPOTrainer(
            model=self.model,
            ref_model=None,
            args=args,
            train_dataset=ds,
            processing_class=self.tok,
            callbacks=[cb],
        )
        self._run_trainer(ctx, "dpo", trainer, cb, resume_from, save_to)

    # -- eval / convert -------------------------------------------------------

    def _logprob(self, prompt: str, completion: str) -> Optional[float]:
        torch = self.deps.torch
        p_ids = self.tok(prompt, add_special_tokens=False)["input_ids"]
        ids = self.tok(prompt + completion, add_special_tokens=False)["input_ids"]
        ids = ids[: int(self.hp["max_seq_len"])]
        if len(ids) <= len(p_ids):
            return None
        x = torch.tensor([ids], device=self.model.device)
        logits = self.model(input_ids=x).logits[0, :-1].float()
        logp = torch.log_softmax(logits, dim=-1)
        tok_lp = logp.gather(1, x[0, 1:, None])[:, 0]
        return float(tok_lp[len(p_ids) - 1 :].sum().item())

    def _prefers_chosen(self, ex: Dict[str, Any]) -> Optional[bool]:
        pc, cc = render_pair(self.tok, ex["system"], ex["user"], ex["chosen"])
        _, rc = render_pair(self.tok, ex["system"], ex["user"], ex["rejected"])
        a, b = self._logprob(pc, cc), self._logprob(pc, rc)
        if a is None or b is None:
            return None
        return a > b

    def evaluate(
        self,
        ctx: "w.StageContext",
        holdout: List[Dict[str, Any]],
        adapter_dir: Path,
    ) -> Tuple[Optional[float], Optional[float]]:
        """Fraction of holdout pairs whose summed chosen log-prob beats the
        rejected one, with the adapter (first) and without it (second)."""
        if not holdout:
            return None, None
        self._ensure(str(adapter_dir))
        self.model.eval()
        torch = self.deps.torch
        with_adapter: List[bool] = []
        without: List[bool] = []
        with torch.no_grad():
            for i, ex in enumerate(holdout, start=1):
                a = self._prefers_chosen(ex)
                with self.model.disable_adapter():
                    b = self._prefers_chosen(ex)
                if a is not None and b is not None:
                    with_adapter.append(a)
                    without.append(b)
                ctx.report("eval", i)
                if ctx.check() == "cancel":
                    raise w.Cancelled()
        if not with_adapter:
            return None, None
        skipped = len(holdout) - len(with_adapter)
        if skipped:
            logger.warning(
                "writer train: %d holdout pair(s) too long to score", skipped
            )
        n = len(with_adapter)
        return sum(with_adapter) / n, sum(without) / n

    def convert(self, ctx: "w.StageContext", peft_dir: Path, gguf_path: Path) -> None:
        self._release()  # free VRAM: the converter loads tensors on CPU
        assert self.task.converter is not None  # parse_task guarantees it
        w.run_converter(
            self.task.converter, self.task.base_dir, str(peft_dir), str(gguf_path)
        )
