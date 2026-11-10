"""Refine prompts: short prompts about the detail to add, for a refine
workflow (Moss + Ultimate SD Upscale) at a low denoise.

Every tile is redrawn with the whole prompt, so at 0.2-0.3 a prompt that
names surfaces and textures beats the image's own scene prompt, which pushes
a tile showing only a cheek or a wall toward the whole scene. The Upscale
dialog offers these beside the image's own prompt and starts on one at or
below ``upscale.refine_prompt_max_denoise``.

The prompts are built in, and ``<data dir>/upscale_refine_prompts.yml``
replaces them when it holds at least one valid entry; it is read again when
its modification time changes, so edits show on the dialog's next open. A
file that is missing or unusable gives the built-in ones (a broken file is
logged once per change, never fatal).

``guess_refine_prompt`` picks the entry for the image from its tags: an
illustration (VLM or CLIP tags only: a prompt that mentions "a painting on
the wall" is still a photo), else people (any tag), else a scene.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

FILENAME = "upscale_refine_prompts.yml"

# What an entry's ``for`` may say: the guess picks the first entry for it.
SUBJECTS = ("people", "scene", "illustration")

# Tag words that say the image shows people, from any tag source.
PEOPLE_TAGS = frozenset(
    {
        "woman",
        "women",
        "man",
        "men",
        "person",
        "people",
        "portrait",
        "face",
        "lady",
        "couple",
        "bride",
        "groom",
        "selfie",
        "model",
        "girl",
        "boy",
    }
)

# Tag words that say the image is not a photograph. Read from VLM and CLIP
# tags only: prompt words name what is in the picture as often as its medium.
ILLUSTRATION_TAGS = frozenset(
    {
        "illustration",
        "anime",
        "manga",
        "cartoon",
        "comic",
        "painting",
        "oil painting",
        "digital painting",
        "watercolor",
        "drawing",
        "sketch",
        "digital art",
        "concept art",
        "3d render",
        "cgi",
        "fantasy art",
    }
)


@dataclass(frozen=True)
class RefinePrompt:
    id: str
    label: str
    prompt: str
    negative: str
    # The subject the guess picks it for (SUBJECTS), or None.
    subject: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_PHOTO_NEGATIVE = (
    "airbrushed skin, plastic skin, waxy skin, oversaturated, painting, "
    "illustration, cartoon, CGI, blurry, soft focus, jpeg artifacts, noise, "
    "oversharpened, halo, watermark, text"
)

BUILT_IN: Tuple[RefinePrompt, ...] = (
    RefinePrompt(
        id="photo_people",
        label="Photo, people",
        subject="people",
        prompt=(
            "A high-resolution photograph with natural, true-to-life detail. "
            "Skin shows realistic texture: fine pores, subtle variation in tone, "
            "faint vellus hair and soft natural highlights, never smooth or "
            "airbrushed. Hair has distinct individual strands. Eyes are clear and "
            "sharp, with crisp eyelashes and natural catchlights. Fabric shows its "
            "weave and stitching, and surfaces keep their material texture. Sharp "
            "focus, accurate colour, consistent lighting, clean photographic detail."
        ),
        negative=_PHOTO_NEGATIVE + ", deformed eyes, extra fingers",
    ),
    RefinePrompt(
        id="photo_scene",
        label="Photo, scene",
        subject="scene",
        prompt=(
            "A high-resolution photograph with natural, true-to-life detail. "
            "Foliage shows individual leaves and blades of grass, bark and stone "
            "keep their grain and texture, water has crisp reflections, and wood, "
            "brick and fabric show their material surface. Edges are clean and "
            "sharp, distant detail stays crisp, with fine natural film grain. "
            "Accurate colour, consistent lighting, clean photographic detail."
        ),
        negative=(
            "blurry, soft focus, smeared texture, oversaturated, painting, "
            "illustration, cartoon, CGI, jpeg artifacts, noise, oversharpened, "
            "halo, watermark, text"
        ),
    ),
    RefinePrompt(
        id="illustration",
        label="Illustration",
        subject="illustration",
        prompt=(
            "A high-resolution illustration with clean, deliberate detail. Lines "
            "are crisp and confident, brushwork and shading are intentional, "
            "gradients are smooth and colours stay true to the original palette. "
            "Edges are sharp, small details are clearly defined, and the style "
            "stays consistent across the whole image."
        ),
        negative=(
            "photograph, photorealistic, film grain, noise, skin pores, blurry, "
            "muddy colours, smeared lines, jpeg artifacts, oversharpened, halo, "
            "watermark, text"
        ),
    ),
    RefinePrompt(
        id="gentle",
        label="Gentle",
        prompt=(
            "Sharp, clean, natural detail. Accurate colour, consistent lighting, "
            "true to the original image."
        ),
        negative="blurry, jpeg artifacts, noise, oversharpened, halo, watermark, text",
    ),
)


def _text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text or None


def parse_refine_prompts(data: Any) -> List[RefinePrompt]:
    """The valid entries of a loaded YAML document (``{prompts: [...]}``),
    in file order. An entry needs ``id``, ``label`` and ``prompt``; a second
    entry with an id already taken is skipped, and so is a ``for`` that
    names no subject. Raises ValueError when the shape is wrong."""
    if not isinstance(data, dict) or not isinstance(data.get("prompts"), list):
        raise ValueError("expected a mapping with a 'prompts' list")
    out: List[RefinePrompt] = []
    seen = set()
    for i, raw in enumerate(data["prompts"]):
        if not isinstance(raw, dict):
            logger.warning("%s: entry %d is not a mapping; skipped", FILENAME, i + 1)
            continue
        pid, label, prompt = (
            _text(raw.get("id")),
            _text(raw.get("label")),
            _text(raw.get("prompt")),
        )
        if not pid or not label or not prompt:
            logger.warning(
                "%s: entry %d needs id, label and prompt; skipped", FILENAME, i + 1
            )
            continue
        if pid in seen:
            logger.warning("%s: id %r is used twice; skipped", FILENAME, pid)
            continue
        subject = raw.get("for")
        if subject is not None and subject not in SUBJECTS:
            logger.warning(
                "%s: entry %r has for: %r, not one of %s; ignored",
                FILENAME,
                pid,
                subject,
                ", ".join(SUBJECTS),
            )
            subject = None
        seen.add(pid)
        out.append(
            RefinePrompt(
                id=pid,
                label=label,
                prompt=prompt,
                negative=_text(raw.get("negative")) or "",
                subject=subject,
            )
        )
    return out


class RefinePromptStore:
    """The refine prompts, from ``path`` when it holds a valid entry, else
    built in. Re-read when the file's modification time or size changes."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._stamp: Optional[Tuple[int, int]] = None
        self._prompts: Tuple[RefinePrompt, ...] = BUILT_IN

    @property
    def path(self) -> Path:
        return self._path

    def prompts(self) -> Tuple[RefinePrompt, ...]:
        try:
            st = self._path.stat()
            stamp: Optional[Tuple[int, int]] = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        with self._lock:
            if stamp != self._stamp:
                self._stamp = stamp
                self._prompts = self._read() if stamp is not None else BUILT_IN
            return self._prompts

    def _read(self) -> Tuple[RefinePrompt, ...]:
        try:
            data = yaml.safe_load(self._path.read_text(encoding="utf-8"))
            parsed = parse_refine_prompts(data)
        except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as exc:
            logger.warning(
                "%s is unusable (%s); using the built-in refine prompts",
                self._path.name,
                type(exc).__name__,
            )
            return BUILT_IN
        if not parsed:
            logger.warning(
                "%s has no valid entry; using the built-in refine prompts",
                self._path.name,
            )
            return BUILT_IN
        return tuple(parsed)


_default_store: Optional[RefinePromptStore] = None
_default_lock = threading.Lock()


def default_store() -> RefinePromptStore:
    """The store for ``<data dir>/upscale_refine_prompts.yml``, made again
    when the data dir changes (tests point it elsewhere)."""
    global _default_store
    from metascan.utils.app_paths import get_data_dir

    path = get_data_dir() / FILENAME
    with _default_lock:
        if _default_store is None or _default_store.path != path:
            _default_store = RefinePromptStore(path)
        return _default_store


def guess_subject(tags: Iterable[Tuple[str, str]]) -> str:
    """``people``, ``scene`` or ``illustration`` for an image with these
    ``(tag, source)`` pairs (``db.get_tags_for_file``)."""
    people = False
    for name, source in tags:
        key = name.strip().lower()
        if key in ILLUSTRATION_TAGS and source != "prompt":
            return "illustration"
        if key in PEOPLE_TAGS:
            people = True
    return "people" if people else "scene"


def guess_refine_prompt(
    tags: Iterable[Tuple[str, str]], prompts: Iterable[RefinePrompt]
) -> Optional[str]:
    """The id of the first prompt for the image's subject; the first prompt
    when none is for it; None when there are none."""
    entries = list(prompts)
    if not entries:
        return None
    subject = guess_subject(tags)
    for entry in entries:
        if entry.subject == subject:
            return entry.id
    return entries[0].id


__all__ = [
    "BUILT_IN",
    "FILENAME",
    "ILLUSTRATION_TAGS",
    "PEOPLE_TAGS",
    "RefinePrompt",
    "RefinePromptStore",
    "SUBJECTS",
    "default_store",
    "guess_refine_prompt",
    "guess_subject",
    "parse_refine_prompts",
]
