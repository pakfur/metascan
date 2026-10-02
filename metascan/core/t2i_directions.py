"""Caption directions for t2i prompt writing (spec 2026-10-02).

When a caption's classification passes a threshold, a short snippet is
picked from a list in ``data/t2i_captions/directions/`` and handed to the
prompt-writing model as a DIRECTION next to the description. Pure apart
from the cached file reads:

* lists are ``<key>.txt``: ``act.<act-name>[.pov]``, ``kissing``,
  ``emotion`` and ``emotion.sensual``; one snippet per line, ``#`` comments;
  every line passes the adult-only screen (``t2i_wildcards._read_list``);
* the line is picked from the seed exactly like a plain wildcard:
  ``sha256(f"{seed}|direction|{key}|0")`` modulo the list length;
* parts come in a fixed order -- act, kissing, emotion -- one sentence each;
* an empty list file means "not written yet": that part is skipped quietly,
  while a missing list file adds a warning;
* ``sfw`` content mode never gets an act or kissing part, and its emotion
  part always comes from ``emotion.txt``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from metascan.core.t2i_captions import CaptionRow
from metascan.core.t2i_characters import _draw
from metascan.core.t2i_wildcards import _read_list

logger = logging.getLogger(__name__)

DIRECTIONS_DIRNAME = "directions"
NO_DIRECTION_ACTS = frozenset({"none-artistic", "unclear"})

Snippets = Mapping[str, Tuple[str, ...]]


@dataclass(frozen=True)
class DirectionSettings:
    enabled: bool = True
    emotion_missing_min: float = 0.70
    emotion_sensual_from: float = 0.60
    kiss_min: float = 0.80
    act_min: float = 0.80
    skip_act_on_conflict: bool = True

    @classmethod
    def from_config(cls, section: Mapping[str, Any]) -> "DirectionSettings":
        """From an already-sanitised ``t2i.directions`` section."""
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in section.items() if k in names})


@dataclass(frozen=True)
class Direction:
    text: str
    parts: Tuple[str, ...]
    warnings: Tuple[str, ...]


def load_snippets(directory: Path) -> Tuple[Dict[str, Tuple[str, ...]], List[str]]:
    warnings: List[str] = []
    snippets: Dict[str, Tuple[str, ...]] = {}
    if not directory.is_dir():
        return snippets, warnings
    for path in sorted(directory.glob("*.txt")):
        values = _read_list(path, "direction", warnings)
        if values is not None:  # None: unreadable, so it counts as missing
            snippets[path.stem] = values
    return snippets, warnings


class SnippetCache:
    """Serves the snippet lists, reloading when a file changes. Never raises."""

    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)
        self._lock = threading.Lock()
        self._signature: Optional[Tuple[Any, ...]] = None
        self._snippets: Dict[str, Tuple[str, ...]] = {}
        self._warnings: List[str] = []

    def _signature_now(self) -> Tuple[Any, ...]:
        if not self._directory.is_dir():
            return ("missing",)
        signature: List[Tuple[str, int, int]] = []
        for path in sorted(self._directory.glob("*.txt")):
            try:
                stat = path.stat()
            except OSError:
                continue
            signature.append((path.name, stat.st_mtime_ns, stat.st_size))
        return tuple(signature)

    def get(self) -> Tuple[Dict[str, Tuple[str, ...]], List[str]]:
        with self._lock:
            try:
                signature = self._signature_now()
                if signature != self._signature:
                    self._snippets, self._warnings = load_snippets(self._directory)
                    self._signature = signature
            except Exception as exc:  # never raise into a request or a batch
                logger.exception("t2i direction lists reload failed")
                return dict(self._snippets), self._warnings + [
                    f"could not reload the direction lists: {exc}"
                ]
            return dict(self._snippets), list(self._warnings)


def _sentence(text: str) -> str:
    text = text.strip().rstrip(".")
    return (text[:1].upper() + text[1:] + ".") if text else ""


def build_direction(
    row: Optional[CaptionRow],
    seed: int,
    content_mode: str,
    settings: DirectionSettings,
    snippets: Snippets,
) -> Optional[Direction]:
    if not settings.enabled or row is None or row.classification is None:
        return None
    c = row.classification
    sfw = content_mode == "sfw"
    texts: List[str] = []
    parts: List[str] = []
    warnings: List[str] = []

    def pick(keys: Sequence[str], labels: Sequence[str]) -> None:
        for key, label in zip(keys, labels):
            values = snippets.get(key)
            if values:
                texts.append(
                    _sentence(values[_draw(seed, "direction", key, 0, len(values))])
                )
                parts.append(label)
                return
        if keys[-1] in snippets:
            return  # the base list exists but is empty: not written yet
        tried = " or ".join(f"{DIRECTIONS_DIRNAME}/{k}.txt" for k in keys)
        warnings.append(f"no direction snippets for {keys[-1]}: {tried} is missing")

    act_ok = (
        not sfw
        and c.act not in NO_DIRECTION_ACTS
        and c.act_p >= settings.act_min
        and not (settings.skip_act_on_conflict and c.act_conflict)
    )
    if act_ok:
        if c.partner != "none":
            pick(
                [f"act.{c.act}.pov", f"act.{c.act}"],
                [f"act:{c.act}:pov", f"act:{c.act}"],
            )
        else:
            pick([f"act.{c.act}"], [f"act:{c.act}"])
    if not sfw and c.kiss >= settings.kiss_min:
        pick(["kissing"], ["kissing"])
    if round(1.0 - c.emotion_explicit, 6) >= settings.emotion_missing_min:
        erotic = row.erotic_score
        if not sfw and erotic is not None and erotic >= settings.emotion_sensual_from:
            pick(["emotion.sensual", "emotion"], ["emotion:sensual", "emotion"])
        else:
            pick(["emotion"], ["emotion"])
    return Direction(text=" ".join(texts), parts=tuple(parts), warnings=tuple(warnings))
