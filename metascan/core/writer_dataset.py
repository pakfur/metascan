"""Training data for the prompt-writer LoRA (taste learning project 4).

Turns the owner's generation lineage and judgments into three example kinds
for fine-tuning the 8B writer. Everything is built locally and NEVER logged
(counts only); the window's preview is the one place example text leaves
this module.

Example kinds
-------------
* **Variant preference pairs** (``PrefExample``, ``source="variant"``):
  within a T2I ``variant_group`` (one caption, one target model, one set of
  seeds, several prompts), a *winner* variant -- at least one of its images
  is a favorite -- against a *loser* variant -- every one of its images was
  seen or passed and none is a favorite. Every winner x loser combination
  is a pair (``chosen`` = the winner's rendered prompt, ``rejected`` = the
  loser's). A variant with an unseen image is neither.
* **Edit pairs** (``source="edit"``): a rendered prompt with ``edited = 1``
  whose whitespace-normalized text differs from its draft. ``chosen`` = the
  rendered (edited) prompt, ``rejected`` = the writer's draft. Each edit
  yields one ``PrefExample`` (DPO) AND one ``SftExample`` (SFT target),
  counted once (``edit_pairs``). Several seeds of one edited variant are one
  edit (deduplicated on target model + caption + both normalized texts).
* **Hindsight examples** (``SftExample``, ``source="hindsight"``): a
  favorited image with a known prompt and a cached ``hindsight_briefs``
  brief. User turn = the brief, target = the prompt that produced the
  image. A T2I image's target model is its ``model`` column; a library
  item (no T2I row) needs a metadata prompt and a model name that
  ``taste_examples.matches_model_family`` places in exactly one
  ``MODEL_PROFILES`` family (none or several: left out).

Inclusion rules (all kinds)
---------------------------
* Images only: I2V clips are never read, and every path must pass
  ``taste_examples.is_image_path``.
* Scorecard images (``t2i_images.scorecard_id`` not NULL) are dropped
  before anything else -- a loser variant that is a scorecard image simply
  is not a candidate.
* ``writer_id = 'manual'`` is excluded everywhere: a pair with a manual
  side, a manual edit (there is no writer draft to prefer against) and a
  manual image as hindsight target. Every other writer counts (``base``,
  ``base+taste``, ``fallback``, ``adapter:<id>``); two sides that share a
  ``writer_id`` + ``writer_model`` are fine.
* The target model must be a ``MODEL_PROFILES`` id and the caption (brief
  for hindsight) non-blank; a pair whose two prompts normalize equal, or
  whose sides disagree on caption or model, is skipped.
* Deleted generations count: a path with no live media row is read from its
  newest ``media_tombstones`` row whose lineage carries the ``t2i`` row
  (judgment state and category NAMES as at deletion; a category scope
  matches them by the category's current name). A live row always wins. A
  deleted image has no brief (the table cascades), so no hindsight.
* An example's *categories* are the union over the images it was built
  from (both variants of a pair; every seed of an edit; the one hindsight
  image). Scope ``"all"`` keeps every example and adds a
  ``CATEGORIES: a, b`` line to its user turn (none when it has no
  categories); scope ``"<category id>"`` keeps only examples carrying that
  category and adds no line.
* An example's ``as_of`` is the newest of its images' ``created_at``,
  ``judged_at`` and ``seen_at`` (for a library item: its ``judged_at`` and
  ``seen_at``; never the brief's own time, which says nothing about the
  owner's taste), as UTC ISO-8601. Examples with ``as_of`` after ``now`` are
  left out, so a run that records ``data_cutoff = now`` (or
  ``counts.date_span[1]``) counts everything later as new.

Prompts (as at inference)
-------------------------
``system`` and ``user`` come from ``t2i_prompt.compose_guided_t2i_prompts``
with the row's target-model profile, ``content_mode`` (the T2I config's;
``"default"`` adds no directive) and NO direction, angle or taste examples:
the angle a variant was written with is not in the user turn, so a pair
compares two outputs for the very same request. The user turn's
``DESCRIPTION`` is the stored caption as typed (``t2i_images.caption``, its
tokens unresolved), or the brief. ``add_categories_line`` adds the
``CATEGORIES`` line for scope ``all``.

JSONL schema (one JSON object per line, UTF-8; what the training worker
reads)
----------------------------------------------------------------------
* SFT file (``sft.jsonl``)::

    {"system": str, "user": str, "chosen": str, "target_model": str,
     "categories": [str], "source": "edit" | "hindsight",
     "group": str | null}

* Preference file (``prefs.jsonl``) and holdout file (``holdout.jsonl``,
  same shape)::

    {"system": str, "user": str, "chosen": str, "rejected": str,
     "target_model": str, "categories": [str],
     "source": "variant" | "edit", "group": str | null}

``chosen`` is the assistant turn to learn (SFT) or prefer (DPO);
``rejected`` the dispreferred one. ``group`` is the ``variant_group`` the
example came from (null for an edit or hindsight image outside one): the
holdout is drawn by group, and ``sft_without_holdout`` drops SFT examples of
held-out groups, so no held-out prompt is ever trained on.

Holdout
-------
``split_holdout`` holds out ``fraction`` of the variant groups (seeded,
order-independent; at least one when there are two or more groups, never
all), and with them every preference example of those groups (edit pairs
included). Holdout agreement = the fraction of held-out pairs whose chosen
log-prob beats the rejected one.

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

import json
import logging
import os
import random
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

from metascan.core.judgments import WRITER_MANUAL, normalize_prompt
from metascan.core.prompt_store import PromptStore
from metascan.core.t2i_models import MODEL_PROFILES, T2iModelProfile
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


@dataclass
class SftExample:
    system: str
    user: str
    chosen: str
    target_model: str
    categories: List[str]
    source: str  # "edit" | "hindsight"
    group: Optional[str]
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
    group: Optional[str]
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

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["date_span"] = list(self.date_span)
        return d


Example = Union[SftExample, PrefExample]


# ---- small pure helpers ---------------------------------------------------


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


def _seen(row: Dict[str, Any]) -> bool:
    return bool(row.get("is_favorite") or row.get("passed") or row.get("seen_at"))


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
    return [row.get("created_at"), row.get("judged_at"), row.get("seen_at")]


def _union_categories(
    rows: Iterable[Dict[str, Any]],
) -> Tuple[Set[int], List[str]]:
    ids: Set[int] = set()
    names: Dict[str, str] = {}
    for r in rows:
        ids.update(int(i) for i in r.get("category_ids") or [])
        for n in r.get("category_names") or []:
            names.setdefault(str(n).lower(), str(n))
    return ids, sorted(names.values(), key=str.lower)


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


class _Composer:
    """System/user turns exactly as the T2I writer composes them."""

    def __init__(
        self, store: PromptStore, content_mode: str, scope_id: Optional[int]
    ) -> None:
        self.store = store
        self.content_mode = content_mode
        self.scope_id = scope_id

    def turns(
        self, profile: T2iModelProfile, description: str, categories: List[str]
    ) -> Tuple[str, str]:
        composed = compose_guided_t2i_prompts(
            profile, description, self.content_mode, store=self.store
        )
        user = composed.user
        if self.scope_id is None:
            user = add_categories_line(user, categories)
        return composed.system, user


# ---- the builder ----------------------------------------------------------


def examples_from_raw(
    raw: Dict[str, Any],
    scope: Union[str, int],
    store: PromptStore,
    now: datetime,
    content_mode: str = "default",
    last_cutoff: Optional[str] = None,
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

    def in_scope(ids: Set[int], names: List[str]) -> bool:
        if scope_id is None:
            return True
        return scope_id in ids or any(n.lower() == scope_name for n in names)

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
        winners: List[Tuple[int, List[Dict[str, Any]]]] = []
        losers: List[Tuple[int, List[Dict[str, Any]]]] = []
        for index in sorted(variants[group]):
            rows = variants[group][index]
            prompts = {r.get("prompt_used") for r in rows}
            if len(prompts) != 1 or _blank(next(iter(prompts))):
                continue
            if any(r.get("is_favorite") for r in rows):
                winners.append((index, rows))
            elif all(_seen(r) for r in rows):
                losers.append((index, rows))
        for _, win in winners:
            for _, lose in losers:
                head, other = win[0], lose[0]
                profile = _profile(head.get("model"))
                caption = head.get("caption")
                if (
                    profile is None
                    or _blank(caption)
                    or other.get("model") != head.get("model")
                    or other.get("caption") != caption
                    or normalize_prompt(head["prompt_used"])
                    == normalize_prompt(other["prompt_used"])
                ):
                    continue
                both = win + lose
                ids, names = _union_categories(both)
                as_of = _newest(s for r in both for s in _row_stamps(r))
                if not in_scope(ids, names) or not in_time(as_of):
                    continue
                system, user = composer.turns(profile, str(caption), names)
                prefs.append(
                    PrefExample(
                        system=system,
                        user=user,
                        chosen=head["prompt_used"],
                        rejected=other["prompt_used"],
                        target_model=profile.id,
                        categories=names,
                        source=SOURCE_VARIANT,
                        group=group,
                        as_of=as_of,
                    )
                )
    n_variant = len(prefs)

    # -- edit pairs --
    sft: List[SftExample] = []
    edits: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}
    for g in gens:
        if not g.get("edited"):
            continue
        draft, used = g.get("draft_prompt"), g.get("prompt_used")
        if _blank(draft) or _blank(used) or _blank(g.get("caption")):
            continue
        if _profile(g.get("model")) is None:
            continue
        nd, nu = normalize_prompt(draft), normalize_prompt(used)
        if nd == nu:
            continue
        edits.setdefault((g["model"], g["caption"], nd, nu), []).append(g)
    n_edit = 0
    for key in sorted(edits):
        rows = edits[key]
        head = rows[0]
        profile = _profile(head["model"])
        assert profile is not None
        ids, names = _union_categories(rows)
        as_of = _newest(s for r in rows for s in _row_stamps(r))
        if not in_scope(ids, names) or not in_time(as_of):
            continue
        groups = sorted({str(r["variant_group"]) for r in rows if r["variant_group"]})
        group = groups[0] if groups else None
        system, user = composer.turns(profile, head["caption"], names)
        prefs.append(
            PrefExample(
                system,
                user,
                head["prompt_used"],
                head["draft_prompt"],
                profile.id,
                names,
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
                names,
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
    for path in sorted(briefs):
        brief = briefs[path].get("brief")
        if _blank(brief) or not is_image_path(path):
            continue
        gen = by_path.get(path)
        stamps: List[Any] = []
        if gen is not None:
            if not gen.get("live") or not gen.get("is_favorite") or not _usable(gen):
                continue
            profile = _profile(gen.get("model"))
            prompt = gen.get("prompt_used")
            group = gen.get("variant_group")
            source_rows = [gen]
            stamps += _row_stamps(gen)
        else:
            lib = (raw.get("library") or {}).get(path)
            if lib is None:
                continue
            families = [
                pid
                for pid in MODEL_PROFILES
                if matches_model_family(lib.get("model_names") or [], pid)
            ]
            profile = MODEL_PROFILES[families[0]] if len(families) == 1 else None
            prompt = lib.get("prompt")
            group = None
            source_rows = [lib]
            stamps += [lib.get("judged_at"), lib.get("seen_at")]
        if profile is None or _blank(prompt):
            continue
        ids, names = _union_categories(source_rows)
        as_of = _newest(stamps)
        if not in_scope(ids, names) or not in_time(as_of):
            continue
        system, user = composer.turns(profile, str(brief), names)
        sft.append(
            SftExample(
                system,
                user,
                str(prompt),
                profile.id,
                names,
                SOURCE_HINDSIGHT,
                str(group) if group else None,
                as_of,
            )
        )
        n_hindsight += 1

    # Newest first: the window's preview shows recent data.
    prefs.sort(key=lambda e: e.as_of, reverse=True)
    sft.sort(key=lambda e: e.as_of, reverse=True)

    by_model: Dict[str, int] = {}
    pairs = prefs  # variant + edit, each edit once
    for e in list(pairs) + [s for s in sft if s.source == SOURCE_HINDSIGHT]:
        by_model[e.target_model] = by_model.get(e.target_model, 0) + 1
    cutoff = _utc_iso(last_cutoff) if last_cutoff else None
    new = sum(1 for p in pairs if cutoff is None or (p.as_of and p.as_of > cutoff))
    stamps_all = [e.as_of for e in list(prefs) + list(sft) if e.as_of]
    counts = DatasetCounts(
        pref_pairs=n_variant,
        edit_pairs=n_edit,
        hindsight=n_hindsight,
        by_target_model=dict(sorted(by_model.items())),
        new_since_last=new,
        date_span=(min(stamps_all), max(stamps_all)) if stamps_all else ("", ""),
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
    content_mode: str = "default",
) -> Tuple[List[SftExample], List[PrefExample], DatasetCounts]:
    """``(sft, prefs, counts)`` for ``scope`` (``"all"`` or a category id).
    Synchronous: the caller wraps it in ``asyncio.to_thread``. ``prefs``
    holds variant AND edit pairs before any holdout split."""
    raw = db.writer_dataset_raw()
    cutoff = last_adapter_cutoff(db.list_writer_adapters(), scope)
    return examples_from_raw(raw, scope, store, now, content_mode, cutoff)


# ---- holdout, output, readiness -------------------------------------------


def split_holdout(
    prefs: Sequence[PrefExample],
    fraction: float = DEFAULT_HOLDOUT_FRACTION,
    seed: int = 0,
) -> Tuple[List[PrefExample], List[PrefExample]]:
    """``(train, holdout)``: ``fraction`` of the variant groups (seeded),
    and every preference example of those groups. Input order is kept."""
    groups = sorted({p.group for p in prefs if p.source == SOURCE_VARIANT and p.group})
    held: Set[str] = set()
    if len(groups) >= 2 and fraction > 0:
        n = min(len(groups) - 1, max(1, round(fraction * len(groups))))
        held = set(random.Random(seed).sample(groups, n))
    train = [p for p in prefs if p.group not in held]
    holdout = [p for p in prefs if p.group in held]
    return train, holdout


def sft_without_holdout(
    sft: Sequence[SftExample], holdout: Sequence[PrefExample]
) -> List[SftExample]:
    """The SFT examples safe to train on: none from a held-out group."""
    held = {p.group for p in holdout if p.group}
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
