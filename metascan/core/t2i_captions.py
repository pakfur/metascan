"""Caption index for the t2i Random mode (spec section 4).

``CaptionStore`` indexes a large caption CSV once and answers filter, count
and pick queries from compact numpy arrays. Caption text is never held in
memory: the index keeps each record's byte range and ``get`` reads the record
back on demand.

* **Records are quote-balanced, not physical lines.** The file is fed to the
  ``csv`` module one line at a time while the byte position is tracked, so a
  caption containing a quoted newline (LF or CRLF) is still one record, a
  stray quote inside an unquoted field stays literal, and blank lines between
  or after records are skipped. A UTF-8 BOM and CRLF endings are handled.
* **Columns** are matched by name, ignoring case and spacing. ``Caption`` and
  ``Aspect Ratio`` are required; each optional column (``Nudity``, three
  scores, ``Males``, ``Females``, ``Clothing``) enables its filter and shows
  up in ``meta()`` only when present. Clothing is a Python-literal list
  string, parsed with ``ast.literal_eval`` and treated as empty on failure.
* **Reload** is by ``(mtime_ns, size)``, checked on every call.
* A missing or unusable file does not raise from ``available``, ``error``,
  ``total``, ``meta`` or ``count``: the store reports ``available() == False``
  and a message from ``error()``. ``get`` then raises ``KeyError`` and
  ``picker`` raises ``CaptionFilterError`` (as does ``CaptionPicker.next`` if
  the file disappears mid-run).
"""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import logging
import math
import random
import threading
import time
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    BinaryIO,
    Callable,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Tuple,
    TypeVar,
)

import numpy as np
import numpy.typing as npt

logger = logging.getLogger(__name__)

RANGE_STEP = 0.05
_MISSING_COUNT = 255  # marker for a Males/Females cell that is blank or unusable
_MAX_COUNT = 254
_CLOTHING_CACHE_MAX = 4096
_NUDITY_ORDER = ("none", "partial", "full")

_SCORE_COLUMNS: Dict[str, Tuple[str, str]] = {
    "artistic_quality": ("artistic quality", "Artistic Quality"),
    "erotic_score": ("erotic score", "Erotic Score"),
    "pornographic_score": ("pornographic score", "Pornographic Score"),
}
_COUNT_COLUMNS: Dict[str, Tuple[str, str]] = {
    "males": ("males", "Males"),
    "females": ("females", "Females"),
}
_FILTER_KEYS = (
    "nudity",
    "artistic_quality",
    "erotic_score",
    "pornographic_score",
    "males",
    "females",
    "aspect_ratios",
    "clothing_any",
    "clothing_none",
)

BoolArray = npt.NDArray[np.bool_]
_T = TypeVar("_T", bound=np.generic)


class CaptionFilterError(ValueError):
    """A filter names an unknown key, has a bad value, or matches nothing."""


class _CaptionFileError(Exception):
    """The caption file cannot be used; the message is shown to the user."""


@dataclass(frozen=True)
class Classification:
    """A row's caption-classifier result (merged in by
    ``scripts/caption_classifier/merge.py``); drives t2i directions."""

    emotion: str
    emotion_explicit: float
    kiss: float
    partner: str
    act: str
    act_p: float
    act_conflict: bool
    issues: Tuple[str, ...]


@dataclass(frozen=True)
class CaptionRow:
    id: int
    caption: str
    aspect_ratio: str
    nudity: Optional[str]
    artistic_quality: Optional[float]
    erotic_score: Optional[float]
    pornographic_score: Optional[float]
    males: Optional[int]
    females: Optional[int]
    clothing: Tuple[str, ...]
    classification: Optional[Classification] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "caption": self.caption,
            "aspect_ratio": self.aspect_ratio,
            "nudity": self.nudity,
            "artistic_quality": self.artistic_quality,
            "erotic_score": self.erotic_score,
            "pornographic_score": self.pornographic_score,
            "males": self.males,
            "females": self.females,
            "clothing": list(self.clothing),
        }


# --- Cell parsing -----------------------------------------------------------


def _cell(row: List[str], position: Optional[int]) -> str:
    if position is None or position >= len(row):
        return ""
    return row[position]


def _norm_header(name: str) -> str:
    return " ".join(name.replace("\ufeff", "").split()).lower()


def _parse_score(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        return math.nan
    return value if math.isfinite(value) else math.nan


def _parse_count(text: str) -> int:
    """A whole number 0..254, or the missing marker."""
    try:
        value = float(text.strip())
    except ValueError:
        return _MISSING_COUNT
    if not math.isfinite(value) or value != int(value):
        return _MISSING_COUNT
    return int(value) if 0 <= value <= _MAX_COUNT else _MISSING_COUNT


def _parse_clothing(text: str) -> Tuple[str, ...]:
    text = text.strip()
    if not text or text == "[]":
        return ()
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return ()
    if isinstance(value, (set, frozenset)):
        value = sorted(item for item in value if isinstance(item, str))
    if not isinstance(value, (list, tuple)):
        return ()
    items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return tuple(dict.fromkeys(items))


def _as_array(values: "array[Any]", dtype: "type[_T]") -> "npt.NDArray[_T]":
    if not len(values):
        return np.zeros(0, dtype=dtype)
    return np.frombuffer(values, dtype=dtype).copy()


def _caption_key(caption: str) -> bytes:
    """Hash-map key for ``CaptionStore.find``: SHA-1 prefix of the exact text."""
    return hashlib.sha1(caption.encode("utf-8", "surrogatepass")).digest()[:8]


def _sha1_hex(caption: str) -> str:
    return hashlib.sha1(caption.encode("utf-8", "surrogatepass")).hexdigest()


# --- The index --------------------------------------------------------------


@dataclass
class _Index:
    path: Path
    signature: Tuple[int, int]
    columns: Dict[str, int]  # normalised header -> position
    starts: "npt.NDArray[np.int64]"  # byte range of each record
    ends: "npt.NDArray[np.int64]"
    aspect_values: List[str]
    aspect_codes: "npt.NDArray[np.uint16]"
    nudity_values: Optional[List[str]]
    nudity_codes: Optional["npt.NDArray[np.int8]"]  # -1 = blank
    scores: Dict[str, "npt.NDArray[np.float32]"]  # NaN = blank or unusable
    counts: Dict[str, "npt.NDArray[np.uint8]"]  # 255 = blank or unusable
    clothing: Optional[Dict[str, "npt.NDArray[np.intc]"]]  # item -> row ids
    by_hash: Dict[bytes, int]  # _caption_key(caption) -> first row id

    @property
    def total(self) -> int:
        return int(self.starts.shape[0])


class _Accumulator:
    """Collects one compact array per column while the file is scanned."""

    def __init__(self, columns: Dict[str, int]) -> None:
        self.columns = columns
        self.caption_at = columns["caption"]
        self.aspect_at = columns["aspect ratio"]
        self.nudity_at = columns.get("nudity")
        self.clothing_at = columns.get("clothing")
        self.starts: "array[int]" = array("q")
        self.ends: "array[int]" = array("q")
        self.aspect_codes: "array[int]" = array("H")
        self.aspect_index: Dict[str, int] = {}
        self.nudity_codes: "array[int]" = array("b")
        self.nudity_index: Dict[str, int] = {}
        self.scores: Dict[str, "array[float]"] = {
            key: array("f")
            for key, (name, _) in _SCORE_COLUMNS.items()
            if name in columns
        }
        self.counts: Dict[str, "array[int]"] = {
            key: array("B")
            for key, (name, _) in _COUNT_COLUMNS.items()
            if name in columns
        }
        self.clothing_rows: Dict[str, "array[int]"] = {}
        self._clothing_cache: Dict[str, Tuple[str, ...]] = {}
        self.by_hash: Dict[bytes, int] = {}
        self.total = 0

    def add(self, row: List[str], start: int, end: int) -> None:
        if not _cell(row, self.caption_at).strip():
            return  # nothing to render from a blank caption
        row_id = self.total
        self.total += 1
        self.by_hash.setdefault(_caption_key(_cell(row, self.caption_at)), row_id)
        self.starts.append(start)
        self.ends.append(end)
        self.aspect_codes.append(
            self._code(
                self.aspect_index,
                _cell(row, self.aspect_at).strip(),
                65535,
                "aspect ratios",
            )
        )
        if self.nudity_at is not None:
            value = _cell(row, self.nudity_at).strip().lower()
            self.nudity_codes.append(
                self._code(self.nudity_index, value, 126, "Nudity values")
                if value
                else -1
            )
        for key, values in self.scores.items():
            values.append(
                _parse_score(_cell(row, self.columns[_SCORE_COLUMNS[key][0]]))
            )
        for key, counts in self.counts.items():
            counts.append(
                _parse_count(_cell(row, self.columns[_COUNT_COLUMNS[key][0]]))
            )
        if self.clothing_at is not None:
            for item in self._clothing(_cell(row, self.clothing_at)):
                self.clothing_rows.setdefault(item, array("i")).append(row_id)

    @staticmethod
    def _code(table: Dict[str, int], value: str, limit: int, what: str) -> int:
        code = table.get(value)
        if code is None:
            code = len(table)
            if code > limit:
                raise _CaptionFileError(f"caption file has too many distinct {what}")
            table[value] = code
        return code

    def _clothing(self, text: str) -> Tuple[str, ...]:
        cached = self._clothing_cache.get(text)
        if cached is None:
            cached = _parse_clothing(text)
            if len(self._clothing_cache) < _CLOTHING_CACHE_MAX:
                self._clothing_cache[text] = cached
        return cached

    def finish(self, path: Path, signature: Tuple[int, int]) -> _Index:
        nudity_values = None
        if self.nudity_at is not None:
            nudity_values = sorted(self.nudity_index, key=self.nudity_index.__getitem__)
        return _Index(
            path=path,
            signature=signature,
            columns=self.columns,
            starts=_as_array(self.starts, np.int64),
            ends=_as_array(self.ends, np.int64),
            aspect_values=sorted(self.aspect_index, key=self.aspect_index.__getitem__),
            aspect_codes=_as_array(self.aspect_codes, np.uint16),
            nudity_values=nudity_values,
            nudity_codes=(
                _as_array(self.nudity_codes, np.int8)
                if self.nudity_at is not None
                else None
            ),
            scores={
                key: _as_array(values, np.float32)
                for key, values in self.scores.items()
            },
            counts={
                key: _as_array(values, np.uint8) for key, values in self.counts.items()
            },
            clothing=(
                {
                    item: _as_array(rows, np.intc)
                    for item, rows in self.clothing_rows.items()
                }
                if self.clothing_at is not None
                else None
            ),
            by_hash=self.by_hash,
        )


def _header_columns(row: List[str]) -> Dict[str, int]:
    columns: Dict[str, int] = {}
    for position, name in enumerate(row):
        columns.setdefault(_norm_header(name), position)
    missing = [
        label
        for key, label in (("caption", "Caption"), ("aspect ratio", "Aspect Ratio"))
        if key not in columns
    ]
    if missing:
        raise _CaptionFileError(
            "caption file is missing required column(s): " + ", ".join(missing)
        )
    return columns


def _scan_records(handle: BinaryIO) -> _Accumulator:
    """Feed the file to ``csv`` line by line, tracking each record's byte range."""
    position = 0

    def lines() -> Iterator[str]:
        nonlocal position
        for raw in handle:  # splits on b"\n" only; CRLF and lone CR stay put
            position += len(raw)
            yield raw.decode("utf-8", errors="replace")

    reader = csv.reader(lines())
    accumulator: Optional[_Accumulator] = None
    while True:
        start = position
        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            raise _CaptionFileError(
                f"caption file is not valid CSV near line {reader.line_num}: {exc}"
            ) from exc
        if not row:
            continue  # a blank line
        if accumulator is None:
            accumulator = _Accumulator(_header_columns(row))
            continue
        accumulator.add(row, start, position)
    if accumulator is None:
        raise _CaptionFileError("caption file is empty")
    return accumulator


def _build_index(path: Path, signature: Tuple[int, int]) -> _Index:
    with path.open("rb") as handle:
        accumulator = _scan_records(handle)
    if accumulator.total == 0:
        raise _CaptionFileError("caption file has no captions")
    return accumulator.finish(path, signature)


# --- Reading one record -----------------------------------------------------


def _classification(
    cell: Callable[[str], str], caption: str
) -> Optional[Classification]:
    """The row's classification, or None when the cells are blank, unusable,
    or were computed for a different caption text."""
    act = cell("act").strip()
    if not act:
        return None
    digest = cell("caption sha1").strip().lower()
    if digest and digest != _sha1_hex(caption):
        return None
    numbers = [
        _parse_score(cell(name)) for name in ("emotion explicit", "kiss", "act p")
    ]
    if any(math.isnan(n) for n in numbers):
        return None
    emotion_explicit, kiss, act_p = numbers
    issues = tuple(t for t in (p.strip() for p in cell("issues").split(";")) if t)
    return Classification(
        emotion=cell("emotion").strip().lower(),
        emotion_explicit=emotion_explicit,
        kiss=kiss,
        partner=cell("partner").strip().lower() or "none",
        act=act,
        act_p=act_p,
        act_conflict=cell("act conflict").strip().lower() == "true",
        issues=issues,
    )


def _read_row(index: _Index, row_id: int) -> CaptionRow:
    start = int(index.starts[row_id])
    end = int(index.ends[row_id])
    try:
        with index.path.open("rb") as handle:
            handle.seek(start)
            raw = handle.read(end - start)
        text = raw.decode("utf-8", errors="replace")
        fields = next(csv.reader(io.StringIO(text, newline="\n")))
    except (OSError, StopIteration, csv.Error) as exc:
        raise KeyError(row_id) from exc

    def cell(name: str) -> str:
        return _cell(fields, index.columns.get(name))

    def score(key: str) -> Optional[float]:
        if key not in index.scores:
            return None
        value = _parse_score(cell(_SCORE_COLUMNS[key][0]))
        return None if math.isnan(value) else value

    def count(key: str) -> Optional[int]:
        if key not in index.counts:
            return None
        value = _parse_count(cell(_COUNT_COLUMNS[key][0]))
        return None if value == _MISSING_COUNT else value

    caption = cell("caption")
    return CaptionRow(
        id=row_id,
        caption=caption,
        aspect_ratio=cell("aspect ratio").strip(),
        nudity=(cell("nudity").strip().lower() or None),
        artistic_quality=score("artistic_quality"),
        erotic_score=score("erotic_score"),
        pornographic_score=score("pornographic_score"),
        males=count("males"),
        females=count("females"),
        clothing=_parse_clothing(cell("clothing")),
        classification=_classification(cell, caption),
    )


# --- Filters ----------------------------------------------------------------


def _normalise_filter(flt: Any) -> Dict[str, Any]:
    if flt is None:
        return {}
    if not isinstance(flt, Mapping):
        raise CaptionFilterError("the filter must be an object")
    for key in flt:
        if key not in _FILTER_KEYS:
            raise CaptionFilterError(f"unknown filter key '{key}'")
    return {key: value for key, value in flt.items() if value is not None}


def _string_list(key: str, value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(v, str) for v in value
    ):
        raise CaptionFilterError(f"filter '{key}' must be a list of strings")
    return list(value)


def _bound(key: str, field: str, value: Any, whole: bool) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CaptionFilterError(f"filter '{key}.{field}' must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise CaptionFilterError(f"filter '{key}.{field}' must be a finite number")
    if whole and number != int(number):
        raise CaptionFilterError(f"filter '{key}.{field}' must be a whole number")
    return number


def _bounds(
    key: str, spec: Any, whole: bool
) -> Tuple[Optional[float], Optional[float]]:
    if not isinstance(spec, Mapping):
        raise CaptionFilterError(
            f"filter '{key}' must be an object with min and/or max"
        )
    for field in spec:
        if field not in ("min", "max"):
            raise CaptionFilterError(f"filter '{key}' has unknown field '{field}'")
    low = _bound(key, "min", spec.get("min"), whole)
    high = _bound(key, "max", spec.get("max"), whole)
    if low is not None and high is not None and low > high:
        raise CaptionFilterError(f"filter '{key}': min is greater than max")
    return low, high


def _needs_column(key: str, label: str) -> CaptionFilterError:
    return CaptionFilterError(
        f"filter '{key}' is not available: the caption file has no '{label}' column"
    )


def _apply_choice(
    mask: BoolArray,
    codes: "npt.NDArray[Any]",
    values: List[str],
    wanted: List[str],
    fold: bool,
) -> None:
    lookup = {value: code for code, value in enumerate(values)}
    hits = [
        lookup[w.strip().lower() if fold else w.strip()]
        for w in wanted
        if (w.strip().lower() if fold else w.strip()) in lookup
    ]
    mask &= np.isin(codes, np.array(hits, dtype=np.int64))


def _apply_scores(mask: BoolArray, index: _Index, flt: Mapping[str, Any]) -> None:
    for key, (_, label) in _SCORE_COLUMNS.items():
        if key not in flt:
            continue
        if key not in index.scores:
            raise _needs_column(key, label)
        low, high = _bounds(key, flt[key], whole=False)
        values = index.scores[key]
        with np.errstate(invalid="ignore"):
            if low is not None:
                mask &= values >= np.float32(max(min(low, 3e38), -3e38))
            if high is not None:
                mask &= values <= np.float32(max(min(high, 3e38), -3e38))


def _apply_counts(mask: BoolArray, index: _Index, flt: Mapping[str, Any]) -> None:
    for key, (_, label) in _COUNT_COLUMNS.items():
        if key not in flt:
            continue
        if key not in index.counts:
            raise _needs_column(key, label)
        low, high = _bounds(key, flt[key], whole=True)
        values = index.counts[key].astype(np.int16)
        mask &= values != _MISSING_COUNT
        if low is not None:
            mask &= values >= int(max(min(low, 1000), -1))
        if high is not None:
            mask &= values <= int(max(min(high, 1000), -1))


def _apply_clothing(mask: BoolArray, index: _Index, flt: Mapping[str, Any]) -> None:
    for key in ("clothing_any", "clothing_none"):
        if key not in flt:
            continue
        if index.clothing is None:
            raise _needs_column(key, "Clothing")
        wanted = _string_list(key, flt[key])
        if not wanted:
            continue
        if key == "clothing_any":
            hit = np.zeros(index.total, dtype=np.bool_)
            for item in wanted:
                rows = index.clothing.get(item)
                if rows is not None:
                    hit[rows] = True
            mask &= hit
        else:
            for item in wanted:
                rows = index.clothing.get(item)
                if rows is not None:
                    mask[rows] = False


def _match_mask(index: _Index, flt: Any) -> BoolArray:
    """A boolean mask of the rows a filter keeps. Raises CaptionFilterError."""
    spec = _normalise_filter(flt)
    mask: BoolArray = np.ones(index.total, dtype=np.bool_)
    if "nudity" in spec:
        if index.nudity_codes is None or index.nudity_values is None:
            raise _needs_column("nudity", "Nudity")
        wanted = _string_list("nudity", spec["nudity"])
        if wanted:
            _apply_choice(
                mask, index.nudity_codes, index.nudity_values, wanted, fold=True
            )
    if "aspect_ratios" in spec:
        wanted = _string_list("aspect_ratios", spec["aspect_ratios"])
        if wanted:
            _apply_choice(
                mask, index.aspect_codes, index.aspect_values, wanted, fold=False
            )
    _apply_scores(mask, index, spec)
    _apply_counts(mask, index, spec)
    _apply_clothing(mask, index, spec)
    return mask


# --- meta() -----------------------------------------------------------------


def _options(pairs: List[Tuple[str, int]]) -> List[Dict[str, Any]]:
    ordered = sorted(pairs, key=lambda pair: (-pair[1], pair[0]))
    return [{"value": value, "count": count} for value, count in ordered]


def _code_counts(codes: "npt.NDArray[Any]", values: List[str]) -> List[Tuple[str, int]]:
    seen = codes[codes >= 0].astype(np.intp)
    tally = np.bincount(seen, minlength=len(values))
    return [(value, int(tally[code])) for code, value in enumerate(values) if value]


def _nudity_options(pairs: List[Tuple[str, int]]) -> List[Dict[str, Any]]:
    known = [p for name in _NUDITY_ORDER for p in pairs if p[0] == name]
    rest = [p for p in pairs if p[0] not in _NUDITY_ORDER]
    return [{"value": v, "count": c} for v, c in known] + _options(rest)


def _range_meta(
    key: str, label: str, values: "npt.NDArray[np.float32]"
) -> Dict[str, Any]:
    finite = values[~np.isnan(values)]
    low, high = (0.0, 1.0)
    if finite.size:
        low, high = round(float(finite.min()), 4), round(float(finite.max()), 4)
    return {
        "key": key,
        "label": label,
        "type": "range",
        "min": low,
        "max": high,
        "step": RANGE_STEP,
    }


def _int_range_meta(
    key: str, label: str, values: "npt.NDArray[np.uint8]"
) -> Dict[str, Any]:
    present = values[values != _MISSING_COUNT]
    low, high = (int(present.min()), int(present.max())) if present.size else (0, 0)
    return {"key": key, "label": label, "type": "int_range", "min": low, "max": high}


def _meta_columns(index: _Index) -> List[Dict[str, Any]]:
    columns: List[Dict[str, Any]] = []
    if index.nudity_codes is not None and index.nudity_values is not None:
        pairs = _code_counts(index.nudity_codes, index.nudity_values)
        columns.append(
            {
                "key": "nudity",
                "label": "Nudity",
                "type": "choice",
                "options": _nudity_options(pairs),
            }
        )
    for key, (_, label) in _SCORE_COLUMNS.items():
        if key in index.scores:
            columns.append(_range_meta(key, label, index.scores[key]))
    for key, (_, label) in _COUNT_COLUMNS.items():
        if key in index.counts:
            columns.append(_int_range_meta(key, label, index.counts[key]))
    aspects = _code_counts(index.aspect_codes, index.aspect_values)
    columns.append(
        {
            "key": "aspect_ratios",
            "label": "Aspect Ratio",
            "type": "choice",
            "options": _options(aspects),
        }
    )
    if index.clothing is not None:
        tags = [(item, int(rows.shape[0])) for item, rows in index.clothing.items()]
        columns.append(
            {
                "key": "clothing",
                "label": "Clothing",
                "type": "tags",
                "options": _options(tags),
            }
        )
    return columns


# --- Public classes ---------------------------------------------------------


class CaptionStore:
    """Lazy, reloading index over the caption CSV. Thread-safe."""

    def __init__(self, csv_path: Path) -> None:
        self._path = Path(csv_path)
        self._lock = threading.Lock()
        self._index: Optional[_Index] = None
        self._error: Optional[str] = None
        self._signature: Optional[Tuple[int, int]] = None

    def _current(self) -> Optional[_Index]:
        """The up-to-date index, (re)building it when the file changed."""
        with self._lock:
            try:
                stat = self._path.stat()
            except OSError:
                self._index, self._signature = None, None
                self._error = f"caption file not found: {self._path}"
                return None
            signature = (stat.st_mtime_ns, stat.st_size)
            if signature != self._signature:
                self._rebuild(signature)
            return self._index

    def _rebuild(self, signature: Tuple[int, int]) -> None:
        started = time.perf_counter()
        try:
            self._index = _build_index(self._path, signature)
        except _CaptionFileError as exc:
            self._index, self._error, self._signature = None, str(exc), signature
            logger.warning("t2i captions unavailable: %s", exc)
        except OSError as exc:
            # Possibly transient: forget the signature so the next call retries.
            self._index, self._error, self._signature = (
                None,
                f"caption file cannot be read: {exc}",
                None,
            )
            logger.warning("t2i captions unreadable: %s", exc)
        else:
            self._error, self._signature = None, signature
            logger.info(
                "t2i captions: indexed %d rows from %s in %.2fs",
                self._index.total,
                self._path.name,
                time.perf_counter() - started,
            )

    def available(self) -> bool:
        return self._current() is not None

    def error(self) -> Optional[str]:
        self._current()
        return self._error

    def total(self) -> int:
        index = self._current()
        return index.total if index is not None else 0

    def meta(self) -> Dict[str, Any]:
        """Filter columns, option lists with counts, and numeric ranges."""
        index = self._current()
        if index is None:
            return {"total": 0, "columns": []}
        return {"total": index.total, "columns": _meta_columns(index)}

    def count(self, flt: Optional[Mapping[str, Any]]) -> int:
        """Rows matching ``flt`` (0 when the file is unavailable)."""
        index = self._current()
        if index is None:
            return 0
        return int(np.count_nonzero(_match_mask(index, flt)))

    def get(self, row_id: int) -> CaptionRow:
        """One row, read from disk on demand. ``KeyError`` if there is none."""
        index = self._current()
        if index is None or not 0 <= row_id < index.total:
            raise KeyError(row_id)
        return _read_row(index, int(row_id))

    def find(self, caption: str) -> Optional[CaptionRow]:
        """The first row whose caption is exactly ``caption``, or None."""
        index = self._current()
        if index is None:
            return None
        row_id = index.by_hash.get(_caption_key(caption))
        if row_id is None:
            return None
        try:
            row = _read_row(index, row_id)
        except KeyError:
            return None
        return row if row.caption == caption else None

    def picker(
        self, flt: Optional[Mapping[str, Any]], rng: random.Random
    ) -> "CaptionPicker":
        index = self._current()
        if index is None:
            raise CaptionFilterError(self._error or "the caption file is unavailable")
        return CaptionPicker(self, index, flt, rng)


class CaptionPicker:
    """Hands out matching rows in random order without repeats.

    When every match has been used the pool is reshuffled (never repeating
    the last row first). If the caption file changes mid-run the filter is
    re-applied to the new file and a fresh cycle starts.
    """

    pool_size: int

    def __init__(
        self,
        store: CaptionStore,
        index: _Index,
        flt: Optional[Mapping[str, Any]],
        rng: random.Random,
    ) -> None:
        self._store = store
        self._flt = _normalise_filter(flt)
        self._rng = rng
        self._index = index
        self._order: List[int] = []
        self._pos = 0
        self._last: Optional[int] = None
        self.pool_size = 0
        self._fill(index)

    def _fill(self, index: _Index) -> None:
        ids = np.flatnonzero(_match_mask(index, self._flt))
        if ids.size == 0:
            raise CaptionFilterError("no captions match the filter")
        self._index = index
        self._order = [int(i) for i in ids]
        self.pool_size = len(self._order)
        self._rng.shuffle(self._order)
        self._pos = 0
        self._last = None

    def _reshuffle(self) -> None:
        self._rng.shuffle(self._order)
        if len(self._order) > 1 and self._order[0] == self._last:
            other = self._rng.randrange(1, len(self._order))
            self._order[0], self._order[other] = self._order[other], self._order[0]
        self._pos = 0

    def next(self) -> CaptionRow:
        index = self._store._current()
        if index is None:
            raise CaptionFilterError(
                self._store._error or "the caption file is unavailable"
            )
        if index is not self._index:
            self._fill(index)
        elif self._pos >= len(self._order):
            self._reshuffle()
        row_id = self._order[self._pos]
        self._pos += 1
        self._last = row_id
        return _read_row(index, row_id)
