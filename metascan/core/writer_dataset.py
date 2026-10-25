"""Training data for the prompt-writer LoRA (taste learning project 4).

Turns the owner's generation lineage and grades into three example kinds
for fine-tuning the 8B writer. Everything is built locally and NEVER logged
(counts only); the window's preview is the one place example text leaves
this module.

Labels
------
Only an image's grade trains the writer (spec 2026-10-24-pass-fail-grades):
✓ ``pass`` ("the prompt did what I wanted") and ✗ ``fail``. A ★ favorite, a
view (``seen_at``) or the absence of a grade teaches nothing, so the raw
rows (``DatabaseManager.writer_dataset_raw``) do not even carry them.

Example kinds
-------------
* **Variant preference pairs** (``PrefExample``, ``source="variant"``):
  within a T2I ``variant_group`` (one caption, one target model, one set of
  seeds, several prompts), a *winner* variant -- at least one of its images
  is ✓ -- against a *loser* variant -- every one of its images is ✗. Every
  winner x loser combination is a pair (``chosen`` = the winner's rendered
  prompt, ``rejected`` = the loser's). Any other variant (one ungraded
  image is enough, viewed or not) is neither.
* **Edit pairs** (``source="edit"``): a rendered prompt with ``edited = 1``
  whose whitespace-normalized text differs from its draft, once at least one
  image of it is ✓ (an edit with no ✓ render was an experiment). ``chosen``
  = the rendered (edited) prompt, ``rejected`` = the writer's draft. Each edit
  yields one ``PrefExample`` (DPO) AND one ``SftExample`` (SFT target),
  counted once (``edit_pairs``). Several seeds of one edited variant are one
  edit (deduplicated on target model + description + direction + both
  normalized texts); one ✓ among them makes it count.
* **Hindsight examples** (``SftExample``, ``source="hindsight"``): a ✓
  image with a known prompt and a brief -- a live image's from
  ``hindsight_briefs``, a deleted one's from its tombstone (copied at
  deletion, or written later by the briefs job from its archived
  thumbnail). User turn = the brief (no direction), target = the prompt
  that produced the image. A T2I image's target model is its ``model``
  column; a library item (no T2I row; deleted, its tombstone keeps the
  metadata prompt and model names) needs a metadata prompt and a model
  name that ``taste_examples.matches_model_family`` places in exactly one
  ``MODEL_PROFILES`` family (none or several: left out). A brief always
  comes from the very row whose prompt it is paired with, and one path
  has one target: a live image (T2I first, then library) wins; a deleted
  one counts only while no live file holds its path, the newest tombstone
  of the path winning.

Inclusion rules (all kinds)
---------------------------
* Images only: I2V clips are never read, and every path must pass
  ``taste_examples.is_image_path``.
* Scorecard images (``t2i_images.scorecard_id`` not NULL) are dropped
  before anything else -- a loser variant that is a scorecard image simply
  is not a candidate.
* ``writer_id = 'manual'`` is excluded everywhere: a pair with a manual
  side, a manual edit (there is no writer draft to prefer against) and a
  manual image as hindsight target.
* ``writer_id = 'fallback'`` (the resolved caption with stock pieces, not
  something a writer wrote) is never a target: never ``chosen`` of a pair,
  never a hindsight target. It may be ``rejected`` (a loser variant, or the
  draft an edit replaced). Every other writer counts (``base``,
  ``base+taste``, ``adapter:<id>``); two sides that share a ``writer_id`` +
  ``writer_model`` are fine.
* The target model must be a ``MODEL_PROFILES`` id and the description
  (below) known; a pair whose two prompts normalize equal, or whose sides
  disagree on model, description or direction, is skipped.
* Deleted generations count: a path with no live media row is read from its
  newest ``media_tombstones`` row whose lineage carries the ``t2i`` row
  (grade, ``judged_at`` and category NAMES as at deletion; a category scope
  matches them by the category's current name). A live row always wins.
  Deleted ✓ images keep their hindsight too (see above).
  Malformed tombstones are skipped with one WARNING carrying the count.
* Scope: ``"all"`` keeps every example; ``"<category id>"`` keeps only
  examples whose images (the union over both variants of a pair, every seed
  of an edit, the one hindsight image) carry that category, from any link
  source.
* An example's ``as_of`` is the newest of its images' ``created_at`` and
  ``judged_at`` (when the grade was last set or cleared; for a library item
  its ``judged_at`` alone), as UTC ISO-8601 -- never a view, a ★ or the
  brief's own time, which say nothing about the owner's taste, so browsing
  never makes old data "new". Examples with ``as_of`` after ``now`` are
  left out, so a run that records ``data_cutoff = now`` (or
  ``counts.date_span[1]``) counts everything later as new.

Prompts (exactly as the T2I runner sends them)
----------------------------------------------
``system`` and ``user`` come from ``t2i_prompt.compose_guided_t2i_prompts``
with the row's target-model profile, the REQUIRED ``content_mode`` (the
T2I config's; recorded in ``DatasetCounts.content_mode`` for the adapter's
meta), the row's stored ``direction`` (NULL: no DIRECTION block) and NO
angle or taste examples -- an adapter writes without an angle (the runner
passes none for an adapter writer), so a pair compares two outputs for the
very same request.

The user turn's ``DESCRIPTION`` is the caption as the writer saw it:
``t2i_images.resolved_caption`` when stored (Random steps store their own;
a supplied drafted step stores the lineage its ``prompts[]`` entry carried
from Generate Prompt, a hand-back frame or the tile's row, so a seed that
moved on since cannot change it); for an older row, the caption
itself when it has no ``__TOKEN__``s, else the injected ``resolve(caption,
prompt_seed, model)`` (``caption_resolver`` builds the real one from the
T2I library snapshot); a row with tokens and no ``prompt_seed`` or resolver
is left out. A hindsight user turn's DESCRIPTION is the brief.

For scope ``all`` the user turn also carries ``CATEGORIES: a, b``
(``t2i_prompt.add_categories_line``) naming ``t2i_images.prompt_categories``
-- the category names the prompt was written for, stored at ingest, which
is exactly the line the runner sends when an ``all``-scope adapter writes.
Older rows fall back to the image's ``generation`` category links
(tombstones keep no link source: their stored names stand in). A
one-category scope has no line, at training and at inference.

JSONL schema (one JSON object per line, UTF-8; what the training worker
reads)
----------------------------------------------------------------------
* SFT file (``sft.jsonl``)::

    {"system": str, "user": str, "chosen": str, "target_model": str,
     "categories": [str], "source": "edit" | "hindsight",
     "group": str}

* Preference file (``prefs.jsonl``) and holdout file (``holdout.jsonl``,
  same shape)::

    {"system": str, "user": str, "chosen": str, "rejected": str,
     "target_model": str, "categories": [str],
     "source": "variant" | "edit", "group": str}

``chosen`` is the assistant turn to learn (SFT) or prefer (DPO);
``rejected`` the dispreferred one. ``categories`` are the generation
categories in the CATEGORIES line (scope ``all``) or ``[]``. ``group`` is
the holdout key: ``holdout_key(target_model, description)`` of the request
the example answers (a hindsight example of a T2I image uses the image's
caption description, not the brief, so it shares its pairs' group).

Holdout
-------
``split_holdout`` holds out ``fraction`` of the groups that have a variant
pair by a stable sha256 rank of the group key (order- and set-independent,
so a "Continue from" run never holds out a group its source trained on;
at least one when there are two or more, never all), and with them every preference example of those groups (edit
pairs included). Grouping by (target model, resolved caption) rather than
``variant_group`` keeps a caption generated twice on one side.
``sft_without_holdout`` drops SFT examples of held-out groups, so no
held-out prompt is ever trained on. Holdout agreement = the fraction of
held-out pairs whose chosen log-prob beats the rejected one.

Readiness
---------
Trainable with >= 100 preference + edit pairs. "Enough new" when >= 200
pairs have ``as_of`` after the newest ``ready`` / ``untested`` T2I adapter
of the same scope's ``data_cutoff`` (an untested adapter is a finished run
too: counting only ``ready`` ones would call the data just trained on
"new"); with no such adapter every pair is new and "enough new" follows
"trainable".
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

from metascan.core.judgments import (
    GRADES,
    WRITER_FALLBACK,
    WRITER_MANUAL,
    normalize_prompt,
)
from metascan.core.prompt_store import PromptStore
from metascan.core.t2i_characters import Library, has_tokens, resolve_caption
from metascan.core.t2i_models import MODEL_PROFILES, T2iModelProfile, effective_identity
from metascan.core.t2i_prompt import add_categories_line, compose_guided_t2i_prompts
from metascan.core.taste_examples import is_image_path, matches_model_family
from metascan.core.writer_adapters import STATUS_READY, STATUS_UNTESTED

logger = logging.getLogger(__name__)

SCOPE_ALL = "all"
MIN_TRAINABLE_PAIRS = 100
ENOUGH_NEW_PAIRS = 200
DEFAULT_HOLDOUT_FRACTION = 0.1
PREVIEW_N = 10

SOURCE_VARIANT = "variant"
SOURCE_EDIT = "edit"
SOURCE_HINDSIGHT = "hindsight"

_CUTOFF_STATUSES = (STATUS_READY, STATUS_UNTESTED)

# (caption, prompt_seed, target model id) -> the resolved caption, or None.
CaptionResolver = Callable[[str, int, str], Optional[str]]


@dataclass
class SftExample:
    system: str
    user: str
    chosen: str
    target_model: str
    categories: List[str]
    source: str  # "edit" | "hindsight"
    group: str
    as_of: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("as_of")
        return d


@dataclass
class PrefExample:
    system: str
    user: str
    chosen: str
    rejected: str
    target_model: str
    categories: List[str]
    source: str  # "variant" | "edit"
    group: str
    as_of: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("as_of")
        return d


@dataclass
class DatasetCounts:
    pref_pairs: int
    edit_pairs: int
    hindsight: int
    by_target_model: Dict[str, int] = field(default_factory=dict)
    new_since_last: int = 0
    date_span: Tuple[str, str] = ("", "")
    # The content mode the system prompts were composed with (the adapter's
    # meta.json records it: inference must use the same one).
    content_mode: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["date_span"] = list(self.date_span)
        return d


Example = Union[SftExample, PrefExample]


# ---- small pure helpers ---------------------------------------------------


def holdout_key(target_model: str, description: str) -> str:
    """The holdout group of a request: target model + description."""
    raw = f"{target_model}\n{description}".encode("utf-8", "surrogatepass")
    return hashlib.sha1(raw).hexdigest()[:16]


def caption_resolver(
    library: Library, identity_overrides: Mapping[str, str]
) -> CaptionResolver:
    """The real ``resolve`` for :func:`build_examples`: resolves a caption
    the way the T2I runner does (``resolve_caption`` in the target model's
    effective identity style, ``identity_overrides`` = the T2I config's
    ``identity``) against one library snapshot (``T2iRunner.library.get()``).
    An unknown model or a resolving error gives None (the row is skipped).
    Lists edited since the image was made can make an old row resolve
    differently; new rows store ``resolved_caption`` and never need this."""

    def resolve(caption: str, seed: int, model: str) -> Optional[str]:
        profile = MODEL_PROFILES.get(model)
        if profile is None:
            return None
        try:
            identity = effective_identity(profile, identity_overrides)
            return resolve_caption(caption, seed, identity, library).text
        except Exception:
            return None

    return resolve


def _utc_iso(ts: Any) -> Optional[str]:
    """A stored timestamp (SQLite ``datetime('now')`` or ISO-8601) as UTC
    ISO-8601, or None when blank/unparseable."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _newest(values: Iterable[Any]) -> str:
    stamps = [s for s in (_utc_iso(v) for v in values) if s]
    return max(stamps) if stamps else ""


def _grade(row: Mapping[str, Any]) -> Optional[str]:
    """The image's grade, ✓ ``"pass"`` / ✗ ``"fail"``, else None: the only
    label there is (★ favorites and views never train the writer)."""
    grade = row.get("grade")
    return grade if grade in GRADES else None


def _blank(text: Any) -> bool:
    return not isinstance(text, str) or not text.strip()


def _profile(model: Any) -> Optional[T2iModelProfile]:
    return MODEL_PROFILES.get(model) if isinstance(model, str) else None


def _usable(row: Dict[str, Any]) -> bool:
    """A generation that may feed any example kind."""
    return (
        row.get("scorecard_id") is None
        and row.get("writer_id") != WRITER_MANUAL
        and is_image_path(str(row.get("file_path") or ""))
    )


def _row_stamps(row: Dict[str, Any]) -> List[Any]:
    """The times that can make an example new: made, and graded / cleared."""
    return [row.get("created_at"), row.get("judged_at")]


def _direction(row: Dict[str, Any]) -> Optional[str]:
    text = row.get("direction")
    return text if isinstance(text, str) and text.strip() else None


def _scope_categories(rows: Iterable[Dict[str, Any]]) -> Tuple[Set[int], Set[str]]:
    """Every category (any link source) of ``rows``: ids and lowered names."""
    ids: Set[int] = set()
    names: Set[str] = set()
    for r in rows:
        ids.update(int(i) for i in r.get("category_ids") or [])
        names.update(str(n).lower() for n in r.get("category_names") or [])
    return ids, names


def _line_categories(rows: Iterable[Dict[str, Any]]) -> List[str]:
    """The CATEGORIES names of ``rows``: each row's stored
    ``prompt_categories`` (exactly what its prompt was written for), else
    its generation-source category links."""
    names: Dict[str, str] = {}
    for r in rows:
        stored = r.get("prompt_categories")
        source = stored if isinstance(stored, list) else r.get("generation_categories")
        for n in source or []:
            clean = " ".join(str(n).split())
            if clean:
                names.setdefault(clean.lower(), clean)
    return [names[k] for k in sorted(names)]


def parse_scope(scope: Union[str, int], categories: Dict[int, str]) -> Optional[int]:
    """None for ``"all"``, else the category id; ValueError when unknown."""
    if isinstance(scope, str) and scope.strip().lower() == SCOPE_ALL:
        return None
    try:
        cid = int(str(scope).strip())
    except ValueError:
        raise ValueError(f"unknown scope {scope!r}") from None
    if cid not in categories:
        raise ValueError(f"unknown scope {scope!r}")
    return cid


def _hindsight_source(
    raw: Mapping[str, Any], by_path: Mapping[str, Dict[str, Any]], path: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """``(T2I row, library row)`` -- at most one of them -- that is the
    hindsight candidate of ``path``: a live T2I row, else a live library
    image, else the newest deleted row of the path, and a deleted row only
    while no live file holds the path (a file restored or new there is the
    image hindsight speaks of now, so a brief is never paired with another
    image's prompt)."""
    gen = by_path.get(path)
    lib = (raw.get("library") or {}).get(path)
    if gen is not None and gen.get("live"):
        return gen, None
    if lib is not None and lib.get("live"):
        return None, lib
    if gen is not None and gen.get("path_live"):
        gen = None
    if gen is not None and lib is not None:
        newer_lib = int(lib["tombstone_id"]) > int(gen["tombstone_id"])
        return (None, lib) if newer_lib else (gen, None)
    return gen, lib


def _hindsight_target(
    raw: Mapping[str, Any],
    by_path: Mapping[str, Dict[str, Any]],
    path: str,
    describe: Optional["_Describer"] = None,
) -> Optional[Tuple[T2iModelProfile, Any, Dict[str, Any], List[Any], Optional[str]]]:
    """Whether ``path`` may be a hindsight target, and its parts:
    ``(profile, prompt, source row, timestamp stamps, group description)``.
    The ONE place the rule lives -- the dataset builder and the briefs job
    (which decides what to write a brief for) both call it. The source row
    is ``_hindsight_source``'s, live or deleted; then: ✓ (grade ``pass``;
    ★ is no grade), image, not manual / fallback / scorecard, a known
    prompt and exactly one target-model family."""
    if not is_image_path(path):
        return None
    gen, lib = _hindsight_source(raw, by_path, path)
    group_text: Optional[str] = None
    if gen is not None:
        if (
            _grade(gen) != "pass"
            or not _usable(gen)
            or gen.get("writer_id") == WRITER_FALLBACK
        ):
            return None
        profile = _profile(gen.get("model"))
        prompt = gen.get("prompt_used")
        group_text = describe(gen) if describe is not None else None
        source_row: Dict[str, Any] = gen
        stamps: List[Any] = _row_stamps(gen)
    else:
        if lib is None or _grade(lib) != "pass":
            return None
        families = [
            pid
            for pid in MODEL_PROFILES
            if matches_model_family(lib.get("model_names") or [], pid)
        ]
        profile = MODEL_PROFILES[families[0]] if len(families) == 1 else None
        prompt = lib.get("prompt")
        source_row = lib
        stamps = [lib.get("judged_at")]
    if profile is None or _blank(prompt):
        return None
    return profile, prompt, source_row, stamps, group_text


def _brief_of(
    briefs: Mapping[str, Mapping[str, Any]], path: str, row: Mapping[str, Any]
) -> Optional[str]:
    """The brief of a hindsight source row: a live image's from
    ``hindsight_briefs``, a deleted one's from its own tombstone."""
    brief = (
        (briefs.get(path) or {}).get("brief") if row.get("live") else row.get("brief")
    )
    return None if _blank(brief) else str(brief)


def _briefed_paths(raw: Mapping[str, Any]) -> List[str]:
    """Sorted paths that have a brief: live images' (``briefs``) and the
    deleted rows' own."""
    paths = set(raw.get("briefs") or {})
    deleted = list(raw.get("generations") or []) + [
        dict(row, file_path=path) for path, row in (raw.get("library") or {}).items()
    ]
    paths.update(
        str(r["file_path"])
        for r in deleted
        if not r.get("live") and not _blank(r.get("brief"))
    )
    return sorted(paths)


@dataclass(frozen=True)
class BriefTarget:
    """One image the hindsight-briefs job briefs (or already has a brief
    for). A live one's brief goes to ``hindsight_briefs`` by ``path``; a
    deleted one's (``tombstone_id`` set) onto its tombstone, written from
    its archived thumbnail (``thumb_path``, relative to the data dir)."""

    path: str
    briefed: bool
    tombstone_id: Optional[int] = None
    thumb_path: Optional[str] = None


def hindsight_brief_targets(raw: Mapping[str, Any]) -> List[BriefTarget]:
    """Every hindsight target of
    ``DatabaseManager.writer_dataset_raw(candidates=True)``, sorted by
    path: live ones, briefed or not, and deleted ones that have a brief or
    an archived thumbnail to write one from (a deleted image with neither
    can never be briefed)."""
    by_path = {g["file_path"]: g for g in raw.get("generations") or []}
    briefs: Mapping[str, Mapping[str, Any]] = raw.get("briefs") or {}
    targets: List[BriefTarget] = []
    for path in sorted(set(by_path) | set(raw.get("library") or {})):
        target = _hindsight_target(raw, by_path, path)
        if target is None:
            continue
        row = target[2]
        if row.get("live"):
            targets.append(BriefTarget(path, briefed=path in briefs))
        elif _brief_of(briefs, path, row) is not None:
            targets.append(BriefTarget(path, True, int(row["tombstone_id"])))
        elif row.get("thumb_path"):
            targets.append(
                BriefTarget(path, False, int(row["tombstone_id"]), row["thumb_path"])
            )
    return targets


def hindsight_candidates(raw: Mapping[str, Any]) -> List[str]:
    """Sorted paths a hindsight brief may be written for (or already has
    one), from ``DatabaseManager.writer_dataset_raw(candidates=True)``."""
    return [t.path for t in hindsight_brief_targets(raw)]


class _Describer:
    """The DESCRIPTION of a T2I row (see the module docstring), cached."""

    def __init__(self, resolve: Optional[CaptionResolver]) -> None:
        self.resolve = resolve
        self._cache: Dict[Tuple[str, int, str], Optional[str]] = {}

    def __call__(self, row: Dict[str, Any]) -> Optional[str]:
        stored = row.get("resolved_caption")
        if not _blank(stored):
            return str(stored)
        caption = row.get("caption")
        if _blank(caption):
            return None
        caption = str(caption)
        if not has_tokens(caption):
            return caption
        seed, model = row.get("prompt_seed"), row.get("model")
        if self.resolve is None or seed is None or not isinstance(model, str):
            return None
        key = (caption, int(seed), model)
        if key not in self._cache:
            try:
                text = self.resolve(caption, int(seed), model)
            except Exception:
                text = None
            self._cache[key] = None if _blank(text) else text
        return self._cache[key]


class _Composer:
    """System/user turns exactly as the T2I writer composes them."""

    def __init__(
        self, store: PromptStore, content_mode: str, scope_id: Optional[int]
    ) -> None:
        self.store = store
        self.content_mode = content_mode
        self.scope_id = scope_id

    def turns(
        self,
        profile: T2iModelProfile,
        description: str,
        direction: Optional[str],
        categories: List[str],
    ) -> Tuple[str, str, List[str]]:
        """``(system, user, categories shown)``."""
        composed = compose_guided_t2i_prompts(
            profile, description, self.content_mode, direction, store=self.store
        )
        if self.scope_id is not None:
            return composed.system, composed.user, []
        return (
            composed.system,
            add_categories_line(composed.user, categories),
            categories,
        )


# ---- the builder ----------------------------------------------------------


def examples_from_raw(
    raw: Dict[str, Any],
    scope: Union[str, int],
    store: PromptStore,
    now: datetime,
    *,
    content_mode: str,
    last_cutoff: Optional[str] = None,
    resolve: Optional[CaptionResolver] = None,
) -> Tuple[List[SftExample], List[PrefExample], DatasetCounts]:
    """The pure core of :func:`build_examples` over
    ``DatabaseManager.writer_dataset_raw()``; see the module docstring."""
    categories: Dict[int, str] = {
        int(k): str(v) for k, v in (raw.get("categories") or {}).items()
    }
    scope_id = parse_scope(scope, categories)
    scope_name = categories[scope_id].lower() if scope_id is not None else None
    now_iso = _utc_iso(now.isoformat()) or ""
    composer = _Composer(store, content_mode, scope_id)
    describe = _Describer(resolve)

    def in_scope(rows: Iterable[Dict[str, Any]]) -> bool:
        if scope_id is None:
            return True
        ids, names = _scope_categories(rows)
        return scope_id in ids or scope_name in names

    def in_time(as_of: str) -> bool:
        return not as_of or as_of <= now_iso

    gens = [g for g in raw.get("generations") or [] if _usable(g)]

    # -- variant preference pairs --
    prefs: List[PrefExample] = []
    variants: Dict[str, Dict[int, List[Dict[str, Any]]]] = {}
    for g in gens:
        group = g.get("variant_group")
        if group is None or g.get("variant_index") is None:
            continue
        variants.setdefault(str(group), {}).setdefault(
            int(g["variant_index"]), []
        ).append(g)
    for group in sorted(variants):
        winners: List[List[Dict[str, Any]]] = []
        losers: List[List[Dict[str, Any]]] = []
        for index in sorted(variants[group]):
            rows = variants[group][index]
            prompts = {r.get("prompt_used") for r in rows}
            if len(prompts) != 1 or _blank(next(iter(prompts))):
                continue
            grades = [_grade(r) for r in rows]
            if "pass" in grades:
                # A fallback prompt is never a target.
                if rows[0].get("writer_id") != WRITER_FALLBACK:
                    winners.append(rows)
            elif all(g == "fail" for g in grades):
                losers.append(rows)
        for win in winners:
            for lose in losers:
                head, other = win[0], lose[0]
                profile = _profile(head.get("model"))
                description = describe(head)
                if (
                    profile is None
                    or description is None
                    or other.get("model") != head.get("model")
                    or describe(other) != description
                    or _direction(other) != _direction(head)
                    or normalize_prompt(head["prompt_used"])
                    == normalize_prompt(other["prompt_used"])
                ):
                    continue
                both = win + lose
                as_of = _newest(s for r in both for s in _row_stamps(r))
                if not in_scope(both) or not in_time(as_of):
                    continue
                system, user, shown = composer.turns(
                    profile, description, _direction(head), _line_categories(both)
                )
                prefs.append(
                    PrefExample(
                        system=system,
                        user=user,
                        chosen=head["prompt_used"],
                        rejected=other["prompt_used"],
                        target_model=profile.id,
                        categories=shown,
                        source=SOURCE_VARIANT,
                        group=holdout_key(profile.id, description),
                        as_of=as_of,
                    )
                )
    n_variant = len(prefs)

    # -- edit pairs --
    sft: List[SftExample] = []
    edits: Dict[Tuple[str, str, str, str, str], List[Dict[str, Any]]] = {}
    for g in gens:
        if not g.get("edited"):
            continue
        draft, used = g.get("draft_prompt"), g.get("prompt_used")
        if _blank(draft) or _blank(used) or _profile(g.get("model")) is None:
            continue
        nd, nu = normalize_prompt(draft), normalize_prompt(used)
        description = describe(g)
        if nd == nu or description is None:
            continue
        key = (g["model"], description, _direction(g) or "", nd, nu)
        edits.setdefault(key, []).append(g)
    n_edit = 0
    for key in sorted(edits):
        rows = edits[key]
        if not any(_grade(r) == "pass" for r in rows):
            continue  # no render of it is ✓: an experiment, not a signal
        head = rows[0]
        profile = _profile(head["model"])
        assert profile is not None
        description = key[1]
        as_of = _newest(s for r in rows for s in _row_stamps(r))
        if not in_scope(rows) or not in_time(as_of):
            continue
        system, user, shown = composer.turns(
            profile, description, _direction(head), _line_categories(rows)
        )
        group = holdout_key(profile.id, description)
        prefs.append(
            PrefExample(
                system,
                user,
                head["prompt_used"],
                head["draft_prompt"],
                profile.id,
                shown,
                SOURCE_EDIT,
                group,
                as_of,
            )
        )
        sft.append(
            SftExample(
                system,
                user,
                head["prompt_used"],
                profile.id,
                shown,
                SOURCE_EDIT,
                group,
                as_of,
            )
        )
        n_edit += 1

    # -- hindsight examples --
    briefs: Dict[str, Dict[str, Any]] = raw.get("briefs") or {}
    by_path = {g["file_path"]: g for g in raw.get("generations") or []}
    n_hindsight = 0
    for path in _briefed_paths(raw):
        target = _hindsight_target(raw, by_path, path, describe)
        if target is None:
            continue
        profile, prompt, source_row, stamps, group_text = target
        brief = _brief_of(briefs, path, source_row)
        if brief is None:
            continue
        as_of = _newest(stamps)
        if not in_scope([source_row]) or not in_time(as_of):
            continue
        system, user, shown = composer.turns(
            profile, brief, None, _line_categories([source_row])
        )
        sft.append(
            SftExample(
                system,
                user,
                str(prompt),
                profile.id,
                shown,
                SOURCE_HINDSIGHT,
                holdout_key(profile.id, group_text or brief),
                as_of,
            )
        )
        n_hindsight += 1

    # Newest first: the window's preview shows recent data.
    prefs.sort(key=lambda e: e.as_of, reverse=True)
    sft.sort(key=lambda e: e.as_of, reverse=True)

    by_model: Dict[str, int] = {}
    for e in list(prefs) + [s for s in sft if s.source == SOURCE_HINDSIGHT]:
        by_model[e.target_model] = by_model.get(e.target_model, 0) + 1
    cutoff = _utc_iso(last_cutoff) if last_cutoff else None
    new = sum(1 for p in prefs if cutoff is None or (p.as_of and p.as_of > cutoff))
    stamps_all = [e.as_of for e in list(prefs) + list(sft) if e.as_of]
    counts = DatasetCounts(
        pref_pairs=n_variant,
        edit_pairs=n_edit,
        hindsight=n_hindsight,
        by_target_model=dict(sorted(by_model.items())),
        new_since_last=new,
        date_span=(min(stamps_all), max(stamps_all)) if stamps_all else ("", ""),
        content_mode=content_mode,
    )
    logger.info(
        "writer dataset (scope %s): %d preference, %d edit, %d hindsight",
        "all" if scope_id is None else scope_id,
        n_variant,
        n_edit,
        n_hindsight,
    )
    return sft, prefs, counts


def last_adapter_cutoff(
    adapters: Sequence[Dict[str, Any]], scope: Union[str, int]
) -> Optional[str]:
    """``data_cutoff`` of the newest ready/untested T2I adapter whose scope
    is ``scope`` (deleted ones excluded), or None."""
    want = str(scope).strip().lower()
    best: Optional[Dict[str, Any]] = None
    for a in adapters:
        if (
            a.get("kind") != "t2i"
            or a.get("status") not in _CUTOFF_STATUSES
            or a.get("deleted_at")
            or str(a.get("scope", "")).strip().lower() != want
            or not a.get("data_cutoff")
        ):
            continue
        if best is None or (a.get("created_at") or "", a.get("id") or 0) > (
            best.get("created_at") or "",
            best.get("id") or 0,
        ):
            best = a
    return str(best["data_cutoff"]) if best is not None else None


def build_examples(
    db: Any,
    scope: Union[str, int],
    store: PromptStore,
    now: datetime,
    *,
    content_mode: str,
    resolve: Optional[CaptionResolver] = None,
) -> Tuple[List[SftExample], List[PrefExample], DatasetCounts]:
    """``(sft, prefs, counts)`` for ``scope`` (``"all"`` or a category id).
    ``content_mode`` is the T2I config's (required: the system prompt must
    match inference); ``resolve`` re-resolves older rows' captions (see
    :func:`caption_resolver`). Synchronous: the caller wraps it in
    ``asyncio.to_thread``. ``prefs`` holds variant AND edit pairs before any
    holdout split."""
    raw = db.writer_dataset_raw()
    cutoff = last_adapter_cutoff(db.list_writer_adapters(), scope)
    return examples_from_raw(
        raw,
        scope,
        store,
        now,
        content_mode=content_mode,
        last_cutoff=cutoff,
        resolve=resolve,
    )


# ---- holdout, output, readiness -------------------------------------------


def _holdout_rank(group: str, seed: int) -> float:
    """A stable number in ``[0, 1)`` for ``group``: sha256 of the seed and
    the group key. Depends on nothing else, so adding or removing other
    groups never moves a group across the split."""
    digest = hashlib.sha256(f"{seed}:{group}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def split_holdout(
    prefs: Sequence[PrefExample],
    fraction: float = DEFAULT_HOLDOUT_FRACTION,
    seed: int = 0,
) -> Tuple[List[PrefExample], List[PrefExample]]:
    """``(train, holdout)``: the groups that have a variant pair and whose
    stable hash rank (:func:`_holdout_rank`) is below ``fraction`` (about
    ``fraction`` of them), and every preference example of those groups.
    Membership is per group, so a run continued from an earlier adapter
    never finds that adapter's training groups in its holdout. Edge cases
    on tiny sets: with two or more groups at least one is held (the lowest
    rank) and never all (the highest rank trains). Input order is kept."""
    groups = sorted({p.group for p in prefs if p.source == SOURCE_VARIANT})
    held: Set[str] = set()
    if len(groups) >= 2 and fraction > 0:
        ranked = sorted(groups, key=lambda g: (_holdout_rank(g, seed), g))
        held = {g for g in ranked if _holdout_rank(g, seed) < fraction}
        if not held:
            held = {ranked[0]}
        elif len(held) == len(groups):
            held.discard(ranked[-1])
    train = [p for p in prefs if p.group not in held]
    holdout = [p for p in prefs if p.group in held]
    return train, holdout


def sft_without_holdout(
    sft: Sequence[SftExample], holdout: Sequence[PrefExample]
) -> List[SftExample]:
    """The SFT examples safe to train on: none from a held-out group."""
    held = {p.group for p in holdout}
    return [s for s in sft if s.group not in held]


def write_jsonl(examples: Sequence[Example], path: Union[str, Path]) -> int:
    """Write one JSON object per line (schema in the module docstring),
    atomically (temp file + rename). Returns the line count."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=target.name, suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for e in examples:
                fh.write(json.dumps(e.to_dict(), ensure_ascii=False) + "\n")
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(examples)


def preview(examples: Sequence[Example], n: int = PREVIEW_N) -> List[Dict[str, Any]]:
    """The first ``n`` examples as JSONL dicts, for the training window.
    Never logged."""
    return [e.to_dict() for e in list(examples)[: max(0, n)]]


PREVIEW_KINDS = ("preference", "edit", "hindsight")


def preview_mix(
    prefs: Sequence[PrefExample], sft: Sequence[SftExample], n: int = PREVIEW_N
) -> List[Dict[str, Any]]:
    """Up to ``n`` examples balanced across the three kinds (round robin:
    variant ``preference`` pairs, ``edit`` pairs, ``hindsight`` targets),
    each dict labelled with ``kind``. An edit is shown once, as its DPO
    pair (draft = rejected) when it has one, else as its SFT target.
    For the training window only; never logged."""
    edits: List[Example] = [p for p in prefs if p.source == SOURCE_EDIT]
    if not edits:
        edits = [s for s in sft if s.source == SOURCE_EDIT]
    buckets: Dict[str, List[Example]] = {
        "preference": [p for p in prefs if p.source == SOURCE_VARIANT],
        "edit": edits,
        "hindsight": [s for s in sft if s.source == SOURCE_HINDSIGHT],
    }
    out: List[Dict[str, Any]] = []
    i = 0
    while len(out) < max(0, n):
        added = False
        for kind in PREVIEW_KINDS:
            bucket = buckets[kind]
            if i < len(bucket) and len(out) < n:
                out.append({**bucket[i].to_dict(), "kind": kind})
                added = True
        if not added:
            break
        i += 1
    return out


def readiness(
    counts: DatasetCounts, last_adapter_cutoff: Optional[str]
) -> Dict[str, Any]:
    """``{"trainable", "enough_new", "missing"}``; see the module docstring."""
    total = counts.pref_pairs + counts.edit_pairs
    trainable = total >= MIN_TRAINABLE_PAIRS
    if last_adapter_cutoff is None:
        enough_new = trainable
    else:
        enough_new = counts.new_since_last >= ENOUGH_NEW_PAIRS
    return {
        "trainable": trainable,
        "enough_new": enough_new,
        "missing": max(0, MIN_TRAINABLE_PAIRS - total),
    }
