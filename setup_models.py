#!/usr/bin/env python3
"""
Setup script to download required NLTK data and AI upscaling models
"""
import argparse
import nltk
import shutil
import ssl
import os
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Add project to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from metascan.core.media_upscaler import MediaUpscaler
from metascan.utils.app_paths import get_data_dir

# Handle SSL certificate issues
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


def resolve_qwen3vl_targets(model_id: str) -> list[DownloadTarget]:
    """Return GGUF + mmproj + llama-server-binary download targets for ``model_id``."""
    from metascan.core.vlm_models import REGISTRY
    from metascan.utils.llama_server import binary_path, pick_release_asset, release_url
    from metascan.core.hardware import detect_hardware

    spec = REGISTRY[model_id]
    vlm_dir = get_data_dir() / "models" / "vlm"

    rpt = detect_hardware()
    return [
        DownloadTarget(
            repo=spec.hf_repo,
            filename=spec.gguf_filename,
            dest=vlm_dir / spec.gguf_filename,
        ),
        DownloadTarget(
            repo=spec.hf_repo,
            filename=spec.mmproj_repo_filename,
            dest=vlm_dir / spec.mmproj_filename,
        ),
        DownloadTarget(
            url=release_url(pick_release_asset(rpt)),
            dest=binary_path(),
        ),
    ]


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
        # Direct URL target. The llama.cpp release URLs are .zip archives
        # that contain the llama-server binary; extract it.
        from metascan.utils.llama_server import binary_filename

        print(f"  ⇣ Downloading {t.url}…")
        tmp = t.dest.with_suffix(".zip")
        try:
            urllib.request.urlretrieve(t.url, tmp)
            with zipfile.ZipFile(tmp, "r") as zf:
                target_name = binary_filename()
                # The archive layout varies; find the entry whose basename matches.
                member = next(
                    (
                        n
                        for n in zf.namelist()
                        if n.endswith(f"/{target_name}") or n == target_name
                    ),
                    None,
                )
                if member is None:
                    raise RuntimeError(f"{target_name} not found inside {t.url}")
                with zf.open(member) as src, open(t.dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
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


def download_qwen3vl(model_id: str) -> bool:
    """Fetch all artefacts for the given Qwen3-VL model id."""
    print("\n" + "=" * 60)
    print(f"Setting up Qwen3-VL VLM tagger ({model_id})…")
    print("=" * 60)
    targets = resolve_qwen3vl_targets(model_id)
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
    packages = ["stopwords", "punkt"]

    for package in packages:
        try:
            nltk.data.find(f"tokenizers/{package}")
            print(f"✓ {package} already downloaded")
        except LookupError:
            print(f"Downloading {package}...")
            nltk.download(package)
            print(f"✓ {package} downloaded successfully")


def download_upscaling_models():
    """Download required AI upscaling models"""
    print("\n" + "=" * 60)
    print("Setting up AI upscaling models...")
    print("=" * 60)

    models_dir = get_data_dir() / "models"
    print(f"Models directory: {models_dir}")

    # Initialize upscaler
    upscaler = MediaUpscaler(
        models_dir=models_dir, device="auto", tile_size=512, debug=False
    )

    if upscaler.models_available:
        print("✓ All required models are already available")
        return True

    print("Downloading required AI models...")
    print("This may take several minutes depending on your internet connection.")
    print("\nModels to download:")
    print("• RealESRGAN x2 model (~64 MB)")
    print("• RealESRGAN x4 model (~64 MB)")
    print("• RealESRGAN x4 anime model (~17 MB)")
    print("• GFPGAN face enhancement model (~333 MB)")
    print("• RIFE frame interpolation binary (~437 MB)")
    print("Total download size: ~915 MB\n")

    def progress_callback(message: str, progress: float):
        # Simple progress display
        bar_length = 40
        filled_length = int(bar_length * progress / 100)
        bar = "█" * filled_length + "░" * (bar_length - filled_length)
        print(f"\r[{bar}] {progress:6.1f}% - {message}", end="", flush=True)

    success = upscaler.setup_models(progress_callback)
    print()  # New line after progress bar

    if success:
        print("✓ All AI models downloaded successfully!")
        return True
    else:
        print("✗ Failed to download AI models")
        print("Please check your internet connection and try again.")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Metascan setup — downloads NLTK data, AI upscaling models, "
        "and (optionally) Qwen3-VL tagging weights."
    )
    parser.add_argument(
        "--qwen3vl",
        metavar="MODEL_ID",
        help="Also download the chosen Qwen3-VL GGUF + mmproj + llama-server binary "
        "(e.g. qwen3vl-2b, qwen3vl-4b, qwen3vl-8b, qwen3vl-30b-a3b)",
    )
    parser.add_argument(
        "--skip-nltk",
        action="store_true",
        help="Skip the NLTK data download.",
    )
    parser.add_argument(
        "--skip-upscaling",
        action="store_true",
        help="Skip the upscaling-model download.",
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

    if not args.skip_upscaling:
        try:
            models_success = download_upscaling_models()
            if not models_success:
                print(
                    "\nWarning: AI model setup failed. Upscaling features will not work."
                )
                print("You can try running this script again later or download models")
                print("automatically when you first attempt to upscale media.")
        except Exception as e:
            print(f"✗ AI model setup failed: {e}")
            print("Upscaling features will not work until models are downloaded.")

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
