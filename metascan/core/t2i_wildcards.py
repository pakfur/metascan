"""Loads and validates the t2i wildcard lists and ``characters.yml``.

``data/t2i_captions/`` holds one plain-text list per slot -- ``hair.txt``,
``body.female.txt``, ``setting.txt`` and so on (one value per line, blank
lines and ``#`` comment lines ignored, duplicates dropped case-insensitively,
file order preserved) -- plus an optional ``characters.yml`` that overrides
the engine's names, nouns and slot layout. A missing ``characters.yml`` is
not an error: every key has a built-in default.

Two guards run on every list line (spec 3.7). Rejected lines are reported
as ``"<file>:<line>: <reason>"`` warnings, logged at WARNING, and left out:

* **Adult only.** In an ``age`` list any integer under 18, or a spelled-out
  age of thirteen to seventeen, is rejected. In every list a line containing
  a minor-indicating term is rejected (word-bounded, case-insensitive, with
  plural forms such as ``teens`` and ``children``).
* **No parentheses.** ComfyUI reads them as weighting syntax, so generated
  prompt text must never contain them.

Nothing here raises on bad data: a broken file becomes defaults or an empty
list plus a warning. ``LibraryCache`` reloads by polling file mtimes and
sizes (no watcher), so list edits apply to the next resolve.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

import yaml  # type: ignore[import-untyped]

from metascan.core.t2i_characters import CharacterConfig, Library

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "characters.yml"

MINOR_TERMS: Tuple[str, ...] = (
    "teen",
    "teenage",
    "teenager",
    "underage",
    "minor",
    "child",
    "kid",
    "preteen",
    "juvenile",
    "schoolgirl",
    "schoolboy",
    "loli",
    "shota",
    "under 18",
    "under eighteen",
)

_GENDERS = ("female", "male")
_KNOWN_KEYS = (
    "names",
    "nouns",
    "slots",
    "intro",
    "token_slots",
    "token_gender",
    "body_hair_prefixes",
)

_NAME_RE = re.compile(r"^[A-Z][A-Z0-9]*$")
_IDENT_RE = re.compile(r"^[a-z][a-z0-9]*$")
_LIST_KEY_RE = re.compile(r"^[a-z][a-z0-9]*(?:\.(?:female|male))?$")


def _term_pattern(term: str) -> str:
    return r"[\s-]+".join(re.escape(word) for word in term.split())


# More wording that names someone under 18, beyond the spec's deny list
# above. A space or a hyphen may separate the words ("school girl",
# "school-boy"); a plural is fine.
MINOR_PHRASES: Tuple[str, ...] = (
    "school girl",
    "school boy",
    "little girl",
    "little boy",
    "young girl",
    "young boy",
    "pre teen",
    "under age",
    "under18",
    "kiddo",
    "kiddie",
)

# Word families matched from the start of a word whatever follows, so
# "teenaged", "childlike", "lolita", "adolescent" and "prepubescent" are
# caught along with their plurals. The leading ``\b`` keeps "canteen",
# "between" and "kidney" legal.
MINOR_STEMS: Tuple[str, ...] = (
    "teen",
    "child",
    "loli",
    "shota",
    "adolescen",
    "pubescen",
    "prepubescen",
    "tween",
    "toddler",
    "infant",
    "juvenile",
    "underage",
    "preteen",
    "jailbait",
)

# ``\b`` on both sides keeps "canteen" and "kidney" legal; the optional
# suffix catches "teens", "kids", "minors"; "children" is irregular; the
# second alternative is the stem families above.
_MINOR_RE = re.compile(
    r"\b(?:"
    + "|".join(_term_pattern(term) for term in MINOR_TERMS + MINOR_PHRASES)
    + r"|children)(?:s|es)?\b"
    + r"|\b(?:"
    + "|".join(MINOR_STEMS)
    + r")\w*",
    re.IGNORECASE,
)

_UNIT_WORDS: Dict[str, int] = {
    word: number
    for number, word in enumerate(
        (
            "zero one two three four five six seven eight nine ten eleven twelve "
            "thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
        ).split()
    )
}
_TENS_WORDS: Dict[str, int] = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fourty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
# A spelled-out number below 100. The tens-plus-unit form comes first so
# "twenty-one" is one number (21), not "twenty" and a stray "one".
_NUMBER_WORD = (
    r"(?:(?:"
    + "|".join(_TENS_WORDS)
    + r")(?:[\s-]+(?:"
    + "|".join(w for w, n in _UNIT_WORDS.items() if 1 <= n <= 9)
    + r"))?|"
    + "|".join(sorted(_UNIT_WORDS, key=len, reverse=True))
    + r")"
)
_YEARS_OLD = r"(?:years?[\s-]*old|yrs?[\s-]*old|y/?o)"
_SPELLED_NUMBER_RE = re.compile(r"\b" + _NUMBER_WORD + r"\b", re.IGNORECASE)
# "14-year-old", "9 yo", "twelve year old", "aged 12", "age 16".
_DIGIT_AGE_RE = re.compile(
    r"(?<!\d)(\d{1,3})[\s-]*" + _YEARS_OLD + r"\b", re.IGNORECASE
)
_SPELLED_AGE_RE = re.compile(
    r"\b(" + _NUMBER_WORD + r")[\s-]*" + _YEARS_OLD + r"\b", re.IGNORECASE
)
_AGED_RE = re.compile(r"\bage[ds]?[\s:-]*(\d{1,3})\b", re.IGNORECASE)


def _spelled_value(words: str) -> int:
    """The value of a spelled-out number matched by ``_NUMBER_WORD``."""
    return sum(
        _TENS_WORDS.get(word, _UNIT_WORDS.get(word, 0))
        for word in re.split(r"[\s-]+", words.lower())
    )


def default_library_config() -> CharacterConfig:
    return CharacterConfig()


# --- characters.yml ---------------------------------------------------------


def _config_warning(message: str) -> str:
    return f"{CONFIG_FILENAME}: {message}"


def _text_items(value: Any, where: str, warnings: List[str]) -> Optional[List[str]]:
    """The non-empty text entries of a YAML list; None if it is not a list."""
    if not isinstance(value, list):
        warnings.append(_config_warning(f"'{where}' must be a list; using the default"))
        return None
    items: List[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            items.append(item.strip())
        else:
            warnings.append(
                _config_warning(f"'{where}' has a non-text entry {item!r}; skipped")
            )
    return items


def _identifiers(
    value: Any, where: str, warnings: List[str]
) -> Optional[Tuple[str, ...]]:
    items = _text_items(value, where, warnings)
    if items is None:
        return None
    found: List[str] = []
    for item in items:
        ident = item.lower()
        if not _IDENT_RE.match(ident):
            warnings.append(
                _config_warning(
                    f"'{where}' entry {item!r} is not a lowercase word; skipped"
                )
            )
        elif ident not in found:
            found.append(ident)
    return tuple(found)


def _names(value: Any, where: str, warnings: List[str]) -> Optional[Tuple[str, ...]]:
    items = _text_items(value, where, warnings)
    if items is None:
        return None
    found: List[str] = []
    for item in items:
        name = item.upper()
        if not _NAME_RE.match(name):
            warnings.append(
                _config_warning(
                    f"'{where}' entry {item!r} must be letters and digits, "
                    "starting with a letter; skipped"
                )
            )
            continue
        reason = _reject_reason("name", name.lower())
        if reason is not None:
            warnings.append(
                _config_warning(f"'{where}' entry {item!r} rejected: {reason}; skipped")
            )
        elif name not in found:
            found.append(name)
    return tuple(found)


def _by_gender(value: Any, where: str, warnings: List[str]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        warnings.append(
            _config_warning(f"'{where}' must map female and male; using the defaults")
        )
        return {}
    for key in value:
        if key not in _GENDERS:
            warnings.append(
                _config_warning(f"'{where}.{key}' is not female or male; ignored")
            )
    return {gender: value[gender] for gender in _GENDERS if gender in value}


def _name_updates(value: Any, warnings: List[str]) -> Dict[str, Any]:
    updates: Dict[str, Any] = {}
    for gender, raw in _by_gender(value, "names", warnings).items():
        names = _names(raw, f"names.{gender}", warnings)
        if names is not None:
            updates[f"{gender}_names"] = names
    female = updates.get("female_names", CharacterConfig().female_names)
    male = updates.get("male_names", CharacterConfig().male_names)
    clash = [name for name in male if name in female]
    for name in clash:
        warnings.append(
            _config_warning(f"{name} is listed as female and male; kept as female")
        )
    if clash:
        updates["male_names"] = tuple(name for name in male if name not in clash)
    return updates


def _noun_updates(value: Any, warnings: List[str]) -> Dict[str, Any]:
    updates: Dict[str, Any] = {}
    for gender, raw in _by_gender(value, "nouns", warnings).items():
        if isinstance(raw, str) and raw.strip():
            noun = raw.strip()
            reason = _reject_reason("noun", noun)
            if reason is None:
                updates[f"noun_{gender}"] = noun
            else:
                warnings.append(
                    _config_warning(
                        f"'nouns.{gender}' rejected: {reason}; using the default"
                    )
                )
        else:
            warnings.append(
                _config_warning(f"'nouns.{gender}' must be a word; using the default")
            )
    return updates


def _gender_map(
    value: Any, warnings: List[str]
) -> Optional[Tuple[Tuple[str, str], ...]]:
    if not isinstance(value, dict):
        warnings.append(
            _config_warning("'token_gender' must map a slot to female or male")
        )
        return None
    pairs: List[Tuple[str, str]] = []
    for slot, gender in value.items():
        if isinstance(slot, str) and gender in _GENDERS:
            pairs.append((slot.strip().lower(), str(gender)))
        else:
            warnings.append(
                _config_warning(
                    f"'token_gender.{slot}' must be female or male; skipped"
                )
            )
    return tuple(pairs)


def _intro_updates(
    value: Any,
    slots: Tuple[str, ...],
    token_slots: Tuple[str, ...],
    warnings: List[str],
) -> Dict[str, Any]:
    if not isinstance(value, dict):
        warnings.append(_config_warning("'intro' must map head and with to lists"))
        return {}
    known = set(slots) | set(token_slots)
    updates: Dict[str, Any] = {}
    for key, field in (("head", "head_slots"), ("with", "with_slots")):
        if key not in value:
            continue
        found = _identifiers(value[key], f"intro.{key}", warnings)
        if found is None:
            continue
        for slot in found:
            if slot not in known:
                warnings.append(
                    _config_warning(
                        f"'intro.{key}' names unknown slot '{slot}'; skipped"
                    )
                )
        updates[field] = tuple(slot for slot in found if slot in known)
    return updates


def _word_list_updates(data: Mapping[str, Any], warnings: List[str]) -> Dict[str, Any]:
    """slots, token_slots, token_gender and body_hair_prefixes."""
    updates: Dict[str, Any] = {}
    for key in ("slots", "token_slots"):
        if key not in data:
            continue
        found = _identifiers(data[key], key, warnings)
        if found:
            updates[key] = found
        elif found is not None:
            warnings.append(_config_warning(f"'{key}' is empty; using the default"))
    if "token_gender" in data:
        pairs = _gender_map(data["token_gender"], warnings)
        if pairs is not None:
            updates["token_gender"] = pairs
    if "body_hair_prefixes" in data:
        items = _text_items(data["body_hair_prefixes"], "body_hair_prefixes", warnings)
        if items is not None:
            updates["body_hair_prefixes"] = tuple(
                dict.fromkeys(item.lower() for item in items)
            )
    return updates


def _config_updates(data: Mapping[str, Any], warnings: List[str]) -> Dict[str, Any]:
    for key in data:
        if key not in _KNOWN_KEYS:
            warnings.append(_config_warning(f"unknown key '{key}' ignored"))
    updates: Dict[str, Any] = {}
    if "names" in data:
        updates.update(_name_updates(data["names"], warnings))
    if "nouns" in data:
        updates.update(_noun_updates(data["nouns"], warnings))
    updates.update(_word_list_updates(data, warnings))
    if "intro" in data:
        base = CharacterConfig()
        updates.update(
            _intro_updates(
                data["intro"],
                updates.get("slots", base.slots),
                updates.get("token_slots", base.token_slots),
                warnings,
            )
        )
    return updates


def _load_config(path: Path, warnings: List[str]) -> CharacterConfig:
    if not path.is_file():
        return default_library_config()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark is not None else ""
        problem = getattr(exc, "problem", None) or "invalid syntax"
        warnings.append(
            _config_warning(f"malformed YAML{where}: {problem}; using defaults")
        )
        return default_library_config()
    except (OSError, UnicodeDecodeError) as exc:
        warnings.append(
            _config_warning(
                f"could not be read ({exc.__class__.__name__}); using defaults"
            )
        )
        return default_library_config()
    if data is None:
        return default_library_config()
    if not isinstance(data, dict):
        warnings.append(_config_warning("must be a mapping; using defaults"))
        return default_library_config()
    return replace(default_library_config(), **_config_updates(data, warnings))


# --- list files -------------------------------------------------------------


def _age_under_18(slot: str, value: str) -> bool:
    """True if ``value`` states an age below 18.

    In an ``age`` list any number on the line counts, digits or words. In
    every other text ("14-year-old body", "aged 12", "a thirteen year old")
    only a number that is plainly an age does.
    """
    if slot == "age":
        for run in re.findall(r"\d+", value):
            digits = run.lstrip("0") or "0"
            if len(digits) <= 2 and int(digits) < 18:
                return True
        return any(
            _spelled_value(found.group(0)) < 18
            for found in _SPELLED_NUMBER_RE.finditer(value)
        )
    return (
        any(int(found.group(1)) < 18 for found in _DIGIT_AGE_RE.finditer(value))
        or any(int(found.group(1)) < 18 for found in _AGED_RE.finditer(value))
        or any(
            _spelled_value(found.group(1)) < 18
            for found in _SPELLED_AGE_RE.finditer(value)
        )
    )


def _reject_reason(slot: str, value: str) -> Optional[str]:
    """Why ``value`` must not reach a prompt, or None if it may.

    Used for every list line and for the free text of ``characters.yml``
    (nouns, names), so nothing a user edits bypasses the adult-only screen.
    """
    if "(" in value or ")" in value:
        return "contains parentheses (ComfyUI reads them as weights)"
    if _age_under_18(slot, value):
        return "age under 18"
    found = _MINOR_RE.search(value)
    if found:
        return f'contains minor term "{found.group(0).lower()}"'
    return None


def _read_list(path: Path, slot: str, warnings: List[str]) -> Optional[Tuple[str, ...]]:
    """The cleaned values of one list file; None if it could not be read."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        warnings.append(
            f"{path.name}: could not be read ({exc.__class__.__name__}); ignored"
        )
        return None
    values: List[str] = []
    seen: Set[str] = set()
    for lineno, raw in enumerate(
        text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1
    ):
        line = " ".join(raw.split())
        if not line or line.startswith("#"):
            continue
        reason = _reject_reason(slot, line)
        if reason is not None:
            message = f"{path.name}:{lineno}: rejected: {reason}"
            logger.warning("t2i list line %s", message)
            warnings.append(message)
            continue
        key = line.casefold()
        if key not in seen:
            seen.add(key)
            values.append(line)
    return tuple(values)


def load_library(directory: Path) -> Tuple[Library, List[str]]:
    """Read ``characters.yml`` and every ``*.txt`` list in ``directory``.

    Returns ``(library, warnings)``. Never raises on bad data.
    """
    directory = Path(directory)
    warnings: List[str] = []
    if not directory.is_dir():
        warnings.append(f"list directory not found: {directory}")
        return Library(default_library_config(), {}), warnings
    config = _load_config(directory / CONFIG_FILENAME, warnings)
    lists: Dict[str, Tuple[str, ...]] = {}
    for path in sorted(directory.glob("*.txt")):
        if not path.is_file():
            continue
        key = path.stem.lower()
        if not _LIST_KEY_RE.match(key):
            warnings.append(
                f"{path.name}: list names are lowercase letters and digits with an "
                "optional .female or .male suffix; ignored"
            )
            continue
        if key in lists:
            warnings.append(f"{path.name}: duplicate list '{key}'; ignored")
            continue
        values = _read_list(path, key.split(".")[0], warnings)
        if values is None:
            continue
        if not values:
            warnings.append(f"{path.name}: no usable lines")
        lists[key] = values
    return Library(config, lists), warnings


# --- cache ------------------------------------------------------------------


class LibraryCache:
    """Serves the library, reloading when a list or ``characters.yml`` changes.

    Each ``get()`` stats about a dozen files (name, mtime_ns, size) and
    reloads only when that signature changed -- a new or removed file counts.
    ``get()`` never raises: if a reload fails it keeps serving the last good
    library and adds a warning.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)
        self._lock = threading.Lock()
        self._signature: Optional[Tuple[Any, ...]] = None
        self._library = Library(default_library_config(), {})
        self._warnings: List[str] = []

    def _signature_now(self) -> Tuple[Any, ...]:
        if not self._directory.is_dir():
            return ("missing",)
        entries = [self._directory / CONFIG_FILENAME]
        entries.extend(sorted(self._directory.glob("*.txt")))
        signature: List[Tuple[str, int, int]] = []
        for path in entries:
            try:
                stat = path.stat()
            except OSError:
                continue  # e.g. no characters.yml
            signature.append((path.name, stat.st_mtime_ns, stat.st_size))
        return tuple(signature)

    def get(self) -> Tuple[Library, List[str]]:
        with self._lock:
            try:
                signature = self._signature_now()
                if signature != self._signature:
                    self._library, self._warnings = load_library(self._directory)
                    self._signature = signature
            except Exception as exc:  # never raise into a request or a batch
                logger.exception("t2i list reload failed; keeping the previous lists")
                return self._library, self._warnings + [
                    f"could not reload the caption lists: {exc}"
                ]
            return self._library, list(self._warnings)
