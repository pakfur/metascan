"""Server configuration for the metascan backend."""

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from metascan.core.t2i_directions import DirectionSettings
from metascan.utils.app_paths import get_config_path


@dataclass
class DirectoryConfig:
    filepath: str
    search_subfolders: bool = True


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8700
    api_key: Optional[str] = None
    cors_origins: List[str] = field(default_factory=lambda: ["*"])


def load_app_config() -> dict:
    """Load the metascan config.json file."""
    config_path = get_config_path()
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def save_app_config(config: dict) -> None:
    """Save the metascan config.json file."""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


def get_server_config() -> ServerConfig:
    """Load server-specific config from environment variables or defaults."""
    return ServerConfig(
        host=os.environ.get("METASCAN_HOST", "0.0.0.0"),
        port=int(os.environ.get("METASCAN_PORT", "8700")),
        api_key=os.environ.get("METASCAN_API_KEY"),
        cors_origins=os.environ.get("METASCAN_CORS_ORIGINS", "*").split(","),
    )


def get_directories(config: dict) -> List[DirectoryConfig]:
    """Extract directory configurations from app config."""
    return [
        DirectoryConfig(
            filepath=d["filepath"],
            search_subfolders=d.get("search_subfolders", True),
        )
        for d in config.get("directories", [])
    ]


def get_ui_config(config: dict) -> dict:
    """UI section of config.json. Currently exposes:
    - map_tile_url: MapLibre GL style URL for the location panel.
                    Defaults to OpenFreeMap liberty.
    """
    ui = config.get("ui") or {}
    return {
        "map_tile_url": ui.get(
            "map_tile_url",
            "https://tiles.openfreemap.org/styles/liberty",
        ),
    }


def get_models_config(config: dict) -> dict:
    """Return the ``models`` section with defaults filled in.

    Shape:
        {
            "preload_at_startup": ["clip-large", ...],  # model ids
            "huggingface_token": "<str>",               # "" if unset
        }
    """
    raw = config.get("models", {}) or {}
    preload = raw.get("preload_at_startup") or []
    if not isinstance(preload, list):
        preload = []
    return {
        "preload_at_startup": [str(x) for x in preload],
        "huggingface_token": str(raw.get("huggingface_token") or ""),
    }


def get_vlm_roles_config(config: dict) -> dict:
    """Return the configured VLM role models, ``None`` where unset.

    Shape:
        {
            "writer_vlm": "qwen3vl-8b" | None,   # T2I / I2V prompt writing
            "general_vlm": "qwen38-27b" | None,  # tagging, chat, storyboard
        }

    Defaults for unset keys need the hardware report and the installed
    weights, so they are resolved in ``backend.services.vlm_roles``.
    """
    raw = config.get("models", {}) or {}

    def _id(value: object) -> Optional[str]:
        return value.strip() if isinstance(value, str) and value.strip() else None

    return {
        "writer_vlm": _id(raw.get("writer_vlm")),
        "general_vlm": _id(raw.get("general_vlm")),
    }


def get_taste_config(config: dict) -> dict:
    """Return the ``taste`` section with defaults filled in.

    Shape:
        {
            "half_life_days": 180.0,  # label time-decay half-life
            "auto_retrain": True,     # background retrain after new ratings
        }
    """
    from metascan.core.taste_labels import DEFAULT_HALF_LIFE_DAYS

    raw = config.get("taste", {}) or {}
    try:
        half_life = float(raw.get("half_life_days", DEFAULT_HALF_LIFE_DAYS))
    except (TypeError, ValueError):
        half_life = DEFAULT_HALF_LIFE_DAYS
    if not half_life > 0:
        half_life = DEFAULT_HALF_LIFE_DAYS
    return {
        "half_life_days": half_life,
        "auto_retrain": bool(raw.get("auto_retrain", True)),
    }


def get_comfy_config(config: dict) -> dict:
    """Return the ``comfy`` section with defaults filled in.

    Shape:
        {
            "base_url": "http://127.0.0.1:8188",
            "in_flight": 2,                  # jobs held inside ComfyUI at once
            "unload_vlm_during_generation": True,
            "output_root": "data/storyboards",
            "request_timeout_s": 30.0,
            "transfer_timeout_s": 600.0,     # one upload or download
        }
    """
    raw = config.get("comfy", {}) or {}
    return {
        "base_url": str(raw.get("base_url") or "http://127.0.0.1:8188"),
        "in_flight": max(1, int(raw.get("in_flight") or 2)),
        "unload_vlm_during_generation": bool(
            raw.get("unload_vlm_during_generation", True)
        ),
        "output_root": str(raw.get("output_root") or "data/storyboards"),
        "request_timeout_s": float(raw.get("request_timeout_s") or 30.0),
        "transfer_timeout_s": float(raw.get("transfer_timeout_s") or 600.0),
    }


# The longest output edges the Upscale dialog offers for a workflow with
# MS_MAX_SIZE; 0 means no limit.
DEFAULT_MAX_SIZES = (0, 1920, 2560, 3840)


def get_upscale_config(config: dict) -> dict:
    """Return the ``upscale`` section with defaults filled in.

    Shape:
        {
            "original_root": "",             # "" = <data dir>/_original
            "default_image_preset_id": None,
            "default_video_preset_id": None,
            "max_count": 8,                  # results one run may make
            "window": 2,                     # unfinished jobs of one run
            "scales": [2, 4],                # offered when a workflow has MS_SCALE
            "default_scale": 2,
            "denoise_levels": [0.2, 0.3, 0.4, 0.5],  # offered with MS_DENOISE
            "default_denoise": 0.3,
            # At or below this denoise the dialog starts on a refine prompt
            # (upscale_refine_prompts) instead of the image's own.
            "refine_prompt_max_denoise": 0.3,
            # Longest output edge offered with MS_MAX_SIZE (0 = no limit)
            "max_sizes": [0, 1920, 2560, 3840],
            "default_max_size": 2560,
        }
    """
    raw = config.get("upscale", {}) or {}

    def preset_id(key: str) -> Optional[int]:
        value = raw.get(key)
        return value if isinstance(value, int) and value > 0 else None

    def limit(key: str, default: int) -> int:
        try:
            return max(1, int(raw.get(key, default)))
        except (TypeError, ValueError):
            return default

    scales = raw.get("scales")
    if not isinstance(scales, list):
        scales = [2, 4]
    scales = [float(s) for s in scales if isinstance(s, (int, float)) and s > 0]
    scales = scales or [2.0, 4.0]
    default_scale = raw.get("default_scale")
    if not isinstance(default_scale, (int, float)) or default_scale <= 0:
        default_scale = scales[0]
    levels = raw.get("denoise_levels")
    if not isinstance(levels, list):
        levels = [0.2, 0.3, 0.4, 0.5]
    levels = [
        float(v)
        for v in levels
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 1
    ]
    levels = levels or [0.2, 0.3, 0.4, 0.5]
    default_denoise = raw.get("default_denoise")
    if (
        not isinstance(default_denoise, (int, float))
        or isinstance(default_denoise, bool)
        or not 0 < default_denoise <= 1
    ):
        default_denoise = 0.3 if 0.3 in levels else levels[0]
    refine_max = raw.get("refine_prompt_max_denoise")
    if (
        not isinstance(refine_max, (int, float))
        or isinstance(refine_max, bool)
        or not 0 <= refine_max <= 1
    ):
        refine_max = 0.3
    sizes = raw.get("max_sizes")
    if not isinstance(sizes, list):
        sizes = list(DEFAULT_MAX_SIZES)
    sizes = sorted(
        {
            int(v)
            for v in sizes
            if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 16384
        }
    )
    sizes = sizes or list(DEFAULT_MAX_SIZES)
    default_max_size = raw.get("default_max_size")
    if (
        not isinstance(default_max_size, int)
        or isinstance(default_max_size, bool)
        or default_max_size not in sizes
    ):
        default_max_size = 2560 if 2560 in sizes else sizes[0]
    original_root = raw.get("original_root")
    original_root = original_root.strip() if isinstance(original_root, str) else ""
    return {
        "original_root": original_root,
        "default_image_preset_id": preset_id("default_image_preset_id"),
        "default_video_preset_id": preset_id("default_video_preset_id"),
        "max_count": limit("max_count", 8),
        "window": limit("window", 2),
        "scales": scales,
        "default_scale": float(default_scale),
        "denoise_levels": levels,
        "default_denoise": float(default_denoise),
        "refine_prompt_max_denoise": float(refine_max),
        "max_sizes": sizes,
        "default_max_size": default_max_size,
    }


I2V_DEFAULT_OUTPUT_PREFIX = "/%Y-%m-%d/i2v_"


def get_i2v_config(config: dict) -> dict:
    """Return the ``i2v`` section with defaults filled in.

    Shape:
        {
            "fast_preset_id": None,      # workflow_presets.id or None
            "quality_preset_id": None,
            "durations": [6.0, 10.0, 15.0, 20.0],
            "default_duration": 6.0,
            "default_quality": "fast",   # "fast" | "quality"
            "megapixels": [0.25, 0.5, 0.75, 1.0],
            "default_megapixels": 0.75,
            "steps": [20, 25, 30, 35, 40],   # High quality only
            "default_steps": 25,
            "output_root": "",           # "" = <comfy.output_root>/i2v/<image>/
            "output_prefix": "/%Y-%m-%d/i2v_",
            "max_batch_size": 20,        # batches per run, each >= 1
            "max_count_per_batch": 20,   # videos per batch, each >= 1
            "window": 4,                 # unfinished jobs per run
        }

    ``output_root`` + ``output_prefix`` place generated clips (see
    metascan/core/i2v_output.py): the prefix is a strftime-expanded path
    relative to the root whose last component is the filename prefix.

    Output dimensions are derived from the source image's aspect ratio
    and the chosen megapixel budget (i2v_compiler.i2v_dims), so there is
    no orientation setting -- orientation always follows the source.
    """
    raw = config.get("i2v", {}) or {}

    def _preset_id(v: object) -> Optional[int]:
        try:
            return int(v) if v else None
        except (TypeError, ValueError):
            return None

    fallback = [6.0, 10.0, 15.0, 20.0]
    try:
        durations = [float(d) for d in (raw.get("durations") or [])]
    except (TypeError, ValueError):
        durations = []
    if not durations:
        durations = fallback
    try:
        default_duration = float(raw.get("default_duration") or durations[0])
    except (TypeError, ValueError):
        default_duration = durations[0]
    quality = str(raw.get("default_quality") or "fast")

    mp_fallback = [0.25, 0.5, 0.75, 1.0]
    try:
        megapixels = [float(m) for m in (raw.get("megapixels") or [])]
    except (TypeError, ValueError):
        megapixels = []
    megapixels = [m for m in megapixels if m > 0]
    if not megapixels:
        megapixels = mp_fallback
    try:
        default_mp = float(raw.get("default_megapixels") or 0.75)
    except (TypeError, ValueError):
        default_mp = 0.75
    if default_mp not in megapixels:
        default_mp = megapixels[0]

    steps_fallback = [20, 25, 30, 35, 40]
    try:
        steps = [int(s) for s in (raw.get("steps") or [])]
    except (TypeError, ValueError):
        steps = []
    steps = [s for s in steps if s > 0]
    if not steps:
        steps = steps_fallback
    try:
        default_steps = int(raw.get("default_steps") or 25)
    except (TypeError, ValueError):
        default_steps = 25
    if default_steps not in steps:
        default_steps = steps[0]

    output_root = raw.get("output_root")
    output_root = output_root.strip() if isinstance(output_root, str) else ""
    # An explicit "" is a real choice (files straight into the root, bare
    # number as the name); only an absent/junk value gets the default.
    output_prefix = raw.get("output_prefix")
    if not isinstance(output_prefix, str):
        output_prefix = I2V_DEFAULT_OUTPUT_PREFIX

    def _limit(key: str, default: int) -> int:
        try:
            value = raw.get(key)
            return max(1, int(default if value is None else value))
        except (TypeError, ValueError):
            return default

    return {
        "fast_preset_id": _preset_id(raw.get("fast_preset_id")),
        "quality_preset_id": _preset_id(raw.get("quality_preset_id")),
        "durations": durations,
        "default_duration": default_duration,
        "default_quality": quality if quality in ("fast", "quality") else "fast",
        "megapixels": megapixels,
        "default_megapixels": default_mp,
        "steps": steps,
        "default_steps": default_steps,
        "output_root": output_root,
        "output_prefix": output_prefix.strip(),
        "max_batch_size": _limit("max_batch_size", 20),
        "max_count_per_batch": _limit("max_count_per_batch", 20),
        "window": _limit("window", 4),
    }


T2I_DEFAULT_OUTPUT_PREFIX = "/%Y-%m-%d/t2i_"


def get_t2i_config(config: dict) -> dict:
    """Return the ``t2i`` section with defaults filled in.

    Shape:
        {
            "output_root": "",           # "" = <comfy.output_root>/t2i
            "output_prefix": "/%Y-%m-%d/t2i_",
            "megapixels": [0.5, 1.0, 1.5, 2.0],
            "default_megapixels": 1.0,
            "default_model": "krea2",    # a t2i_models profile id
            "model_workflows": {"krea2": None, "qwen": None, "sd": None,
                                "zimage": None, "moss": None},  # preset id or None
            "content_mode": "uncensored",  # uncensored | sfw | default
            "identity": {},              # per-model override: ref | noun | name
            "window": 4,                 # unfinished jobs per batch
            "max_batch_size": 500,
            "max_count_per_batch": 32,
            "directions": {              # caption directions (t2i_directions)
                "enabled": True,
                "emotion_missing_min": 0.70,
                "emotion_sensual_from": 0.60,
                "kiss_min": 0.80,
                "act_min": 0.80,
                "skip_act_on_conflict": True,
            },
            "structured_json_log": False,  # write the structured writer's JSON
                                           # to logs/structured_prompts.log
        }

    ``output_root`` + ``output_prefix`` place generated images the way the
    i2v pair places clips (metascan/core/i2v_output.py). ``window`` and the
    two limits are config-file-only; the config tab never writes them.

    Values are sanitised, not coerced: one of the wrong JSON type is
    ignored (``True`` is not 1, ``"3"`` is not 3, ``3.0`` is not a preset
    id). Unknown model ids and identity styles are dropped, megapixel
    entries outside ``(0, MAX_MEGAPIXELS]`` are dropped one by one, and the
    integer settings are clamped to at least 1.
    """
    # Function-level: keeps this module free of import cycles and stops
    # every route module that reads config.json from loading the prompt
    # store (t2i_prompt) just to import backend.config.
    from metascan.core.t2i_characters import IDENTITY_STYLES
    from metascan.core.t2i_form import MAX_MEGAPIXELS
    from metascan.core.t2i_models import MODEL_PROFILES
    from metascan.core.t2i_prompt import CONTENT_MODES

    raw = config.get("t2i")
    if not isinstance(raw, dict):
        raw = {}

    def _number(value: object) -> Optional[float]:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                return float(value)
            except OverflowError:  # an int too large for a float
                return None
        return None

    def _whole(value: object) -> Optional[int]:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return None

    def _preset_id(value: object) -> Optional[int]:
        whole = _whole(value)
        return whole if whole is not None and whole >= 1 else None

    def _at_least_one(value: object, default: int) -> int:
        whole = _whole(value)
        return default if whole is None else max(1, whole)

    output_root = raw.get("output_root")
    output_root = output_root.strip() if isinstance(output_root, str) else ""
    # An explicit "" is a real choice (files straight into the root, bare
    # number as the name); only an absent/junk value gets the default.
    output_prefix = raw.get("output_prefix")
    if not isinstance(output_prefix, str):
        output_prefix = T2I_DEFAULT_OUTPUT_PREFIX

    raw_megapixels = raw.get("megapixels")
    megapixels: List[float] = []
    for entry in raw_megapixels if isinstance(raw_megapixels, list) else []:
        number = _number(entry)
        # The range test is False for nan, and inf is above the cap.
        if number is not None and 0 < number <= MAX_MEGAPIXELS:
            megapixels.append(number)
    if not megapixels:
        megapixels = [0.5, 1.0, 1.5, 2.0]
    default_mp = _number(raw.get("default_megapixels"))
    if default_mp is None:
        default_mp = 1.0
    if default_mp not in megapixels:
        default_mp = megapixels[0]

    default_model = raw.get("default_model")
    if not isinstance(default_model, str) or default_model not in MODEL_PROFILES:
        default_model = "krea2"

    raw_workflows = raw.get("model_workflows")
    if not isinstance(raw_workflows, dict):
        raw_workflows = {}
    model_workflows = {
        model_id: _preset_id(raw_workflows.get(model_id)) for model_id in MODEL_PROFILES
    }

    content_mode = raw.get("content_mode")
    if not isinstance(content_mode, str) or content_mode not in CONTENT_MODES:
        content_mode = "uncensored"

    raw_identity = raw.get("identity")
    if not isinstance(raw_identity, dict):
        raw_identity = {}
    identity = {
        model_id: raw_identity[model_id]
        for model_id in MODEL_PROFILES
        if raw_identity.get(model_id) in IDENTITY_STYLES
    }

    raw_directions = raw.get("directions")
    if not isinstance(raw_directions, dict):
        raw_directions = {}
    base = DirectionSettings()
    directions: Dict[str, Any] = {}
    for name in ("enabled", "skip_act_on_conflict"):
        flag = raw_directions.get(name)
        directions[name] = flag if isinstance(flag, bool) else getattr(base, name)
    for name in ("emotion_missing_min", "emotion_sensual_from", "kiss_min", "act_min"):
        number = _number(raw_directions.get(name))
        if number is None or not math.isfinite(number):
            directions[name] = getattr(base, name)
        else:
            directions[name] = min(1.0, max(0.0, number))

    return {
        "output_root": output_root,
        "output_prefix": output_prefix.strip(),
        "megapixels": megapixels,
        "default_megapixels": default_mp,
        "default_model": default_model,
        "model_workflows": model_workflows,
        "content_mode": content_mode,
        "identity": identity,
        "window": _at_least_one(raw.get("window"), 4),
        "max_batch_size": _at_least_one(raw.get("max_batch_size"), 500),
        "max_count_per_batch": _at_least_one(raw.get("max_count_per_batch"), 32),
        "directions": directions,
        "structured_json_log": raw.get("structured_json_log") is True,
    }
