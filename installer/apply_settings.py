"""Write the Windows installer's answers into config.json.

The wizard collects the settings and saves them as JSON; provision.ps1 runs
this with the new virtualenv's Python:

    python -m installer.apply_settings --settings wizard.json

A config.json that already exists keeps everything the wizard did not ask
about. Prints the CLIP model size to download, the one thing provision.ps1
needs back, as ``clip_model=<key>``.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from metascan.utils.app_paths import get_config_path

DEFAULT_COMFY_HOST = "127.0.0.1"
DEFAULT_COMFY_PORT = 8188


def comfy_base_url(host: str, port: int) -> str:
    """``http://host:port``, from a host that may be pasted as a URL."""
    host = host.strip().rstrip("/")
    for scheme in ("http://", "https://"):
        if host.lower().startswith(scheme):
            host = host[len(scheme) :]
    if not host:
        host = DEFAULT_COMFY_HOST
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # a bare IPv6 address
    return f"http://{host}:{port}"


def scan_directories(folders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The config's directory rows. Scan folders are used exactly as written,
    and the docs' form on Windows is forward slashes: ``D:/Media``."""
    rows: List[Dict[str, Any]] = []
    seen = set()
    for folder in folders:
        path = str(folder.get("filepath") or "").strip().replace("\\", "/")
        if len(path) > 3:
            path = path.rstrip("/")
        if not path or path.casefold() in seen:
            continue
        seen.add(path.casefold())
        rows.append(
            {
                "filepath": path,
                "search_subfolders": bool(folder.get("search_subfolders", True)),
            }
        )
    return rows


def recommended_clip_model() -> Optional[str]:
    """The size the hardware tier recommends, or None if the probe fails."""
    try:
        from metascan.core.hardware import detect_hardware, feature_gates

        gates = feature_gates(detect_hardware())
    except Exception:
        return None
    for key in ("large", "medium", "small"):
        gate = gates.get(f"clip-{key}")
        if gate is not None and gate.recommended:
            return key
    return None


def merge_settings(
    config: Dict[str, Any],
    settings: Dict[str, Any],
    clip_model: Optional[str],
) -> Dict[str, Any]:
    """``config`` with the wizard's answers in, its other keys untouched."""
    merged = dict(config)
    merged["directories"] = scan_directories(settings.get("directories") or [])
    merged["comfy"] = {
        **(config.get("comfy") or {}),
        "base_url": comfy_base_url(
            str(settings.get("comfy_host") or ""),
            int(settings.get("comfy_port") or DEFAULT_COMFY_PORT),
        ),
    }
    # "" is the default, <data dir>/_original, which follows the install.
    merged["upscale"] = {
        **(config.get("upscale") or {}),
        "original_root": str(settings.get("original_root") or "").strip(),
    }
    if clip_model is not None:
        merged["similarity"] = {
            **(config.get("similarity") or {}),
            "clip_model": clip_model,
        }
    return merged


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--settings",
        type=Path,
        help="The wizard's answers. Without it, config.json is left as it is.",
    )
    args = parser.parse_args(argv)

    config_path = get_config_path()  # creates it from config_example.json
    config = json.loads(config_path.read_text(encoding="utf-8"))

    if args.settings is not None:
        # The wizard saves UTF-8 with a BOM.
        settings = json.loads(args.settings.read_text(encoding="utf-8-sig"))
        config = merge_settings(config, settings, recommended_clip_model())
        original = config["upscale"]["original_root"]
        if original:
            Path(original).mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    clip = (config.get("similarity") or {}).get("clip_model") or "small"
    print(f"clip_model={clip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
