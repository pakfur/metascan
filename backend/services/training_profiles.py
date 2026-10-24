"""Bounded history of writer training profiles (VRAM + performance evidence).

When a training attempt ends (any outcome) the manager copies the worker's
``<run_dir>/out/profile.json`` -- minus its ``timeline`` and plus the
adapter's id and name -- into ``<data_dir>/logs/writer_training_profiles.json``:

    {"schema": 1, "max_entries": 50, "profiles": [...]}   # newest last

The newest ``MAX_ENTRIES`` are kept (oldest dropped) and the file is
rewritten atomically (tmp + fsync + ``os.replace`` + directory fsync). It
is a capped JSON document, NOT an appended log stream, so the log-file
rotation policy (``metascan/utils/log_files.py``) does not apply; 50 entries
without timelines stay at a few hundred KB. A file that cannot be parsed
or has the wrong shape is renamed ``<name>.corrupt-<ts>`` (WARNING, never
fatal) and the history starts fresh. The full profile of the most recent
attempt, timeline included, is kept beside it in
``writer_training_last_profile.json`` (the run dir is deleted when a run
completes or is cancelled). The history survives adapter deletion.

Profiles are numbers only (no prompt / brief / example text): safe to share.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from metascan.workers.writer_train_profile import PROFILE_NAME

logger = logging.getLogger(__name__)

HISTORY_NAME = "writer_training_profiles.json"
LAST_PROFILE_NAME = "writer_training_last_profile.json"
HISTORY_SCHEMA = 1
MAX_ENTRIES = 50


def logs_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "logs"


def history_path(data_dir: Path) -> Path:
    return logs_dir(data_dir) / HISTORY_NAME


def last_profile_path(data_dir: Path) -> Path:
    return logs_dir(data_dir) / LAST_PROFILE_NAME


def _empty() -> Dict[str, Any]:
    return {"schema": HISTORY_SCHEMA, "max_entries": MAX_ENTRIES, "profiles": []}


def write_json_durable(path: Path, data: Any) -> None:
    """tmp + fsync + ``os.replace`` + fsync of the directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        fd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return  # e.g. Windows: directories cannot be opened
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _quarantine(path: Path, why: str) -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.corrupt-{stamp}")
    n = 1
    while dest.exists():
        n += 1
        dest = path.with_name(f"{path.name}.corrupt-{stamp}-{n}")
    try:
        os.replace(path, dest)
        logger.warning(
            "writer training profiles: %s is corrupt (%s); moved to %s, "
            "starting a fresh history",
            path.name,
            why,
            dest.name,
        )
    except OSError as exc:
        logger.warning(
            "writer training profiles: %s is corrupt (%s) and could not be "
            "moved aside (%s)",
            path.name,
            why,
            exc,
        )


def read_history(data_dir: Path) -> Dict[str, Any]:
    """The history (``profiles`` newest last); a corrupt file is
    quarantined and an empty history returned. Never raises."""
    path = history_path(data_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty()
    except OSError as exc:
        logger.warning("writer training profiles: cannot read %s (%s)", path, exc)
        return _empty()
    try:
        data = json.loads(raw)
    except ValueError:
        _quarantine(path, "not valid JSON")
        return _empty()
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, list) or not all(isinstance(p, dict) for p in profiles):
        _quarantine(path, "unexpected shape")
        return _empty()
    out = _empty()
    out["profiles"] = profiles[-MAX_ENTRIES:]
    return out


def append_profile(
    data_dir: Path,
    profile: Dict[str, Any],
    *,
    adapter_id: Optional[int],
    adapter_name: Optional[str],
    max_entries: int = MAX_ENTRIES,
) -> None:
    """Add one attempt (its ``timeline`` dropped), keep the newest
    ``max_entries`` and rewrite the file atomically."""
    entry = {k: v for k, v in profile.items() if k != "timeline"}
    entry["adapter_id"] = adapter_id
    entry["adapter_name"] = adapter_name
    history = read_history(data_dir)
    profiles: List[Dict[str, Any]] = history["profiles"] + [entry]
    history["profiles"] = profiles[-max_entries:]
    write_json_durable(history_path(data_dir), history)


def save_last_profile(data_dir: Path, profile: Dict[str, Any]) -> None:
    write_json_durable(last_profile_path(data_dir), profile)


def read_last_profile(data_dir: Path) -> Optional[Dict[str, Any]]:
    return read_profile(last_profile_path(data_dir))


def read_profile(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def run_profile_path(run_dir: Path) -> Path:
    return Path(run_dir) / "out" / PROFILE_NAME


def record_attempt(
    data_dir: Path, active: Any, *, verdict: str, exit_code: int
) -> None:
    """The end of one attempt of ``active`` (the manager's ``_Active``):
    keep its full profile as the last one and add it to the history. A
    profile the worker never closed (terminated on shutdown, killed) gets
    the manager's ``verdict`` and ``ended_by: "server"``. A missing or
    unreadable profile is skipped. Never raises."""
    try:
        profile = read_profile(run_profile_path(active.run_dir))
        if profile is None:
            logger.info("writer training run %s: no profile to record", active.run_id)
            return
        attempt = profile.get("attempt")
        if not isinstance(attempt, dict):
            attempt = profile["attempt"] = {}
        if attempt.get("outcome") is None:
            attempt["outcome"] = verdict
            attempt["exit_code"] = exit_code
            attempt["ended_by"] = "server"
        if attempt.get("run_id") is None:
            attempt["run_id"] = active.run_id
        save_last_profile(data_dir, profile)
        append_profile(
            data_dir, profile, adapter_id=active.adapter_id, adapter_name=active.name
        )
    except Exception:
        logger.exception("Recording the writer training profile failed")


def profiles_payload(data_dir: Path, active_run_dir: Optional[Path]) -> Dict[str, Any]:
    """``GET /api/writers/training/profiles``: the history newest first and
    the current run's live profile (else the last attempt's), timeline
    included."""
    history = read_history(data_dir)
    current = None
    if active_run_dir is not None:
        current = read_profile(run_profile_path(active_run_dir))
    if current is None:
        current = read_last_profile(data_dir)
    return {
        "schema": history["schema"],
        "max_entries": history["max_entries"],
        "profiles": list(reversed(history["profiles"])),
        "current": current,
    }
