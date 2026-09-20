"""Where an image-to-video clip lands on disk.

Pure (no I/O): the i2v config's ``output_root`` plus a date-expanding
``output_prefix`` resolve to a directory and a filename stem. The prefix
is a path *relative to the root* whose last component is the filename
prefix -- ``/%Y-%m-%d/minimax_`` under ``/mnt/d/Media/images`` yields
``/mnt/d/Media/images/2026-09-20/minimax_<number>.mp4``. A leading slash
is cosmetic; it never means the filesystem root.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

# Windows filename rules plus separators and control characters -- the
# same set storyboard_runner.expand_name_template replaces. Applied per
# path component AFTER date expansion, so a slash born from a token
# (%D -> 09/20/26) cannot create directories.
_COMPONENT_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# strftime directives in a template, with "%%" consumed as a unit so an
# escaped percent is never mistaken for one.
_DIRECTIVE = re.compile(r"%(%|[A-Za-z])")


class I2vOutputError(ValueError):
    """The configured output root/prefix cannot produce a safe path."""


@dataclass(frozen=True)
class OutputTarget:
    directory: Path
    stem: str


def _split_prefix(prefix: Optional[str]) -> Tuple[List[str], str]:
    """(directory components, filename prefix), both still unexpanded."""
    text = (prefix or "").strip().replace("\\", "/")
    if not text:
        return [], ""
    *dirs, file_prefix = text.split("/")
    return dirs, file_prefix


def _expand(component: str, now: datetime) -> str:
    try:
        expanded = now.strftime(component) if "%" in component else component
    except ValueError as exc:
        raise I2vOutputError(
            f"Invalid date token in output prefix component {component!r}: {exc}"
        ) from exc
    return _COMPONENT_ILLEGAL.sub("-", expanded)


def resolve_output_target(
    root: Optional[str], prefix: Optional[str], now: datetime, number: int
) -> OutputTarget:
    """Directory + filename stem for one clip. ``number`` is the caller's
    uniqueness counter (epoch seconds); the suffix comes from ComfyUI."""
    root_text = (root or "").strip()
    if not root_text:
        raise I2vOutputError("No output root directory is configured")
    root_path = Path(root_text)
    if not root_path.is_absolute():
        raise I2vOutputError(f"Output root must be an absolute path: {root_text}")

    dirs, file_prefix = _split_prefix(prefix)
    directory = root_path
    for raw in dirs:
        part = raw.strip()
        if part in ("", "."):
            continue
        if part == "..":
            raise I2vOutputError(
                "Output prefix may not contain '..' -- it is always relative "
                "to the output root"
            )
        directory = directory / _expand(part, now)
    if file_prefix.strip() == "..":
        raise I2vOutputError(
            "Output prefix may not contain '..' -- it is always relative "
            "to the output root"
        )
    return OutputTarget(
        directory=directory, stem=f"{_expand(file_prefix.strip(), now)}{number}"
    )


def output_prefix_warnings(prefix: Optional[str]) -> List[str]:
    """Advisory lint for a prefix template; never blocks."""
    tokens = {m.group(1) for m in _DIRECTIVE.finditer(prefix or "")}
    warnings: List[str] = []
    if "M" in tokens and "H" not in tokens:
        warnings.append(
            "%M is minutes, not the month -- use %m for the month " "(e.g. %Y-%m-%d)."
        )
    return warnings


__all__ = [
    "I2vOutputError",
    "OutputTarget",
    "output_prefix_warnings",
    "resolve_output_target",
]
