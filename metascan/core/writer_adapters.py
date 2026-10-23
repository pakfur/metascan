"""Prompt-writer adapters (LoRA fine-tunes of the writer VLM): lineage ids
and the statuses a registry row can have.

Generated media names the writer that drafted its prompt; an adapter's
writer id is ``adapter:<registry id>``. The id is the lineage key, so it
survives a rename, and a deleted adapter's row is kept (soft delete) to
remember its name in history."""

from pathlib import Path
from typing import Any, Optional, Tuple

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
