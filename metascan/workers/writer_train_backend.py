"""Real training backend for the writer LoRA worker: TRL + PEFT on CUDA.

Imported only by ``writer_train_worker`` for ``backend: "real"``. The
training stack (torch, transformers, peft, trl, datasets -- see
``requirements-train.txt``) is imported lazily inside ``import_deps`` so
importing this module costs nothing and the FastAPI process never loads
it. The pure helpers (``render_pair``, ``lora_config_kwargs``,
``training_args_kwargs``, ``dpo_args_kwargs``, ``dpo_reference_mode``,
``check_adapter_keys``) are unit-tested without the deps.

Method (global constraints): LoRA in bf16 on the language model only --
``q/k/v/o_proj`` and ``gate/up/down_proj`` -- the vision tower frozen and
untouched; SFT (loss on the assistant completion only, ``lr``) then DPO
(beta 0.1, ``dpo_lr``, reference = the SFT adapter); max sequence 2048
tokens for system + user + completion -- longer examples are dropped and
counted, never truncated, so the system prompt always matches inference;
seed 0. Inputs are text-only chats built with the base tokenizer's chat
template: system + user, assistant = the target.

DPO reference: the adapter DPO starts from (the SFT result, or the
continued adapter) is loaded twice -- trainable ``default`` and frozen
``reference`` -- and TRL swaps to ``ref_adapter_name`` for the reference
pass (``DPOConfig.model_adapter_name`` / ``ref_adapter_name``, TRL >=
0.25). A TRL without those fields falls back to a separate frozen merged
copy when VRAM allows. With no adapter to start from (no SFT, fresh) the
reference is the base with the adapter disabled (TRL's PEFT default).
"""

from __future__ import annotations

import dataclasses
import gc
import importlib
import logging
import math
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

from metascan.workers import writer_train_profile as wp
from metascan.workers import writer_train_worker as w

logger = logging.getLogger("writer_train_worker")

LORA_TARGETS = w.LORA_TARGETS
# Full-match on the module name: only Linear layers under the language model
# (``model.language_model.layers.N...`` in transformers' Qwen3-VL), never
# the vision tower (``model.visual...``: qkv/proj/linear_fc*).
VL_TARGET_REGEX = r".*language_model\..*\.(" + "|".join(LORA_TARGETS) + r")"

TRAIN_ADAPTER = "default"
REF_ADAPTER = "reference"

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


# ---- profile: memory, environment, VRAM cap -----------------------------


class TorchMemoryProbe:
    """torch's CUDA caching-allocator counters for device 0 (bytes); any
    failure reads as None (a profile never breaks a run)."""

    def __init__(self, torch: Any, device: int = 0) -> None:
        self.torch = torch
        self.device = device

    def _call(self, name: str) -> Optional[float]:
        try:
            return float(getattr(self.torch.cuda, name)(self.device))
        except Exception:
            return None

    def reset_peak(self) -> None:
        try:
            self.torch.cuda.reset_peak_memory_stats(self.device)
        except Exception:
            pass

    def allocated(self) -> Optional[float]:
        return self._call("memory_allocated")

    def reserved(self) -> Optional[float]:
        return self._call("memory_reserved")

    def peak_allocated(self) -> Optional[float]:
        return self._call("max_memory_allocated")

    def peak_reserved(self) -> Optional[float]:
        return self._call("max_memory_reserved")


def _driver_version() -> Optional[str]:
    """The NVIDIA driver version from ``nvidia-smi`` (a 5 s query), or None."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = (proc.stdout or "").strip().splitlines()
    return lines[0].strip() if proc.returncode == 0 and lines else None


def cuda_environment(torch: Any, device: int = 0) -> Dict[str, Any]:
    props = torch.cuda.get_device_properties(device)
    total = round(float(props.total_memory) / wp.GIB, 2)
    return {
        "gpu_name": str(props.name),
        "total_vram_gib": total,
        "nominal_vram_gb": wp.nominal_vram_gb(total),
        "compute_capability": f"{props.major}.{props.minor}",
        "cuda_runtime": getattr(getattr(torch, "version", None), "cuda", None),
        "driver_version": _driver_version(),
    }


def apply_vram_cap(
    torch: Any, cap_gb: Optional[float], total_gib: Optional[float]
) -> Optional[float]:
    """Limit this process to ``cap_gb`` (the rehearsal of a smaller card)
    when it is below the card; returns the fraction set, or None."""
    frac = wp.vram_cap_fraction(cap_gb, total_gib)
    if frac is not None:
        torch.cuda.set_per_process_memory_fraction(frac, 0)
    return frac


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
    """Arguments shared by ``SFTConfig`` and ``DPOConfig`` (SFT learning
    rate; ``dpo_args_kwargs`` overrides it). ``max_length=None``: the
    backend already dropped every example over ``max_seq_len``, so the
    trainer must never truncate (the system prompt stays whole)."""
    return {
        "output_dir": str(output_dir),
        "num_train_epochs": int(hp["epochs"]),
        "learning_rate": float(hp["lr"]),
        "per_device_train_batch_size": int(hp["batch_size"]),
        "gradient_accumulation_steps": int(hp["grad_accum"]),
        "lr_scheduler_type": "cosine",
        "warmup_steps": int(math.ceil(float(hp["warmup_ratio"]) * max(0, total_steps))),
        "bf16": True,
        "max_length": None,
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


def dpo_reference_mode(
    init_adapter: Optional[str], config_fields: Iterable[str]
) -> str:
    """``"disabled"`` (no adapter to start from: the base is the
    reference), ``"adapter"`` (load it twice, TRL swaps adapters) or
    ``"copy"`` (TRL lacks the adapter-name fields: a frozen merged copy)."""
    if not init_adapter:
        return "disabled"
    if {"model_adapter_name", "ref_adapter_name"} <= set(config_fields):
        return "adapter"
    return "copy"


def dpo_args_kwargs(
    hp: Dict[str, Any], output_dir: Path, total_steps: int, ref_mode: str
) -> Dict[str, Any]:
    kw = training_args_kwargs(hp, output_dir, total_steps)
    kw["learning_rate"] = float(hp["dpo_lr"])
    kw["beta"] = float(hp["beta"])
    if ref_mode == "adapter":
        kw["model_adapter_name"] = TRAIN_ADAPTER
        kw["ref_adapter_name"] = REF_ADAPTER
    return kw


def text_only_dpo_trainer(dpo_trainer: Any) -> Any:
    """TRL's ``DPOTrainer`` for text-only preference data on an image-text
    base. TRL 0.25 takes any model whose type is an image-text one (Qwen3-VL
    is ``qwen3_vl``) for a vision model and tokenizes its dataset with
    ``process_row``, which needs a processor and an ``images`` column: an
    ``AttributeError`` on the tokenizer the writer passes (``_ensure``: TRL
    gets the tokenizer, never the processor). The writer's pairs are text,
    so the dataset keeps ``tokenize_row``; its batches carry no
    ``pixel_values`` and TRL's forward is text-only, as in SFT. Only the
    tokenizing choice changes (``is_vision_model`` is read nowhere else in
    TRL 0.25's DPO trainer); revisit with the trl cap."""

    class TextOnlyDPOTrainer(dpo_trainer):  # type: ignore[misc, valid-type]
        def _prepare_dataset(self, *args: Any, **kwargs: Any) -> Any:
            self.is_vision_model = False
            return super()._prepare_dataset(*args, **kwargs)

    return TextOnlyDPOTrainer


def check_adapter_keys(keys: Iterable[str]) -> int:
    """A saved adapter must hold LoRA weights and nothing from the vision
    tower. Returns the LoRA key count."""
    keys = list(keys)
    vision = [k for k in keys if "visual" in k or "vision" in k]
    if vision:
        raise w.TrainWorkerError(
            f"the saved adapter has {len(vision)} vision-tower key(s) (e.g. {vision[0]})"
        )
    lora = sum(1 for k in keys if "lora_" in k)
    if lora == 0:
        raise w.TrainWorkerError("the saved adapter has no LoRA weights")
    return lora


def _config(cls: Any, kwargs: Dict[str, Any], optional: Dict[str, Any]) -> Any:
    """Build a TRL config; ``optional`` keys are passed only when this TRL
    version still has the field (e.g. ``max_prompt_length``, removed in
    newer TRL and defaulting to a truncating 512 in older ones)."""
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
        self._ctx: Optional["w.StageContext"] = None

    def setup(self, ctx: "w.StageContext") -> None:
        """Before any model load: the deps, CUDA, the environment and the
        memory probe for the profile, and the VRAM cap (it must be set
        before the allocator grows)."""
        self._ctx = ctx
        if self.deps is None:
            self.deps = import_deps()
            require_cuda(self.deps.torch)
            self.deps.transformers.set_seed(int(self.hp["seed"]))
        torch = self.deps.torch
        env = cuda_environment(torch)
        ctx.profile.update_environment(**env)
        frac = apply_vram_cap(torch, self.task.vram_cap_gb, env["total_vram_gib"])
        if frac is not None:
            logger.info(
                "writer train: VRAM capped at %.1f GiB (rehearsal, %.3f of the card)",
                self.task.vram_cap_gb,
                frac,
            )
        ctx.profile.update_config(
            vram_cap_applied=frac is not None,
            vram_cap_fraction=None if frac is None else round(frac, 4),
        )
        ctx.profile.set_probe(TorchMemoryProbe(torch))

    # -- model --------------------------------------------------------------

    def _load_base(self) -> Any:
        d = self.deps
        tf = d.transformers
        base = self.task.base_dir
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
        text_config = getattr(model.config, "text_config", None)
        if text_config is not None:
            text_config.use_cache = False
        for p in model.parameters():  # vision tower and base stay frozen
            p.requires_grad_(False)
        return model

    def _ensure(self, init_adapter: Optional[str]) -> None:
        if self.deps is None:
            self.deps = import_deps()
            require_cuda(self.deps.torch)
            self.deps.transformers.set_seed(int(self.hp["seed"]))
        if self._loaded and self._adapter_src == init_adapter:
            return
        self._release()
        tf = self.deps.transformers
        base = self.task.base_dir
        logger.info("writer train: loading base model (bf16, CUDA)")
        if self._ctx is not None:
            self._ctx.load_begin()
        model = self._load_base()
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
        peft = self.deps.peft
        if init_adapter:
            model = peft.PeftModel.from_pretrained(
                model, init_adapter, adapter_name=TRAIN_ADAPTER, is_trainable=True
            )
        else:
            model = peft.get_peft_model(
                model, peft.LoraConfig(**lora_config_kwargs(self.hp, self.is_vl))
            )
        trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
        logger.info("writer train: %d trainable LoRA parameter(s)", trainable)
        if self._ctx is not None:
            cfg = getattr(model, "config", None)
            self._ctx.profile.update_config(
                attn_implementation=getattr(cfg, "_attn_implementation", None)
                or "sdpa",
                lora_target_regex=(
                    VL_TARGET_REGEX if self.is_vl else "|".join(LORA_TARGETS)
                ),
            )
            self._ctx.load_end()
        self.model, self.tok = model, tok
        self._adapter_src = init_adapter
        self._loaded = True

    def _release(self) -> None:
        self.model = None
        self._loaded = False
        self._adapter_src = None
        gc.collect()
        if self.deps is not None:
            self.deps.torch.cuda.empty_cache()

    def _load_reference_adapter(self, adapter_dir: str) -> None:
        if REF_ADAPTER not in self.model.peft_config:
            self.model.load_adapter(
                adapter_dir, adapter_name=REF_ADAPTER, is_trainable=False
            )
        self.model.set_adapter(TRAIN_ADAPTER)
        for name, p in self.model.named_parameters():
            if f".{REF_ADAPTER}." in name:
                p.requires_grad_(False)

    def _reference_copy(self, adapter_dir: str) -> Any:
        """Fallback for a TRL without ``ref_adapter_name``: a frozen merged
        copy of base + adapter, only when free VRAM can hold it."""
        torch = self.deps.torch
        need = sum(p.numel() * p.element_size() for p in self.model.parameters())
        free, _total = torch.cuda.mem_get_info()
        if free < need * 1.1:
            raise w.TrainWorkerError(
                "this TRL cannot use the SFT adapter as the DPO reference "
                "(no ref_adapter_name) and there is not enough free VRAM for a "
                "reference copy; install the trl version pinned in requirements-train.txt"
            )
        logger.warning(
            "writer train: TRL lacks ref_adapter_name; loading a reference copy"
        )
        ref = self.deps.peft.PeftModel.from_pretrained(self._load_base(), adapter_dir)
        ref = ref.merge_and_unload()
        ref.eval()
        for p in ref.parameters():
            p.requires_grad_(False)
        return ref

    # -- data ---------------------------------------------------------------

    def _length(self, prompt: str, completion: str) -> int:
        return len(self.tok(prompt + completion, add_special_tokens=False)["input_ids"])

    def _longest(self, ex: Dict[str, Any], keys: Tuple[str, ...]) -> int:
        """Tokens of system + user + the longest of ``keys``' completions."""
        out = 0
        for k in keys:
            p, c = render_pair(self.tok, ex["system"], ex["user"], ex[k])
            out = max(out, self._length(p, c))
        return out

    def _filter(
        self,
        ctx: "w.StageContext",
        examples: List[Dict[str, Any]],
        keys: Tuple[str, ...],
        kind: str,
    ) -> List[Dict[str, Any]]:
        limit = int(self.hp["max_seq_len"])
        kept: List[Dict[str, Any]] = []
        lengths: List[int] = []
        for ex in examples:
            n = self._longest(ex, keys)
            if n <= limit:
                kept.append(ex)
                lengths.append(n)
        dropped = len(examples) - len(kept)
        ctx.dropped[kind] = dropped
        ctx.token_lengths(kind, lengths)  # numbers only
        if dropped:
            logger.warning(
                "writer train: %d of %d %s example(s) longer than %d tokens left out",
                dropped,
                len(examples),
                kind,
                self.hp["max_seq_len"],
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

    def _save_adapter(self, save_to: Path) -> None:
        save_to.mkdir(parents=True, exist_ok=True)
        # Only the trained adapter: never the frozen DPO reference copy.
        self.model.save_pretrained(str(save_to), selected_adapters=[TRAIN_ADAPTER])
        from safetensors import safe_open

        with safe_open(str(save_to / "adapter_model.safetensors"), framework="pt") as f:
            n = check_adapter_keys(f.keys())
        logger.info("writer train: saved adapter with %d LoRA tensor(s)", n)

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
        self._save_adapter(save_to)
        self._adapter_src = str(save_to)
        self.deps.torch.cuda.empty_cache()

    def _steps(self, n: int) -> int:
        return w.stage_steps(
            n,
            batch_size=self.hp["batch_size"],
            grad_accum=self.hp["grad_accum"],
            epochs=self.hp["epochs"],
        )

    def train_sft(
        self,
        ctx: "w.StageContext",
        examples: List[Dict[str, Any]],
        init_adapter: Optional[str],
        resume_from: Optional[Path],
        save_to: Path,
    ) -> bool:
        self._ensure(init_adapter)
        d = self.deps
        kept = self._filter(ctx, examples, ("chosen",), "sft")
        if not kept:
            return False
        ds = d.datasets.Dataset.from_list(
            [
                {
                    "prompt": prompt_messages(ex),
                    "completion": [{"role": "assistant", "content": ex["chosen"]}],
                }
                for ex in kept
            ]
        )
        total = self._steps(len(kept))
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
        return True

    def train_dpo(
        self,
        ctx: "w.StageContext",
        examples: List[Dict[str, Any]],
        init_adapter: Optional[str],
        resume_from: Optional[Path],
        save_to: Path,
    ) -> bool:
        self._ensure(init_adapter)
        d = self.deps
        kept = self._filter(ctx, examples, ("chosen", "rejected"), "dpo")
        if not kept:
            return False
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
        total = self._steps(len(kept))
        ctx.set_total("dpo", total)
        fields = [f.name for f in dataclasses.fields(d.trl.DPOConfig)]
        mode = dpo_reference_mode(init_adapter, fields)
        ref_model: Any = None
        if mode == "adapter":
            assert init_adapter is not None
            self._load_reference_adapter(init_adapter)
        elif mode == "copy":
            assert init_adapter is not None
            ref_model = self._reference_copy(init_adapter)
        logger.info("writer train: DPO reference = %s", mode)
        args = _config(
            d.trl.DPOConfig,
            dpo_args_kwargs(self.hp, self.task.checkpoint_dir / "dpo", total, mode),
            # Older TRL truncates prompts to 512 by default; never truncate.
            {"max_prompt_length": None, "max_completion_length": None},
        )
        cb = self._callback(ctx, "dpo")
        trainer = text_only_dpo_trainer(d.trl.DPOTrainer)(
            model=self.model,
            ref_model=ref_model,
            args=args,
            train_dataset=ds,
            processing_class=self.tok,
            callbacks=[cb],
        )
        try:
            self._run_trainer(ctx, "dpo", trainer, cb, resume_from, save_to)
        finally:
            del ref_model
        return True

    # -- eval / convert -------------------------------------------------------

    def _logprob(self, prompt: str, completion: str) -> Optional[float]:
        """Summed completion log-prob; None when the sequence is longer than
        ``max_seq_len`` (never truncated)."""
        torch = self.deps.torch
        p_ids = self.tok(prompt, add_special_tokens=False)["input_ids"]
        ids = self.tok(prompt + completion, add_special_tokens=False)["input_ids"]
        if len(ids) > int(self.hp["max_seq_len"]) or len(ids) <= len(p_ids):
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
        rejected one, with the adapter (first) and with every adapter
        disabled, i.e. the base (second)."""
        if not holdout:
            return None, None
        self._ensure(str(adapter_dir))
        self.model.set_adapter(TRAIN_ADAPTER)
        self.model.eval()
        torch = self.deps.torch
        with_adapter: List[bool] = []
        without: List[bool] = []
        with torch.no_grad():
            for i, ex in enumerate(holdout, start=1):
                a = self._prefers_chosen(ex)
                if a is not None:
                    with self.model.disable_adapter():
                        b = self._prefers_chosen(ex)
                    if b is not None:
                        with_adapter.append(a)
                        without.append(b)
                ctx.report("eval", i)
                if ctx.check() == "cancel":
                    raise w.Cancelled()
        ctx.dropped["holdout"] = len(holdout) - len(with_adapter)
        if ctx.dropped["holdout"]:
            logger.warning(
                "writer train: %d holdout pair(s) longer than %d tokens left out",
                ctx.dropped["holdout"],
                self.hp["max_seq_len"],
            )
        if not with_adapter:
            return None, None
        n = len(with_adapter)
        return sum(with_adapter) / n, sum(without) / n

    def convert(self, ctx: "w.StageContext", peft_dir: Path, gguf_path: Path) -> str:
        self._release()  # free VRAM: the converter loads tensors on CPU
        assert self.task.converter is not None  # parse_task guarantees it
        return w.convert_with_fallback(
            self.task.converter,
            self.task.base_dir,
            peft_dir,
            gguf_path,
            self.task.checkpoint_dir,
        )

    def gguf_tensor_names(self, gguf_path: Path) -> List[str]:
        return w.read_gguf_tensor_names(gguf_path)

    def expected_lora_tensors(self) -> int:
        return w.expected_lora_tensors(self.task.base_dir)
