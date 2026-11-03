#!/usr/bin/env python3
"""
Setup script to download required NLTK data and optional VLM weights
"""
import argparse
import json
import nltk
import shutil
import ssl
import os
import sys
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Add project to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from metascan.utils.app_paths import get_data_dir, get_nltk_data_dir

# Handle SSL certificate issues. Not on Windows: there Python reads the system
# certificates, and this module fetches executables the app then runs.
if sys.platform != "win32":
    try:
        _create_unverified_https_context = ssl._create_unverified_context
    except AttributeError:
        pass
    else:
        ssl._create_default_https_context = _create_unverified_https_context


@dataclass
class DownloadTarget:
    """One file to fetch. ``url`` is for HTTP GET; ``repo``+``filename`` are
    for HuggingFace Hub. Exactly one shape is set per target."""

    dest: Path
    url: Optional[str] = None
    repo: Optional[str] = None
    filename: Optional[str] = None


def _config_json_path() -> Path:
    """Path to the app config.json (patchable in tests)."""
    from metascan.utils.app_paths import get_config_path

    return get_config_path()


def _load_vlm_repo_overrides() -> dict[str, str]:
    """Read ``models.vlm_repos`` (legacy ``models.qwen3vl_repos``) from
    config.json. Returns {} when the file or key is absent/malformed."""
    try:
        raw = json.loads(_config_json_path().read_text())
    except (OSError, ValueError):
        return {}
    models = raw.get("models") or {}
    ov = models.get("vlm_repos") or models.get("qwen3vl_repos") or {}
    if not isinstance(ov, dict):
        return {}
    return {str(k): str(v) for k, v in ov.items()}


def resolve_vlm_targets(model_id: str) -> list[DownloadTarget]:
    """Return GGUF + mmproj + llama-server-binary download targets for ``model_id``,
    plus the CUDA runtime DLLs where upstream ships them apart."""
    from metascan.core.vlm_models import REGISTRY, resolve_repo
    from metascan.utils.llama_server import (
        CUDA_RUNTIME_DLL,
        binary_path,
        cuda_runtime_asset,
        pick_release_asset,
        release_url,
    )
    from metascan.core.hardware import detect_hardware

    spec = REGISTRY[model_id]
    repo = resolve_repo(model_id, _load_vlm_repo_overrides())
    vlm_dir = get_data_dir() / "models" / "vlm"

    rpt = detect_hardware()
    targets = [
        DownloadTarget(
            repo=repo,
            filename=spec.gguf_filename,
            dest=vlm_dir / spec.gguf_filename,
        ),
        DownloadTarget(
            repo=repo,
            filename=spec.mmproj_repo_filename,
            dest=vlm_dir / spec.mmproj_filename,
        ),
        DownloadTarget(
            url=release_url(pick_release_asset(rpt)),
            dest=binary_path(),
        ),
    ]
    runtime = cuda_runtime_asset(rpt)
    if runtime is not None:
        targets.append(
            DownloadTarget(
                url=release_url(runtime),
                dest=binary_path().parent / CUDA_RUNTIME_DLL,
            )
        )
    return targets


# Backwards-compat alias (pre-Qwen3.8 name).
resolve_qwen3vl_targets = resolve_vlm_targets


def _ensure_target(t: DownloadTarget) -> bool:
    """Fetch one target if it isn't already on disk. Returns True on success."""
    if t.dest.exists():
        print(f"  ✓ {t.dest.name} (already present)")
        return True

    t.dest.parent.mkdir(parents=True, exist_ok=True)

    if t.repo and t.filename:
        # HuggingFace target — use hf_hub_download then move into place.
        from huggingface_hub import hf_hub_download

        print(f"  ⇣ Downloading {t.filename} from {t.repo}…")
        cached = hf_hub_download(repo_id=t.repo, filename=t.filename)
        shutil.copy(cached, t.dest)
        print(f"    → {t.dest}")
        return True

    if t.url:
        # Direct URL target. The llama.cpp release assets (.zip on Windows,
        # .tar.gz on Linux/macOS) carry the server binary AND its sister
        # shared libraries (libllama.so, libmtmd.so, libggml*.so, …) under a
        # ``bin/`` folder. ``llama-server`` is linked with RUNPATH=$ORIGIN,
        # so every .so must land next to the binary or it fails at startup
        # with "error while loading shared libraries". Extract the whole bin
        # folder into ``t.dest.parent``.
        print(f"  ⇣ Downloading {t.url}…")
        is_targz = t.url.endswith(".tar.gz")
        tmp = t.dest.with_suffix(".tar.gz" if is_targz else ".zip")
        try:
            urllib.request.urlretrieve(t.url, tmp)
            target_name = t.dest.name
            if is_targz:
                _extract_flat_bin_targz(tmp, t.dest, target_name)
            else:
                _extract_flat_bin_zip(tmp, t.dest, target_name)
            # Ensure the server binary is executable even if the archive's
            # mode bits were stripped (e.g. some Windows zip producers).
            t.dest.chmod(0o755)
            print(f"    → {t.dest}")
            return True
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    raise ValueError(f"DownloadTarget has neither url nor repo+filename: {t}")


def _extract_flat_bin_zip(tmp: Path, dest: Path, target_name: str) -> None:
    """Extract the flat ``bin/`` payload of a llama.cpp .zip release asset.

    Locates the binary to learn the archive's bin-folder prefix, extracts
    only that folder's direct children next to ``dest`` (RUNPATH=$ORIGIN
    needs the .dll/.so files beside the binary), and materializes SONAME
    symlink chains after their targets.
    """
    with zipfile.ZipFile(tmp, "r") as zf:
        # Locate the binary entry to learn the archive's bin-folder
        # prefix; everything sharing that prefix is a runtime payload.
        member = next(
            (
                n
                for n in zf.namelist()
                if n.endswith(f"/{target_name}") or n == target_name
            ),
            None,
        )
        if member is None:
            raise RuntimeError(f"{target_name} not found inside {tmp.name}")
        bin_prefix = member[: -len(target_name)]  # "" or "build/bin/"
        # Two passes: extract regular files first so the symlink
        # targets exist on disk; then materialize the symlinks. The
        # archive uses SONAME chains like
        # libllama.so → libllama.so.0 → libllama.so.0.0.7400.
        deferred_links: list[tuple[Path, str]] = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if not name.startswith(bin_prefix):
                continue
            rel = name[len(bin_prefix) :]
            if not rel or "/" in rel:
                # Skip nested files; we only want flat bin/* contents.
                continue
            mode = (info.external_attr >> 16) & 0xFFFF
            is_symlink = (mode & 0o170000) == 0o120000
            out_path = dest.parent / rel
            if out_path.is_symlink() or out_path.exists():
                out_path.unlink()
            if is_symlink:
                # zip stores the link target as the file contents.
                link_target = zf.read(info).decode("utf-8")
                deferred_links.append((out_path, link_target))
                continue
            with zf.open(info) as src, open(out_path, "wb") as dst_f:
                shutil.copyfileobj(src, dst_f)
            # Preserve executable bits from the archive's external
            # attributes (lower 9 bits of the unix mode).
            perm = mode & 0o777
            if perm:
                out_path.chmod(perm)
        for link_path, link_target in deferred_links:
            os.symlink(link_target, link_path)


def _extract_flat_bin_targz(tmp: Path, dest: Path, target_name: str) -> None:
    """Extract the flat ``bin/`` payload of a llama.cpp .tar.gz release asset.

    Mirrors ``_extract_flat_bin_zip``: locate the binary to learn the
    archive's bin-folder prefix, extract only that folder's direct children
    next to ``dest`` (RUNPATH=$ORIGIN needs the .so files beside the
    binary), and materialize SONAME symlink chains after their targets.
    """
    with tarfile.open(tmp, "r:gz") as tf:
        members = tf.getmembers()
        member = next(
            (
                m
                for m in members
                if m.name.endswith(f"/{target_name}") or m.name == target_name
            ),
            None,
        )
        if member is None:
            raise RuntimeError(f"{target_name} not found inside {tmp.name}")
        bin_prefix = member.name[: -len(target_name)]
        deferred_links: list[tuple[Path, str]] = []
        for m in members:
            if m.isdir():
                continue
            if not m.name.startswith(bin_prefix):
                continue
            rel = m.name[len(bin_prefix) :]
            if not rel or "/" in rel:
                continue  # flat bin/* contents only
            out_path = dest.parent / rel
            if out_path.is_symlink() or out_path.exists():
                out_path.unlink()
            if m.issym():
                deferred_links.append((out_path, m.linkname))
                continue
            src = tf.extractfile(m)
            if src is None:
                continue
            with src, open(out_path, "wb") as dst_f:
                shutil.copyfileobj(src, dst_f)
            perm = m.mode & 0o777
            if perm:
                out_path.chmod(perm)
        for link_path, link_target in deferred_links:
            os.symlink(link_target, link_path)


def download_qwen3vl(model_id: str) -> bool:
    """Fetch all artefacts for the given VLM model id."""
    print("\n" + "=" * 60)
    print(f"Setting up VLM tagger ({model_id})…")
    print("=" * 60)
    targets = resolve_vlm_targets(model_id)
    all_ok = True
    for t in targets:
        try:
            _ensure_target(t)
        except Exception as e:
            print(f"  ✗ Failed to fetch {t.dest.name}: {e}")
            all_ok = False
    return all_ok


def download_nltk_data():
    """Download required NLTK data packages"""
    print("=" * 60)
    print("Setting up NLTK data packages...")
    print("=" * 60)

    # Download required data
    packages = {"stopwords": "corpora", "punkt": "tokenizers"}
    data_dir = get_nltk_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    for package, kind in packages.items():
        if (data_dir / kind / package).exists():
            print(f"✓ {package} already downloaded")
            continue
        print(f"Downloading {package}...")
        if not nltk.download(package, download_dir=str(data_dir)):
            raise RuntimeError(f"could not download {package}")
        print(f"✓ {package} downloaded successfully")


def download_clip(key: str) -> None:
    """Fetch the CLIP weights for ``key`` into the Hugging Face cache, so the
    first Embedding does not sit at 0 of N while they download."""
    from metascan.core.embedding_manager import EmbeddingManager

    print("=" * 60)
    print(f"Setting up CLIP ({key})...")
    print("=" * 60)
    EmbeddingManager(model_key=key, device="cpu")._ensure_model_loaded()
    print(f"✓ CLIP {key} ready")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Metascan setup — downloads NLTK data "
        "and (optionally) Qwen3-VL tagging weights."
    )
    parser.add_argument(
        "--qwen3vl",
        metavar="MODEL_ID",
        help="Also download the chosen VLM GGUF + mmproj + llama-server binary "
        "(e.g. qwen3vl-2b, qwen3vl-4b, qwen3vl-8b, qwen3vl-30b-a3b, qwen38-27b)",
    )
    parser.add_argument(
        "--vlm",
        dest="qwen3vl",
        metavar="MODEL_ID",
        help="Alias for --qwen3vl.",
    )
    parser.add_argument(
        "--clip",
        choices=["small", "medium", "large"],
        help="Also download the CLIP weights for this model size.",
    )
    parser.add_argument(
        "--skip-nltk",
        action="store_true",
        help="Skip the NLTK data download.",
    )
    args = parser.parse_args()

    print("Metascan Setup - Downloading required data and models")

    if not args.skip_nltk:
        try:
            download_nltk_data()
            print("✓ NLTK setup complete!")
        except Exception as e:
            print(f"✗ NLTK setup failed: {e}")
            sys.exit(1)

    if args.clip:
        try:
            download_clip(args.clip)
        except Exception as e:
            print(f"✗ CLIP setup failed: {e}")
            sys.exit(1)

    if args.qwen3vl:
        try:
            ok = download_qwen3vl(args.qwen3vl)
            if not ok:
                print(
                    f"\nWarning: Qwen3-VL setup did not fully complete for {args.qwen3vl}."
                )
        except Exception as e:
            print(f"✗ Qwen3-VL setup failed: {e}")

    print("\n" + "=" * 60)
    print("Setup complete! You can now run Metascan.")
    print("=" * 60)
