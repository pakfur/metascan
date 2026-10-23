"""The two on-disk pieces writer LoRA training needs, as Models-tab rows:

* ``writer-train-base``: the writer model's HF safetensors weights
  (``models.writer_train_repo``, default the 8B spec's ``hf_train_repo``)
  in ``data/models/vlm/train/<org>--<name>/``. Downloaded with
  ``huggingface_hub.snapshot_download`` into ``<base_dir>.partial`` and
  renamed into place only once complete, so a partial download never
  satisfies the ``writer_training`` gate.
* ``llama-cpp-converter``: llama.cpp's ``convert_lora_to_gguf.py`` (plus
  the modules it imports) at the pinned ``LLAMA_CPP_RELEASE``, so the
  adapter GGUF matches the llama-server that loads it. Fetched from the
  GitHub source tarball into ``data/llama.cpp`` (a ``.metascan-release``
  marker names the tag and marks the dir as ours). When config
  ``models.llama_cpp_dir`` is set the user manages that checkout: the row
  only reports present / missing.

Path resolution is shared with the training manager
(``training_paths_from_config`` delegates to :func:`asset_paths`).
Everything here is synchronous (filesystem / network); the routes run it
on a worker thread."""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tarfile
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional

from metascan.core.hardware import WriterTrainingPaths, base_weights_present
from metascan.core.writer_adapters import (
    CONVERTER_SCRIPT,
    DEFAULT_WRITER_TRAIN_REPO,
    default_llama_cpp_dir,
    train_base_dir,
)
from metascan.utils.llama_server import LLAMA_CPP_RELEASE

logger = logging.getLogger(__name__)

BASE_ID = "writer-train-base"
CONVERTER_ID = "llama-cpp-converter"
GROUP = "Writer training"
IDS = (BASE_ID, CONVERTER_ID)

# Everything a transformers load + the chat template need, and the weights;
# never README / .gitattributes / other formats.
BASE_ALLOW_PATTERNS: List[str] = ["*.json", "*.jinja", "*.txt", "*.safetensors"]

RELEASE_MARKER = ".metascan-release"
TARBALL_URL = "https://github.com/ggml-org/llama.cpp/archive/refs/tags/{tag}.tar.gz"
# What convert_lora_to_gguf.py needs at the pinned tag: it imports gguf
# (gguf-py/) and the conversion/ package (the model classes, split out of
# convert_hf_to_gguf.py); conversion/ reads chat templates from
# models/templates/. LICENSE travels with the code (MIT notice).
CONVERTER_FILES = frozenset(
    {CONVERTER_SCRIPT, "convert_hf_to_gguf.py", "requirements.txt", "LICENSE"}
)
CONVERTER_DIRS = (
    ("gguf-py",),
    ("conversion",),
    ("requirements",),
    ("models", "templates"),
)
_FETCH_TIMEOUT_S = 60.0


class AssetError(Exception):
    """A refused or failed asset operation; ``status``/``code`` map to the
    HTTP answer (409 for refusals)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def detail(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message}


# ---- paths ------------------------------------------------------------------


@dataclass(frozen=True)
class TrainingAssetPaths:
    """Training paths resolved from config (``models.writer_train_repo``,
    ``models.llama_cpp_dir``, ``models.writer_train_python``)."""

    repo: str
    base_dir: Path
    llama_cpp_dir: Path
    llama_cpp_configured: bool
    python: str

    @property
    def converter_script(self) -> Path:
        return self.llama_cpp_dir / CONVERTER_SCRIPT

    def gate_paths(self) -> WriterTrainingPaths:
        return WriterTrainingPaths(
            python=self.python,
            base_dir=self.base_dir,
            converter_script=self.converter_script,
        )


def asset_paths(
    config: Optional[Dict[str, Any]] = None, data_dir: Optional[Path] = None
) -> TrainingAssetPaths:
    """The single resolver for every writer-training path (the training
    manager uses it too). Reads config.json / the data dir when omitted."""
    if config is None:
        from backend.config import load_app_config

        config = load_app_config()
    if data_dir is None:
        from metascan.utils.app_paths import get_data_dir

        data_dir = get_data_dir()
    models = config.get("models") or {}
    if not isinstance(models, dict):
        models = {}

    def _str(key: str) -> str:
        value = models.get(key)
        return value.strip() if isinstance(value, str) else ""

    repo = _str("writer_train_repo") or DEFAULT_WRITER_TRAIN_REPO
    llama_dir = _str("llama_cpp_dir")
    return TrainingAssetPaths(
        repo=repo,
        base_dir=train_base_dir(data_dir, repo),
        llama_cpp_dir=(
            Path(llama_dir).expanduser()
            if llama_dir
            else default_llama_cpp_dir(data_dir)
        ),
        llama_cpp_configured=bool(llama_dir),
        python=_str("writer_train_python") or sys.executable,
    )


def partial_dir(target: Path) -> Path:
    """Where a download stages before the atomic rename into ``target``."""
    return target.with_name(target.name + ".partial")


def _old_dir(target: Path) -> Path:
    return target.with_name(target.name + ".old")


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for child in path.rglob("*"):
            try:
                if child.is_file() and not child.is_symlink():
                    total += child.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total


def _swap_into_place(staged: Path, target: Path) -> None:
    """Replace ``target`` with ``staged`` (renames on one filesystem); the
    previous ``target``, if any, is moved aside first and removed after."""
    old = _old_dir(target)
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    if target.exists():
        os.replace(target, old)
    try:
        os.replace(staged, target)
    except OSError:
        if old.exists() and not target.exists():
            os.replace(old, target)  # put the previous install back
        raise
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)


# ---- rows ----------------------------------------------------------------------


def read_release_marker(llama_dir: Path) -> Optional[str]:
    """The llama.cpp tag metascan extracted into ``llama_dir``; None when
    the dir is not metascan-managed (or unreadable)."""
    try:
        tag = (llama_dir / RELEASE_MARKER).read_text().strip()
    except OSError:
        return None
    return tag or None


def _base_row(paths: TrainingAssetPaths) -> Dict[str, Any]:
    base = paths.base_dir
    present = base_weights_present(base)
    staged = partial_dir(base)
    partial = not present and (base.exists() or staged.exists())
    size = _dir_size(base) + (_dir_size(staged) if staged.exists() else 0)
    return {
        "id": BASE_ID,
        "group": GROUP,
        "name": "Writer training base (HF, ~16 GB)",
        "description": (
            f"{paths.repo} safetensors: the weights writer LoRA fine-tunes " "train on."
        ),
        "status": "available" if present else "missing",
        "size_bytes": size or None,
        "cache_path": str(base) if present else None,
        "required_vram_mb": None,
        "preload_at_startup": False,
        "writer_training": True,
        "repo": paths.repo,
        "partial": partial,
        "managed": True,
        "download_allowed": True,
        "hint": (
            "Interrupted download: Download resumes it."
            if partial
            else (
                ""
                if present
                else "Set models.writer_train_repo to train on another repo."
            )
        ),
    }


def _converter_row(paths: TrainingAssetPaths) -> Dict[str, Any]:
    script = paths.converter_script
    present = script.is_file()
    release = read_release_marker(paths.llama_cpp_dir)
    managed = not paths.llama_cpp_configured
    if paths.llama_cpp_configured:
        hint = (
            f"Managed by config models.llama_cpp_dir ({paths.llama_cpp_dir}): "
            f"keep that llama.cpp checkout at release {LLAMA_CPP_RELEASE}, the "
            "tag scripts/build_llama_server.sh builds the server from."
            + ("" if present else f" {CONVERTER_SCRIPT} not found there.")
        )
    elif present and release is None:
        hint = (
            f"{paths.llama_cpp_dir} was not installed by metascan: it is left "
            "alone (set models.llama_cpp_dir to point at it explicitly)."
        )
    elif present:
        hint = ""
    else:
        hint = (
            f"Download fetches {CONVERTER_SCRIPT} from llama.cpp "
            f"{LLAMA_CPP_RELEASE}; or set models.llama_cpp_dir to a llama.cpp "
            "checkout (see scripts/build_llama_server.sh)."
        )
    downloadable = managed and (release is not None or not paths.llama_cpp_dir.exists())
    return {
        "id": CONVERTER_ID,
        "group": GROUP,
        "name": "llama.cpp LoRA converter",
        "description": f"{CONVERTER_SCRIPT}: turns a trained adapter into GGUF.",
        "status": "available" if present else "missing",
        "size_bytes": (_dir_size(paths.llama_cpp_dir) or None) if present else None,
        "cache_path": str(script) if present else None,
        "required_vram_mb": None,
        "preload_at_startup": False,
        "writer_training": True,
        "release": release,
        "server_release": LLAMA_CPP_RELEASE,
        "release_mismatch": release is not None and release != LLAMA_CPP_RELEASE,
        # metascan owns (or would create) the dir: Download / Delete apply.
        "managed": downloadable,
        "download_allowed": downloadable,
        "hint": hint,
    }


def status_rows(paths: Optional[TrainingAssetPaths] = None) -> List[Dict[str, Any]]:
    paths = paths or asset_paths()
    return [_base_row(paths), _converter_row(paths)]


# ---- base weights download ------------------------------------------------------


def _hf_snapshot_download(**kwargs: Any) -> str:
    from huggingface_hub import snapshot_download

    return str(snapshot_download(**kwargs))


def _hf_expected_bytes(repo: str, token: Optional[str]) -> Optional[int]:
    """Total size of the files the download fetches (for the percentage);
    None when the hub cannot say."""
    import fnmatch

    from huggingface_hub import HfApi

    info = HfApi().model_info(repo, files_metadata=True, token=token)
    total = 0
    for sibling in info.siblings or []:
        name = sibling.rfilename
        if any(fnmatch.fnmatch(name, p) for p in BASE_ALLOW_PATTERNS):
            total += int(sibling.size or 0)
    return total or None


def download_base(
    paths: TrainingAssetPaths,
    *,
    token: Optional[str] = None,
    on_progress: Optional[Callable[[float], None]] = None,
    poll_s: float = 2.0,
) -> None:
    """Fetch the training weights into ``<base_dir>.partial`` and rename it
    into place once complete. A failure leaves the partial dir for the
    next attempt (snapshot_download resumes into it) and never creates
    ``base_dir``. A complete ``base_dir`` is left as is."""
    base = paths.base_dir
    if base_weights_present(base):
        return
    staged = partial_dir(base)
    staged.parent.mkdir(parents=True, exist_ok=True)
    expected: Optional[int] = None
    if on_progress is not None:
        try:
            expected = _hf_expected_bytes(paths.repo, token)
        except Exception as exc:  # progress only: never fatal
            logger.info("Could not size %s for progress: %s", paths.repo, exc)
    stop = threading.Event()
    poller: Optional[threading.Thread] = None
    if on_progress is not None and expected:
        total = expected

        def _poll() -> None:
            while not stop.wait(poll_s):
                done = _dir_size(staged) if staged.exists() else 0
                on_progress(min(done / total, 0.99))

        poller = threading.Thread(
            target=_poll, name="writer-base-progress", daemon=True
        )
        poller.start()
    try:
        _hf_snapshot_download(
            repo_id=paths.repo,
            local_dir=str(staged),
            allow_patterns=list(BASE_ALLOW_PATTERNS),
            token=token,
        )
    finally:
        stop.set()
        if poller is not None:
            poller.join(timeout=5)
    if not base_weights_present(staged):
        raise AssetError(
            500,
            "download_incomplete",
            f"{paths.repo}: the download is missing config.json or a weight "
            "shard; try again.",
        )
    # huggingface_hub's local_dir bookkeeping; not part of the checkpoint.
    shutil.rmtree(staged / ".cache", ignore_errors=True)
    _swap_into_place(staged, base)


def delete_base(paths: TrainingAssetPaths) -> None:
    """Remove the weights and any partial download."""
    for d in (paths.base_dir, partial_dir(paths.base_dir), _old_dir(paths.base_dir)):
        if d.exists():
            shutil.rmtree(d)


# ---- converter fetch ----------------------------------------------------------------


def _http_fetch(url: str, dest: Path) -> None:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "metascan"})
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_S) as resp:
        with open(dest, "wb") as fh:
            shutil.copyfileobj(resp, fh, length=1 << 20)


def _wanted(rel: PurePosixPath) -> bool:
    parts = rel.parts
    if len(parts) == 1:
        return parts[0] in CONVERTER_FILES
    return any(parts[: len(d)] == d and len(parts) > len(d) for d in CONVERTER_DIRS)


def _safe_rel(name: str) -> PurePosixPath:
    """The member path below the archive's top-level dir; AssetError for an
    absolute path or any ``..`` component."""
    if name.startswith("/") or "\\" in name or (len(name) > 1 and name[1] == ":"):
        raise AssetError(500, "unsafe_archive", f"absolute path in archive: {name}")
    p = PurePosixPath(name)
    if any(part == ".." for part in p.parts):
        raise AssetError(500, "unsafe_archive", f"path traversal in archive: {name}")
    return PurePosixPath(*p.parts[1:]) if len(p.parts) > 1 else PurePosixPath()


def extract_converter(tar_path: Path, dest: Path, release: str) -> None:
    """Extract only the converter's files from a llama.cpp source tarball
    into ``dest`` (created; must not exist) and write the release marker.
    Any absolute / ``..`` member anywhere, or a link / device among the
    wanted paths, refuses the whole archive (AssetError, ``dest`` removed)."""
    if dest.exists():
        shutil.rmtree(dest)
    try:
        with tarfile.open(tar_path, mode="r:gz") as tf:
            members = tf.getmembers()
            plan = []
            for m in members:
                rel = _safe_rel(m.name)
                if not rel.parts or not _wanted(rel):
                    continue
                if m.isdir():
                    continue
                if not m.isfile():
                    raise AssetError(
                        500,
                        "unsafe_archive",
                        f"link or special file in archive: {m.name}",
                    )
                plan.append((m, rel))
            if not any(rel.as_posix() == CONVERTER_SCRIPT for _, rel in plan):
                raise AssetError(
                    500,
                    "converter_missing",
                    f"{CONVERTER_SCRIPT} not found in the llama.cpp {release} source",
                )
            dest.mkdir(parents=True)
            for m, rel in plan:
                out = dest.joinpath(*rel.parts)
                out.parent.mkdir(parents=True, exist_ok=True)
                src = tf.extractfile(m)
                if src is None:
                    raise AssetError(500, "unsafe_archive", f"unreadable: {m.name}")
                with src, open(out, "wb") as fh:
                    shutil.copyfileobj(src, fh)
        (dest / RELEASE_MARKER).write_text(release + "\n")
    except AssetError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    except (tarfile.TarError, OSError) as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise AssetError(500, "bad_archive", f"llama.cpp source archive: {exc}")


def require_managed(paths: TrainingAssetPaths) -> None:
    if paths.llama_cpp_configured:
        raise AssetError(
            409,
            "llama_cpp_dir_configured",
            f"models.llama_cpp_dir is set ({paths.llama_cpp_dir}): that "
            "llama.cpp checkout is managed by you.",
        )
    lc = paths.llama_cpp_dir
    if lc.exists() and read_release_marker(lc) is None:
        raise AssetError(
            409,
            "llama_cpp_dir_unmanaged",
            f"{lc} exists and was not installed by metascan; it is left alone.",
        )


def fetch_converter(
    paths: TrainingAssetPaths, *, release: str = LLAMA_CPP_RELEASE
) -> None:
    """Download the llama.cpp source at ``release`` and install the
    converter into the default llama.cpp dir (temp dir + rename)."""
    require_managed(paths)
    lc = paths.llama_cpp_dir
    lc.parent.mkdir(parents=True, exist_ok=True)
    tarball = lc.with_name(lc.name + ".download.tar.gz")
    staged = partial_dir(lc)
    try:
        _http_fetch(TARBALL_URL.format(tag=release), tarball)
        extract_converter(tarball, staged, release)
        _swap_into_place(staged, lc)
    finally:
        tarball.unlink(missing_ok=True)
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)


def delete_converter(paths: TrainingAssetPaths) -> None:
    """Remove a metascan-managed llama.cpp dir (never the user's)."""
    require_managed(paths)
    if paths.llama_cpp_dir.exists():
        shutil.rmtree(paths.llama_cpp_dir)
