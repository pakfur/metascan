"""Move a library's own data to another install: the bundle behind the
Configuration window's Import / Export tab.

A bundle is what a media import can NOT rebuild, read by path: ★ favorites,
playback speeds, grades and reasons, categories, hindsight briefs, the judgment
event log, saved prompts, folders, the per-image T2I / I2V rows (settings and
lineage), the workflow presets they name, and the tombstones of deleted images
(their CLIP vectors and archived thumbnails too: no file is left to compute
them from). The media files are never in it, nor is anything an import or a
later background pass makes (metadata, tags, embeddings, thumbnails, taste
scores); ``NOT_TRANSFERRED_TABLES`` says what stays behind, and a test fails on
a table on neither list.

There is no content hash, so **a file is identified by its path below a library
root**: the bundle keeps the paths as stored and lists the roots they were
under, and an import translates each root to the one that holds the same tree
here (:class:`PathMap`). A file whose relative path changed is not found.

An import is safe to repeat and never overwrites newer data here: a live grade,
★, category link, brief or lineage row stays. Only rows for files in the
library are applied (tombstones and events have no file to wait for): the rest
are counted as not found, and importing again once the files arrive adds them.
Timestamps are kept verbatim, so decay clocks do not restart, and no
judgment_events are logged. It is one transaction: a failure changes nothing.

A trained adapter's weights are not moved. Its row comes along as ``deleted``,
under a new id, only so old generations still name it, and ``adapter:<id>``
writer ids are rewritten to match.

Training text (prompts, briefs) is never logged: counts only.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import sqlite3
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from metascan.core.judgments import GRADES
from metascan.core.writer_adapters import (
    WRITER_ADAPTER_PREFIX,
    parse_adapter_writer_id,
)

if TYPE_CHECKING:
    from metascan.core.database_sqlite import DatabaseManager

logger = logging.getLogger(__name__)

BUNDLE_FORMAT = "metascan-library-data"
BUNDLE_VERSION = 1
MANIFEST_NAME = "manifest.json"
DATA_NAME = "data.json"
THUMBS_PREFIX = "thumbs/"

# Past these a file is not a bundle this app wrote.
MAX_DATA_BYTES = 8 * 1024**3
MAX_THUMB_BYTES = 16 * 1024**2

_THUMB_PATH_RX = re.compile(r"archive/thumbs/([0-9a-f]{32})\.jpg")

# Tables a bundle carries (``media`` only for ★ and the playback speed).
TRANSFERRED_TABLES = frozenset(
    {
        "categories",
        "folder_items",
        "folders",
        "hindsight_briefs",
        "i2v_videos",
        "judgment_events",
        "media",
        "media_categories",
        "media_judgments",
        "media_tombstones",
        "saved_prompts",
        "t2i_images",
        "tombstone_vectors",
        "workflow_presets",
        "writer_adapters",
    }
)

# Tables that stay behind, each for a reason. Rebuilt by an import or a
# background pass: indices, media_hashes, the tagging queue tables, the FAISS
# renames, and the taste model (it retrains from the grades). Scorecards and
# training runs are about adapter weights, which are not moved; storyboards and
# their generation jobs are projects, not per-image data.
NOT_TRANSFERRED_TABLES = frozenset(
    {
        "beat_images",
        "beats",
        "faiss_pending_renames",
        "generation_jobs",
        "indices",
        "media_hashes",
        "panel_videos",
        "panels",
        "scenes",
        "scorecard_batches",
        "scorecard_jobs",
        "scorecards",
        "sqlite_sequence",
        "storyboard_subjects",
        "storyboards",
        "taste_diversity",
        "taste_models",
        "taste_scores",
        "training_runs",
        "vlm_tag_exhausted",
        "vlm_tag_queue",
    }
)

_TOMBSTONE_COLUMNS = (
    "file_path",
    "deleted_at",
    "is_favorite",
    "grade",
    "reasons",
    "categories",
    "seen_at",
    "judged_at",
    "confirmed_at",
    "lineage",
    "brief",
    "brief_model",
    "prompt",
    "model_name",
    "thumb_path",
    "scorecard_id",
)

_PRESET_COLUMNS = (
    "name",
    "kind",
    "workflow_json",
    "bindings",
    "video_target",
    "video_mode",
    "created_at",
    "updated_at",
)

_ADAPTER_COLUMNS = (
    "name",
    "generated_name",
    "scope",
    "kind",
    "base_model",
    "created_at",
    "data_cutoff",
    "hyperparams",
    "counts",
    "holdout_agreement",
    "base_agreement",
)

# The fields each list must carry: "s" text, "s?" text or null, "n" a number,
# "n?" a number or null, "i?" a whole number or null. Rows copied column by
# column (t2i_images, i2v_videos, saved_prompts) may carry more; their other
# values are checked to be scalars and the names against the live table.
_ROW_FIELDS: Dict[str, Dict[str, str]] = {
    "playback_speeds": {"file_path": "s", "playback_speed": "n"},
    "judgments": {
        "file_path": "s",
        "grade": "s?",
        "reasons": "s",
        "seen_at": "s?",
        "judged_at": "s?",
        "confirmed_at": "s?",
    },
    "categories": {"name": "s", "created_at": "s"},
    "media_categories": {
        "file_path": "s",
        "category": "s",
        "source": "s",
        "assigned_at": "s",
    },
    "hindsight_briefs": {
        "file_path": "s",
        "brief": "s",
        "model": "s",
        "created_at": "s",
    },
    "judgment_events": {
        "file_path": "s",
        "event": "s",
        "reason": "s?",
        "category": "s?",
        "score_shown": "n?",
        "created_at": "s",
    },
    "saved_prompts": {
        "file_path": "s",
        "name": "s",
        "prompt": "s",
        "target_model": "s",
        "architecture": "s",
        "mode": "s",
        "created_at": "s",
    },
    "folders": {
        "id": "s",
        "kind": "s",
        "name": "s",
        "icon": "s",
        "rules": "s?",
        "sort_order": "n",
        "created_at": "n",
        "updated_at": "n",
    },
    "folder_items": {"folder_id": "s", "file_path": "s", "added_at": "n"},
    "t2i_images": {"file_path": "s", "preset_id": "i?", "writer_id": "s?"},
    "i2v_videos": {
        "file_path": "s",
        "source_path": "s",
        "preset_id": "i?",
        "writer_id": "s?",
    },
    "workflow_presets": {
        "id": "n",
        "name": "s",
        "kind": "s",
        "workflow_json": "s",
        "bindings": "s",
        "video_target": "s?",
        "video_mode": "s?",
        "created_at": "s",
        "updated_at": "s",
    },
    "writer_adapters": {
        "id": "n",
        "name": "s",
        "generated_name": "s?",
        "scope": "s",
        "kind": "s",
        "base_model": "s",
        "created_at": "s",
        "data_cutoff": "s?",
        "hyperparams": "s",
        "counts": "s",
        "holdout_agreement": "n?",
        "base_agreement": "n?",
    },
    "tombstones": {
        "file_path": "s",
        "deleted_at": "s",
        "is_favorite": "n",
        "grade": "s?",
        "reasons": "s",
        "categories": "s",
        "seen_at": "s?",
        "judged_at": "s?",
        "confirmed_at": "s?",
        "lineage": "s?",
        "brief": "s?",
        "brief_model": "s?",
        "prompt": "s?",
        "model_name": "s?",
        "thumb_path": "s?",
        "scorecard_id": "s?",
    },
}

# Lists whose rows may carry columns beyond ``_ROW_FIELDS``.
_COPIED_LISTS = ("t2i_images", "i2v_videos", "saved_prompts")

# What a report counts, in the order the UI lists them.
REPORT_KINDS = (
    "favorites",
    "playback_speeds",
    "judgments",
    "categories",
    "media_categories",
    "hindsight_briefs",
    "judgment_events",
    "saved_prompts",
    "folders",
    "folder_items",
    "t2i_images",
    "i2v_videos",
    "workflow_presets",
    "writer_adapters",
    "tombstones",
)


class BundleError(ValueError):
    """The file is not a usable bundle. The message is written for the owner
    and never quotes the file's contents."""


# ---- paths -----------------------------------------------------------------


def path_key(path: str) -> str:
    """How two spellings of one path compare across file systems: macOS
    stores NFD names and Windows ignores case."""
    return unicodedata.normalize("NFC", path).casefold()


class PathMap:
    """Translates stored paths from their source roots to this install's.

    ``mapping`` is ``{source root: target root or None}`` (POSIX, as stored).
    A path belongs to the deepest root above it; one under no root, or under
    a root with no target, is returned unchanged, which is right for a library
    that did not move."""

    def __init__(self, mapping: Mapping[str, Optional[str]]) -> None:
        self._roots: List[Tuple[Tuple[str, ...], str, Optional[str]]] = sorted(
            (
                (tuple(path_key(root).rstrip("/").split("/")), root, target)
                for root, target in mapping.items()
            ),
            key=lambda entry: -len(entry[0]),
        )

    def _find(self, path: str) -> Optional[Tuple[str, Optional[str], str]]:
        keys = path_key(path).split("/")
        for parts, root, target in self._roots:
            n = len(parts)
            if len(keys) > n and tuple(keys[:n]) == parts:
                return root, target, "/".join(path.split("/")[n:])
        return None

    def root_of(self, path: str) -> Optional[str]:
        found = self._find(path)
        return found[0] if found is not None else None

    def relative(self, path: str) -> Optional[str]:
        found = self._find(path)
        return found[2] if found is not None else None

    def translate(self, path: str) -> str:
        found = self._find(path)
        if found is None or found[1] is None:
            return path
        return f"{found[1].rstrip('/')}/{found[2]}"


class MediaResolver:
    """Finds a translated path among the library's rows: exactly, else by
    ``path_key`` when one row alone has that key (ambiguity is "not found")."""

    def __init__(self, paths: Iterable[str]) -> None:
        self._exact: Set[str] = set()
        self._by_key: Dict[str, Optional[str]] = {}
        for path in paths:
            self._exact.add(path)
            key = path_key(path)
            self._by_key[key] = None if key in self._by_key else path

    def resolve(self, path: str) -> Optional[str]:
        if path in self._exact:
            return path
        return self._by_key.get(path_key(path))


def media_paths(data: Mapping[str, Any]) -> Set[str]:
    """Every path in ``data`` that needs a media row to apply."""
    paths: Set[str] = set(data["favorites"])
    for key in (
        "playback_speeds",
        "judgments",
        "media_categories",
        "hindsight_briefs",
        "saved_prompts",
        "folder_items",
        "t2i_images",
        "i2v_videos",
    ):
        paths.update(row["file_path"] for row in data[key])
    return paths


def generated_paths(data: Mapping[str, Any]) -> Set[str]:
    """The T2I images and I2V clips in ``data``. They usually sit outside every
    scan folder, so a new install's import never finds them by itself."""
    return {
        row["file_path"] for key in ("t2i_images", "i2v_videos") for row in data[key]
    }


def suggest_targets(
    roots: Sequence[str],
    paths: Iterable[str],
    candidates: Sequence[str],
    library: MediaResolver,
    *,
    sample: int = 1000,
) -> Dict[str, Optional[str]]:
    """The likeliest target of each source root: the candidate under which
    the most of its files (a sample) exist in the library. A tie, none found
    included, goes to the same path, then to the same folder name; with nothing
    to go on a root has no suggestion."""
    finder = PathMap({root: None for root in roots})
    relatives: Dict[str, List[str]] = {root: [] for root in roots}
    for path in paths:
        root = finder.root_of(path)
        relative = finder.relative(path)
        if root is not None and relative is not None:
            relatives[root].append(relative)
    out: Dict[str, Optional[str]] = {}
    for root in roots:
        rels = relatives[root]
        step = max(1, len(rels) // sample)
        picked = rels[::step]
        found = {
            target: sum(
                library.resolve(f"{target.rstrip('/')}/{rel}") is not None
                for rel in picked
            )
            for target in candidates
        }
        best = max(found.values(), default=0)
        tied = [t for t in candidates if found[t] == best]
        name = path_key(root).rstrip("/").rsplit("/", 1)[-1]
        same_path = [t for t in tied if path_key(t) == path_key(root)]
        same_name = [
            t for t in tied if path_key(t).rstrip("/").rsplit("/", 1)[-1] == name
        ]
        if same_path:
            out[root] = same_path[0]
        elif same_name:
            out[root] = same_name[0]
        elif best > 0:
            out[root] = tied[0]
        else:
            out[root] = None
    return out


# ---- the bundle file ---------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bind_text(value: Any) -> bool:
    """Text SQLite can store: a JSON escape can read as a lone surrogate."""
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _field_ok(value: Any, kind: str) -> bool:
    if kind.endswith("?") and value is None:
        return True
    base = kind.rstrip("?")
    if base == "s":
        return _bind_text(value)
    if base == "i":
        return isinstance(value, int) and not isinstance(value, bool)
    return _is_number(value)


def validate_data(data: Any) -> Dict[str, Any]:
    """``data`` when every list has the shape an import relies on, else
    BundleError naming the list (never a value)."""
    if not isinstance(data, dict):
        raise BundleError("The bundle's data is not an object.")
    favorites = data.get("favorites")
    if not isinstance(favorites, list) or not all(_bind_text(p) for p in favorites):
        raise BundleError("The bundle's favorites are unreadable.")
    for name, fields in _ROW_FIELDS.items():
        rows = data.get(name)
        if not isinstance(rows, list):
            raise BundleError(f"The bundle has no readable {name} list.")
        for row in rows:
            if not isinstance(row, dict) or not all(
                key in row and _field_ok(row[key], kind) for key, kind in fields.items()
            ):
                raise BundleError(f"A row in the bundle's {name} is unreadable.")
    for name in _COPIED_LISTS:
        for row in data[name]:
            for value in row.values():
                if not (value is None or _bind_text(value) or _is_number(value)):
                    raise BundleError(f"A row in the bundle's {name} is unreadable.")
    for name in ("judgments", "tombstones"):
        for row in data[name]:
            if row["grade"] is not None and row["grade"] not in GRADES:
                raise BundleError(f"A grade in the bundle's {name} is unknown.")
    for row in data["tombstones"]:
        vectors = row.get("vectors")
        if not isinstance(vectors, list):
            raise BundleError("A tombstone in the bundle has no vector list.")
        for vector in vectors:
            if not isinstance(vector, dict) or not all(
                _bind_text(vector.get(key))
                for key in ("model_key", "embedding", "created_at")
            ):
                raise BundleError("A tombstone vector in the bundle is unreadable.")
            try:
                base64.b64decode(vector["embedding"], validate=True)
            except (binascii.Error, ValueError) as e:
                raise BundleError(
                    "A tombstone vector in the bundle is unreadable."
                ) from e
    return data


def write_bundle(
    target: Path, data: Mapping[str, Any], roots: Sequence[str], data_dir: Path
) -> Dict[str, Any]:
    """Write the bundle to ``target`` and return its manifest. A tombstone
    whose archived thumbnail is gone goes without one."""
    rows = dict(data)
    tombstones = [dict(row) for row in rows["tombstones"]]
    rows["tombstones"] = tombstones
    written: Set[str] = set()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as bundle:
        for row in tombstones:
            thumb = row.get("thumb_path")
            match = _THUMB_PATH_RX.fullmatch(thumb) if thumb else None
            source = data_dir / match.group(0) if match else None
            if match is None or source is None or not source.is_file():
                row["thumb_path"] = None
                continue
            if match.group(1) not in written:
                written.add(match.group(1))
                bundle.write(
                    source,
                    f"{THUMBS_PREFIX}{match.group(1)}.jpg",
                    compress_type=zipfile.ZIP_STORED,
                )
        manifest = {
            "format": BUNDLE_FORMAT,
            "version": BUNDLE_VERSION,
            "created_at": _utc_now(),
            "roots": list(roots),
            "counts": _counts(rows),
        }
        bundle.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
        bundle.writestr(DATA_NAME, json.dumps(rows))
    return manifest


def _counts(data: Mapping[str, Any]) -> Dict[str, int]:
    return {key: len(data[key]) for key in ("favorites", *_ROW_FIELDS) if key in data}


def read_bundle(source: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """``(manifest, data)`` of a bundle, validated, else BundleError."""
    try:
        with zipfile.ZipFile(source) as bundle:
            info = bundle.getinfo(DATA_NAME)
            if info.file_size > MAX_DATA_BYTES:
                raise BundleError("The bundle's data is too large.")
            manifest = json.loads(bundle.read(MANIFEST_NAME))
            if not isinstance(manifest, dict):
                raise BundleError("This is not a Metascan library data file.")
            _check_manifest(manifest)
            data = json.loads(bundle.read(DATA_NAME))
    except (
        zipfile.BadZipFile,
        KeyError,
        OSError,
        RuntimeError,
        NotImplementedError,
        ValueError,
        RecursionError,
    ) as e:
        if isinstance(e, BundleError):
            raise
        raise BundleError("This is not a Metascan library data file.") from e
    return manifest, validate_data(data)


def _check_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("format") != BUNDLE_FORMAT:
        raise BundleError("This is not a Metascan library data file.")
    if manifest.get("version") != BUNDLE_VERSION:
        raise BundleError(
            "This file was written by a different version of Metascan "
            "(update Metascan on both machines, then export again)."
        )
    roots = manifest.get("roots")
    if not isinstance(roots, list) or not all(_bind_text(r) for r in roots):
        raise BundleError("The bundle's library folders are unreadable.")


def extract_thumbnails(source: Path, data: Mapping[str, Any], data_dir: Path) -> int:
    """Copy the tombstones' archived thumbnails into ``data_dir``, written
    under a name built from the checked uuid (never the file's own name),
    leaving one already there. Runs before the database transaction: a stray
    file costs nothing, a tombstone naming no file would."""
    wanted = {
        match.group(1)
        for match in (
            _THUMB_PATH_RX.fullmatch(row["thumb_path"] or "")
            for row in data["tombstones"]
        )
        if match
    }
    copied = 0
    with zipfile.ZipFile(source) as bundle:
        names = {info.filename: info for info in bundle.infolist()}
        for stem in sorted(wanted):
            info = names.get(f"{THUMBS_PREFIX}{stem}.jpg")
            if info is None or info.file_size > MAX_THUMB_BYTES:
                continue
            target = data_dir / "archive" / "thumbs" / f"{stem}.jpg"
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(".partial")
            partial.write_bytes(bundle.read(info))
            os.replace(partial, target)
            copied += 1
    return copied


# ---- reading the database ----------------------------------------------------


def _rows(conn: sqlite3.Connection, sql: str) -> List[Dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql).fetchall()]


def _without_id(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{k: v for k, v in row.items() if k != "id"} for row in rows]


def _lineage(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """A tombstone's lineage, or None when it is not the expected object."""
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _lineage_rows(lineage: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [
        lineage[key] for key in ("t2i", "i2v") if isinstance(lineage.get(key), dict)
    ]


def export_library_data(db: "DatabaseManager") -> Dict[str, Any]:
    """Everything a bundle carries, as stored, read in one go under the
    database lock. Lineage rows whose media row is gone are left out: nothing
    could use them. Plain JSON-serialisable data."""
    with db.lock, db._get_connection() as conn:
        favorites = [
            r["file_path"]
            for r in conn.execute(
                "SELECT file_path FROM media WHERE is_favorite = 1 ORDER BY file_path"
            ).fetchall()
        ]
        data: Dict[str, Any] = {
            "favorites": favorites,
            "playback_speeds": _rows(
                conn,
                "SELECT file_path, playback_speed FROM media "
                "WHERE playback_speed IS NOT NULL ORDER BY file_path",
            ),
            "judgments": _rows(
                conn,
                "SELECT file_path, grade, reasons, seen_at, judged_at, confirmed_at "
                "FROM media_judgments ORDER BY file_path",
            ),
            "categories": _rows(
                conn, "SELECT name, created_at FROM categories ORDER BY id"
            ),
            "media_categories": _rows(
                conn,
                "SELECT mc.file_path, c.name AS category, mc.source, mc.assigned_at "
                "FROM media_categories mc JOIN categories c ON c.id = mc.category_id "
                "ORDER BY mc.file_path, c.id",
            ),
            "hindsight_briefs": _rows(
                conn,
                "SELECT file_path, brief, model, created_at FROM hindsight_briefs "
                "ORDER BY file_path",
            ),
            "judgment_events": _rows(
                conn,
                "SELECT file_path, event, reason, category, score_shown, created_at "
                "FROM judgment_events ORDER BY id",
            ),
            "saved_prompts": _without_id(
                _rows(conn, "SELECT * FROM saved_prompts ORDER BY id")
            ),
            "folders": _rows(
                conn,
                "SELECT id, kind, name, icon, rules, sort_order, created_at, "
                "updated_at FROM folders ORDER BY created_at, id",
            ),
            "folder_items": _rows(
                conn,
                "SELECT folder_id, file_path, added_at FROM folder_items "
                "ORDER BY folder_id, file_path",
            ),
            "t2i_images": _without_id(
                _rows(
                    conn,
                    "SELECT g.* FROM t2i_images g "
                    "JOIN media m ON m.file_path = g.file_path ORDER BY g.id",
                )
            ),
            "i2v_videos": _without_id(
                _rows(
                    conn,
                    "SELECT v.* FROM i2v_videos v "
                    "JOIN media m ON m.file_path = v.file_path ORDER BY v.id",
                )
            ),
            "workflow_presets": _rows(
                conn,
                f"SELECT id, {', '.join(_PRESET_COLUMNS)} FROM workflow_presets "
                "ORDER BY id",
            ),
        }
        tombstones = _rows(
            conn,
            f"SELECT id, {', '.join(_TOMBSTONE_COLUMNS)} FROM media_tombstones "
            "ORDER BY id",
        )
        vectors: Dict[int, List[Dict[str, Any]]] = {}
        for row in conn.execute(
            "SELECT tombstone_id, model_key, embedding, created_at "
            "FROM tombstone_vectors ORDER BY tombstone_id, model_key"
        ):
            vectors.setdefault(int(row["tombstone_id"]), []).append(
                {
                    "model_key": row["model_key"],
                    "embedding": base64.b64encode(row["embedding"]).decode("ascii"),
                    "created_at": row["created_at"],
                }
            )
        for tombstone in tombstones:
            tombstone["vectors"] = vectors.get(tombstone.pop("id"), [])
        data["tombstones"] = tombstones

        writer_ids = {
            row.get("writer_id") for row in data["t2i_images"] + data["i2v_videos"]
        }
        for tombstone in tombstones:
            lineage = _lineage(tombstone["lineage"])
            for row in _lineage_rows(lineage or {}):
                writer_ids.add(row.get("writer_id"))
        adapter_ids = {parse_adapter_writer_id(w) for w in writer_ids} - {None}
        data["writer_adapters"] = [
            row
            for row in _rows(
                conn,
                f"SELECT id, {', '.join(_ADAPTER_COLUMNS)} FROM writer_adapters "
                "ORDER BY id",
            )
            if row["id"] in adapter_ids
        ]
    return data


# ---- writing the database ----------------------------------------------------


class _Report:
    def __init__(self) -> None:
        self.counts: Dict[str, Dict[str, int]] = {
            kind: {"added": 0, "already": 0, "not_found": 0} for kind in REPORT_KINDS
        }

    def note(self, kind: str, changed: bool) -> None:
        self.counts[kind]["added" if changed else "already"] += 1

    def not_found(self, kind: str) -> None:
        self.counts[kind]["not_found"] += 1


def _columns(conn: sqlite3.Connection, table: str) -> Set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")} - {"id"}


def _insert_known(
    conn: sqlite3.Connection,
    table: str,
    columns: Set[str],
    row: Mapping[str, Any],
    conflict: str,
) -> bool:
    """Insert ``row``'s columns that exist here. Column names come from the
    file, so only the live table's own are ever put into the SQL."""
    names = sorted(key for key in row if key in columns)
    cursor = conn.execute(
        f"INSERT INTO {table} ({', '.join(names)}) "
        f"VALUES ({', '.join('?' * len(names))}) {conflict}",
        [row[name] for name in names],
    )
    return cursor.rowcount > 0


def _import_presets(
    conn: sqlite3.Connection, rows: Sequence[Mapping[str, Any]], report: _Report
) -> Dict[int, int]:
    """``{bundle id: id here}``, a preset of the same name being the same
    preset (the name is unique, and an existing one is never overwritten)."""
    ids: Dict[int, int] = {}
    for row in rows:
        found = conn.execute(
            "SELECT id FROM workflow_presets WHERE name = ?", (row["name"],)
        ).fetchone()
        if found is not None:
            ids[int(row["id"])] = int(found["id"])
            report.note("workflow_presets", False)
            continue
        cursor = conn.execute(
            f"INSERT INTO workflow_presets ({', '.join(_PRESET_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_PRESET_COLUMNS))})",
            [row[column] for column in _PRESET_COLUMNS],
        )
        ids[int(row["id"])] = int(cursor.lastrowid or 0)
        report.note("workflow_presets", True)
    return ids


def _free_adapter_name(conn: sqlite3.Connection, name: str) -> str:
    candidate = name
    n = 1
    while conn.execute(
        "SELECT 1 FROM writer_adapters WHERE name = ? COLLATE NOCASE", (candidate,)
    ).fetchone():
        candidate = f"{name} (imported)" if n == 1 else f"{name} (imported {n})"
        n += 1
    return candidate


def _import_adapters(
    conn: sqlite3.Connection,
    rows: Sequence[Mapping[str, Any]],
    now: str,
    report: _Report,
) -> Dict[int, int]:
    """``{bundle id: id here}``. Each comes in ``deleted``: its weights stay
    behind, and only a row's name is needed. Its created name and time say
    it is the same adapter on a second import."""
    ids: Dict[int, int] = {}
    for row in rows:
        generated = row["generated_name"] or row["name"]
        found = conn.execute(
            "SELECT id FROM writer_adapters WHERE kind = ? AND created_at = ? "
            "AND COALESCE(generated_name, name) = ?",
            (row["kind"], row["created_at"], generated),
        ).fetchone()
        if found is not None:
            ids[int(row["id"])] = int(found["id"])
            report.note("writer_adapters", False)
            continue
        fields = {
            **{column: row[column] for column in _ADAPTER_COLUMNS},
            "name": _free_adapter_name(conn, row["name"]),
            "generated_name": generated,
            "status": "deleted",
            "deleted_at": now,
        }
        names = sorted(fields)
        cursor = conn.execute(
            f"INSERT INTO writer_adapters ({', '.join(names)}) "
            f"VALUES ({', '.join('?' * len(names))})",
            [fields[name] for name in names],
        )
        ids[int(row["id"])] = int(cursor.lastrowid or 0)
        report.note("writer_adapters", True)
    return ids


class _Ids:
    """Rewrites the ids that are local to the database a bundle came from."""

    def __init__(self, presets: Mapping[int, int], adapters: Mapping[int, int]) -> None:
        self._presets = presets
        self._adapters = adapters

    def preset(self, value: Any) -> Optional[int]:
        return self._presets.get(value) if isinstance(value, int) else None

    def writer(self, value: Any) -> Any:
        adapter = parse_adapter_writer_id(value)
        new = self._adapters.get(adapter) if adapter is not None else None
        return f"{WRITER_ADAPTER_PREFIX}{new}" if new is not None else value

    def form_state(self, text: Any) -> Any:
        """The T2I form names its preset by id."""
        if not isinstance(text, str):
            return text
        try:
            state = json.loads(text)
        except (ValueError, RecursionError):
            return text
        if not isinstance(state, dict) or "preset_id" not in state:
            return text
        state["preset_id"] = self.preset(state["preset_id"])
        return json.dumps(state)


def _import_lineage(
    conn: sqlite3.Connection,
    table: str,
    rows: Sequence[Mapping[str, Any]],
    paths: PathMap,
    library: MediaResolver,
    ids: _Ids,
    report: _Report,
) -> None:
    columns = _columns(conn, table)
    for row in rows:
        path = library.resolve(paths.translate(row["file_path"]))
        if path is None:
            report.not_found(table)
            continue
        copy = dict(row)
        copy["file_path"] = path
        if "source_path" in copy:
            mapped = paths.translate(copy["source_path"])
            copy["source_path"] = library.resolve(mapped) or mapped
        copy["preset_id"] = ids.preset(row["preset_id"])
        copy["writer_id"] = ids.writer(row["writer_id"])
        copy["form_state"] = ids.form_state(copy.get("form_state"))
        changed = _insert_known(
            conn, table, columns, copy, "ON CONFLICT(file_path) DO NOTHING"
        )
        report.note(table, changed)


def _tombstone_lineage(text: Optional[str], paths: PathMap, ids: _Ids) -> Optional[str]:
    lineage = _lineage(text)
    if lineage is None:
        return text
    for row in _lineage_rows(lineage):
        for key in ("file_path", "source_path"):
            if isinstance(row.get(key), str):
                row[key] = paths.translate(row[key])
        if "preset_id" in row:
            row["preset_id"] = ids.preset(row["preset_id"])
        if "writer_id" in row:
            row["writer_id"] = ids.writer(row["writer_id"])
    return json.dumps(lineage, default=str)


def _import_tombstones(
    db: "DatabaseManager",
    conn: sqlite3.Connection,
    rows: Sequence[Mapping[str, Any]],
    paths: PathMap,
    ids: _Ids,
    report: _Report,
    thumbnails: Set[str],
) -> None:
    """A tombstone is the same one when its path and deletion time agree. It
    says nothing about a FAISS row here (another machine's index), so it
    carries no evidence and is marked rewritten: the index is never read by
    its path; the vectors and the archived thumbnail are its own."""
    fill_columns, fill_values = db._tombstone_fill
    columns = (*_TOMBSTONE_COLUMNS, "embedded_model", "embedded_at", "path_rewritten")
    sql = (
        f"INSERT INTO media_tombstones ({', '.join(columns)}{fill_columns}) "
        f"VALUES ({', '.join('?' * (len(columns) + len(fill_values)))})"
    )
    for row in rows:
        path = paths.translate(row["file_path"])
        if conn.execute(
            "SELECT 1 FROM media_tombstones WHERE file_path = ? AND deleted_at = ?",
            (path, row["deleted_at"]),
        ).fetchone():
            report.note("tombstones", False)
            continue
        values = dict(row)
        values["file_path"] = path
        values["lineage"] = _tombstone_lineage(row["lineage"], paths, ids)
        thumb = row["thumb_path"]
        match = _THUMB_PATH_RX.fullmatch(thumb) if thumb else None
        if match is None or f"{THUMBS_PREFIX}{match.group(1)}.jpg" not in thumbnails:
            values["thumb_path"] = None
        cursor = conn.execute(
            sql,
            [
                *(values[column] for column in _TOMBSTONE_COLUMNS),
                "",
                None,
                1,
                *fill_values,
            ],
        )
        for vector in row["vectors"]:
            conn.execute(
                "INSERT OR IGNORE INTO tombstone_vectors "
                "(tombstone_id, model_key, embedding, created_at) VALUES (?, ?, ?, ?)",
                (
                    cursor.lastrowid,
                    vector["model_key"],
                    base64.b64decode(vector["embedding"]),
                    vector["created_at"],
                ),
            )
        report.note("tombstones", True)


def _import_media_rows(
    db: "DatabaseManager",
    conn: sqlite3.Connection,
    data: Mapping[str, Any],
    paths: PathMap,
    library: MediaResolver,
    report: _Report,
) -> None:
    """★, speeds, grades, categories, briefs and prompts: what needs a row."""

    def here(path: str, kind: str) -> Optional[str]:
        found = library.resolve(paths.translate(path))
        if found is None:
            report.not_found(kind)
        return found

    for favorite in data["favorites"]:
        path = here(favorite, "favorites")
        if path is not None:
            cursor = conn.execute(
                "UPDATE media SET is_favorite = 1 WHERE file_path = ? "
                "AND COALESCE(is_favorite, 0) = 0",
                (path,),
            )
            report.note("favorites", cursor.rowcount > 0)
    for row in data["playback_speeds"]:
        path = here(row["file_path"], "playback_speeds")
        if path is not None:
            cursor = conn.execute(
                "UPDATE media SET playback_speed = ? WHERE file_path = ? "
                "AND playback_speed IS NULL",
                (row["playback_speed"], path),
            )
            report.note("playback_speeds", cursor.rowcount > 0)
    for row in data["judgments"]:
        path = here(row["file_path"], "judgments")
        if path is None:
            continue
        # A view alone adds nothing to a row that exists, and writing it back
        # could put an older view over a newer one.
        rated = (
            row["grade"] is not None
            or row["judged_at"] is not None
            or row["reasons"] != "[]"
        )
        exists = conn.execute(
            "SELECT 1 FROM media_judgments WHERE file_path = ?", (path,)
        ).fetchone()
        apply = not db._has_newer_judgment(conn, path) and (rated or not exists)
        if apply:
            db._put_back_judgment(conn, path, row)
        report.note("judgments", apply)
    for row in data["hindsight_briefs"]:
        path = here(row["file_path"], "hindsight_briefs")
        if path is not None:
            before = conn.total_changes
            db._put_back_brief(conn, path, row)
            report.note("hindsight_briefs", conn.total_changes > before)
    saved = _columns(conn, "saved_prompts")
    for row in data["saved_prompts"]:
        path = here(row["file_path"], "saved_prompts")
        if path is None:
            continue
        if conn.execute(
            "SELECT 1 FROM saved_prompts WHERE file_path = ? AND name = ? "
            "AND created_at = ?",
            (path, row["name"], row["created_at"]),
        ).fetchone():
            report.note("saved_prompts", False)
            continue
        report.note(
            "saved_prompts",
            _insert_known(conn, "saved_prompts", saved, {**row, "file_path": path}, ""),
        )


def _import_categories(
    conn: sqlite3.Connection,
    data: Mapping[str, Any],
    paths: PathMap,
    library: MediaResolver,
    report: _Report,
) -> None:
    for row in data["categories"]:
        cursor = conn.execute(
            "INSERT INTO categories (name, created_at) VALUES (?, ?) "
            "ON CONFLICT(name) DO NOTHING",
            (row["name"], row["created_at"]),
        )
        report.note("categories", cursor.rowcount > 0)
    for row in data["media_categories"]:
        path = library.resolve(paths.translate(row["file_path"]))
        found = conn.execute(
            "SELECT id FROM categories WHERE name = ?", (row["category"],)
        ).fetchone()
        if path is None or found is None:
            report.not_found("media_categories")
            continue
        cursor = conn.execute(
            "INSERT INTO media_categories (file_path, category_id, source, "
            "assigned_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(file_path, category_id) DO NOTHING",
            (path, found["id"], row["source"], row["assigned_at"]),
        )
        report.note("media_categories", cursor.rowcount > 0)


def _import_folders(
    conn: sqlite3.Connection,
    data: Mapping[str, Any],
    paths: PathMap,
    library: MediaResolver,
    report: _Report,
) -> None:
    for row in data["folders"]:
        cursor = conn.execute(
            "INSERT INTO folders (id, kind, name, icon, rules, sort_order, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO NOTHING",
            (
                row["id"],
                row["kind"],
                row["name"],
                row["icon"],
                row["rules"],
                row["sort_order"],
                row["created_at"],
                row["updated_at"],
            ),
        )
        report.note("folders", cursor.rowcount > 0)
    known = {row["id"] for row in conn.execute("SELECT id FROM folders")}
    for row in data["folder_items"]:
        path = library.resolve(paths.translate(row["file_path"]))
        if path is None or row["folder_id"] not in known:
            report.not_found("folder_items")
            continue
        cursor = conn.execute(
            "INSERT INTO folder_items (folder_id, file_path, added_at) "
            "VALUES (?, ?, ?) ON CONFLICT(folder_id, file_path) DO NOTHING",
            (row["folder_id"], path, row["added_at"]),
        )
        report.note("folder_items", cursor.rowcount > 0)


def _import_events(
    conn: sqlite3.Connection,
    rows: Sequence[Mapping[str, Any]],
    paths: PathMap,
    library: MediaResolver,
    report: _Report,
) -> None:
    """The log has no FK: a deleted file's events come too, and a row that
    already reads the same is the same event."""
    for row in rows:
        mapped = paths.translate(row["file_path"])
        path = library.resolve(mapped) or mapped
        if conn.execute(
            "SELECT 1 FROM judgment_events WHERE file_path = ? AND event = ? "
            "AND created_at = ? AND reason IS ? AND category IS ?",
            (path, row["event"], row["created_at"], row["reason"], row["category"]),
        ).fetchone():
            report.note("judgment_events", False)
            continue
        conn.execute(
            "INSERT INTO judgment_events (file_path, event, reason, category, "
            "score_shown, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                path,
                row["event"],
                row["reason"],
                row["category"],
                row["score_shown"],
                row["created_at"],
            ),
        )
        report.note("judgment_events", True)


def import_library_data(
    db: "DatabaseManager",
    data: Mapping[str, Any],
    paths: PathMap,
    thumbnails: Optional[Set[str]] = None,
    now: Optional[str] = None,
) -> Dict[str, Dict[str, int]]:
    """Apply a validated bundle in ONE transaction, under the database lock:
    ``{kind: {added, already, not_found}}``. ``thumbnails`` is the bundle's
    member names; a tombstone whose thumbnail is not among them has none. Any
    failure rolls everything back and raises."""
    report = _Report()
    stamp = now or _utc_now()
    with db.lock, db._get_connection() as conn:
        try:
            library = MediaResolver(
                r["file_path"] for r in conn.execute("SELECT file_path FROM media")
            )
            ids = _Ids(
                _import_presets(conn, data["workflow_presets"], report),
                _import_adapters(conn, data["writer_adapters"], stamp, report),
            )
            _import_media_rows(db, conn, data, paths, library, report)
            _import_categories(conn, data, paths, library, report)
            _import_folders(conn, data, paths, library, report)
            for table in ("t2i_images", "i2v_videos"):
                _import_lineage(conn, table, data[table], paths, library, ids, report)
            _import_events(conn, data["judgment_events"], paths, library, report)
            _import_tombstones(
                db,
                conn,
                data["tombstones"],
                paths,
                ids,
                report,
                thumbnails or set(),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return report.counts


def bundle_members(source: Path) -> Set[str]:
    """The names in a bundle (which archived thumbnails it holds)."""
    with zipfile.ZipFile(source) as bundle:
        return set(bundle.namelist())
