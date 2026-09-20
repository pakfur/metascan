import re
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any, Set, Tuple, ClassVar
from contextlib import contextmanager
import logging
from datetime import datetime
from threading import Lock

from metascan.utils.startup_profiler import log_startup
from metascan.utils.path_utils import to_posix_path, to_native_path
from metascan.core.media import Media
from metascan.core.prompt_tokenizer import PromptTokenizer

logger = logging.getLogger(__name__)


SUBJECT_TYPE_VALUES = ("character", "location", "prop")

_LOCATION_WORDS = (
    "cabin",
    "house",
    "home",
    "room",
    "kitchen",
    "bedroom",
    "bathroom",
    "hall",
    "hallway",
    "corridor",
    "office",
    "college",
    "school",
    "campus",
    "lawn",
    "garden",
    "yard",
    "street",
    "road",
    "alley",
    "city",
    "town",
    "village",
    "forest",
    "woods",
    "beach",
    "shore",
    "lake",
    "river",
    "mountain",
    "field",
    "farm",
    "barn",
    "church",
    "bar",
    "cafe",
    "diner",
    "restaurant",
    "hotel",
    "motel",
    "station",
    "airport",
    "harbor",
    "dock",
    "warehouse",
    "factory",
    "lab",
    "laboratory",
    "hospital",
    "clinic",
    "apartment",
    "flat",
    "loft",
    "attic",
    "basement",
    "cellar",
    "garage",
    "rooftop",
    "bridge",
    "tunnel",
    "park",
    "plaza",
    "square",
    "market",
    "shop",
    "store",
    "interior",
    "exterior",
    "landscape",
    "setting",
    "location",
    "building",
    "castle",
    "temple",
    "ruins",
    "cave",
    "desert",
    "island",
    "ship",
    "spaceship",
    "corridor",
    "deck",
)
_PROP_WORDS = (
    "knife",
    "gun",
    "pistol",
    "rifle",
    "sword",
    "letter",
    "note",
    "phone",
    "key",
    "keys",
    "ring",
    "necklace",
    "watch",
    "bag",
    "suitcase",
    "book",
    "map",
    "photo",
    "photograph",
    "bottle",
    "glass",
    "cup",
    "mug",
    "box",
    "package",
    "envelope",
    "coin",
    "coins",
    "lamp",
    "candle",
    "mirror",
    "painting",
    "device",
    "gadget",
    "artifact",
    "relic",
    "object",
    "item",
    "tool",
    "weapon",
    "prop",
)


def infer_subject_type(name: str, description: str = "") -> str:
    """Spec Phase C1 backfill heuristic: classify a roster entry from its
    name (weighted) and description as ``location`` / ``prop`` when the
    text makes it obvious, else ``character``. Deliberately conservative
    -- a wrong ``location`` guess un-casts a real performer, whereas a
    wrong ``character`` guess just keeps the old behavior."""
    n = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower()).split()
    d = re.sub(r"[^a-z0-9 ]+", " ", (description or "").lower()).split()
    if not n:
        return "character"
    if n[-1] in _LOCATION_WORDS or all(w in _LOCATION_WORDS for w in n):
        return "location"
    if n[-1] in _PROP_WORDS or all(w in _PROP_WORDS for w in n):
        return "prop"
    head = d[:12]
    person_words = {
        "woman",
        "man",
        "girl",
        "boy",
        "female",
        "male",
        "person",
        "child",
        "teen",
        "teenager",
        "lady",
        "gentleman",
        "dog",
        "cat",
        "creature",
        "he",
        "she",
        "her",
        "his",
        "they",
        "years",
        "old",
        "aged",
    }
    if any(w in person_words for w in head):
        return "character"
    if head and head[0] in ("a", "an", "the") and len(head) > 1:
        # "a sun-drenched college lawn ..." / "a rusted iron key"
        for w in head[1:6]:
            if w in _LOCATION_WORDS:
                return "location"
            if w in _PROP_WORDS:
                return "prop"
    return "character"


def _idempotent_add_column(
    conn: sqlite3.Connection, table: str, column: str, ddl: str
) -> None:
    """Run ``ddl`` (an ``ALTER TABLE ... ADD COLUMN``) and swallow the
    duplicate-column error so concurrent ``DatabaseManager`` instances can
    both try the migration without one crashing the other.

    ``get_db()`` constructs a new manager per request and FastAPI resolves
    dependencies in a threadpool, so it's normal for two inits to enter
    ``_init_database`` simultaneously on a cold DB. The PRAGMA-then-ALTER
    pattern has a TOCTOU race — the loser raises
    ``sqlite3.OperationalError: duplicate column name``, which is benign."""
    try:
        conn.execute(ddl)
        logger.info("Added %s column to %s table", column, table)
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug(
                "Migration for %s.%s lost race with another init; " "already present.",
                table,
                column,
            )
            return
        raise


class DatabaseManager:
    def __init__(self, db_path: Path):
        log_startup("    DatabaseManager.__init__: Starting")
        self.db_path = db_path
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.db_file = self.db_path / "metascan.db"
        self.lock = Lock()

        # Lazy-initialize prompt tokenizer (deferred until first use)
        self._prompt_tokenizer: Optional[PromptTokenizer] = None

        log_startup("    DatabaseManager: Initializing database schema...")
        self._init_database()
        log_startup("    DatabaseManager.__init__: Complete")

    @property
    def prompt_tokenizer(self) -> PromptTokenizer:
        """Lazy-load the PromptTokenizer on first access."""
        if self._prompt_tokenizer is None:
            log_startup("    DatabaseManager: Lazy-loading PromptTokenizer...")
            self._prompt_tokenizer = PromptTokenizer()
        return self._prompt_tokenizer

    def _init_database(self) -> None:
        with self._get_connection() as conn:
            # Main media table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS media (
                    file_path TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    is_favorite INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )

            cursor = conn.execute("PRAGMA table_info(media)")
            columns = [column[1] for column in cursor.fetchall()]
            if "is_favorite" not in columns:
                _idempotent_add_column(
                    conn,
                    "media",
                    "is_favorite",
                    "ALTER TABLE media ADD COLUMN is_favorite INTEGER DEFAULT 0",
                )
            if "playback_speed" not in columns:
                _idempotent_add_column(
                    conn,
                    "media",
                    "playback_speed",
                    "ALTER TABLE media ADD COLUMN playback_speed REAL DEFAULT NULL",
                )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS indices (
                    index_type TEXT NOT NULL,
                    index_key TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    source TEXT,
                    PRIMARY KEY (index_type, index_key, file_path),
                    FOREIGN KEY (file_path) REFERENCES media(file_path) ON DELETE CASCADE
                )
            """
            )

            # Migration for existing DBs created before the `source` column
            # was added. For non-tag rows ``source`` is always NULL; for tag
            # rows it's one of 'prompt', 'clip', 'both' — see
            # `_generate_indices` and `add_tag_indices`.
            cursor = conn.execute("PRAGMA table_info(indices)")
            index_cols = [column[1] for column in cursor.fetchall()]
            if "source" not in index_cols:
                _idempotent_add_column(
                    conn,
                    "indices",
                    "source",
                    "ALTER TABLE indices ADD COLUMN source TEXT",
                )

            # Materialize summary fields that would otherwise live inside the
            # ``data`` JSON blob. Without these columns, the list endpoint
            # had to pull the entire blob off disk through ``json_extract``
            # (710 MB of overflow pages for a 12 K-row library on WSL
            # /mnt/c). Adding columns means the summary SELECT touches only
            # narrow rows — orders of magnitude faster.
            #
            # We detect the migration using ``modified_at`` as a sentinel:
            # if it's absent we assume *none* of the six are present and
            # treat them as one group so the backfill does a single pass
            # over the blob instead of six.
            if "modified_at" not in columns:
                _idempotent_add_column(
                    conn, "media", "width", "ALTER TABLE media ADD COLUMN width INTEGER"
                )
                _idempotent_add_column(
                    conn,
                    "media",
                    "height",
                    "ALTER TABLE media ADD COLUMN height INTEGER",
                )
                _idempotent_add_column(
                    conn,
                    "media",
                    "file_size",
                    "ALTER TABLE media ADD COLUMN file_size INTEGER",
                )
                _idempotent_add_column(
                    conn,
                    "media",
                    "frame_rate",
                    "ALTER TABLE media ADD COLUMN frame_rate REAL",
                )
                _idempotent_add_column(
                    conn,
                    "media",
                    "duration",
                    "ALTER TABLE media ADD COLUMN duration REAL",
                )
                _idempotent_add_column(
                    conn,
                    "media",
                    "modified_at",
                    "ALTER TABLE media ADD COLUMN modified_at TEXT",
                )
                logger.info(
                    "Backfilling materialized summary columns from Media "
                    "JSON blob (one-time, may take a minute on large "
                    "libraries)…"
                )
                conn.execute(
                    """
                    UPDATE media SET
                        width       = json_extract(data, '$.width'),
                        height      = json_extract(data, '$.height'),
                        file_size   = json_extract(data, '$.file_size'),
                        frame_rate  = json_extract(data, '$.frame_rate'),
                        duration    = json_extract(data, '$.duration'),
                        modified_at = json_extract(data, '$.modified_at')
                    """
                )
                logger.info("Summary-column backfill complete.")

            # Photo-EXIF columns (real-world photo support).
            _idempotent_add_column(
                conn,
                "media",
                "camera_make",
                "ALTER TABLE media ADD COLUMN camera_make TEXT",
            )
            _idempotent_add_column(
                conn,
                "media",
                "camera_model",
                "ALTER TABLE media ADD COLUMN camera_model TEXT",
            )
            _idempotent_add_column(
                conn,
                "media",
                "lens_model",
                "ALTER TABLE media ADD COLUMN lens_model TEXT",
            )
            _idempotent_add_column(
                conn,
                "media",
                "datetime_original",
                "ALTER TABLE media ADD COLUMN datetime_original TEXT",
            )
            _idempotent_add_column(
                conn,
                "media",
                "gps_latitude",
                "ALTER TABLE media ADD COLUMN gps_latitude REAL",
            )
            _idempotent_add_column(
                conn,
                "media",
                "gps_longitude",
                "ALTER TABLE media ADD COLUMN gps_longitude REAL",
            )
            _idempotent_add_column(
                conn,
                "media",
                "gps_altitude",
                "ALTER TABLE media ADD COLUMN gps_altitude REAL",
            )
            _idempotent_add_column(
                conn,
                "media",
                "orientation",
                "ALTER TABLE media ADD COLUMN orientation INTEGER",
            )
            _idempotent_add_column(
                conn,
                "media",
                "photo_exposure",
                "ALTER TABLE media ADD COLUMN photo_exposure TEXT",
            )
            _idempotent_add_column(
                conn,
                "media",
                "hidden",
                "ALTER TABLE media ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0",
            )

            # Covering indexes for the grid list endpoint. The `media` row
            # layout is `[file_path][data][is_favorite]...[width]...`, so
            # reading any column positioned after `data` would force SQLite
            # to seek through `data`'s 700+ MB overflow-page chain. A
            # covering index holds every summary column at the leaf level,
            # letting the query planner answer list requests without
            # touching the main table — ~6 ms instead of ~25 s.
            #
            # Both indexes must include *every* column the summary SELECT
            # projects. When a new column (e.g. ``modified_at`` /
            # ``created_at``) is added to the summary response, the old
            # covering index silently stops covering and each list request
            # falls back to the main table — the 20-second regression we're
            # guarding against. Drop any pre-existing index whose DDL is
            # missing the current column set so the CREATE below rebuilds.
            required_cols = (
                "modified_at",
                "created_at",
                "camera_make",
                "camera_model",
                "datetime_original",
                "gps_latitude",
                "gps_longitude",
                "orientation",
                "hidden",
            )
            for idx_name in ("idx_media_summary_added", "idx_media_summary_modified"):
                ddl_row = conn.execute(
                    "SELECT sql FROM sqlite_master " "WHERE type='index' AND name=?",
                    (idx_name,),
                ).fetchone()
                ddl_sql = (ddl_row["sql"] or "") if ddl_row else ""
                if ddl_row and any(col not in ddl_sql for col in required_cols):
                    conn.execute(f"DROP INDEX IF EXISTS {idx_name}")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_media_summary_added
                ON media(
                    created_at, file_path, is_favorite, playback_speed,
                    width, height, file_size, frame_rate, duration,
                    modified_at,
                    camera_make, camera_model, datetime_original,
                    gps_latitude, gps_longitude, orientation, hidden
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_media_summary_modified
                ON media(
                    modified_at, file_path, is_favorite, playback_speed,
                    width, height, file_size, frame_rate, duration,
                    created_at,
                    camera_make, camera_model, datetime_original,
                    gps_latitude, gps_longitude, orientation, hidden
                )
                """
            )

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_indices_lookup ON indices(index_type, index_key)"
            )
            # Composite index serving the two per-file workloads that were
            # otherwise SQLite's worst cases here:
            #
            # (1) `DELETE/UPDATE ... WHERE file_path = ?` inside
            #     `_update_indices` — the compound PK starts with
            #     index_type, so it can't serve a file_path lookup and
            #     writes degrade to O(total rows) per file, making a full
            #     scan O(N²).
            # (2) `get_tags_for_file` (`SELECT index_key ... WHERE
            #     file_path=? AND index_type='tag' ORDER BY index_key`) —
            #     the SQLite planner prefers the autoindex on
            #     (index_type, index_key, file_path) as a covering index
            #     for `index_type='tag'` alone, which means scanning every
            #     `tag` row (~115 K on a 12 K-file library) to filter down
            #     to the ~30-150 rows actually matching. That took ~1.8 s
            #     per detail-panel open.
            #
            # A composite `(file_path, index_type, index_key)` covers both:
            # DELETE seeks on the file_path prefix, and the tag SELECT is
            # answered entirely from the index (prefix match + covered
            # projection, and `index_key` is already sorted so no explicit
            # sort step). Result: DELETE ~ms instead of O(N), tag lookup
            # ~0.3 ms instead of 1.8 s.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_indices_by_file_type "
                "ON indices(file_path, index_type, index_key)"
            )
            # Supersedes the earlier narrower file_path-only index — the
            # composite above handles the same queries plus more. Drop
            # idempotently so existing DBs reclaim the space on next boot.
            conn.execute("DROP INDEX IF EXISTS idx_indices_file_path")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_created ON media(created_at)"
            )

            # Similarity / embedding tracking table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS media_hashes (
                    file_path TEXT PRIMARY KEY,
                    phash TEXT,
                    clip_model TEXT,
                    has_embedding INTEGER DEFAULT 0,
                    embedding_updated_at TIMESTAMP,
                    FOREIGN KEY (file_path) REFERENCES media(file_path) ON DELETE CASCADE
                )
            """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_hashes_phash ON media_hashes(phash)"
            )

            # Folders (manual + smart) — persistent replacement for the
            # localStorage-backed folders store. ``kind='manual'`` folders
            # have explicit memberships in folder_items; ``kind='smart'``
            # folders carry a JSON rules blob and compute membership live.
            # sort_order exists so drag-to-reorder lands without another
            # migration; the current UI writes 0 for everything.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS folders (
                    id         TEXT PRIMARY KEY,
                    kind       TEXT NOT NULL
                               CHECK(kind IN ('manual','smart')),
                    name       TEXT NOT NULL,
                    icon       TEXT NOT NULL DEFAULT 'pi-folder',
                    rules      TEXT,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS folder_items (
                    folder_id TEXT NOT NULL
                              REFERENCES folders(id) ON DELETE CASCADE,
                    file_path TEXT NOT NULL
                              REFERENCES media(file_path) ON DELETE CASCADE,
                    added_at  REAL NOT NULL,
                    PRIMARY KEY (folder_id, file_path)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_folder_items_by_file "
                "ON folder_items(file_path)"
            )

            # Saved prompts. Side-channel storage for the Prompt Playground
            # feature (TA-10): named, persisted prompts associated with an
            # image. NOT linked to the inverted `indices` table — these are
            # user-curated experimental prompts, NOT canonical metadata, and
            # must not affect tag search.
            # CASCADE on file_path so deleting a media row clears its saved
            # prompts.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS saved_prompts (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path     TEXT NOT NULL
                                  REFERENCES media(file_path) ON DELETE CASCADE,
                    name          TEXT NOT NULL,
                    prompt        TEXT NOT NULL,
                    negative      TEXT,
                    target_model  TEXT NOT NULL,
                    architecture  TEXT NOT NULL,
                    styles        TEXT NOT NULL DEFAULT '[]',
                    temperature   REAL,
                    max_tokens    INTEGER,
                    source_prompt TEXT,
                    mode          TEXT NOT NULL
                                  CHECK(mode IN ('generate','transform','clean')),
                    vlm_model_id  TEXT,
                    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_saved_prompts_file "
                "ON saved_prompts(file_path)"
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_presets (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    name          TEXT NOT NULL UNIQUE,
                    kind          TEXT NOT NULL
                                  CHECK(kind IN ('t2i','ref','ref2v')),
                    workflow_json TEXT NOT NULL,
                    bindings      TEXT NOT NULL,
                    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
                    video_target  TEXT,
                    video_mode    TEXT
                )
                """
            )
            # workflow_presets.kind originally only allowed ('t2i','ref').
            # V4 (video generation) adds 'ref2v'. A dev DB created before
            # this change still has the old CHECK baked into its DDL --
            # SQLite CHECK constraints can't be altered in place, so detect
            # the stale constraint from the live schema and rebuild the
            # table (create/copy/drop/rename), mirroring the
            # storyboards.folder_id INTEGER->TEXT rebuild above.
            old_presets_ddl = conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='workflow_presets'"
            ).fetchone()
            if old_presets_ddl and "ref2v" not in (old_presets_ddl["sql"] or ""):
                logger.info(
                    "Migrating workflow_presets.kind CHECK to allow 'ref2v' "
                    "(dev DB predating video generation)…"
                )
                # PRAGMA foreign_keys is a no-op while a transaction is
                # open (SQLite requires it be toggled outside one) -- the
                # summary-column backfill earlier in this method runs an
                # UPDATE that leaves an implicit transaction pending even
                # on a fresh DB with zero media rows. Commit it first so
                # the OFF actually takes, or DROP TABLE below raises
                # "FOREIGN KEY constraint failed" the moment any other
                # table (e.g. generation_jobs) has a row referencing
                # workflow_presets.
                conn.commit()
                conn.execute("PRAGMA foreign_keys = OFF")
                conn.execute(
                    """
                    CREATE TABLE workflow_presets_kind_migration (
                        id            INTEGER PRIMARY KEY AUTOINCREMENT,
                        name          TEXT NOT NULL UNIQUE,
                        kind          TEXT NOT NULL
                                      CHECK(kind IN ('t2i','ref','ref2v')),
                        workflow_json TEXT NOT NULL,
                        bindings      TEXT NOT NULL,
                        created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO workflow_presets_kind_migration "
                    "(id, name, kind, workflow_json, bindings, created_at, "
                    "updated_at) "
                    "SELECT id, name, kind, workflow_json, bindings, "
                    "created_at, updated_at FROM workflow_presets"
                )
                conn.execute("DROP TABLE workflow_presets")
                conn.execute(
                    "ALTER TABLE workflow_presets_kind_migration "
                    "RENAME TO workflow_presets"
                )
                conn.commit()
                conn.execute("PRAGMA foreign_keys = ON")
            _idempotent_add_column(
                conn,
                "workflow_presets",
                "video_target",
                "ALTER TABLE workflow_presets ADD COLUMN video_target TEXT",
            )
            _idempotent_add_column(
                conn,
                "workflow_presets",
                "video_mode",
                "ALTER TABLE workflow_presets ADD COLUMN video_mode TEXT",
            )
            # NOTE: panel_id deliberately carries no REFERENCES clause. The
            # panels table arrives in Phase B; with PRAGMA foreign_keys = ON
            # an INSERT naming a FK to a missing table fails at runtime, and
            # SQLite cannot add a FK to an existing table without rebuilding
            # it. Phase B deletes matching job rows explicitly instead.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS generation_jobs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    preset_id       INTEGER NOT NULL
                                    REFERENCES workflow_presets(id),
                    panel_id        INTEGER,
                    state           TEXT NOT NULL DEFAULT 'queued'
                                    CHECK(state IN ('queued','running','done',
                                                    'failed','cancelled')),
                    comfy_prompt_id TEXT,
                    params          TEXT NOT NULL,
                    error           TEXT,
                    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                    started_at      TEXT,
                    finished_at     TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_generation_jobs_state "
                "ON generation_jobs(state)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_generation_jobs_prompt "
                "ON generation_jobs(comfy_prompt_id)"
            )
            _idempotent_add_column(
                conn,
                "generation_jobs",
                "output_dir",
                "ALTER TABLE generation_jobs ADD COLUMN output_dir TEXT",
            )

            # ---- Phase B: storyboard tables --------------------------------
            # Shot/beat model reorganization (spec 2026-08-17): panels thin
            # to H3-scene containers, beats carry framing/prompt/keeper,
            # panel_images becomes beat_images. Read user_version once, up
            # front -- this same value is reused below for the '<1'/'<2'
            # one-shot migrations further down in this method; nothing
            # writes the pragma in between. Dev data is disposable by
            # decision -- drop and recreate the panel-tree tables, but
            # first release what the old tables were holding: unhide media
            # the old panel_images kept hidden, and purge panel-scoped
            # generation_jobs so a restart can't re-adopt jobs for panels
            # that no longer exist. This must run before the
            # ``CREATE TABLE IF NOT EXISTS panels/beats/beat_images``
            # statements below so the drop is followed by a clean re-create
            # in this same _init_database call. ``PRAGMA user_version = 3``
            # itself is written further down, alongside the other one-shot
            # migration gates.
            version_row = conn.execute("PRAGMA user_version").fetchone()
            user_version = int(version_row[0]) if version_row else 0
            if user_version < 3:
                old_tables = {
                    r["name"]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if "panel_images" in old_tables:
                    conn.execute(
                        "UPDATE media SET hidden = 0 WHERE file_path IN "
                        "(SELECT file_path FROM panel_images)"
                    )
                if "generation_jobs" in old_tables:
                    conn.execute(
                        "DELETE FROM generation_jobs WHERE panel_id IS NOT NULL"
                    )
                conn.execute("DROP TABLE IF EXISTS panel_images")
                conn.execute("DROP TABLE IF EXISTS beat_images")
                conn.execute("DROP TABLE IF EXISTS beats")
                conn.execute("DROP TABLE IF EXISTS panels")

            # storyboards.folder_id was originally declared INTEGER, but
            # folders.id is TEXT (a uuid4 string) -- a numeric-looking uuid
            # would silently coerce and corrupt add_folder_items lookups.
            # This table shipped only on the storyboard-domain branch (never
            # released), so a fresh DB just gets the correct DDL below. A dev
            # DB created from an earlier commit on this branch would still
            # have the old INTEGER column, though -- detect that from the
            # live schema and do a standard SQLite column-type rebuild
            # (create/copy/drop/rename) rather than DROP+recreate, which
            # would lose data. Follows the off/rebuild/on procedure SQLite's
            # own docs recommend for schema changes under FK enforcement.
            old_storyboards_ddl = conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='storyboards'"
            ).fetchone()
            # sqlite_master preserves the CREATE TABLE text verbatim,
            # whitespace and all -- match on the column pair with
            # flexible whitespace rather than a literal substring so this
            # doesn't depend on exact formatting.
            if old_storyboards_ddl and re.search(
                r"folder_id\s+INTEGER", old_storyboards_ddl["sql"] or ""
            ):
                logger.info(
                    "Migrating storyboards.folder_id INTEGER -> TEXT "
                    "(dev DB predating the folder_id type fix)…"
                )
                conn.execute("PRAGMA foreign_keys = OFF")
                conn.execute(
                    """
                    CREATE TABLE storyboards_folder_id_migration (
                        id            INTEGER PRIMARY KEY AUTOINCREMENT,
                        name          TEXT NOT NULL,
                        source_text   TEXT,
                        aspect_ratio  TEXT NOT NULL DEFAULT '16:9',
                        style_block   TEXT,
                        negative      TEXT,
                        target_model  TEXT NOT NULL,
                        architecture  TEXT NOT NULL,
                        preset_id     INTEGER REFERENCES workflow_presets(id),
                        base_seed     INTEGER NOT NULL,
                        batch_size    INTEGER NOT NULL DEFAULT 4,
                        folder_id     TEXT REFERENCES folders(id)
                                      ON DELETE SET NULL,
                        created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO storyboards_folder_id_migration "
                    "(id, name, source_text, aspect_ratio, style_block, "
                    "negative, target_model, architecture, preset_id, "
                    "base_seed, batch_size, folder_id, created_at, updated_at) "
                    "SELECT id, name, source_text, aspect_ratio, style_block, "
                    "negative, target_model, architecture, preset_id, "
                    "base_seed, batch_size, CAST(folder_id AS TEXT), "
                    "created_at, updated_at FROM storyboards"
                )
                conn.execute("DROP TABLE storyboards")
                conn.execute(
                    "ALTER TABLE storyboards_folder_id_migration "
                    "RENAME TO storyboards"
                )
                conn.commit()
                conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS storyboards (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    name          TEXT NOT NULL,
                    source_text   TEXT,
                    aspect_ratio  TEXT NOT NULL DEFAULT '16:9',
                    style_block   TEXT,
                    negative      TEXT,
                    target_model  TEXT NOT NULL,
                    architecture  TEXT NOT NULL,
                    preset_id     INTEGER REFERENCES workflow_presets(id),
                    base_seed     INTEGER NOT NULL,
                    batch_size    INTEGER NOT NULL DEFAULT 4,
                    folder_id     TEXT REFERENCES folders(id)
                                  ON DELETE SET NULL,
                    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
                    pacing        TEXT NOT NULL DEFAULT 'standard',
                    story_scale   TEXT NOT NULL DEFAULT 'standard'
                )
                """
            )
            _idempotent_add_column(
                conn,
                "storyboards",
                "pacing",
                "ALTER TABLE storyboards ADD COLUMN pacing "
                "TEXT NOT NULL DEFAULT 'standard'",
            )
            _idempotent_add_column(
                conn,
                "storyboards",
                "story_scale",
                "ALTER TABLE storyboards ADD COLUMN story_scale "
                "TEXT NOT NULL DEFAULT 'standard'",
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS storyboard_subjects (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    storyboard_id  INTEGER NOT NULL
                                   REFERENCES storyboards(id) ON DELETE CASCADE,
                    name           TEXT NOT NULL,
                    description    TEXT NOT NULL,
                    lora_name      TEXT,
                    lora_strength  REAL DEFAULT 0.8,
                    reference_path TEXT REFERENCES media(file_path)
                                   ON DELETE SET NULL,
                    sort_order     INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scenes (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    storyboard_id INTEGER NOT NULL
                                  REFERENCES storyboards(id) ON DELETE CASCADE,
                    sort_order    INTEGER NOT NULL DEFAULT 0,
                    name          TEXT NOT NULL,
                    subtitle      TEXT,
                    setting       TEXT,
                    location      TEXT,
                    time_of_day   TEXT,
                    mood          TEXT,
                    lighting      TEXT,
                    notes         TEXT,
                    arc_beats     TEXT NOT NULL DEFAULT '[]',
                    charge_in     INTEGER,
                    charge_out    INTEGER
                )
                """
            )
            _idempotent_add_column(
                conn,
                "scenes",
                "subtitle",
                "ALTER TABLE scenes ADD COLUMN subtitle TEXT",
            )
            _idempotent_add_column(
                conn,
                "scenes",
                "setting",
                "ALTER TABLE scenes ADD COLUMN setting TEXT",
            )
            _idempotent_add_column(
                conn,
                "scenes",
                "arc_beats",
                "ALTER TABLE scenes ADD COLUMN arc_beats " "TEXT NOT NULL DEFAULT '[]'",
            )
            _idempotent_add_column(
                conn,
                "scenes",
                "charge_in",
                "ALTER TABLE scenes ADD COLUMN charge_in INTEGER",
            )
            _idempotent_add_column(
                conn,
                "scenes",
                "charge_out",
                "ALTER TABLE scenes ADD COLUMN charge_out INTEGER",
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS panels (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    scene_id           INTEGER NOT NULL
                                       REFERENCES scenes(id) ON DELETE CASCADE,
                    sort_order         INTEGER NOT NULL DEFAULT 0,
                    action             TEXT NOT NULL,
                    duration_s         REAL NOT NULL DEFAULT 12.0,
                    image_loras        TEXT NOT NULL DEFAULT '[]',
                    video_loras        TEXT NOT NULL DEFAULT '[]',
                    is_turn            INTEGER NOT NULL DEFAULT 0,
                    subtext            TEXT,
                    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            _idempotent_add_column(
                conn,
                "panels",
                "image_loras",
                "ALTER TABLE panels ADD COLUMN image_loras "
                "TEXT NOT NULL DEFAULT '[]'",
            )
            _idempotent_add_column(
                conn,
                "panels",
                "video_loras",
                "ALTER TABLE panels ADD COLUMN video_loras "
                "TEXT NOT NULL DEFAULT '[]'",
            )
            _idempotent_add_column(
                conn,
                "panels",
                "is_turn",
                "ALTER TABLE panels ADD COLUMN is_turn " "INTEGER NOT NULL DEFAULT 0",
            )
            _idempotent_add_column(
                conn,
                "panels",
                "subtext",
                "ALTER TABLE panels ADD COLUMN subtext TEXT",
            )
            # beats.selected_image_id references beat_images, which in turn
            # references beats -- a circular FK. SQLite allows forward
            # references in DDL as long as both tables exist before rows
            # are inserted, so creation order is beats -> beat_images
            # (mirroring the old panels -> panel_images precedent, which
            # had the same cycle).
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS beats (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    panel_id    INTEGER NOT NULL
                                REFERENCES panels(id) ON DELETE CASCADE,
                    sort_order  INTEGER NOT NULL DEFAULT 0,
                    duration_s  REAL NOT NULL DEFAULT 4.0,
                    action      TEXT NOT NULL,
                    shot_size   TEXT,
                    angle       TEXT,
                    lens        TEXT,
                    subject_ids TEXT NOT NULL DEFAULT '[]',
                    camera_motion    TEXT,
                    camera_amplitude TEXT,
                    camera_speed     TEXT,
                    is_cut      INTEGER NOT NULL DEFAULT 0,
                    dialog      TEXT NOT NULL DEFAULT '[]',
                    sound       TEXT,
                    brief       TEXT,
                    prompt      TEXT,
                    prompt_locked INTEGER NOT NULL DEFAULT 0,
                    prompt_source TEXT,
                    selected_image_id INTEGER REFERENCES beat_images(id)
                                      ON DELETE SET NULL,
                    composition TEXT,
                    light_quality TEXT,
                    emotional_intent TEXT,
                    reveals     TEXT,
                    movement_motivation TEXT,
                    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            _idempotent_add_column(
                conn,
                "beats",
                "composition",
                "ALTER TABLE beats ADD COLUMN composition TEXT",
            )
            _idempotent_add_column(
                conn,
                "beats",
                "light_quality",
                "ALTER TABLE beats ADD COLUMN light_quality TEXT",
            )
            _idempotent_add_column(
                conn,
                "beats",
                "emotional_intent",
                "ALTER TABLE beats ADD COLUMN emotional_intent TEXT",
            )
            _idempotent_add_column(
                conn,
                "beats",
                "reveals",
                "ALTER TABLE beats ADD COLUMN reveals TEXT",
            )
            _idempotent_add_column(
                conn,
                "beats",
                "movement_motivation",
                "ALTER TABLE beats ADD COLUMN movement_motivation TEXT",
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS beat_images (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    beat_id         INTEGER NOT NULL
                                    REFERENCES beats(id) ON DELETE CASCADE,
                    file_path       TEXT NOT NULL REFERENCES media(file_path)
                                    ON DELETE CASCADE,
                    seed            INTEGER,
                    variant_index   INTEGER NOT NULL DEFAULT 0,
                    prompt_used     TEXT,
                    preset_id       INTEGER REFERENCES workflow_presets(id),
                    comfy_prompt_id TEXT,
                    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                # Rendered clips are panel-scoped (one clip covers the whole
                # shot / all its beats), unlike the per-beat keyframe images
                # above. Mirrors beat_images otherwise.
                """
                CREATE TABLE IF NOT EXISTS panel_videos (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    panel_id        INTEGER NOT NULL
                                    REFERENCES panels(id) ON DELETE CASCADE,
                    file_path       TEXT NOT NULL REFERENCES media(file_path)
                                    ON DELETE CASCADE,
                    seed            INTEGER,
                    variant_index   INTEGER NOT NULL DEFAULT 0,
                    prompt_used     TEXT,
                    preset_id       INTEGER REFERENCES workflow_presets(id),
                    comfy_prompt_id TEXT,
                    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            # Image-to-video flow: which library image produced which clip.
            # No REFERENCES media(file_path) on either column by design:
            # list_i2v_videos JOINs media and lazily prunes rows whose media
            # is gone, and deleting the source image keeps the videos.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS i2v_videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_path TEXT NOT NULL,
                    file_path TEXT NOT NULL UNIQUE,
                    prompt_used TEXT,
                    idea TEXT,
                    seed INTEGER,
                    duration_s REAL,
                    quality TEXT,
                    width INTEGER,
                    height INTEGER,
                    preset_id INTEGER,
                    comfy_prompt_id TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_i2v_videos_source "
                "ON i2v_videos(source_path)"
            )
            # Dev DBs created before the resolution feature lack these.
            _idempotent_add_column(
                conn,
                "i2v_videos",
                "width",
                "ALTER TABLE i2v_videos ADD COLUMN width INTEGER",
            )
            _idempotent_add_column(
                conn,
                "i2v_videos",
                "height",
                "ALTER TABLE i2v_videos ADD COLUMN height INTEGER",
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_scenes_storyboard "
                "ON scenes(storyboard_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_panels_scene ON panels(scene_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_subjects_storyboard "
                "ON storyboard_subjects(storyboard_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_beats_panel ON beats(panel_id)"
            )
            _idempotent_add_column(
                conn,
                "storyboards",
                "outline",
                "ALTER TABLE storyboards ADD COLUMN outline TEXT",
            )
            _idempotent_add_column(
                conn,
                "storyboard_subjects",
                "voice",
                "ALTER TABLE storyboard_subjects ADD COLUMN voice TEXT",
            )
            _idempotent_add_column(
                conn,
                "storyboard_subjects",
                "reference_path_2",
                "ALTER TABLE storyboard_subjects ADD COLUMN reference_path_2 "
                "TEXT REFERENCES media(file_path) ON DELETE SET NULL",
            )
            _idempotent_add_column(
                conn,
                "storyboard_subjects",
                "sheet_ref",
                "ALTER TABLE storyboard_subjects ADD COLUMN sheet_ref "
                "INTEGER NOT NULL DEFAULT 0",
            )
            _idempotent_add_column(
                conn,
                "storyboard_subjects",
                "pov_ref",
                "ALTER TABLE storyboard_subjects ADD COLUMN pov_ref "
                "INTEGER NOT NULL DEFAULT 0",
            )
            # Spec Phase C1: only 'character' subjects are castable into a
            # beat's subject_ids (locations/props are roster entries for
            # the compiler's <Subject N> definitions, never performers).
            _idempotent_add_column(
                conn,
                "storyboard_subjects",
                "subject_type",
                "ALTER TABLE storyboard_subjects ADD COLUMN subject_type "
                "TEXT NOT NULL DEFAULT 'character'",
            )
            # Spec Phase D: the dramatic function of a scene -- the shot
            # template selection key. Nullable; existing rows stay NULL.
            _idempotent_add_column(
                conn,
                "scenes",
                "function",
                "ALTER TABLE scenes ADD COLUMN function TEXT",
            )
            # User-selected scene templates: the chosen shot-list template,
            # the compose-time brief driving it, and provenance of the last
            # compose that touched the scene. All nullable; existing rows
            # stay NULL.
            _idempotent_add_column(
                conn,
                "scenes",
                "template_id",
                "ALTER TABLE scenes ADD COLUMN template_id TEXT",
            )
            _idempotent_add_column(
                conn, "scenes", "brief", "ALTER TABLE scenes ADD COLUMN brief TEXT"
            )
            _idempotent_add_column(
                conn,
                "scenes",
                "composed_from",
                "ALTER TABLE scenes ADD COLUMN composed_from TEXT",
            )
            # Spec Phase E: template slot kind
            # (establishing/action/reaction/insert). NULL for beats the
            # beats stage composed.
            _idempotent_add_column(
                conn,
                "beats",
                "kind",
                "ALTER TABLE beats ADD COLUMN kind TEXT",
            )
            _idempotent_add_column(
                conn,
                "scenes",
                "reference_path",
                "ALTER TABLE scenes ADD COLUMN reference_path "
                "TEXT REFERENCES media(file_path) ON DELETE SET NULL",
            )
            _idempotent_add_column(
                conn,
                "storyboards",
                "video_target",
                "ALTER TABLE storyboards ADD COLUMN video_target TEXT",
            )
            _idempotent_add_column(
                conn,
                "storyboards",
                "video_mode",
                "ALTER TABLE storyboards ADD COLUMN video_mode TEXT",
            )
            _idempotent_add_column(
                conn,
                "panels",
                "video_prompt",
                "ALTER TABLE panels ADD COLUMN video_prompt TEXT",
            )
            _idempotent_add_column(
                conn,
                "panels",
                "video_prompt_locked",
                "ALTER TABLE panels ADD COLUMN video_prompt_locked "
                "INTEGER NOT NULL DEFAULT 0",
            )
            _idempotent_add_column(
                conn,
                "panels",
                "video_prompt_source",
                "ALTER TABLE panels ADD COLUMN video_prompt_source TEXT",
            )
            _idempotent_add_column(
                conn,
                "panels",
                "video_prompt_warnings",
                "ALTER TABLE panels ADD COLUMN video_prompt_warnings TEXT",
            )
            _idempotent_add_column(
                conn,
                "storyboards",
                "video_preset_id",
                "ALTER TABLE storyboards ADD COLUMN video_preset_id "
                "INTEGER REFERENCES workflow_presets(id)",
            )
            _idempotent_add_column(
                conn,
                "panels",
                "video_anchor",
                "ALTER TABLE panels ADD COLUMN video_anchor TEXT",
            )
            _idempotent_add_column(
                conn,
                "panels",
                "video_compiled_anchor",
                "ALTER TABLE panels ADD COLUMN video_compiled_anchor TEXT",
            )
            # When the panel's video_prompt was last compiled (UTC, SQLite
            # datetime('now') shape). The frontend flags "beats changed
            # since compile" when any beat's updated_at is newer.
            _idempotent_add_column(
                conn,
                "panels",
                "video_compiled_at",
                "ALTER TABLE panels ADD COLUMN video_compiled_at TEXT",
            )
            _idempotent_add_column(
                conn,
                "storyboard_subjects",
                "voice_ref_path",
                # Plain filesystem path -- audio files are not library media,
                # so unlike reference_path(/_2) this deliberately carries no
                # REFERENCES media(file_path) FK.
                "ALTER TABLE storyboard_subjects ADD COLUMN voice_ref_path TEXT",
            )
            _idempotent_add_column(
                conn,
                "storyboards",
                "notes",
                "ALTER TABLE storyboards ADD COLUMN notes TEXT",
            )
            _idempotent_add_column(
                conn,
                "generation_jobs",
                "beat_id",
                # Deliberately no REFERENCES clause -- same rationale as
                # panel_id: the release helpers delete job rows explicitly.
                "ALTER TABLE generation_jobs ADD COLUMN beat_id INTEGER",
            )
            _idempotent_add_column(
                conn,
                "storyboards",
                "video_output_dir",
                # A configured library directory (validated at the API
                # layer); rendered clips land under it instead of
                # comfy.output_root when set.
                "ALTER TABLE storyboards ADD COLUMN video_output_dir TEXT",
            )
            _idempotent_add_column(
                conn,
                "storyboards",
                "video_name_template",
                "ALTER TABLE storyboards ADD COLUMN video_name_template TEXT",
            )
            _idempotent_add_column(
                conn,
                "storyboards",
                "image_name_template",
                "ALTER TABLE storyboards ADD COLUMN image_name_template TEXT",
            )
            _idempotent_add_column(
                conn,
                "generation_jobs",
                "output_prefix",
                # Expanded (strftime) filename prefix computed by the
                # runner at submit time; collect_outputs prepends it to
                # ComfyUI's filename when writing the local copy.
                "ALTER TABLE generation_jobs ADD COLUMN output_prefix TEXT",
            )
            # i2v flow correlation. Like panel_id/beat_id: no REFERENCES
            # clause (SQLite cannot add an FK to an existing table without a
            # rebuild); cleanup is explicit in the i2v delete paths.
            _idempotent_add_column(
                conn,
                "generation_jobs",
                "i2v_source_path",
                "ALTER TABLE generation_jobs ADD COLUMN i2v_source_path TEXT",
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_beat_images_beat "
                "ON beat_images(beat_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_panel_videos_panel "
                "ON panel_videos(panel_id)"
            )

            # Data move: rendered clips used to ingest as beat_images rows
            # on the panel's first beat (the only container that existed
            # before panel_videos). Relocate them to their panel. Idempotent
            # by construction -- after the move no video-suffixed rows
            # remain in beat_images. The keeper pointer is cleared first
            # where it referenced a moved clip.
            _video_pred = (
                "(lower(file_path) LIKE '%.mp4' OR lower(file_path) "
                "LIKE '%.webm' OR lower(file_path) LIKE '%.mov')"
            )
            conn.execute(
                "UPDATE beats SET selected_image_id = NULL "
                "WHERE selected_image_id IN "
                f"(SELECT id FROM beat_images WHERE {_video_pred})"
            )
            conn.execute(
                "INSERT INTO panel_videos (panel_id, file_path, seed, "
                "variant_index, prompt_used, preset_id, comfy_prompt_id, "
                "created_at) "
                "SELECT b.panel_id, bi.file_path, bi.seed, bi.variant_index, "
                "bi.prompt_used, bi.preset_id, bi.comfy_prompt_id, "
                "bi.created_at "
                "FROM beat_images bi JOIN beats b ON b.id = bi.beat_id "
                f"WHERE {_video_pred.replace('file_path', 'bi.file_path')}"
            )
            conn.execute(f"DELETE FROM beat_images WHERE {_video_pred}")
            # Invariant enforcement, not a one-shot: a clip attached to a
            # live panel stays hidden -- the frontend surfaces it only
            # inside the storyboard's folder view. Release paths (panel /
            # storyboard delete with keep-in-library) remove the
            # panel_videos rows in the same transaction they unhide, so
            # released clips are never re-hidden here.
            conn.execute(
                "UPDATE media SET hidden = 1 WHERE file_path IN "
                "(SELECT file_path FROM panel_videos)"
            )

            # One-shot backfill: ``created_at`` previously tracked the last
            # rescan (INSERT OR REPLACE was DELETE+INSERT, firing the
            # ``DEFAULT CURRENT_TIMESTAMP`` every time). Smart-folder "Added"
            # rules now need first-ingest semantics; the upsert was fixed to
            # preserve ``created_at`` on update, but existing rows already
            # carry the collapsed rescan timestamp. Copy ``modified_at``
            # (file mtime on disk) over as a reasonable proxy — it's the
            # closest signal we have to when the row really became part of
            # the library. Gated on ``PRAGMA user_version`` so it runs once.
            # (``user_version`` was already read once, up front, in the
            # Phase B storyboard-tables section above -- nothing writes the
            # pragma in between, so it's reused here rather than re-read.)
            if user_version < 1:
                logger.info(
                    "Backfilling created_at from modified_at (one-time; "
                    "fixes 'Added' smart-folder rule collapsing onto the "
                    "last rescan date)…"
                )
                cur = conn.execute(
                    "UPDATE media SET created_at = modified_at "
                    "WHERE modified_at IS NOT NULL"
                )
                logger.info(f"created_at backfill updated {cur.rowcount} row(s).")
                conn.execute("PRAGMA user_version = 1")

            if user_version < 2:
                # Photo-EXIF support landed: orientation is now applied at
                # thumbnail-generation time. Existing thumbnails for sideways
                # iPhone photos would stay cached forever (key is
                # (path, mtime, size), and mtime hasn't changed). Wipe the
                # cache directory contents once so they regenerate correctly
                # on next view. Idempotent guard: only fire on first launch
                # with the v2 schema.
                from metascan.utils.app_paths import get_thumbnail_cache_dir

                try:
                    cache_dir = get_thumbnail_cache_dir()
                    if cache_dir.exists():
                        wiped = 0
                        for entry in cache_dir.iterdir():
                            if entry.is_file():
                                try:
                                    entry.unlink()
                                    wiped += 1
                                except OSError as exc:
                                    logger.warning(
                                        "Could not delete cached thumbnail " "%s: %s",
                                        entry,
                                        exc,
                                    )
                        logger.info(
                            "Wiped %d cached thumbnail(s) for v2 migration "
                            "(EXIF orientation handling).",
                            wiped,
                        )
                except Exception as exc:
                    logger.warning(
                        "Thumbnail cache wipe (v2 migration) failed: %s",
                        exc,
                    )
                # PRAGMA fires regardless of wipe success: a persistent failure
                # in get_thumbnail_cache_dir would otherwise re-attempt every
                # launch. The wipe is best-effort by design (spec §5).
                conn.execute("PRAGMA user_version = 2")

            if user_version < 3:
                # Writes the pragma for the shot/beat model reorganization
                # whose drop-and-recreate ran up front, in the Phase B
                # storyboard-tables section above.
                conn.execute("PRAGMA user_version = 3")

            if user_version < 4:
                # Spec Phase C1: pre-existing roster rows were all written
                # before subject_type existed. Infer location/prop from
                # the name + description where obvious; everything else
                # stays 'character' and the user corrects it in the UI.
                rows = conn.execute(
                    "SELECT id, name, description FROM storyboard_subjects"
                ).fetchall()
                for r in rows:
                    inferred = infer_subject_type(r["name"], r["description"])
                    if inferred != "character":
                        conn.execute(
                            "UPDATE storyboard_subjects SET subject_type = ? "
                            "WHERE id = ?",
                            (inferred, r["id"]),
                        )
                conn.execute("PRAGMA user_version = 4")

            conn.commit()

    @contextmanager
    def _get_connection(self):  # type: ignore[no-untyped-def]
        conn = sqlite3.connect(str(self.db_file))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")  # Better concurrency
        try:
            yield conn
        finally:
            conn.close()

    def close(self) -> None:
        pass

    @contextmanager
    def batch_writer(self):  # type: ignore[no-untyped-def]
        with self.lock:
            with self._get_connection() as conn:
                try:
                    yield conn
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    logger.error(f"Batch write failed: {e}")
                    raise

    # True upsert — preserves the row's original ``created_at`` on rescan.
    # ``INSERT OR REPLACE`` is DELETE+INSERT under the hood, which fires the
    # ``DEFAULT CURRENT_TIMESTAMP`` every time, making ``created_at`` track
    # the most recent rescan rather than the first ingest. Smart folders
    # keying off "Added" need that first-ingest semantic.
    _MEDIA_UPSERT_SQL = """
        INSERT INTO media (
            file_path, data, is_favorite, playback_speed,
            width, height, file_size, frame_rate, duration, modified_at,
            camera_make, camera_model, lens_model, datetime_original,
            gps_latitude, gps_longitude, gps_altitude, orientation,
            photo_exposure,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(file_path) DO UPDATE SET
            data = excluded.data,
            is_favorite = excluded.is_favorite,
            playback_speed = excluded.playback_speed,
            width = excluded.width,
            height = excluded.height,
            file_size = excluded.file_size,
            frame_rate = excluded.frame_rate,
            duration = excluded.duration,
            modified_at = excluded.modified_at,
            camera_make = excluded.camera_make,
            camera_model = excluded.camera_model,
            lens_model = excluded.lens_model,
            datetime_original = excluded.datetime_original,
            gps_latitude = excluded.gps_latitude,
            gps_longitude = excluded.gps_longitude,
            gps_altitude = excluded.gps_altitude,
            orientation = excluded.orientation,
            photo_exposure = excluded.photo_exposure,
            updated_at = CURRENT_TIMESTAMP
    """

    @staticmethod
    def _media_upsert_params(media: Media, posix_path: str) -> tuple:
        import json as _json

        expo_json = None
        if media.photo_exposure is not None:
            expo_json = _json.dumps(
                {
                    "shutter_speed": media.photo_exposure.shutter_speed,
                    "aperture": media.photo_exposure.aperture,
                    "iso": media.photo_exposure.iso,
                    "flash": media.photo_exposure.flash,
                    "focal_length": media.photo_exposure.focal_length,
                    "focal_length_35mm": media.photo_exposure.focal_length_35mm,
                }
            )
        return (
            posix_path,
            media.to_json(),  # type: ignore[attr-defined]
            1 if media.is_favorite else 0,
            media.playback_speed,
            media.width,
            media.height,
            media.file_size,
            media.frame_rate,
            media.duration,
            media.modified_at.isoformat() if media.modified_at else None,
            media.camera_make,
            media.camera_model,
            media.lens_model,
            media.datetime_original.isoformat() if media.datetime_original else None,
            media.gps_latitude,
            media.gps_longitude,
            media.gps_altitude,
            media.orientation,
            expo_json,
        )

    def save_media(self, media: Media) -> bool:
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Convert file_path to POSIX format for storage
                    posix_path = to_posix_path(media.file_path)
                    conn.execute(
                        self._MEDIA_UPSERT_SQL,
                        self._media_upsert_params(media, posix_path),
                    )

                    self._update_indices(conn, media)
                    conn.commit()
                    return True
        except Exception as e:
            logger.error(f"Failed to save media {media.file_path}: {e}")
            return False

    # ------------------------------------------------------------------
    # Saved-prompt CRUD (Prompt Playground — side-channel, never touches
    # the inverted `indices` table).
    # ------------------------------------------------------------------

    def save_prompt(
        self,
        *,
        file_path: str,
        name: str,
        prompt: str,
        target_model: str,
        architecture: str,
        styles: List[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
        source_prompt: Optional[str],
        mode: str,
        negative: Optional[str],
        vlm_model_id: Optional[str],
    ) -> int:
        """Insert a saved prompt; return its new auto-incremented id."""
        import json as _json

        with self.lock:
            with self._get_connection() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO saved_prompts (
                        file_path, name, prompt, negative,
                        target_model, architecture, styles,
                        temperature, max_tokens, source_prompt,
                        mode, vlm_model_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_path,
                        name,
                        prompt,
                        negative,
                        target_model,
                        architecture,
                        _json.dumps(list(styles)),
                        temperature,
                        max_tokens,
                        source_prompt,
                        mode,
                        vlm_model_id,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)

    def list_saved_prompts(self, file_path: str) -> List[Dict[str, Any]]:
        """All saved prompts for a media file_path, newest first."""
        import json as _json

        with self.lock:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM saved_prompts WHERE file_path=? " "ORDER BY id DESC",
                    (file_path,),
                ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            try:
                d["styles"] = _json.loads(d["styles"]) if d.get("styles") else []
            except (TypeError, ValueError):
                d["styles"] = []
            out.append(d)
        return out

    def get_saved_prompt(self, prompt_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single saved prompt by id, or None if not found."""
        import json as _json

        with self.lock:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM saved_prompts WHERE id=?", (prompt_id,)
                ).fetchone()
        if row is None:
            return None
        d = dict(row)
        try:
            d["styles"] = _json.loads(d["styles"]) if d.get("styles") else []
        except (TypeError, ValueError):
            d["styles"] = []
        return d

    def delete_saved_prompt(self, prompt_id: int) -> bool:
        """Delete a saved prompt by id. Return True if deleted, False if missing."""
        with self.lock:
            with self._get_connection() as conn:
                cur = conn.execute("DELETE FROM saved_prompts WHERE id=?", (prompt_id,))
                conn.commit()
                return int(cur.rowcount) > 0

    def search_saved_prompts(
        self,
        folder_id: Optional[str],
        target_model: Optional[str],
        name: Optional[str],
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Search the saved_prompts table by optional folder / model / name.

        When ``folder_id`` is supplied, the folder must be ``kind='manual'`` —
        smart folders raise ``ValueError`` because their membership rule
        engine lives in the frontend (Pinia store) and would need a Python
        port before this method could resolve them. The companion
        ``POST /api/prompt/search`` route catches the ValueError and
        returns 400.

        Returns rows in the same dict shape as ``list_saved_prompts`` so
        ``SavedPromptOut`` Pydantic parsing works unchanged (styles
        deserialized from JSON string to Python list).
        """
        import json as _json

        # Hard cap to match the API contract.
        capped_limit = max(1, min(limit, 500))

        with self.lock:
            with self._get_connection() as conn:
                if folder_id is not None:
                    row = conn.execute(
                        "SELECT kind FROM folders WHERE id = ?", (folder_id,)
                    ).fetchone()
                    if row is None:
                        raise ValueError(f"folder not found: {folder_id}")
                    if row["kind"] == "smart":
                        raise ValueError(
                            f"smart folders are not supported by search_saved_prompts "
                            f"(folder_id={folder_id}); manual folders only"
                        )

                sql_parts = ["SELECT saved_prompts.* FROM saved_prompts"]
                params: List[Any] = []
                wheres: List[str] = []

                if folder_id is not None:
                    sql_parts.append("JOIN folder_items USING (file_path)")
                    wheres.append("folder_items.folder_id = ?")
                    params.append(folder_id)

                if target_model is not None:
                    wheres.append("saved_prompts.target_model = ?")
                    params.append(target_model)

                if name is not None:
                    wheres.append("saved_prompts.name = ?")
                    params.append(name)

                if wheres:
                    sql_parts.append("WHERE " + " AND ".join(wheres))

                sql_parts.append("ORDER BY saved_prompts.created_at DESC")
                sql_parts.append("LIMIT ?")
                params.append(capped_limit)

                sql = " ".join(sql_parts)
                rows = conn.execute(sql, params).fetchall()

        # Mirror list_saved_prompts row shape: deserialize styles JSON
        # for caller convenience. (See list_saved_prompts in this file
        # for the canonical row-shaping logic.)
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            styles_raw = d.get("styles") or "[]"
            try:
                d["styles"] = _json.loads(styles_raw)
            except (ValueError, TypeError):
                d["styles"] = []
            out.append(d)
        return out

    # ---- ComfyUI workflow presets ---------------------------------------

    _JOB_UPDATABLE: ClassVar[frozenset] = frozenset(
        {"state", "comfy_prompt_id", "error", "started_at", "finished_at"}
    )

    # NOTE: the lock attribute is `self.lock` (database_sqlite.py:49), and the
    # established pattern is `with self.lock:` wrapping `with
    # self._get_connection() as conn:`. The combined form below is equivalent.

    def create_workflow_preset(
        self,
        name: str,
        kind: str,
        workflow_json: str,
        bindings: str,
        video_target: Optional[str] = None,
        video_mode: Optional[str] = None,
    ) -> int:
        with self.lock, self._get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO workflow_presets (name, kind, workflow_json, "
                "bindings, video_target, video_mode) VALUES (?, ?, ?, ?, ?, ?)",
                (name, kind, workflow_json, bindings, video_target, video_mode),
            )
            conn.commit()
            return int(cur.lastrowid)

    def get_workflow_preset(self, preset_id: int) -> Optional[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_presets WHERE id = ?", (preset_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_workflow_presets(self) -> List[Dict[str, Any]]:
        """Summary rows. Omits workflow_json — the graphs are large and no
        list view needs them."""
        with self.lock, self._get_connection() as conn:
            rows = conn.execute(
                "SELECT id, name, kind, bindings, video_target, video_mode, "
                "created_at, updated_at FROM workflow_presets ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_workflow_preset(self, preset_id: int) -> bool:
        """Delete a preset.

        Raises ``sqlite3.IntegrityError`` when generation_jobs rows still
        reference it: ``generation_jobs.preset_id`` is NOT NULL with no
        ``ON DELETE`` clause and ``PRAGMA foreign_keys = ON``. That is
        deliberate — cascading would silently destroy job history, and
        making the column nullable would orphan it. Callers should report
        the conflict; see ``count_jobs_for_preset``.
        """
        with self.lock, self._get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM workflow_presets WHERE id = ?", (preset_id,)
            )
            conn.commit()
            return int(cur.rowcount) > 0

    def count_jobs_for_preset(self, preset_id: int) -> int:
        """How many generation_jobs rows reference a preset."""
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM generation_jobs WHERE preset_id = ?",
                (preset_id,),
            ).fetchone()
            return int(row["n"]) if row else 0

    # ---- ComfyUI generation jobs ----------------------------------------

    def create_generation_job(
        self,
        preset_id: int,
        params: str,
        panel_id: Optional[int] = None,
        output_dir: Optional[str] = None,
        beat_id: Optional[int] = None,
        output_prefix: Optional[str] = None,
        i2v_source_path: Optional[str] = None,
    ) -> int:
        with self.lock, self._get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO generation_jobs (preset_id, params, panel_id, "
                "output_dir, beat_id, output_prefix, i2v_source_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    preset_id,
                    params,
                    panel_id,
                    output_dir,
                    beat_id,
                    output_prefix,
                    i2v_source_path,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def get_generation_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM generation_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_job_by_comfy_prompt_id(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM generation_jobs WHERE comfy_prompt_id = ?",
                (prompt_id,),
            ).fetchone()
            return dict(row) if row else None

    def update_generation_job(self, job_id: int, **fields: Any) -> None:
        """Update a whitelisted subset of job columns.

        The whitelist is what keeps this from becoming a SQL-injection
        surface — column names cannot be parameterized.
        """
        unknown = set(fields) - self._JOB_UPDATABLE
        if unknown:
            raise ValueError(
                f"Not updatable on generation_jobs: {', '.join(sorted(unknown))}"
            )
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [job_id]
        with self.lock, self._get_connection() as conn:
            conn.execute(
                f"UPDATE generation_jobs SET {assignments} WHERE id = ?", values
            )
            conn.commit()

    def list_generation_jobs(
        self,
        states: Optional[List[str]] = None,
        limit: int = 100,
        panel_ids: Optional[List[int]] = None,
        beat_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM generation_jobs"
        conditions: List[str] = []
        params: List[Any] = []
        if states:
            conditions.append("state IN (" + ",".join("?" * len(states)) + ")")
            params.extend(states)
        if panel_ids:
            conditions.append("panel_id IN (" + ",".join("?" * len(panel_ids)) + ")")
            params.extend(panel_ids)
        if beat_ids:
            conditions.append("beat_id IN (" + ",".join("?" * len(beat_ids)) + ")")
            params.extend(beat_ids)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY id LIMIT ?"
        params.append(limit)
        with self.lock, self._get_connection() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    # ---- Storyboards ------------------------------------------------------

    _STORYBOARD_UPDATABLE: ClassVar[frozenset] = frozenset(
        {
            "name",
            "source_text",
            "aspect_ratio",
            "style_block",
            "negative",
            "notes",
            "target_model",
            "architecture",
            "preset_id",
            "base_seed",
            "batch_size",
            "folder_id",
            "outline",
            "video_target",
            "video_mode",
            "video_preset_id",
            "video_output_dir",
            "video_name_template",
            "image_name_template",
            "pacing",
            "story_scale",
        }
    )
    _SUBJECT_UPDATABLE: ClassVar[frozenset] = frozenset(
        {
            "name",
            "description",
            "lora_name",
            "lora_strength",
            "reference_path",
            "reference_path_2",
            "sort_order",
            "voice",
            "voice_ref_path",
            "sheet_ref",
            "pov_ref",
            "subject_type",
        }
    )
    _SCENE_UPDATABLE: ClassVar[frozenset] = frozenset(
        {
            "function",
            "name",
            "sort_order",
            "subtitle",
            "setting",
            "location",
            "time_of_day",
            "mood",
            "lighting",
            "notes",
            "reference_path",
            "arc_beats",
            "charge_in",
            "charge_out",
            "template_id",
            "brief",
            "composed_from",
        }
    )
    _PANEL_UPDATABLE: ClassVar[frozenset] = frozenset(
        {
            "sort_order",
            "action",
            "duration_s",
            "image_loras",
            "video_loras",
            "video_prompt",
            "video_prompt_locked",
            "video_prompt_source",
            "video_prompt_warnings",
            "video_anchor",
            "video_compiled_anchor",
            "video_compiled_at",
            "is_turn",
            "subtext",
        }
    )
    # ``selected_image_id`` is deliberately absent -- keeper selection goes
    # through ``select_beat_image``, mirroring the old panel rule.
    _BEAT_UPDATABLE: ClassVar[frozenset] = frozenset(
        {
            "kind",
            "sort_order",
            "duration_s",
            "action",
            "shot_size",
            "angle",
            "lens",
            "subject_ids",
            "camera_motion",
            "camera_amplitude",
            "camera_speed",
            "is_cut",
            "dialog",
            "sound",
            "brief",
            "prompt",
            "prompt_locked",
            "prompt_source",
            "composition",
            "light_quality",
            "emotional_intent",
            "reveals",
            "movement_motivation",
        }
    )

    def create_storyboard(
        self,
        *,
        name: str,
        target_model: str,
        architecture: str,
        aspect_ratio: str = "16:9",
        style_block: Optional[str] = None,
        negative: Optional[str] = None,
        preset_id: Optional[int] = None,
        base_seed: int = 0,
        batch_size: int = 4,
        source_text: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> int:
        with self.lock, self._get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO storyboards (name, source_text, aspect_ratio, "
                "style_block, negative, target_model, architecture, "
                "preset_id, base_seed, batch_size, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    source_text,
                    aspect_ratio,
                    style_block,
                    negative,
                    target_model,
                    architecture,
                    preset_id,
                    base_seed,
                    batch_size,
                    notes,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def get_storyboard(self, storyboard_id: int) -> Optional[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM storyboards WHERE id = ?", (storyboard_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_storyboards(self) -> List[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM storyboards ORDER BY id").fetchall()
            return [dict(r) for r in rows]

    def update_storyboard(self, storyboard_id: int, **fields: Any) -> None:
        unknown = set(fields) - self._STORYBOARD_UPDATABLE
        if unknown:
            raise ValueError(
                f"Not updatable on storyboards: {', '.join(sorted(unknown))}"
            )
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [storyboard_id]
        with self.lock, self._get_connection() as conn:
            conn.execute(
                f"UPDATE storyboards SET {assignments}, "
                "updated_at = datetime('now') WHERE id = ?",
                values,
            )
            conn.commit()

    def delete_storyboard(
        self, storyboard_id: int, purge_images: bool = False
    ) -> Tuple[bool, List[str], Optional[str]]:
        """Delete a storyboard and everything under it, including its
        library folder (the "Storyboard: <name>" folder the runner
        created, if any -- folder_items cascade with it).

        Returns ``(deleted, purged_file_paths, deleted_folder_id)``.

        Before the cascade (storyboards -> scenes -> panels -> beats ->
        beat_images), unhides the media rows any curated beat_images
        pointed at and purges generation_jobs for the panels being
        destroyed -- see _release_panels. With ``purge_images=True`` the
        media rows are deleted instead (unless still referenced elsewhere)
        and their native-format file paths returned so the caller can
        remove the files from disk.
        """
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT folder_id FROM storyboards WHERE id = ?", (storyboard_id,)
            ).fetchone()
            folder_id: Optional[str] = row["folder_id"] if row is not None else None
            panel_ids = self._panel_ids_for_storyboard(conn, storyboard_id)
            purge_paths = self._release_panels(conn, panel_ids, purge_images)
            cur = conn.execute("DELETE FROM storyboards WHERE id = ?", (storyboard_id,))
            deleted_files = self._purge_media_rows(conn, purge_paths)
            deleted_folder: Optional[str] = None
            if folder_id is not None:
                fcur = conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
                if int(fcur.rowcount) > 0:
                    deleted_folder = folder_id
            conn.commit()
            return int(cur.rowcount) > 0, deleted_files, deleted_folder

    # ---- Storyboard subjects ------------------------------------------------

    def create_subject(
        self,
        storyboard_id: int,
        *,
        name: str,
        description: str,
        lora_name: Optional[str] = None,
        lora_strength: float = 0.8,
        reference_path: Optional[str] = None,
        reference_path_2: Optional[str] = None,
        sort_order: int = 0,
        voice: Optional[str] = None,
        voice_ref_path: Optional[str] = None,
        subject_type: str = "character",
    ) -> int:
        # storyboard_subjects.reference_path(/_2) FKs media(file_path),
        # which is always stored POSIX -- a native-style path (Windows/WSL)
        # would never match an existing row and surface as a confusing
        # sqlite3.IntegrityError higher up. voice_ref_path is a plain
        # filesystem path (no media FK -- audio files aren't library media)
        # and is stored verbatim.
        posix_reference_path = (
            to_posix_path(reference_path) if reference_path else reference_path
        )
        posix_reference_path_2 = (
            to_posix_path(reference_path_2) if reference_path_2 else reference_path_2
        )
        with self.lock, self._get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO storyboard_subjects (storyboard_id, name, "
                "description, lora_name, lora_strength, reference_path, "
                "reference_path_2, sort_order, voice, voice_ref_path, "
                "subject_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    storyboard_id,
                    name,
                    description,
                    lora_name,
                    lora_strength,
                    posix_reference_path,
                    posix_reference_path_2,
                    sort_order,
                    voice,
                    voice_ref_path,
                    subject_type,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def update_subject(self, subject_id: int, **fields: Any) -> None:
        unknown = set(fields) - self._SUBJECT_UPDATABLE
        if unknown:
            raise ValueError(
                f"Not updatable on storyboard_subjects: "
                f"{', '.join(sorted(unknown))}"
            )
        if not fields:
            return
        if fields.get("reference_path"):
            fields["reference_path"] = to_posix_path(fields["reference_path"])
        if fields.get("reference_path_2"):
            fields["reference_path_2"] = to_posix_path(fields["reference_path_2"])
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [subject_id]
        with self.lock, self._get_connection() as conn:
            conn.execute(
                f"UPDATE storyboard_subjects SET {assignments} WHERE id = ?",
                values,
            )
            conn.commit()

    def get_subject(self, subject_id: int) -> Optional[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM storyboard_subjects WHERE id = ?", (subject_id,)
            ).fetchone()
            return dict(row) if row else None

    def _subject_referencing_beats(
        self, conn: sqlite3.Connection, subject_id: int
    ) -> List[sqlite3.Row]:
        """Every beat of the subject's storyboard whose ``subject_ids``
        casts it or whose ``dialog`` has a line spoken by it. Returns the
        raw rows (id, panel_id, subject_ids, dialog) so callers can rewrite
        them in the same transaction. Text mentions are never references."""
        import json as _json

        sub = conn.execute(
            "SELECT storyboard_id FROM storyboard_subjects WHERE id = ?",
            (subject_id,),
        ).fetchone()
        if sub is None:
            return []
        rows = conn.execute(
            "SELECT b.id, b.panel_id, b.subject_ids, b.dialog FROM beats b "
            "JOIN panels p ON p.id = b.panel_id "
            "JOIN scenes s ON s.id = p.scene_id "
            "WHERE s.storyboard_id = ? ORDER BY b.id",
            (int(sub["storyboard_id"]),),
        ).fetchall()
        out: List[sqlite3.Row] = []
        for r in rows:
            try:
                ids = _json.loads(r["subject_ids"] or "[]")
            except (TypeError, ValueError):
                ids = []
            try:
                dialog = _json.loads(r["dialog"] or "[]")
            except (TypeError, ValueError):
                dialog = []
            if subject_id in ids or any(
                isinstance(d, dict) and d.get("subject_id") == subject_id
                for d in dialog
            ):
                out.append(r)
        return out

    def subject_references(self, subject_id: int) -> Dict[str, Any]:
        """``{beat_ids, image_count}`` for the beats that cast or voice the
        subject -- what a delete would touch. Read-only; the frontend uses
        it to decide whether to prompt for a deletion mode."""
        with self.lock, self._get_connection() as conn:
            rows = self._subject_referencing_beats(conn, subject_id)
            beat_ids = [int(r["id"]) for r in rows]
            image_count = 0
            if beat_ids:
                placeholders = ",".join("?" * len(beat_ids))
                image_count = int(
                    conn.execute(
                        f"SELECT COUNT(*) AS n FROM beat_images "
                        f"WHERE beat_id IN ({placeholders})",
                        beat_ids,
                    ).fetchone()["n"]
                )
            return {"beat_ids": beat_ids, "image_count": image_count}

    SUBJECT_DELETE_MODES = ("unlink", "content", "purge")

    def delete_subject(
        self, subject_id: int, mode: str = "unlink"
    ) -> Tuple[bool, List[str]]:
        """Delete a subject and every non-text reference to it.

        ``mode`` decides what happens to the beats that cast or voice it:

        * ``unlink`` -- the beats survive; the id is stripped from their
          ``subject_ids`` and dialog lines spoken by the subject keep their
          text with ``subject_id`` set to NULL. Scene/shot/beat prose is
          never touched (a name mention in text is not a reference).
        * ``content`` -- those beats are deleted (images released into the
          library, jobs purged -- see _release_beats), then any shot left
          without beats and any scene left without shots is deleted too.
        * ``purge`` -- ``content``, but the beats' generated media rows are
          deleted and their native paths returned for the caller to trash.

        Returns ``(deleted, purged_file_paths)`` -- same contract as
        delete_beat/delete_panel. Raises ValueError on an unknown mode.
        """
        if mode not in self.SUBJECT_DELETE_MODES:
            raise ValueError(
                f"unknown subject delete mode {mode!r}; "
                f"expected one of {self.SUBJECT_DELETE_MODES}"
            )
        import json as _json

        with self.lock, self._get_connection() as conn:
            rows = self._subject_referencing_beats(conn, subject_id)
            deleted_files: List[str] = []
            if mode == "unlink":
                for r in rows:
                    ids = [
                        i
                        for i in _json.loads(r["subject_ids"] or "[]")
                        if i != subject_id
                    ]
                    dialog = []
                    for d in _json.loads(r["dialog"] or "[]"):
                        if isinstance(d, dict) and d.get("subject_id") == subject_id:
                            d = {**d, "subject_id": None}
                        dialog.append(d)
                    conn.execute(
                        "UPDATE beats SET subject_ids = ?, dialog = ?, "
                        "updated_at = datetime('now') WHERE id = ?",
                        (_json.dumps(ids), _json.dumps(dialog), int(r["id"])),
                    )
            else:
                purge = mode == "purge"
                beat_ids = [int(r["id"]) for r in rows]
                panel_ids = sorted({int(r["panel_id"]) for r in rows})
                purge_paths = self._release_beats(conn, beat_ids, purge)
                if beat_ids:
                    placeholders = ",".join("?" * len(beat_ids))
                    conn.execute(
                        f"DELETE FROM beats WHERE id IN ({placeholders})", beat_ids
                    )
                empty_panels = [
                    pid
                    for pid in panel_ids
                    if conn.execute(
                        "SELECT 1 FROM beats WHERE panel_id = ? LIMIT 1", (pid,)
                    ).fetchone()
                    is None
                ]
                for pid in panel_ids:
                    if pid not in empty_panels:
                        self._sync_panel_duration(conn, pid)
                if empty_panels:
                    ph = ",".join("?" * len(empty_panels))
                    scene_ids = sorted(
                        {
                            int(r["scene_id"])
                            for r in conn.execute(
                                f"SELECT scene_id FROM panels WHERE id IN ({ph})",
                                empty_panels,
                            ).fetchall()
                        }
                    )
                    purge_paths += self._release_panels(conn, empty_panels, purge)
                    conn.execute(f"DELETE FROM panels WHERE id IN ({ph})", empty_panels)
                    for scid in scene_ids:
                        if (
                            conn.execute(
                                "SELECT 1 FROM panels WHERE scene_id = ? LIMIT 1",
                                (scid,),
                            ).fetchone()
                            is None
                        ):
                            conn.execute("DELETE FROM scenes WHERE id = ?", (scid,))
                deleted_files = self._purge_media_rows(conn, purge_paths)
            cur = conn.execute(
                "DELETE FROM storyboard_subjects WHERE id = ?", (subject_id,)
            )
            conn.commit()
            return int(cur.rowcount) > 0, deleted_files

    # ---- Scenes -------------------------------------------------------------

    def create_scene(
        self,
        storyboard_id: int,
        *,
        name: str,
        sort_order: int = 0,
        subtitle: Optional[str] = None,
        setting: Optional[str] = None,
        location: Optional[str] = None,
        time_of_day: Optional[str] = None,
        mood: Optional[str] = None,
        lighting: Optional[str] = None,
        notes: Optional[str] = None,
        reference_path: Optional[str] = None,
        function: Optional[str] = None,
        brief: Optional[str] = None,
        template_id: Optional[str] = None,
    ) -> int:
        # scenes.reference_path FKs media(file_path), which is always
        # stored POSIX -- see create_subject's identical rationale.
        posix_reference_path = (
            to_posix_path(reference_path) if reference_path else reference_path
        )
        with self.lock, self._get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO scenes (storyboard_id, sort_order, name, "
                "subtitle, setting, location, time_of_day, mood, lighting, "
                "notes, reference_path, function, brief, template_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    storyboard_id,
                    sort_order,
                    name,
                    subtitle,
                    setting,
                    location,
                    time_of_day,
                    mood,
                    lighting,
                    notes,
                    posix_reference_path,
                    function,
                    brief,
                    template_id,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def update_scene(self, scene_id: int, **fields: Any) -> None:
        unknown = set(fields) - self._SCENE_UPDATABLE
        if unknown:
            raise ValueError(f"Not updatable on scenes: {', '.join(sorted(unknown))}")
        if not fields:
            return
        if fields.get("reference_path"):
            fields["reference_path"] = to_posix_path(fields["reference_path"])
        if "arc_beats" in fields:
            import json as _json

            fields["arc_beats"] = _json.dumps(list(fields["arc_beats"] or []))
        if "composed_from" in fields:
            import json as _json

            cf = fields["composed_from"]
            fields["composed_from"] = _json.dumps(cf) if cf is not None else None
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [scene_id]
        with self.lock, self._get_connection() as conn:
            conn.execute(f"UPDATE scenes SET {assignments} WHERE id = ?", values)
            conn.commit()

    def get_scene(self, scene_id: int) -> Optional[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM scenes WHERE id = ?", (scene_id,)
            ).fetchone()
            return self._decode_scene_row(row) if row else None

    def merge_scenes(self, scene_ids: List[int]) -> int:
        """Fold two or more ADJACENT scenes into the first (by sort_order)
        and return its id. Deterministic, no VLM: descriptors come from the
        first scene, ``charge_out`` from the last, ``brief``/``notes`` are
        joined, ``arc_beats`` is the ordered union, ``template_id`` is
        cleared (the user picks again for the merged unit). The absorbed
        scenes' panels are re-parented after the survivor's own panels --
        beats, images and jobs are untouched -- then the absorbed rows are
        deleted and the storyboard's scenes resequenced 0..n-1.

        Raises ``ValueError`` on fewer than two ids, ids outside one
        storyboard, unknown ids, or non-contiguous scenes.
        """
        import json as _json
        from datetime import datetime, timezone

        from metascan.core.shot_templates import outline_hash
        from metascan.core.storyboard_story import ARC_BEAT_VALUES

        ids = [int(i) for i in scene_ids]
        if len(set(ids)) < 2:
            raise ValueError("merge needs at least two distinct scenes")
        with self.lock, self._get_connection() as conn:
            rows = [
                self._decode_scene_row(r)
                for r in conn.execute(
                    "SELECT * FROM scenes WHERE id IN (%s)" % ",".join("?" * len(ids)),
                    ids,
                ).fetchall()
            ]
            found = {r["id"] for r in rows}
            missing = [i for i in ids if i not in found]
            if missing:
                raise ValueError(f"unknown scene id(s): {missing}")
            storyboard_ids = {r["storyboard_id"] for r in rows}
            if len(storyboard_ids) != 1:
                raise ValueError("scenes to merge must belong to the same storyboard")
            storyboard_id = storyboard_ids.pop()
            ordered = sorted(rows, key=lambda r: (r["sort_order"], r["id"]))
            all_scenes = conn.execute(
                "SELECT id FROM scenes WHERE storyboard_id = ? "
                "ORDER BY sort_order, id",
                (storyboard_id,),
            ).fetchall()
            positions = [i for i, r in enumerate(all_scenes) if r["id"] in found]
            if positions[-1] - positions[0] != len(positions) - 1:
                raise ValueError("scenes to merge must be adjacent")

            first, last = ordered[0], ordered[-1]
            survivor = int(first["id"])

            def _join(key: str) -> Optional[str]:
                parts = [
                    str(r.get(key)).strip()
                    for r in ordered
                    if r.get(key) and str(r.get(key)).strip()
                ]
                return "\n\n".join(parts) if parts else None

            present = {b for r in ordered for b in (r.get("arc_beats") or [])}
            arc = [b for b in ARC_BEAT_VALUES if b in present]
            outline_row = conn.execute(
                "SELECT outline FROM storyboards WHERE id = ?", (storyboard_id,)
            ).fetchone()
            stamp = {
                "stage": "merge",
                "template_id": None,
                "outline_hash": outline_hash(
                    outline_row["outline"] if outline_row else None
                ),
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            conn.execute(
                "UPDATE scenes SET brief = ?, notes = ?, arc_beats = ?, "
                "charge_out = ?, template_id = NULL, composed_from = ? "
                "WHERE id = ?",
                (
                    _join("brief"),
                    _join("notes"),
                    _json.dumps(arc),
                    last.get("charge_out"),
                    _json.dumps(stamp),
                    survivor,
                ),
            )

            # Re-parent panels in scene order, continuing the survivor's
            # own numbering.
            next_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM panels "
                "WHERE scene_id = ?",
                (survivor,),
            ).fetchone()["n"]
            for r in ordered[1:]:
                panel_rows = conn.execute(
                    "SELECT id FROM panels WHERE scene_id = ? "
                    "ORDER BY sort_order, id",
                    (r["id"],),
                ).fetchall()
                for pr in panel_rows:
                    conn.execute(
                        "UPDATE panels SET scene_id = ?, sort_order = ? WHERE id = ?",
                        (survivor, next_order, pr["id"]),
                    )
                    next_order += 1
                conn.execute("DELETE FROM scenes WHERE id = ?", (r["id"],))

            remaining = conn.execute(
                "SELECT id FROM scenes WHERE storyboard_id = ? "
                "ORDER BY sort_order, id",
                (storyboard_id,),
            ).fetchall()
            for i, r in enumerate(remaining):
                conn.execute(
                    "UPDATE scenes SET sort_order = ? WHERE id = ?", (i, r["id"])
                )
            conn.execute(
                "UPDATE storyboards SET updated_at = datetime('now') WHERE id = ?",
                (storyboard_id,),
            )
            conn.commit()
            return survivor

    def delete_scene(
        self, scene_id: int, purge_images: bool = False
    ) -> Tuple[bool, List[str]]:
        """Delete a scene and its panels.

        Returns ``(deleted, purged_file_paths)``.

        Before the cascade (scenes -> panels -> beats -> beat_images),
        unhides the media rows any curated beat_images pointed at and
        purges generation_jobs for the panels being destroyed -- see
        _release_panels. With ``purge_images=True`` the media rows are
        deleted instead (unless still referenced elsewhere) and their
        native-format file paths returned so the caller can remove the
        files from disk.
        """
        with self.lock, self._get_connection() as conn:
            panel_ids = [
                int(r["id"])
                for r in conn.execute(
                    "SELECT id FROM panels WHERE scene_id = ?", (scene_id,)
                ).fetchall()
            ]
            purge_paths = self._release_panels(conn, panel_ids, purge_images)
            cur = conn.execute("DELETE FROM scenes WHERE id = ?", (scene_id,))
            deleted_files = self._purge_media_rows(conn, purge_paths)
            conn.commit()
            return int(cur.rowcount) > 0, deleted_files

    # ---- Panels ---------------------------------------------------------

    def create_panel(
        self,
        scene_id: int,
        *,
        action: str,
        sort_order: int = 0,
        duration_s: float = 12.0,
    ) -> int:
        with self.lock, self._get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO panels (scene_id, sort_order, action, duration_s) "
                "VALUES (?, ?, ?, ?)",
                (scene_id, sort_order, action, duration_s),
            )
            conn.commit()
            return int(cur.lastrowid)

    def update_panel(self, panel_id: int, **fields: Any) -> None:
        import json as _json

        unknown = set(fields) - self._PANEL_UPDATABLE
        if unknown:
            raise ValueError(f"Not updatable on panels: {', '.join(sorted(unknown))}")
        if not fields:
            return
        for key in ("image_loras", "video_loras"):
            if key in fields:
                fields[key] = _json.dumps(list(fields[key] or []))
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [panel_id]
        with self.lock, self._get_connection() as conn:
            conn.execute(
                f"UPDATE panels SET {assignments}, "
                "updated_at = datetime('now') WHERE id = ?",
                values,
            )
            conn.commit()

    @staticmethod
    def _decode_panel_row(row: sqlite3.Row) -> Dict[str, Any]:
        import json as _json

        d = dict(row)
        for key in ("video_prompt_warnings", "image_loras", "video_loras"):
            try:
                decoded = _json.loads(d.get(key) or "[]")
            except (ValueError, TypeError):
                decoded = []
            d[key] = decoded if isinstance(decoded, list) else []
        return d

    @staticmethod
    def _decode_scene_row(row: sqlite3.Row) -> Dict[str, Any]:
        import json as _json

        d = dict(row)
        try:
            decoded = _json.loads(d.get("arc_beats") or "[]")
        except (ValueError, TypeError):
            decoded = []
        d["arc_beats"] = decoded if isinstance(decoded, list) else []
        cf_raw = d.get("composed_from")
        try:
            cf = _json.loads(cf_raw) if cf_raw else None
        except (ValueError, TypeError):
            cf = None
        d["composed_from"] = cf if isinstance(cf, dict) else None
        return d

    def get_panel(self, panel_id: int) -> Optional[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM panels WHERE id = ?", (panel_id,)
            ).fetchone()
            return self._decode_panel_row(row) if row is not None else None

    def _release_beats(
        self,
        conn: sqlite3.Connection,
        beat_ids: List[int],
        purge_images: bool = False,
    ) -> List[str]:
        """Unhide beat_images' media rows and purge generation_jobs for
        ``beat_ids``, before those beats (and their beat_images) are
        cascade-deleted. Same transaction rule and purge contract as
        _release_panels (which now delegates the image work here)."""
        if not beat_ids:
            return []
        placeholders = ",".join("?" * len(beat_ids))
        purged: List[str] = []
        if purge_images:
            purged = [
                str(r["file_path"])
                for r in conn.execute(
                    f"SELECT DISTINCT file_path FROM beat_images "
                    f"WHERE beat_id IN ({placeholders})",
                    beat_ids,
                ).fetchall()
            ]
        else:
            conn.execute(
                f"UPDATE media SET hidden = 0 WHERE file_path IN "
                f"(SELECT file_path FROM beat_images "
                f"WHERE beat_id IN ({placeholders}))",
                beat_ids,
            )
        conn.execute(
            f"DELETE FROM generation_jobs WHERE beat_id IN ({placeholders})",
            beat_ids,
        )
        return purged

    def _release_panels(
        self,
        conn: sqlite3.Connection,
        panel_ids: List[int],
        purge_images: bool = False,
    ) -> List[str]:
        """Release the panels' beats (unhiding beat_images' media rows and
        purging beat-scoped generation_jobs via _release_beats), then purge
        the panels' own (video) generation_jobs, before those panels (and
        their beats/beat_images) are cascade-deleted.

        Must run in the same transaction as the delete/replace that
        follows -- see delete_panel / delete_scene / delete_storyboard /
        replace_storyboard_structure. Without this, beat_images cascading
        away leaves the underlying media rows permanently hidden=1 (nothing
        else ever flips them back once the panel is gone), and stale
        generation_jobs rows for now-deleted panels/beats could be
        re-adopted by a restart (``ComfyClient._rehydrate_jobs``).

        With ``purge_images=True`` the unhide is skipped; instead the
        beats' image paths (POSIX) are returned so the caller can run
        ``_purge_media_rows`` after the cascade delete. The media rows
        must not be deleted here: beat_images FKs media(file_path) with
        ON DELETE CASCADE, so removing a media row now would also take
        out any *other* beat's beat_images row for a shared file --
        and the shared-reference checks in ``_purge_media_rows`` only
        make sense once the doomed beats' own rows are gone.
        """
        if not panel_ids:
            return []
        placeholders = ",".join("?" * len(panel_ids))
        beat_ids = [
            int(r["id"])
            for r in conn.execute(
                f"SELECT id FROM beats WHERE panel_id IN ({placeholders})",
                panel_ids,
            ).fetchall()
        ]
        purged = self._release_beats(conn, beat_ids, purge_images)
        # The panels' own rendered clips (panel_videos) follow the same
        # contract as beat images: purge collects their paths for
        # _purge_media_rows, release unhides (a no-op for clips, which
        # ingest visible -- but a pre-panel_videos clip may still be
        # hidden).
        if purge_images:
            purged.extend(
                str(r["file_path"])
                for r in conn.execute(
                    f"SELECT DISTINCT file_path FROM panel_videos "
                    f"WHERE panel_id IN ({placeholders})",
                    panel_ids,
                ).fetchall()
            )
        else:
            conn.execute(
                f"UPDATE media SET hidden = 0 WHERE file_path IN "
                f"(SELECT file_path FROM panel_videos "
                f"WHERE panel_id IN ({placeholders}))",
                panel_ids,
            )
        conn.execute(
            f"DELETE FROM generation_jobs WHERE panel_id IN ({placeholders})",
            panel_ids,
        )
        return purged

    def _purge_media_rows(
        self, conn: sqlite3.Connection, posix_paths: List[str]
    ) -> List[str]:
        """Delete the media rows behind purged beat images; return the
        native-format paths actually deleted (the caller removes those
        files from disk).

        Must run after the panels'/beats' cascade delete, in the same
        transaction. A path still referenced by a surviving beat_images
        row (another beat's variant), a storyboard_subjects.reference_path,
        or a scenes.reference_path is NOT deleted -- the media FK's ON
        DELETE CASCADE / SET NULL would silently destroy that other
        beat's image row or null the subject's/scene's reference -- it is
        unhidden instead, the same release-into-the-library semantics as
        a non-purge delete. Deleting a media row cascades its indices
        and folder_items rows.
        """
        deleted: List[str] = []
        for path in posix_paths:
            still_referenced = (
                conn.execute(
                    "SELECT 1 FROM beat_images WHERE file_path = ? LIMIT 1",
                    (path,),
                ).fetchone()
                is not None
                or conn.execute(
                    "SELECT 1 FROM panel_videos WHERE file_path = ? LIMIT 1",
                    (path,),
                ).fetchone()
                is not None
                or conn.execute(
                    "SELECT 1 FROM storyboard_subjects "
                    "WHERE reference_path = ? LIMIT 1",
                    (path,),
                ).fetchone()
                is not None
                or conn.execute(
                    "SELECT 1 FROM scenes WHERE reference_path = ? LIMIT 1",
                    (path,),
                ).fetchone()
                is not None
            )
            if still_referenced:
                conn.execute("UPDATE media SET hidden = 0 WHERE file_path = ?", (path,))
            else:
                conn.execute("DELETE FROM media WHERE file_path = ?", (path,))
                deleted.append(to_native_path(path))
        return deleted

    def _panel_ids_for_storyboard(
        self, conn: sqlite3.Connection, storyboard_id: int
    ) -> List[int]:
        return [
            int(r["id"])
            for r in conn.execute(
                "SELECT p.id AS id FROM panels p "
                "JOIN scenes s ON p.scene_id = s.id "
                "WHERE s.storyboard_id = ?",
                (storyboard_id,),
            ).fetchall()
        ]

    def storyboard_id_for_panel(self, panel_id: int) -> Optional[int]:
        """Resolve a panel's storyboard id via panels -> scenes -> storyboards.

        ``get_panel`` alone doesn't carry the storyboard id, and loading the
        full tree just to find it is wasteful for the ingest hot path in
        ``StoryboardRunner._ingest_outputs`` -- this is a cheap two-JOIN
        SELECT instead.
        """
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT s.storyboard_id AS sid FROM panels p "
                "JOIN scenes s ON p.scene_id = s.id WHERE p.id = ?",
                (panel_id,),
            ).fetchone()
            return int(row["sid"]) if row is not None else None

    def panel_id_for_beat(self, beat_id: int) -> Optional[int]:
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT panel_id FROM beats WHERE id = ?", (beat_id,)
            ).fetchone()
            return int(row["panel_id"]) if row is not None else None

    def delete_panel(
        self, panel_id: int, purge_images: bool = False
    ) -> Tuple[bool, List[str]]:
        """Delete a panel, plus any generation_jobs referencing it.

        Returns ``(deleted, purged_file_paths)``.

        ``generation_jobs.panel_id`` carries no FK/cascade (see spec §4.1:
        the panels table didn't exist yet when generation_jobs was
        created), so the job rows are deleted explicitly first. Also
        unhides the media rows any curated beat_images pointed at before
        they cascade away -- see _release_panels. With
        ``purge_images=True`` the media rows are deleted instead (unless
        still referenced elsewhere) and their native-format file paths
        returned so the caller can remove the files from disk.
        """
        with self.lock, self._get_connection() as conn:
            cur = conn.execute("SELECT id FROM panels WHERE id = ?", (panel_id,))
            if cur.fetchone() is None:
                return False, []
            purge_paths = self._release_panels(conn, [panel_id], purge_images)
            conn.execute("DELETE FROM panels WHERE id = ?", (panel_id,))
            deleted_files = self._purge_media_rows(conn, purge_paths)
            conn.commit()
            return True, deleted_files

    # ---- Beats ----

    def create_beat(
        self,
        panel_id: int,
        *,
        action: str,
        sort_order: int = 0,
        duration_s: float = 4.0,
        shot_size: Optional[str] = None,
        angle: Optional[str] = None,
        lens: Optional[str] = None,
        subject_ids: Optional[List[int]] = None,
        camera_motion: Optional[str] = None,
        camera_amplitude: Optional[str] = None,
        camera_speed: Optional[str] = None,
        is_cut: int = 0,
        dialog: Optional[List[Dict[str, Any]]] = None,
        sound: Optional[str] = None,
    ) -> int:
        import json as _json

        with self.lock, self._get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO beats (panel_id, sort_order, duration_s, action, "
                "shot_size, angle, lens, subject_ids, camera_motion, "
                "camera_amplitude, camera_speed, is_cut, dialog, sound) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    panel_id,
                    sort_order,
                    duration_s,
                    action,
                    shot_size,
                    angle,
                    lens,
                    _json.dumps(list(subject_ids or [])),
                    camera_motion,
                    camera_amplitude,
                    camera_speed,
                    is_cut,
                    _json.dumps(list(dialog or [])),
                    sound,
                ),
            )
            self._sync_panel_duration(conn, panel_id)
            conn.commit()
            return int(cur.lastrowid)

    def _sync_panel_duration(self, conn: sqlite3.Connection, panel_id: int) -> None:
        """panels.duration_s is derived whenever the panel has beats: the
        sum of its beats' durations. Called (same transaction) from every
        beat mutation -- create/update/delete/replace. A beat-less panel
        keeps its stored value: that number is the beats-compose rescale
        target (``rescale_beat_durations``), not a leftover. Bumps
        panels.updated_at so detail editors resync (see the CLAUDE.md
        commit-on-change rule)."""
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(duration_s), 0) AS total "
            "FROM beats WHERE panel_id = ?",
            (panel_id,),
        ).fetchone()
        if row is None or int(row["n"]) == 0:
            return
        conn.execute(
            "UPDATE panels SET duration_s = ?, updated_at = datetime('now') "
            "WHERE id = ? AND duration_s != ?",
            (round(float(row["total"]), 1), panel_id, round(float(row["total"]), 1)),
        )

    @staticmethod
    def _decode_beat_row(row: sqlite3.Row) -> Dict[str, Any]:
        import json as _json

        d = dict(row)
        try:
            d["dialog"] = _json.loads(d["dialog"] or "[]")
        except (ValueError, TypeError):
            d["dialog"] = []
        try:
            d["subject_ids"] = _json.loads(d["subject_ids"] or "[]")
        except (ValueError, TypeError):
            d["subject_ids"] = []
        return d

    def get_beat(self, beat_id: int) -> Optional[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM beats WHERE id = ?", (beat_id,)
            ).fetchone()
            return self._decode_beat_row(row) if row is not None else None

    def list_beats(self, panel_id: int) -> List[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM beats WHERE panel_id = ? ORDER BY sort_order, id",
                (panel_id,),
            ).fetchall()
            return [self._decode_beat_row(r) for r in rows]

    def update_beat(self, beat_id: int, **fields: Any) -> None:
        import json as _json

        unknown = set(fields) - self._BEAT_UPDATABLE
        if unknown:
            raise ValueError(f"Not updatable on beats: {', '.join(sorted(unknown))}")
        if not fields:
            return
        if "dialog" in fields:
            fields["dialog"] = _json.dumps(list(fields["dialog"] or []))
        if "subject_ids" in fields:
            fields["subject_ids"] = _json.dumps(list(fields["subject_ids"] or []))
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [beat_id]
        with self.lock, self._get_connection() as conn:
            conn.execute(
                f"UPDATE beats SET {assignments}, "
                "updated_at = datetime('now') WHERE id = ?",
                values,
            )
            if "duration_s" in fields:
                row = conn.execute(
                    "SELECT panel_id FROM beats WHERE id = ?", (beat_id,)
                ).fetchone()
                if row is not None:
                    self._sync_panel_duration(conn, int(row["panel_id"]))
            conn.commit()

    def delete_beat(
        self, beat_id: int, purge_images: bool = False
    ) -> Tuple[bool, List[str]]:
        """Delete a beat, releasing (or purging) its beat_images/jobs first.

        Returns ``(deleted, purged_file_paths)`` -- same contract as
        delete_panel.
        """
        with self.lock, self._get_connection() as conn:
            cur = conn.execute(
                "SELECT id, panel_id FROM beats WHERE id = ?", (beat_id,)
            )
            row = cur.fetchone()
            if row is None:
                return False, []
            purge_paths = self._release_beats(conn, [beat_id], purge_images)
            conn.execute("DELETE FROM beats WHERE id = ?", (beat_id,))
            self._sync_panel_duration(conn, int(row["panel_id"]))
            deleted_files = self._purge_media_rows(conn, purge_paths)
            conn.commit()
            return True, deleted_files

    def replace_panel_beats(
        self, panel_id: int, beats: List[Dict[str, Any]], purge_images: bool = False
    ) -> Tuple[List[int], List[str]]:
        """Transactionally replace a panel's beats (compose stage 4).
        Releases the old beats' images/jobs first -- beats carry identity
        (keepers, locked prompts) since the shot/beat reorg. With
        ``purge_images=True`` (a user-confirmed destructive recompose) the
        old beats' image media rows are deleted via ``_purge_media_rows``
        instead of unhidden; returns ``(new_beat_ids, purged_files)`` where
        ``purged_files`` are native paths for the caller to trash. The
        panel's rendered clips (panel_videos) are untouched either way --
        clips are shot-scoped and survive a beats recompose."""
        import json as _json

        with self.lock, self._get_connection() as conn:
            old_ids = [
                int(r["id"])
                for r in conn.execute(
                    "SELECT id FROM beats WHERE panel_id = ?", (panel_id,)
                ).fetchall()
            ]
            purge_paths = self._release_beats(conn, old_ids, purge_images)
            conn.execute("DELETE FROM beats WHERE panel_id = ?", (panel_id,))
            deleted_files = (
                self._purge_media_rows(conn, purge_paths) if purge_images else []
            )
            new_ids: List[int] = []
            for i, b in enumerate(beats):
                cur = conn.execute(
                    "INSERT INTO beats (panel_id, sort_order, duration_s, "
                    "action, shot_size, angle, lens, subject_ids, "
                    "camera_motion, camera_amplitude, camera_speed, "
                    "is_cut, dialog, sound, composition, light_quality, "
                    "emotional_intent, reveals, movement_motivation, kind) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "?, ?, ?, ?)",
                    (
                        panel_id,
                        b.get("sort_order", i),
                        b.get("duration_s", 4.0),
                        b["action"],
                        b.get("shot_size"),
                        b.get("angle"),
                        b.get("lens"),
                        _json.dumps(list(b.get("subject_ids") or [])),
                        b.get("camera_motion"),
                        b.get("camera_amplitude"),
                        b.get("camera_speed"),
                        int(b.get("is_cut", 0)),
                        _json.dumps(list(b.get("dialog") or [])),
                        b.get("sound"),
                        b.get("composition"),
                        b.get("light_quality"),
                        b.get("emotional_intent"),
                        b.get("reveals"),
                        b.get("movement_motivation"),
                        b.get("kind"),
                    ),
                )
                new_ids.append(int(cur.lastrowid))
            self._sync_panel_duration(conn, panel_id)
            conn.commit()
            return new_ids, deleted_files

    # ---- Structure replace + tree read -----------------------------------

    def replace_storyboard_structure(
        self, storyboard_id: int, parsed: Dict[str, Any], purge_images: bool = False
    ) -> List[str]:
        """Destructively replace subjects/scenes/panels from a parse result.

        ``parsed`` is the validated shape from storyboard_parse: subjects
        carry name/description; panels carry only ``action`` -- subject
        assignment now lives at the beat level (via ``subject_ids`` on
        ``create_beat``/``replace_panel_beats``), so panel-level subject
        name resolution no longer applies here. One transaction.

        Before the destructive delete (which cascades scenes -> panels ->
        beats -> beat_images), unhides the media rows any curated
        beat_images pointed at and purges generation_jobs for the panels
        (and their beats) being destroyed -- see _release_panels.
        Re-parsing an existing storyboard would otherwise leave those
        media rows hidden forever. With ``purge_images=True`` (a
        user-confirmed destructive re-parse) the generated media rows are
        deleted via ``_purge_media_rows`` instead; returns the native
        paths for the caller to trash (empty when not purging).
        """
        with self.lock:
            with self._get_connection() as conn:
                panel_ids = self._panel_ids_for_storyboard(conn, storyboard_id)
                purge_paths = self._release_panels(conn, panel_ids, purge_images)
                conn.execute(
                    "DELETE FROM storyboard_subjects WHERE storyboard_id = ?",
                    (storyboard_id,),
                )
                conn.execute(
                    "DELETE FROM scenes WHERE storyboard_id = ?", (storyboard_id,)
                )
                deleted_files = (
                    self._purge_media_rows(conn, purge_paths) if purge_images else []
                )
                for i, subj in enumerate(parsed.get("subjects") or []):
                    conn.execute(
                        "INSERT INTO storyboard_subjects "
                        "(storyboard_id, name, description, sort_order) "
                        "VALUES (?, ?, ?, ?)",
                        (storyboard_id, subj["name"], subj["description"], i),
                    )
                for si, scene in enumerate(parsed.get("scenes") or []):
                    cur = conn.execute(
                        "INSERT INTO scenes (storyboard_id, sort_order, name, "
                        "location, time_of_day, mood, lighting) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            storyboard_id,
                            si,
                            scene["name"],
                            scene.get("location"),
                            scene.get("time_of_day"),
                            scene.get("mood"),
                            scene.get("lighting"),
                        ),
                    )
                    scene_id = int(cur.lastrowid)
                    for pi, panel in enumerate(scene.get("panels") or []):
                        conn.execute(
                            "INSERT INTO panels (scene_id, sort_order, action) "
                            "VALUES (?, ?, ?)",
                            (scene_id, pi, panel["action"]),
                        )
                conn.execute(
                    "UPDATE storyboards SET updated_at = datetime('now') "
                    "WHERE id = ?",
                    (storyboard_id,),
                )
                conn.commit()
                return deleted_files

    def replace_storyboard_scenes(
        self,
        storyboard_id: int,
        scenes: List[Dict[str, Any]],
        purge_images: bool = False,
    ) -> Tuple[List[int], List[str]]:
        """Destructively replace all scenes (compose stage 2); subjects are
        untouched. Releases panel media/jobs first — see _release_panels.
        With ``purge_images=True`` (a user-confirmed destructive recompose)
        the generated media rows are deleted via ``_purge_media_rows``
        instead of unhidden; returns ``(new_scene_ids, purged_files)``."""
        import json as _json

        with self.lock, self._get_connection() as conn:
            panel_ids = self._panel_ids_for_storyboard(conn, storyboard_id)
            purge_paths = self._release_panels(conn, panel_ids, purge_images)
            conn.execute("DELETE FROM scenes WHERE storyboard_id = ?", (storyboard_id,))
            deleted_files = (
                self._purge_media_rows(conn, purge_paths) if purge_images else []
            )
            new_ids: List[int] = []
            for i, sc in enumerate(scenes):
                cur = conn.execute(
                    "INSERT INTO scenes (storyboard_id, sort_order, name, "
                    "subtitle, setting, location, time_of_day, mood, "
                    "lighting, notes, arc_beats, charge_in, charge_out, "
                    "function, brief) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        storyboard_id,
                        i,
                        sc["name"],
                        sc.get("subtitle"),
                        sc.get("setting"),
                        sc.get("location"),
                        sc.get("time_of_day"),
                        sc.get("mood"),
                        sc.get("lighting"),
                        sc.get("notes"),
                        _json.dumps(list(sc.get("arc_beats") or [])),
                        sc.get("charge_in"),
                        sc.get("charge_out"),
                        sc.get("function"),
                        sc.get("brief"),
                    ),
                )
                new_ids.append(int(cur.lastrowid))
            conn.execute(
                "UPDATE storyboards SET updated_at = datetime('now') " "WHERE id = ?",
                (storyboard_id,),
            )
            conn.commit()
            return new_ids, deleted_files

    def replace_scene_panels(
        self, scene_id: int, panels: List[Dict[str, Any]], purge_images: bool = False
    ) -> Tuple[List[int], List[str]]:
        """Destructively replace one scene's panels (compose stage 3).
        ``panels`` carry ``action`` + ``duration_s`` -- framing/subject_ids
        now live at the beat level. With ``purge_images=True`` (a
        user-confirmed destructive recompose) the panels' generated media
        rows -- beat images and rendered clips -- are deleted via
        ``_purge_media_rows`` instead of unhidden; returns
        ``(new_panel_ids, purged_files)``."""
        with self.lock, self._get_connection() as conn:
            old_ids = [
                int(r["id"])
                for r in conn.execute(
                    "SELECT id FROM panels WHERE scene_id = ?", (scene_id,)
                ).fetchall()
            ]
            purge_paths = self._release_panels(conn, old_ids, purge_images)
            conn.execute("DELETE FROM panels WHERE scene_id = ?", (scene_id,))
            deleted_files = (
                self._purge_media_rows(conn, purge_paths) if purge_images else []
            )
            new_ids: List[int] = []
            for i, p in enumerate(panels):
                cur = conn.execute(
                    "INSERT INTO panels (scene_id, sort_order, action, "
                    "duration_s, is_turn, subtext) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        scene_id,
                        i,
                        p["action"],
                        p.get("duration_s", 12.0),
                        int(p.get("is_turn", 0)),
                        p.get("subtext"),
                    ),
                )
                new_ids.append(int(cur.lastrowid))
            conn.commit()
            return new_ids, deleted_files

    def get_storyboard_tree(self, storyboard_id: int) -> Optional[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            sb_row = conn.execute(
                "SELECT * FROM storyboards WHERE id = ?", (storyboard_id,)
            ).fetchone()
            if sb_row is None:
                return None
            tree = dict(sb_row)

            subjects = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM storyboard_subjects WHERE storyboard_id = ? "
                    "ORDER BY sort_order, id",
                    (storyboard_id,),
                ).fetchall()
            ]
            tree["subjects"] = subjects

            scene_rows = conn.execute(
                "SELECT * FROM scenes WHERE storyboard_id = ? "
                "ORDER BY sort_order, id",
                (storyboard_id,),
            ).fetchall()
            scenes = []
            for scene_row in scene_rows:
                scene = self._decode_scene_row(scene_row)
                panel_rows = conn.execute(
                    "SELECT * FROM panels WHERE scene_id = ? "
                    "ORDER BY sort_order, id",
                    (scene["id"],),
                ).fetchall()
                panels = []
                for panel_row in panel_rows:
                    panel = self._decode_panel_row(panel_row)
                    beats = []
                    for br in conn.execute(
                        "SELECT * FROM beats WHERE panel_id = ? "
                        "ORDER BY sort_order, id",
                        (panel["id"],),
                    ).fetchall():
                        beat = self._decode_beat_row(br)
                        beat["images"] = []
                        for ir in conn.execute(
                            "SELECT * FROM beat_images WHERE beat_id = ? "
                            "ORDER BY variant_index, id",
                            (beat["id"],),
                        ).fetchall():
                            image = dict(ir)
                            image["file_path"] = to_native_path(image["file_path"])
                            beat["images"].append(image)
                        beats.append(beat)
                    panel["beats"] = beats
                    panel["videos"] = []
                    for vr in conn.execute(
                        "SELECT * FROM panel_videos WHERE panel_id = ? "
                        "ORDER BY variant_index, id",
                        (panel["id"],),
                    ).fetchall():
                        video = dict(vr)
                        video["file_path"] = to_native_path(video["file_path"])
                        panel["videos"].append(video)
                    panels.append(panel)
                scene["panels"] = panels
                scenes.append(scene)
            tree["scenes"] = scenes
            return tree

    # ---- Beat images -----------------------------------------------------

    def create_beat_image(
        self,
        beat_id: int,
        *,
        file_path: str,
        seed: Optional[int] = None,
        variant_index: int = 0,
        prompt_used: Optional[str] = None,
        preset_id: Optional[int] = None,
        comfy_prompt_id: Optional[str] = None,
    ) -> int:
        posix_path = to_posix_path(file_path)
        with self.lock, self._get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO beat_images (beat_id, file_path, seed, "
                "variant_index, prompt_used, preset_id, comfy_prompt_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    beat_id,
                    posix_path,
                    seed,
                    variant_index,
                    prompt_used,
                    preset_id,
                    comfy_prompt_id,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_beat_images(self, beat_id: int) -> List[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM beat_images WHERE beat_id = ? "
                "ORDER BY variant_index, id",
                (beat_id,),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["file_path"] = to_native_path(d["file_path"])
                out.append(d)
            return out

    def count_beat_images(self, beat_id: int) -> int:
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM beat_images WHERE beat_id = ?",
                (beat_id,),
            ).fetchone()
            return int(row["n"]) if row else 0

    # ---- Panel videos ----------------------------------------------------

    def create_panel_video(
        self,
        panel_id: int,
        *,
        file_path: str,
        seed: Optional[int] = None,
        variant_index: int = 0,
        prompt_used: Optional[str] = None,
        preset_id: Optional[int] = None,
        comfy_prompt_id: Optional[str] = None,
    ) -> int:
        posix_path = to_posix_path(file_path)
        with self.lock, self._get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO panel_videos (panel_id, file_path, seed, "
                "variant_index, prompt_used, preset_id, comfy_prompt_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    panel_id,
                    posix_path,
                    seed,
                    variant_index,
                    prompt_used,
                    preset_id,
                    comfy_prompt_id,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_panel_videos(self, panel_id: int) -> List[Dict[str, Any]]:
        with self.lock, self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM panel_videos WHERE panel_id = ? "
                "ORDER BY variant_index, id",
                (panel_id,),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["file_path"] = to_native_path(d["file_path"])
                out.append(d)
            return out

    def count_panel_videos(self, panel_id: int) -> int:
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM panel_videos WHERE panel_id = ?",
                (panel_id,),
            ).fetchone()
            return int(row["n"]) if row else 0

    def delete_panel_video(self, video_id: int) -> Tuple[bool, List[str]]:
        """Delete one rendered clip completely: its panel_videos row, then
        its media row (indices/folder_items cascade with it) via
        _purge_media_rows' survival rules -- a file another take, subject
        reference, or scene reference still points at is unhidden instead
        of deleted. Returns ``(deleted, purged_file_paths)`` with
        native-format paths for the caller to remove from disk."""
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT file_path FROM panel_videos WHERE id = ?", (video_id,)
            ).fetchone()
            if row is None:
                return False, []
            conn.execute("DELETE FROM panel_videos WHERE id = ?", (video_id,))
            deleted_files = self._purge_media_rows(conn, [str(row["file_path"])])
            conn.commit()
            return True, deleted_files

    # ---- i2v videos ------------------------------------------------------

    def create_i2v_video(
        self,
        *,
        source_path: str,
        file_path: str,
        prompt_used: Optional[str] = None,
        idea: Optional[str] = None,
        seed: Optional[int] = None,
        duration_s: Optional[float] = None,
        quality: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        preset_id: Optional[int] = None,
        comfy_prompt_id: Optional[str] = None,
    ) -> int:
        with self.lock, self._get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO i2v_videos (source_path, file_path, prompt_used, "
                "idea, seed, duration_s, quality, width, height, preset_id, "
                "comfy_prompt_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    to_posix_path(source_path),
                    to_posix_path(file_path),
                    prompt_used,
                    idea,
                    seed,
                    duration_s,
                    quality,
                    width,
                    height,
                    preset_id,
                    comfy_prompt_id,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_i2v_videos(self, source_path: str) -> List[Dict[str, Any]]:
        """Videos generated from one source image, newest first, with
        media.is_favorite merged in. Rows whose media row no longer
        exists (deleted from the library) are pruned in the same call --
        the JOIN is the referential integrity here, by design."""
        posix = to_posix_path(source_path)
        with self.lock, self._get_connection() as conn:
            rows = conn.execute(
                "SELECT v.*, m.file_path AS media_path, m.is_favorite "
                "FROM i2v_videos v "
                "LEFT JOIN media m ON m.file_path = v.file_path "
                "WHERE v.source_path = ? ORDER BY v.id DESC",
                (posix,),
            ).fetchall()
            out: List[Dict[str, Any]] = []
            stale: List[int] = []
            for r in rows:
                d = dict(r)
                if d.pop("media_path") is None:
                    stale.append(int(d["id"]))
                    continue
                d["is_favorite"] = bool(d.get("is_favorite"))
                d["file_path"] = to_native_path(d["file_path"])
                d["source_path"] = to_native_path(d["source_path"])
                out.append(d)
            if stale:
                conn.execute(
                    "DELETE FROM i2v_videos WHERE id IN ("
                    + ",".join("?" * len(stale))
                    + ")",
                    stale,
                )
                conn.commit()
            return out

    def delete_i2v_video(self, video_id: int) -> Tuple[bool, List[str]]:
        """Delete one generated clip completely: its i2v_videos row, then
        its media row via _purge_media_rows' survival rules. Returns
        (deleted, purged_native_paths) for the caller to trash."""
        with self.lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT file_path FROM i2v_videos WHERE id = ?", (video_id,)
            ).fetchone()
            if row is None:
                return False, []
            conn.execute("DELETE FROM i2v_videos WHERE id = ?", (video_id,))
            deleted_files = self._purge_media_rows(conn, [str(row["file_path"])])
            conn.commit()
            return True, deleted_files

    def select_beat_image(self, beat_id: int, image_id: Optional[int]) -> bool:
        """Set a beat's keeper. Unhides the new keeper's media row, re-hides
        the previous keeper's. image_id=None clears the selection."""
        with self.lock:
            with self._get_connection() as conn:
                beat = conn.execute(
                    "SELECT selected_image_id FROM beats WHERE id = ?",
                    (beat_id,),
                ).fetchone()
                if beat is None:
                    return False
                new_path = None
                if image_id is not None:
                    row = conn.execute(
                        "SELECT file_path FROM beat_images "
                        "WHERE id = ? AND beat_id = ?",
                        (image_id, beat_id),
                    ).fetchone()
                    if row is None:
                        return False
                    new_path = row["file_path"]
                old_id = beat["selected_image_id"]
                if old_id is not None and old_id != image_id:
                    old = conn.execute(
                        "SELECT file_path FROM beat_images WHERE id = ?",
                        (old_id,),
                    ).fetchone()
                    if old is not None:
                        conn.execute(
                            "UPDATE media SET hidden = 1 WHERE file_path = ?",
                            (old["file_path"],),
                        )
                if new_path is not None:
                    conn.execute(
                        "UPDATE media SET hidden = 0 WHERE file_path = ?",
                        (new_path,),
                    )
                conn.execute(
                    "UPDATE beats SET selected_image_id = ?, "
                    "updated_at = datetime('now') WHERE id = ?",
                    (image_id, beat_id),
                )
                conn.commit()
                return True

    # ---- Generation-job panel lookups --------------------------------------

    def latest_jobs_for_panels(self, panel_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """Latest (max id) generation_jobs row per panel."""
        if not panel_ids:
            return {}
        placeholders = ",".join("?" * len(panel_ids))
        sql = (
            "SELECT gj.* FROM generation_jobs gj "
            "JOIN (SELECT panel_id, MAX(id) AS mid FROM generation_jobs "
            f"WHERE panel_id IN ({placeholders}) GROUP BY panel_id) m "
            "ON gj.id = m.mid"
        )
        with self.lock, self._get_connection() as conn:
            rows = conn.execute(sql, panel_ids).fetchall()
            return {int(r["panel_id"]): dict(r) for r in rows}

    def latest_jobs_for_beats(self, beat_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """Latest (max id) generation_jobs row per beat."""
        if not beat_ids:
            return {}
        placeholders = ",".join("?" * len(beat_ids))
        sql = (
            "SELECT gj.* FROM generation_jobs gj "
            "JOIN (SELECT beat_id, MAX(id) AS mid FROM generation_jobs "
            f"WHERE beat_id IN ({placeholders}) GROUP BY beat_id) m "
            "ON gj.id = m.mid"
        )
        with self.lock, self._get_connection() as conn:
            rows = conn.execute(sql, beat_ids).fetchall()
            return {int(r["beat_id"]): dict(r) for r in rows}

    def save_media_batch(self, media_list: List[Media]) -> int:
        saved_count = 0
        with self.batch_writer() as conn:
            for media in media_list:
                try:
                    # Convert file_path to POSIX format for storage
                    posix_path = to_posix_path(media.file_path)
                    conn.execute(
                        self._MEDIA_UPSERT_SQL,
                        self._media_upsert_params(media, posix_path),
                    )

                    self._update_indices(conn, media)
                    saved_count += 1
                except Exception as e:
                    logger.error(
                        f"Failed to save media in batch {media.file_path}: {e}"
                    )

        return saved_count

    def get_media(self, file_path: Path) -> Optional[Media]:
        try:
            with self._get_connection() as conn:
                # Convert to POSIX format for database lookup
                posix_path = to_posix_path(file_path)
                row = conn.execute(
                    "SELECT data FROM media WHERE file_path = ?", (posix_path,)
                ).fetchone()

                if row:
                    return Media.from_json(row["data"])  # type: ignore[attr-defined,no-any-return]
                return None
        except Exception as e:
            logger.error(f"Failed to get media {file_path}: {e}")
            return None

    def get_all_media(self) -> List[Media]:
        media_list = []
        try:
            with self._get_connection() as conn:
                rows = conn.execute("SELECT data FROM media ORDER BY created_at DESC")
                for row in rows:
                    try:
                        media = Media.from_json(row["data"])  # type: ignore[attr-defined]
                        media_list.append(media)
                    except Exception as e:
                        logger.error(f"Failed to decode media: {e}")
        except Exception as e:
            logger.error(f"Failed to get all media: {e}")

        return media_list

    def get_all_media_with_details(self) -> List[Media]:
        """Load all media with favorite status and playback speed in a single query.

        This eliminates N+1 query problems by fetching data, is_favorite, and
        playback_speed together instead of making separate queries per item.
        """
        media_list = []
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT data, is_favorite, playback_speed FROM media ORDER BY created_at DESC"
                )
                for row in rows:
                    try:
                        media = Media.from_json_fast(row["data"])
                        media.is_favorite = bool(row["is_favorite"])
                        if row["playback_speed"] is not None:
                            media.playback_speed = float(row["playback_speed"])
                        media_list.append(media)
                    except Exception as e:
                        logger.error(f"Failed to decode media: {e}")
        except Exception as e:
            logger.error(f"Failed to get all media with details: {e}")

        return media_list

    def get_all_media_summaries(
        self,
        favorites_only: bool = False,
        sort: str = "date_added",
        include_hidden: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return a per-file summary tailored for the thumbnail grid.

        All fields live on columns (see the ``_init_database`` migration
        that materializes ``width``, ``height``, ``file_size``,
        ``frame_rate``, ``duration`` and ``modified_at`` off the JSON
        blob). A projection across these narrow columns skips the 700+ MB
        of overflow pages a ``json_extract`` query would have to page in.
        """
        order_clause = {
            "date_modified": "modified_at DESC",
            # file_name sort happens in the service layer (Python basename
            # extraction) — SQLite has no cheap basename function.
        }.get(sort, "created_at DESC")
        conditions = []
        if favorites_only:
            conditions.append("is_favorite = 1")
        if not include_hidden:
            conditions.append("hidden = 0")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = (
            "SELECT file_path, is_favorite, playback_speed, "
            "width, height, file_size, frame_rate, duration, "
            "modified_at, created_at, "
            "camera_make, camera_model, datetime_original, "
            "gps_latitude, gps_longitude, orientation, hidden "
            f"FROM media {where} ORDER BY {order_clause}"
        )
        out: List[Dict[str, Any]] = []
        video_exts = {".mp4", ".webm", ".mov"}
        try:
            with self._get_connection() as conn:
                rows = conn.execute(sql).fetchall()
                for row in rows:
                    file_path = to_native_path(row["file_path"])
                    ext = Path(file_path).suffix.lower()
                    playback = row["playback_speed"]
                    out.append(
                        {
                            "file_path": file_path,
                            "is_favorite": bool(row["is_favorite"]),
                            "is_video": ext in video_exts,
                            "playback_speed": (
                                float(playback) if playback is not None else None
                            ),
                            "width": row["width"],
                            "height": row["height"],
                            "file_size": row["file_size"],
                            "frame_rate": row["frame_rate"],
                            "duration": row["duration"],
                            "modified_at": row["modified_at"],
                            "created_at": row["created_at"],
                            "camera_make": row["camera_make"],
                            "camera_model": row["camera_model"],
                            "datetime_original": row["datetime_original"],
                            "gps_latitude": row["gps_latitude"],
                            "gps_longitude": row["gps_longitude"],
                            "orientation": row["orientation"],
                            "hidden": bool(row["hidden"]),
                        }
                    )
        except Exception as e:
            logger.error(f"Failed to get media summaries: {e}")
        return out

    def get_media_with_details(self, file_path: Path) -> Optional[Media]:
        """Load a single media item with favorite status and playback speed.

        Args:
            file_path: Path to the media file

        Returns:
            Media object with is_favorite and playback_speed set, or None if not found
        """
        try:
            with self._get_connection() as conn:
                # Convert to POSIX format for database lookup
                posix_path = to_posix_path(file_path)
                row = conn.execute(
                    "SELECT data, is_favorite, playback_speed FROM media WHERE file_path = ?",
                    (posix_path,),
                ).fetchone()

                if row:
                    media = Media.from_json_fast(row["data"])
                    media.is_favorite = bool(row["is_favorite"])
                    if row["playback_speed"] is not None:
                        media.playback_speed = float(row["playback_speed"])
                    return media
                return None
        except Exception as e:
            logger.error(f"Failed to get media with details {file_path}: {e}")
            return None

    def delete_media(self, file_path: Path) -> bool:
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Convert to POSIX format for database lookup
                    posix_path = to_posix_path(file_path)
                    # Delete from media table (indices will cascade delete)
                    cursor = conn.execute(
                        "DELETE FROM media WHERE file_path = ?", (posix_path,)
                    )
                    conn.commit()
                    return cursor.rowcount > 0  # type: ignore[no-any-return]
        except Exception as e:
            logger.error(f"Failed to delete media {file_path}: {e}")
            return False

    def delete_media_batch(self, file_paths: List[Path]) -> int:
        """Delete multiple media items in a single transaction.

        This is much faster than calling delete_media() repeatedly because
        it uses a single commit for all deletes instead of one per item.

        Returns the number of items successfully deleted.
        """
        if not file_paths:
            return 0

        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Convert paths to POSIX format for database lookup
                    path_tuples = [(to_posix_path(fp),) for fp in file_paths]
                    conn.executemany(
                        "DELETE FROM media WHERE file_path = ?", path_tuples
                    )
                    conn.commit()
                    return len(file_paths)
        except Exception as e:
            logger.error(f"Failed to batch delete media: {e}")
            return 0

    def get_existing_file_paths(self) -> Set[str]:
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT file_path FROM media")
                # Convert paths from POSIX storage format to native format
                return {to_native_path(row["file_path"]) for row in cursor}
        except Exception as e:
            logger.error(f"Failed to get existing file paths: {e}")
            return set()

    def get_favorite_file_paths(self) -> List[str]:
        """Return all file paths flagged as favorite."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT file_path FROM media WHERE is_favorite = 1"
                )
                return [to_native_path(row["file_path"]) for row in cursor]
        except Exception as e:
            logger.error(f"Failed to get favorite file paths: {e}")
            return []

    def search_by_index(self, index_type: str, term: str) -> Set[str]:
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT DISTINCT file_path
                    FROM indices
                    WHERE index_type = ? AND index_key = ?
                """,
                    (index_type, term.lower()),
                )

                # Convert paths from POSIX storage format to native format
                return {to_native_path(row["file_path"]) for row in rows}
        except Exception as e:
            logger.error(f"Index search failed for {index_type}:{term}: {e}")
            return set()

    def get_tag_path_index(self, keys: List[str]) -> Dict[str, List[str]]:
        """Return ``{tag_key: [file_path, ...]}`` for the given tag keys only.

        Smart folders usually reference a handful of tags, not the whole
        universe. Fetching every tag row (``~100k+`` on real libraries) just
        to populate a client-side cache put a multi-megabyte JSON blob
        through the DB lock on every app refresh, blocking the media list
        query behind it. The caller hands us the exact keys it needs.
        """
        if not keys:
            return {}
        out: Dict[str, List[str]] = {k: [] for k in keys}
        try:
            with self._get_connection() as conn:
                placeholders = ",".join("?" for _ in keys)
                rows = conn.execute(
                    f"SELECT index_key, file_path FROM indices "
                    f"WHERE index_type = 'tag' AND index_key IN ({placeholders})",
                    list(keys),
                ).fetchall()
                for row in rows:
                    out[row["index_key"]].append(to_native_path(row["file_path"]))
        except Exception as e:
            logger.error(f"Failed to fetch tag path index: {e}")
        return out

    def get_tags_for_file(self, file_path: Path) -> List[Tuple[str, str]]:
        """Return every tag attached to ``file_path`` with its source.

        Tags are stored in ``indices`` with ``index_type='tag'`` and a
        ``source`` of ``'prompt'``, ``'clip'``, ``'vlm'``, ``'both'``
        (prompt+clip), or ``'vlm+prompt'``. Returns ``(name, source)``
        tuples so the UI can render per-source styling.
        """
        try:
            with self._get_connection() as conn:
                posix_path = to_posix_path(file_path)
                rows = conn.execute(
                    "SELECT index_key, source FROM indices "
                    "WHERE file_path = ? AND index_type = 'tag' "
                    "ORDER BY index_key",
                    (posix_path,),
                )
                return [(row["index_key"], row["source"] or "prompt") for row in rows]
        except Exception as e:
            logger.error(f"Failed to get tags for {file_path}: {e}")
            return []

    def _update_indices(self, conn: sqlite3.Connection, media: Media) -> None:
        """Refresh non-tag indices and prompt-source tag rows for ``media``.

        VLM-source tag rows survive a rescan unchanged (the embedding/VLM
        worker writes them separately via ``add_tag_indices``). Prompt-source
        rows are torn down and rebuilt from the freshly-parsed metadata.
        """
        posix_path = to_posix_path(media.file_path)

        # Drop non-tag rows (we'll rebuild them) and pure prompt-tag rows.
        # Tag rows with source IN ('clip', 'vlm', 'both', 'vlm+prompt') are
        # preserved across this DELETE — but the 'both' / 'vlm+prompt' rows
        # need their prompt half rewritten below.
        conn.execute(
            "DELETE FROM indices WHERE file_path = ? AND "
            "(index_type != 'tag' OR source IS NULL OR source = 'prompt')",
            (posix_path,),
        )
        # Demote 'both' (clip+prompt) → 'clip' so the upsert below can
        # promote back to 'both' for tags the new prompt still contains.
        conn.execute(
            "UPDATE indices SET source = 'clip' "
            "WHERE file_path = ? AND index_type = 'tag' AND source = 'both'",
            (posix_path,),
        )
        # Demote 'vlm+prompt' → 'vlm' for the same reason.
        conn.execute(
            "UPDATE indices SET source = 'vlm' "
            "WHERE file_path = ? AND index_type = 'tag' AND source = 'vlm+prompt'",
            (posix_path,),
        )

        non_tag_rows: List[tuple] = []
        prompt_tag_keys: List[str] = []
        for index_type, index_key, source in self._generate_indices(media):
            if index_type == "tag" and source == "prompt":
                prompt_tag_keys.append(index_key)
            else:
                non_tag_rows.append((index_type, index_key, posix_path, source))

        if non_tag_rows:
            conn.executemany(
                "INSERT INTO indices (index_type, index_key, file_path, source) "
                "VALUES (?, ?, ?, ?)",
                non_tag_rows,
            )
        # Re-insert prompt-source tags. Collisions promote to the merged
        # source name (both / vlm+prompt) per the same case ladder used in
        # _add_prompt_tags.
        for key in prompt_tag_keys:
            conn.execute(
                "INSERT INTO indices (index_type, index_key, file_path, source) "
                "VALUES ('tag', ?, ?, 'prompt') "
                "ON CONFLICT(index_type, index_key, file_path) DO UPDATE SET "
                "source = CASE "
                "  WHEN indices.source = 'clip' THEN 'both' "
                "  WHEN indices.source = 'both' THEN 'both' "
                "  WHEN indices.source = 'vlm' THEN 'vlm+prompt' "
                "  WHEN indices.source = 'vlm+prompt' THEN 'vlm+prompt' "
                "  ELSE 'prompt' END",
                (key, posix_path),
            )

    def add_tag_indices(
        self, file_path: Path, tags: List[str], source: str = "clip"
    ) -> None:
        """Add or merge tag rows sourced from ``source`` for ``file_path``.

        Sources:
          - ``prompt`` — extracted from generation metadata.
          - ``clip``   — CLIP retrieval over the vocabulary.
          - ``vlm``    — generative tagger (Qwen3-VL).

        Merge rules (spec §7.4):
          - vlm × clip → preserve vlm (CLIP cannot overwrite VLM).
          - clip × vlm → replace clip rows with vlm.
          - vlm × prompt or prompt × vlm → upsert to ``vlm+prompt``.
          - clip × prompt or prompt × clip → upsert to ``both`` (legacy name
            for ``clip+prompt``).
          - vlm × vlm → wholesale replace existing vlm tags.
        """
        if source not in ("prompt", "clip", "vlm"):
            raise ValueError(
                f"tag source must be one of prompt/clip/vlm, got {source!r}"
            )
        if not tags:
            return
        posix_path = to_posix_path(file_path)

        try:
            with self.lock:
                with self._get_connection() as conn:
                    if source == "vlm":
                        self._add_vlm_tags(conn, posix_path, tags)
                    elif source == "clip":
                        self._add_clip_tags(conn, posix_path, tags)
                    else:  # prompt
                        self._add_prompt_tags(conn, posix_path, tags)
                    conn.commit()
        except Exception as e:
            logger.error(f"Failed to add {source} tag indices for {file_path}: {e}")

    def _add_vlm_tags(
        self, conn: sqlite3.Connection, posix_path: str, tags: List[str]
    ) -> None:
        """Replace clip-source tags wholesale; merge with prompt-source.

        VLM is the authoritative tagger when it runs, so existing
        clip / clip+prompt rows are downgraded — clip rows are deleted, and
        the prompt half of ``both`` rows is preserved by demoting to prompt.
        Existing vlm / vlm+prompt rows are wholesale replaced (re-tag).
        """
        conn.execute(
            "DELETE FROM indices WHERE file_path=? AND index_type='tag' "
            "AND source IN ('vlm', 'vlm+prompt')",
            (posix_path,),
        )
        conn.execute(
            "DELETE FROM indices WHERE file_path=? AND index_type='tag' "
            "AND source='clip'",
            (posix_path,),
        )
        conn.execute(
            "UPDATE indices SET source='prompt' "
            "WHERE file_path=? AND index_type='tag' AND source='both'",
            (posix_path,),
        )
        for t in tags:
            if not t:
                continue
            conn.execute(
                "INSERT INTO indices (index_type, index_key, file_path, source) "
                "VALUES ('tag', ?, ?, 'vlm') "
                "ON CONFLICT(index_type, index_key, file_path) DO UPDATE SET "
                "source = CASE "
                "  WHEN indices.source = 'prompt' THEN 'vlm+prompt' "
                "  WHEN indices.source = 'vlm+prompt' THEN 'vlm+prompt' "
                "  ELSE excluded.source END",
                (t.lower(), posix_path),
            )

    def _add_clip_tags(
        self, conn: sqlite3.Connection, posix_path: str, tags: List[str]
    ) -> None:
        """Insert clip-source tags. Skipped if any vlm row exists for this
        file (vlm wins). Merges with prompt-source rows to ``both``."""
        row = conn.execute(
            "SELECT 1 FROM indices WHERE file_path=? AND index_type='tag' "
            "AND source IN ('vlm', 'vlm+prompt') LIMIT 1",
            (posix_path,),
        ).fetchone()
        if row is not None:
            return
        for t in tags:
            if not t:
                continue
            conn.execute(
                "INSERT INTO indices (index_type, index_key, file_path, source) "
                "VALUES ('tag', ?, ?, 'clip') "
                "ON CONFLICT(index_type, index_key, file_path) DO UPDATE SET "
                "source = CASE "
                "  WHEN indices.source = 'prompt' THEN 'both' "
                "  WHEN indices.source = 'both' THEN 'both' "
                "  ELSE excluded.source END",
                (t.lower(), posix_path),
            )

    def _add_prompt_tags(
        self, conn: sqlite3.Connection, posix_path: str, tags: List[str]
    ) -> None:
        """Insert prompt-source tags. Merges with both clip and vlm rows."""
        for t in tags:
            if not t:
                continue
            conn.execute(
                "INSERT INTO indices (index_type, index_key, file_path, source) "
                "VALUES ('tag', ?, ?, 'prompt') "
                "ON CONFLICT(index_type, index_key, file_path) DO UPDATE SET "
                "source = CASE "
                "  WHEN indices.source = 'clip' THEN 'both' "
                "  WHEN indices.source = 'both' THEN 'both' "
                "  WHEN indices.source = 'vlm' THEN 'vlm+prompt' "
                "  WHEN indices.source = 'vlm+prompt' THEN 'vlm+prompt' "
                "  ELSE excluded.source END",
                (t.lower(), posix_path),
            )

    def _generate_indices(self, media: Media) -> List[tuple]:
        """Returns ``(index_type, index_key, source)`` triples. ``source`` is
        non-NULL only for tag rows — see the ``indices.source`` column.
        Callers writing non-tag rows should pass ``source=None``."""
        indices: List[tuple] = []

        if media.metadata_source:
            indices.append(("source", media.metadata_source.lower(), None))

        # Add index for each model in the list
        if media.model:
            for model_name in media.model:
                if model_name:  # Skip empty strings
                    indices.append(("model", model_name.lower(), None))

        indices.append(("ext", media.file_extension, None))

        if media.camera_make:
            indices.append(("camera_make", media.camera_make.strip().lower(), None))
        if media.camera_model:
            indices.append(("camera_model", media.camera_model.strip().lower(), None))
        if media.gps_latitude is not None and media.gps_longitude is not None:
            indices.append(("has_gps", "yes", None))

        # Add reverse index for the fully qualified file path (in POSIX format)
        path_str = to_posix_path(media.file_path).lower()
        indices.append(("path", path_str, None))

        # Tag rows coming from media.tags are sourced from the prompt
        # tokenizer (see scanner.py). CLIP-sourced tags are written later
        # by the embedding worker via add_tag_indices().
        for tag in media.tags:
            indices.append(("tag", tag.lower(), "prompt"))

        if media.prompt:
            filtered_words = self.prompt_tokenizer.tokenize(media.prompt)
            for word in filtered_words:
                indices.append(("prompt", word, None))

        for lora in media.loras:
            indices.append(("lora", lora.lora_name.lower(), None))

        return indices

    def get_filter_data(
        self, sort_order: str = "count"
    ) -> Dict[str, List[Dict[str, Any]]]:
        filter_data: Dict[str, List[Dict[str, Any]]] = {}
        try:
            with self._get_connection() as conn:
                # Choose sort order
                if sort_order == "alphabetical":
                    order_clause = "ORDER BY index_type, index_key ASC"
                else:  # default to count
                    order_clause = "ORDER BY index_type, count DESC, index_key"

                query = f"""
                    SELECT index_type, index_key, COUNT(*) as count
                    FROM indices
                    GROUP BY index_type, index_key
                    {order_clause}
                """

                rows = conn.execute(query).fetchall()

                for row in rows:
                    index_type = row["index_type"]
                    if index_type not in filter_data:
                        filter_data[index_type] = []

                    filter_data[index_type].append(
                        {"key": row["index_key"], "count": row["count"]}
                    )
        except Exception as e:
            logger.error(f"Failed to get filter data: {e}")
        return filter_data

    def get_filtered_media_paths(self, filters: Dict[str, List[str]]) -> Set[str]:
        if not filters:
            return set()

        try:
            with self._get_connection() as conn:
                # Start with all media paths
                result_set = None

                for index_type, index_keys in filters.items():
                    if index_keys:  # Skip empty lists
                        # Get paths matching this filter type
                        if index_type == "path":
                            # Special handling for path filters - use LIKE for prefix matching
                            # Convert path to POSIX format for database query
                            path_prefix = to_posix_path(index_keys[0]).lower()
                            query = """
                                SELECT DISTINCT file_path
                                FROM indices
                                WHERE index_type = 'path' AND index_key LIKE ?
                            """
                            rows = conn.execute(query, [f"{path_prefix}%"])
                        else:
                            # Regular filter handling for other types
                            # Multiple values within same type use OR logic
                            placeholders = ",".join(["?" for _ in index_keys])
                            query = f"""
                                SELECT DISTINCT file_path
                                FROM indices
                                WHERE index_type = ? AND index_key IN ({placeholders})
                            """
                            params = [index_type] + list(index_keys)
                            rows = conn.execute(query, params)

                        # Get paths for this filter (convert from POSIX to native)
                        current_paths = {
                            to_native_path(row["file_path"]) for row in rows
                        }

                        # Apply AND logic between different filter types
                        if result_set is None:
                            result_set = current_paths
                        else:
                            result_set = result_set.intersection(current_paths)

                        # Early exit if no matches
                        if not result_set:
                            return set()

                return result_set if result_set is not None else set()
        except Exception as e:
            logger.error(f"Failed to get filtered media paths: {e}")
            return set()

    def toggle_favorite(self, file_path: Path) -> bool:
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Convert to POSIX format for database lookup
                    posix_path = to_posix_path(file_path)
                    # Get current favorite status
                    row = conn.execute(
                        "SELECT is_favorite FROM media WHERE file_path = ?",
                        (posix_path,),
                    ).fetchone()

                    if row is not None:
                        new_status = 0 if row["is_favorite"] else 1
                        conn.execute(
                            """
                            UPDATE media
                            SET is_favorite = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE file_path = ?
                        """,
                            (new_status, posix_path),
                        )
                        conn.commit()
                        return bool(new_status)
                    return False
        except Exception as e:
            logger.error(f"Failed to toggle favorite for {file_path}: {e}")
            return False

    def set_favorite(self, file_path: Path, is_favorite: bool) -> bool:
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Convert to POSIX format for database lookup
                    posix_path = to_posix_path(file_path)
                    cursor = conn.execute(
                        """
                        UPDATE media
                        SET is_favorite = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE file_path = ?
                    """,
                        (1 if is_favorite else 0, posix_path),
                    )
                    conn.commit()
                    return cursor.rowcount > 0  # type: ignore[no-any-return]
        except Exception as e:
            logger.error(f"Failed to set favorite for {file_path}: {e}")
            return False

    def set_media_hidden(self, file_path: str, hidden: bool) -> bool:
        """Hide/unhide one media row from the default grid query."""
        try:
            with self.lock:
                with self._get_connection() as conn:
                    cur = conn.execute(
                        "UPDATE media SET hidden = ? WHERE file_path = ?",
                        (1 if hidden else 0, to_posix_path(file_path)),
                    )
                    conn.commit()
                    return cur.rowcount > 0  # type: ignore[no-any-return]
        except Exception as e:
            logger.error(f"Failed to set hidden for {file_path}: {e}")
            return False

    def get_favorite_media_paths(self) -> Set[str]:
        try:
            with self._get_connection() as conn:
                rows = conn.execute("SELECT file_path FROM media WHERE is_favorite = 1")
                # Convert paths from POSIX storage format to native format
                return {to_native_path(row["file_path"]) for row in rows}
        except Exception as e:
            logger.error(f"Failed to get favorite media paths: {e}")
            return set()

    def load_favorite_status(self, media_list: List[Media]) -> None:
        try:
            with self._get_connection() as conn:
                for media in media_list:
                    # Convert to POSIX format for database lookup
                    posix_path = to_posix_path(media.file_path)
                    row = conn.execute(
                        "SELECT is_favorite FROM media WHERE file_path = ?",
                        (posix_path,),
                    ).fetchone()
                    if row:
                        media.is_favorite = bool(row["is_favorite"])
        except Exception as e:
            logger.error(f"Failed to load favorite status: {e}")

    def load_playback_speed(self, media_list: List[Media]) -> None:
        """Load playback_speed for a list of media objects from the database."""
        try:
            with self._get_connection() as conn:
                for media in media_list:
                    # Convert to POSIX format for database lookup
                    posix_path = to_posix_path(media.file_path)
                    row = conn.execute(
                        "SELECT playback_speed FROM media WHERE file_path = ?",
                        (posix_path,),
                    ).fetchone()
                    if row and row["playback_speed"] is not None:
                        media.playback_speed = float(row["playback_speed"])
        except Exception as e:
            logger.error(f"Failed to load playback speed: {e}")

    def update_playback_speed(self, file_path: Path, speed: float) -> bool:
        """Update the playback speed for a specific media file."""
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Convert to POSIX format for database lookup
                    posix_path = to_posix_path(file_path)
                    conn.execute(
                        """
                        UPDATE media
                        SET playback_speed = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE file_path = ?
                    """,
                        (speed, posix_path),
                    )
                    conn.commit()
                    return True
        except Exception as e:
            logger.error(f"Failed to update playback speed for {file_path}: {e}")
            return False

    def update_media_dimensions(self, file_path: Path, width: int, height: int) -> bool:
        """
        Update the dimensions of a media file in the database.

        Args:
            file_path: Path to the media file
            width: New width in pixels
            height: New height in pixels

        Returns:
            True if updated successfully, False otherwise
        """
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Get the current media record
                    media = self.get_media(file_path)
                    if not media:
                        return False

                    # Update the dimensions in the media object
                    media.width = width
                    media.height = height

                    # Update the database record (use POSIX format for query)
                    posix_path = to_posix_path(file_path)
                    conn.execute(
                        """UPDATE media SET data = ? WHERE file_path = ?""",
                        (media.to_json(), posix_path),  # type: ignore[attr-defined]
                    )

                    conn.commit()
                    return True

        except Exception as e:
            logger.error(f"Error updating media dimensions for {file_path}: {e}")
            return False

    def update_media_technical_metadata(
        self,
        file_path: Path,
        width: int,
        height: int,
        file_size: int,
        modified_at: datetime,
        created_at: Optional[datetime] = None,
        frame_rate: Optional[float] = None,
        duration: Optional[float] = None,
    ) -> bool:
        """
        Update only the technical metadata (dimensions, file size, timestamps, video properties)
        of a media file. Preserves all AI generation metadata (prompts, models, seeds, etc.).

        This is used after upscaling to update the file's physical properties while keeping
        the original AI generation parameters intact.

        Args:
            file_path: Path to the media file
            width: New width in pixels
            height: New height in pixels
            file_size: New file size in bytes
            modified_at: New modification timestamp
            created_at: New creation timestamp (optional, preserves original if not provided)
            frame_rate: New frame rate for videos (optional)
            duration: New duration for videos (optional)

        Returns:
            True if updated successfully, False otherwise
        """
        try:
            with self.lock:
                with self._get_connection() as conn:
                    # Get the current media record (with all AI metadata)
                    media = self.get_media(file_path)
                    if not media:
                        logger.warning(f"Media not found in database: {file_path}")
                        return False

                    # Update technical fields, preserve all AI metadata
                    media.width = width
                    media.height = height
                    media.file_size = file_size
                    media.modified_at = modified_at

                    # Update created_at only if provided
                    if created_at is not None:
                        media.created_at = created_at

                    # Update video-specific properties if provided
                    if frame_rate is not None:
                        media.frame_rate = frame_rate
                    if duration is not None:
                        media.duration = duration

                    # All AI metadata fields remain unchanged:
                    # - prompt, negative_prompt, model, sampler, scheduler
                    # - steps, cfg_scale, seed
                    # - loras, tags, generation_data
                    # - metadata_source

                    # Update the database record (use POSIX format for query)
                    posix_path = to_posix_path(file_path)
                    conn.execute(
                        """UPDATE media SET data = ?, updated_at = CURRENT_TIMESTAMP WHERE file_path = ?""",
                        (media.to_json(), posix_path),  # type: ignore[attr-defined]
                    )

                    conn.commit()

                    # Build log message
                    log_parts = [f"{width}x{height}", f"{file_size} bytes"]
                    if frame_rate is not None:
                        log_parts.append(f"{frame_rate:.2f} fps")
                    if duration is not None:
                        log_parts.append(f"{duration:.2f}s")

                    logger.info(
                        f"Updated technical metadata for {file_path.name}: "
                        f"{', '.join(log_parts)}"
                    )
                    return True

        except Exception as e:
            logger.error(f"Error updating technical metadata for {file_path}: {e}")
            return False

    # ── Similarity / embedding methods ──────────────────────────────

    def save_media_hash(
        self, file_path: Path, phash: Optional[str], clip_model: Optional[str] = None
    ) -> bool:
        """Save a perceptual hash for a media file."""
        try:
            with self.lock:
                with self._get_connection() as conn:
                    posix_path = to_posix_path(file_path)
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO media_hashes
                            (file_path, phash, clip_model, has_embedding, embedding_updated_at)
                        VALUES (?, ?, ?, 0, NULL)
                    """,
                        (posix_path, phash, clip_model),
                    )
                    conn.commit()
                    return True
        except Exception as e:
            logger.error(f"Failed to save media hash for {file_path}: {e}")
            return False

    def save_media_hash_batch(self, items: List[tuple]) -> int:
        """Save perceptual hashes in batch.

        Each item is a tuple of (file_path: Path, phash: str).
        """
        saved = 0
        try:
            with self.lock:
                with self._get_connection() as conn:
                    for file_path, phash in items:
                        try:
                            posix_path = to_posix_path(file_path)
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO media_hashes
                                    (file_path, phash, clip_model, has_embedding, embedding_updated_at)
                                VALUES (?, ?, NULL, 0, NULL)
                            """,
                                (posix_path, phash),
                            )
                            saved += 1
                        except Exception as e:
                            logger.error(f"Failed to save hash for {file_path}: {e}")
                    conn.commit()
        except Exception as e:
            logger.error(f"Failed to save media hash batch: {e}")
        return saved

    def get_all_phashes(self) -> Dict[str, str]:
        """Return dict of {file_path (native): phash_hex} for all files with a pHash."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT file_path, phash FROM media_hashes WHERE phash IS NOT NULL"
                )
                return {to_native_path(row["file_path"]): row["phash"] for row in rows}
        except Exception as e:
            logger.error(f"Failed to get all phashes: {e}")
            return {}

    def get_unembedded_file_paths(self) -> List[str]:
        """Get file paths that exist in media but don't have CLIP embeddings."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT m.file_path FROM media m
                    LEFT JOIN media_hashes mh ON m.file_path = mh.file_path
                    WHERE mh.has_embedding IS NULL OR mh.has_embedding = 0
                """
                )
                return [to_native_path(row["file_path"]) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get unembedded file paths: {e}")
            return []

    def mark_embedded(self, file_paths: List[str], clip_model: str) -> int:
        """Mark files as having CLIP embeddings in the index."""
        marked = 0
        try:
            with self.lock:
                with self._get_connection() as conn:
                    for fp in file_paths:
                        posix_path = to_posix_path(fp)
                        conn.execute(
                            """
                            INSERT INTO media_hashes (file_path, clip_model, has_embedding, embedding_updated_at)
                            VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                            ON CONFLICT(file_path) DO UPDATE SET
                                clip_model = excluded.clip_model,
                                has_embedding = 1,
                                embedding_updated_at = CURRENT_TIMESTAMP
                        """,
                            (posix_path, clip_model),
                        )
                        marked += 1
                    conn.commit()
        except Exception as e:
            logger.error(f"Failed to mark files as embedded: {e}")
        return marked

    def mark_embedding_skipped(self, file_paths: List[str]) -> int:
        """Mark files as permanently skipped for embedding (e.g. corrupt, unreadable).

        Sets has_embedding = -1 so get_unembedded_file_paths() excludes them.
        A full rebuild (clear_embeddings) resets these back to 0 for retry.
        """
        if not file_paths:
            return 0
        marked = 0
        try:
            with self.lock:
                with self._get_connection() as conn:
                    for fp in file_paths:
                        posix_path = to_posix_path(fp)
                        conn.execute(
                            """
                            INSERT INTO media_hashes (file_path, has_embedding)
                            VALUES (?, -1)
                            ON CONFLICT(file_path) DO UPDATE SET
                                has_embedding = -1
                        """,
                            (posix_path,),
                        )
                        marked += 1
                    conn.commit()
        except Exception as e:
            logger.error(f"Failed to mark files as skipped: {e}")
        return marked

    def clear_embeddings(self) -> bool:
        """Reset all has_embedding flags (used when CLIP model changes)."""
        try:
            with self.lock:
                with self._get_connection() as conn:
                    conn.execute(
                        "UPDATE media_hashes SET has_embedding = 0, clip_model = NULL, embedding_updated_at = NULL"
                    )
                    conn.commit()
                    return True
        except Exception as e:
            logger.error(f"Failed to clear embeddings: {e}")
            return False

    def get_embedding_stats(self) -> Dict[str, Any]:
        """Get statistics about embedding coverage."""
        try:
            with self._get_connection() as conn:
                total = conn.execute("SELECT COUNT(*) as c FROM media").fetchone()["c"]
                hashed = conn.execute(
                    "SELECT COUNT(*) as c FROM media_hashes WHERE phash IS NOT NULL"
                ).fetchone()["c"]
                embedded = conn.execute(
                    "SELECT COUNT(*) as c FROM media_hashes WHERE has_embedding = 1"
                ).fetchone()["c"]
                model_row = conn.execute(
                    "SELECT clip_model FROM media_hashes WHERE clip_model IS NOT NULL LIMIT 1"
                ).fetchone()
                return {
                    "total_media": total,
                    "hashed": hashed,
                    "embedded": embedded,
                    "clip_model": model_row["clip_model"] if model_row else None,
                }
        except Exception as e:
            logger.error(f"Failed to get embedding stats: {e}")
            return {
                "total_media": 0,
                "hashed": 0,
                "embedded": 0,
                "clip_model": None,
            }

    def get_stats(self) -> Dict[str, Any]:
        try:
            with self._get_connection() as conn:
                total_media = conn.execute(
                    "SELECT COUNT(*) as count FROM media"
                ).fetchone()["count"]

                sources = {}
                rows = conn.execute(
                    """
                    SELECT json_extract(data, '$.metadata_source') as source, COUNT(*) as count
                    FROM media
                    GROUP BY source
                """
                )
                for row in rows:
                    source = row["source"] or "unknown"
                    sources[source] = row["count"]

                db_size = self.db_file.stat().st_size if self.db_file.exists() else 0

                return {
                    "total_media": total_media,
                    "by_source": sources,
                    "db_size_bytes": db_size,
                }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {"total_media": 0, "by_source": {}, "db_size_bytes": 0}

    def truncate_all_data(self) -> bool:
        try:
            with self.lock:
                # First, delete data in a transaction
                with self._get_connection() as conn:
                    # Delete all data from both tables
                    conn.execute("DELETE FROM indices")
                    conn.execute("DELETE FROM media")

                    try:
                        conn.execute("DELETE FROM sqlite_sequence WHERE name='media'")
                        conn.execute("DELETE FROM sqlite_sequence WHERE name='indices'")
                    except Exception:
                        # sqlite_sequence doesn't exist if no auto-increment columns are used
                        pass

                    conn.commit()

                with self._get_connection() as conn:
                    conn.execute("VACUUM")

                logger.info("Successfully truncated all database data")
                return True
        except Exception as e:
            logger.error(f"Failed to truncate database: {e}")
            return False

    # ------------------------------------------------------------------
    # Folders (manual + smart)
    # ------------------------------------------------------------------
    #
    # Records use the exact shape the frontend already produces so the
    # Pinia store can swap its persistence layer without reshaping the
    # rest of the UI. ``rules`` is stored as a JSON string on disk and
    # surfaced as a dict; ``items`` is resolved from folder_items on read
    # (manual folders only). ``count`` is materialized server-side so the
    # sidebar row count doesn't need a separate round-trip.

    @staticmethod
    def _row_to_folder(
        row: sqlite3.Row,
        items: Optional[List[str]] = None,
        count: int = 0,
    ) -> Dict[str, Any]:
        kind = row["kind"]
        record: Dict[str, Any] = {
            "id": row["id"],
            "kind": kind,
            "name": row["name"],
            "icon": row["icon"],
            "sort_order": row["sort_order"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "count": count,
        }
        if kind == "smart":
            import json as _json

            rules_raw = row["rules"] or ""
            try:
                record["rules"] = _json.loads(rules_raw) if rules_raw else None
            except Exception:
                record["rules"] = None
        else:
            record["items"] = items if items is not None else []
        return record

    def list_folders(self) -> List[Dict[str, Any]]:
        """Return all folders as a list of records.

        Manual folders carry an ``items`` list; smart folders carry a
        ``rules`` dict. Both carry a materialized ``count`` — for manual
        it's the number of items, for smart it's left at 0 (membership
        is computed client-side against in-memory Media; hydrating it
        server-side would require applying the rule engine here too).
        """
        with self._get_connection() as conn:
            folders = conn.execute(
                "SELECT id, kind, name, icon, rules, sort_order, "
                "created_at, updated_at "
                "FROM folders ORDER BY kind, sort_order, created_at"
            ).fetchall()
            if not folders:
                return []
            # Pre-fetch items per manual folder in one pass.
            items_by_folder: Dict[str, List[str]] = {}
            counts: Dict[str, int] = {}
            manual_ids = [f["id"] for f in folders if f["kind"] == "manual"]
            if manual_ids:
                placeholders = ",".join("?" for _ in manual_ids)
                rows = conn.execute(
                    "SELECT folder_id, file_path FROM folder_items "
                    f"WHERE folder_id IN ({placeholders}) "
                    "ORDER BY folder_id, added_at",
                    manual_ids,
                ).fetchall()
                for r in rows:
                    fid = r["folder_id"]
                    items_by_folder.setdefault(fid, []).append(
                        to_native_path(r["file_path"])
                    )
                for fid, paths in items_by_folder.items():
                    counts[fid] = len(paths)
            return [
                self._row_to_folder(
                    f,
                    items=(
                        items_by_folder.get(f["id"], [])
                        if f["kind"] == "manual"
                        else None
                    ),
                    count=counts.get(f["id"], 0),
                )
                for f in folders
            ]

    def get_folder(self, folder_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT id, kind, name, icon, rules, sort_order, "
                "created_at, updated_at FROM folders WHERE id = ?",
                (folder_id,),
            ).fetchone()
            if row is None:
                return None
            items: Optional[List[str]] = None
            count = 0
            if row["kind"] == "manual":
                paths = conn.execute(
                    "SELECT file_path FROM folder_items "
                    "WHERE folder_id = ? ORDER BY added_at",
                    (folder_id,),
                ).fetchall()
                items = [to_native_path(p["file_path"]) for p in paths]
                count = len(items)
            return self._row_to_folder(row, items=items, count=count)

    def create_folder(
        self,
        folder_id: str,
        kind: str,
        name: str,
        icon: str = "pi-folder",
        rules: Optional[Dict[str, Any]] = None,
        items: Optional[List[str]] = None,
        sort_order: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Create a folder and return the normalized record.

        Returns None if the insert conflicts (duplicate id). Callers
        should treat that as a client bug — ids are UUIDs generated by
        the frontend.
        """
        if kind not in ("manual", "smart"):
            raise ValueError(f"invalid folder kind: {kind!r}")
        import json as _json
        import time as _time

        rules_blob = _json.dumps(rules) if kind == "smart" and rules else None
        now = _time.time()
        try:
            with self.lock:
                with self._get_connection() as conn:
                    conn.execute(
                        "INSERT INTO folders "
                        "(id, kind, name, icon, rules, sort_order, "
                        " created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            folder_id,
                            kind,
                            name,
                            icon,
                            rules_blob,
                            sort_order,
                            now,
                            now,
                        ),
                    )
                    if kind == "manual" and items:
                        unique = list(dict.fromkeys(items))
                        conn.executemany(
                            "INSERT OR IGNORE INTO folder_items "
                            "(folder_id, file_path, added_at) "
                            "VALUES (?, ?, ?)",
                            [(folder_id, to_posix_path(Path(p)), now) for p in unique],
                        )
                    conn.commit()
        except sqlite3.IntegrityError as e:
            logger.error(f"Failed to create folder {folder_id}: {e}")
            return None
        return self.get_folder(folder_id)

    def update_folder(
        self,
        folder_id: str,
        name: Optional[str] = None,
        icon: Optional[str] = None,
        rules: Optional[Dict[str, Any]] = None,
        sort_order: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Patch a folder record. Only fields passed as non-None are written.

        Returns the updated record, or None if the folder doesn't exist.
        Passing ``rules`` on a manual folder is silently ignored.
        """
        sets: List[str] = []
        params: List[Any] = []
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if icon is not None:
            sets.append("icon = ?")
            params.append(icon)
        if sort_order is not None:
            sets.append("sort_order = ?")
            params.append(int(sort_order))
        if rules is not None:
            import json as _json

            sets.append("rules = ?")
            params.append(_json.dumps(rules))
        if not sets:
            # No-op patch: still bump updated_at so the WS broadcast is
            # truthful about "something changed".
            return self.get_folder(folder_id)
        import time as _time

        sets.append("updated_at = ?")
        params.append(_time.time())
        params.append(folder_id)
        with self.lock:
            with self._get_connection() as conn:
                cur = conn.execute(
                    f"UPDATE folders SET {', '.join(sets)} WHERE id = ?",
                    params,
                )
                conn.commit()
                if cur.rowcount == 0:
                    return None
        return self.get_folder(folder_id)

    def delete_folder(self, folder_id: str) -> bool:
        with self.lock:
            with self._get_connection() as conn:
                cur = conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
                conn.commit()
                return int(cur.rowcount) > 0

    def add_folder_items(self, folder_id: str, paths: List[str]) -> Optional[int]:
        """Add paths to a manual folder. Returns the number actually added
        (dedupes against existing membership), or None if the folder
        doesn't exist or is a smart folder.
        """
        if not paths:
            return 0
        import time as _time

        with self.lock:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT kind FROM folders WHERE id = ?", (folder_id,)
                ).fetchone()
                if row is None or row["kind"] != "manual":
                    return None
                now = _time.time()
                unique = list(dict.fromkeys(paths))
                before = conn.execute(
                    "SELECT COUNT(*) AS n FROM folder_items " "WHERE folder_id = ?",
                    (folder_id,),
                ).fetchone()["n"]
                conn.executemany(
                    "INSERT OR IGNORE INTO folder_items "
                    "(folder_id, file_path, added_at) VALUES (?, ?, ?)",
                    [(folder_id, to_posix_path(Path(p)), now) for p in unique],
                )
                after = conn.execute(
                    "SELECT COUNT(*) AS n FROM folder_items " "WHERE folder_id = ?",
                    (folder_id,),
                ).fetchone()["n"]
                if after != before:
                    conn.execute(
                        "UPDATE folders SET updated_at = ? WHERE id = ?",
                        (now, folder_id),
                    )
                conn.commit()
                return int(after - before)

    def remove_folder_items(self, folder_id: str, paths: List[str]) -> Optional[int]:
        """Remove paths from a manual folder. Returns the number actually
        removed, or None if the folder doesn't exist or is a smart folder.
        """
        if not paths:
            return 0
        import time as _time

        with self.lock:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT kind FROM folders WHERE id = ?", (folder_id,)
                ).fetchone()
                if row is None or row["kind"] != "manual":
                    return None
                posix_paths = [to_posix_path(Path(p)) for p in paths]
                placeholders = ",".join("?" for _ in posix_paths)
                cur = conn.execute(
                    f"DELETE FROM folder_items WHERE folder_id = ? "
                    f"AND file_path IN ({placeholders})",
                    [folder_id, *posix_paths],
                )
                removed = int(cur.rowcount)
                if removed:
                    conn.execute(
                        "UPDATE folders SET updated_at = ? WHERE id = ?",
                        (_time.time(), folder_id),
                    )
                conn.commit()
                return removed
