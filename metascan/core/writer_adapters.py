"""Prompt-writer adapters (LoRA fine-tunes of the writer VLM): lineage ids
and the statuses a registry row can have.

Generated media names the writer that drafted its prompt; an adapter's
writer id is ``adapter:<registry id>``. The id is the lineage key, so it
survives a rename, and a deleted adapter's row is kept (soft delete) to
remember its name in history."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

WRITER_ADAPTER_PREFIX = "adapter:"

# Registry statuses. ``deleted`` is the soft-delete marker.
STATUS_TRAINING = "training"
STATUS_PAUSED = "paused"
STATUS_UNTESTED = "untested"
STATUS_READY = "ready"
STATUS_FAILED = "failed"
STATUS_DELETED = "deleted"
ADAPTER_STATUSES: Tuple[str, ...] = (
    STATUS_TRAINING,
    STATUS_PAUSED,
    STATUS_UNTESTED,
    STATUS_READY,
    STATUS_FAILED,
    STATUS_DELETED,
)
# Statuses an adapter may serve drafts in (loaded into the writer server).
SERVABLE_STATUSES: Tuple[str, ...] = (STATUS_READY, STATUS_UNTESTED)


def adapter_writer_id(adapter_id: int) -> str:
    """The lineage writer id of registry adapter ``adapter_id``."""
    return f"{WRITER_ADAPTER_PREFIX}{int(adapter_id)}"


def parse_adapter_writer_id(writer_id: Any) -> Optional[int]:
    """The registry id named by ``adapter:<id>``; None for anything else
    (a non-string, another writer, or a non-canonical number such as
    ``adapter:07`` or ``adapter:0``)."""
    if not isinstance(writer_id, str) or not writer_id.startswith(
        WRITER_ADAPTER_PREFIX
    ):
        return None
    digits = writer_id[len(WRITER_ADAPTER_PREFIX) :]
    if not digits.isascii() or not digits.isdigit():
        return None
    value = int(digits)
    if value < 1 or str(value) != digits:
        return None
    return value


def adapter_dir(data_dir: Path, adapter_id: int) -> Path:
    """Where adapter ``adapter_id``'s files live."""
    return data_dir / "models" / "vlm" / "adapters" / str(int(adapter_id))


# The GGUF the writer llama-server loads (``--lora``), inside adapter_dir.
ADAPTER_GGUF = "adapter.gguf"
ADAPTER_CONFIG = "adapter_config.json"
ADAPTER_META = "meta.json"
# The PEFT adapter kept beside the GGUF so "Continue <adapter>" can train on.
ADAPTER_PEFT_DIRNAME = "peft"

# The base model adapters are trained on (the writer role's 8B) and its
# HF-format training weights (the source of the installed GGUF);
# ``models.writer_train_repo`` overrides the repo.
ADAPTER_BASE_MODEL = "qwen3vl-8b"


def _default_train_repo() -> str:
    from metascan.core.vlm_models import REGISTRY

    repo = REGISTRY[ADAPTER_BASE_MODEL].hf_train_repo
    if not repo:
        raise RuntimeError(f"{ADAPTER_BASE_MODEL} has no hf_train_repo")
    return repo


# Single source of truth: the base model's VlmModelSpec.hf_train_repo.
DEFAULT_WRITER_TRAIN_REPO = _default_train_repo()
CONVERTER_SCRIPT = "convert_lora_to_gguf.py"


def train_base_dir(data_dir: Path, repo: str) -> Path:
    """Where the HF training weights of ``repo`` live:
    ``data/models/vlm/train/<org>--<name>/``."""
    return data_dir / "models" / "vlm" / "train" / repo.strip().replace("/", "--")


def default_llama_cpp_dir(data_dir: Path) -> Path:
    """The llama.cpp checkout holding the LoRA converter when config
    ``models.llama_cpp_dir`` is unset (``scripts/build_llama_server.sh``
    builds in a temp dir and keeps no checkout)."""
    return data_dir / "llama.cpp"


def default_converter_script(data_dir: Path) -> Path:
    return default_llama_cpp_dir(data_dir) / CONVERTER_SCRIPT


# 409 detail codes: the adapter cannot be served (the dialog falls back to
# Base), or it is registered but the busy writer has not loaded it yet.
ADAPTER_UNAVAILABLE = "adapter_unavailable"
ADAPTER_LOADING = "adapter_loading"


class WriterAdapterUnavailable(RuntimeError):
    """A request named an adapter the writer cannot serve (deleted, failed,
    still training, another surface's, or not loaded for the writer's model)
    -- the routes answer 409 with ``code`` (``adapter_unavailable``, or
    ``adapter_loading`` while the busy writer has not loaded a new one)."""

    def __init__(
        self, writer_id: str, reason: str = "", code: str = ADAPTER_UNAVAILABLE
    ) -> None:
        self.writer_id = writer_id
        self.code = code
        super().__init__(
            f"writer {writer_id!r} is not available" + (f": {reason}" if reason else "")
        )


@dataclass(frozen=True)
class AdapterFile:
    """A servable adapter (``ready`` / ``untested``) whose GGUF is on disk."""

    adapter_id: int
    kind: str
    base_model: str
    path: Path


def servable_adapter_files(db: Any, data_dir: Path) -> List[AdapterFile]:
    """Every ``ready`` / ``untested`` registry adapter whose GGUF exists, in
    id order -- the ``--lora`` list of the writer llama-server. An adapter
    whose file is missing is skipped (llama-server would refuse to start)."""
    out: List[AdapterFile] = []
    for row in db.list_writer_adapters():
        if row["status"] not in SERVABLE_STATUSES:
            continue
        path = adapter_dir(data_dir, row["id"]) / ADAPTER_GGUF
        if not path.is_file():
            logger.warning(
                "writer adapter %s is %s but %s is missing; not loading it",
                row["id"],
                row["status"],
                path,
            )
            continue
        out.append(
            AdapterFile(
                adapter_id=int(row["id"]),
                kind=str(row["kind"]),
                base_model=str(row["base_model"]),
                path=path,
            )
        )
    return out
