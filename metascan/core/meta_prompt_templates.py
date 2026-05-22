"""Qwen3-VL meta-prompts for image-to-prompt generation, per target model.

A successor to ``prompt_templates.py``. The legacy module composed short,
hand-tuned instructions; this one embeds the full meta-prompts from
``docs/meta-prompts.md`` verbatim — those have been field-tested against
each target image-generation model and produce notably better adherence
than the older terse builders.

Surface area (kept intentionally small):

* ``compose_generate_prompts(target, architecture, extras) -> (system, user)``
  builds the system / user pair that drives ``VlmClient.generate_text``.

* ``parse_output(target, raw)`` splits the model's response into
  ``(positive, negative)`` for targets that emit a ``Negative:`` block
  (sd / pony / chroma / qwen). For other targets ``negative`` is ``None``.

* ``EXTRA_OPTION_LABELS`` / ``MUTEX_PAIRS`` / ``TARGET_PRESETS`` mirror
  the same names exported by ``prompt_templates`` so the API and the
  frontend can swap import sources without restructuring callers.

Caption-length is intentionally absent: each meta-prompt embeds its own
sweet-spot range, and the user has chosen to defer per-target length
control until the meta-prompts prove insufficient.

Pure functions: no I/O, no model calls.
"""

from __future__ import annotations

import json
import re
from typing import Final, Iterator, Literal, Mapping, NamedTuple

from metascan.core.prompt_store import get_prompt_store


TargetModel = Literal["sd", "pony", "flux1", "flux2", "zimage", "chroma", "qwen"]
Architecture = Literal["t2i"]  # t2v / i2v / i2i deferred to v2

# Two-option vocabulary: the user can ask for explicit, anatomically
# correct content (``includeUncensored``); ask Qwen3 to keep output SFW
# (``includeSafety``); or set neither and let Qwen3 decide.
ExtraOption = Literal["includeUncensored", "includeSafety"]

# Per-element policy. The playground table assigns one to each row of
# the meta-prompt's numbered list; the composer turns the assignment
# into a POLICY block in the user turn that Qwen3 reads alongside the
# image. ``auto`` is reserved for rows whose semantics are fixed by the
# meta-prompt itself (Pony's score/source/rating block) — the playground
# locks those rows so the policy can't be changed.
Policy = Literal["extract", "override", "auto"]


# Targets whose meta-prompt instructs Qwen3 to emit a "Negative: …" block
# alongside the positive prompt. The rest produce a single block.
MODELS_WITH_NEGATIVE: Final[frozenset[TargetModel]] = frozenset(
    {"sd", "pony", "chroma", "qwen"}
)


# Maps the public TargetModel literal to the YAML key that stores its
# meta-prompt body. Note ``sd`` -> ``META_SDXL`` (the YAML key tracks
# the model family name, the TargetModel id tracks the API literal).
_YAML_KEY_BY_TARGET: Final[Mapping[TargetModel, str]] = {
    "sd": "META_SDXL",
    "pony": "META_PONY",
    "flux1": "META_FLUX1",
    "flux2": "META_FLUX2",
    "zimage": "META_ZIMAGE",
    "chroma": "META_CHROMA",
    "qwen": "META_QWEN",
}


class _PromptByTarget(Mapping[TargetModel, str]):
    """Read-only ``TargetModel -> meta-prompt`` view backed by the store.

    Behaves like a plain ``dict`` for the calls used downstream
    (``[]``, ``in``, ``.keys()``) but resolves each lookup against the
    live :class:`PromptStore` so hot-reloads are picked up without a
    module import.
    """

    def __getitem__(self, target: TargetModel) -> str:
        return get_prompt_store().get(_YAML_KEY_BY_TARGET[target])

    def __iter__(self) -> "Iterator[TargetModel]":
        return iter(_YAML_KEY_BY_TARGET)

    def __len__(self) -> int:
        return len(_YAML_KEY_BY_TARGET)

    def __contains__(self, target: object) -> bool:
        return target in _YAML_KEY_BY_TARGET


# Mutually-exclusive option pairs. UI prevents both from being checked
# at once; the backend resolver also drops the second one if both arrive.
MUTEX_PAIRS: Final[tuple[tuple[ExtraOption, ExtraOption], ...]] = (
    ("includeSafety", "includeUncensored"),
)


# (short_label, full_instruction) per option. Short label is for the
# checkbox; full instruction is the tooltip text.
EXTRA_OPTION_LABELS: Final[dict[ExtraOption, tuple[str, str]]] = {
    "includeSafety": (
        "Keep SFW",
        (
            "Tell Qwen3 to keep the description SFW: no nudity, no sexual "
            "acts, no exposed genitalia. Mutually exclusive with "
            "Uncensored."
        ),
    ),
    "includeUncensored": (
        "Uncensored / Adult Detail",
        (
            "Tell Qwen3 to describe nudity, anatomy, and sexual acts using "
            "explicit, anatomically-correct vocabulary. Mutually exclusive "
            "with Keep SFW."
        ),
    ),
}


# --- Per-target meta-prompts ----------------------------------------------
#
# The actual meta-prompt bodies live in ``data/meta_prompt.yml`` (one
# YAML key per target — see ``_YAML_KEY_BY_TARGET``). They are reloaded
# in place when the file changes (see :mod:`metascan.core.prompt_store`),
# so a running server picks up edits without a restart.
#
# ``_META_BY_TARGET`` is kept as a read-through view so the existing
# ``_META_BY_TARGET[tid]`` call sites (and a couple of tests that read it
# directly) keep working.

_META_BY_TARGET: Final[_PromptByTarget] = _PromptByTarget()


# --- Safety / uncensored directives ---------------------------------------
#
# Stored in the prompt YAML under ``SAFETY_DIRECTIVE`` /
# ``UNCENSORED_DIRECTIVE``. They are appended to the meta-prompt body so
# they take precedence over earlier formatting rules (e.g. Pony's
# rating_safe convention).


# --- Presets --------------------------------------------------------------


class TargetPreset(NamedTuple):
    label: str
    prefix: str
    suffix: str
    has_negative: bool


TARGET_PRESETS: Final[dict[TargetModel, TargetPreset]] = {
    "sd": TargetPreset(
        label="Stable Diffusion (SDXL)",
        prefix="",
        suffix="",
        has_negative=True,
    ),
    "pony": TargetPreset(
        label="Pony (SDXL)",
        prefix="",
        # Pony's rating tag is emitted INSIDE the meta-prompt's positive
        # block, so the user-side suffix is empty — no double-rating.
        suffix="",
        has_negative=True,
    ),
    "flux1": TargetPreset(
        label="Flux 1",
        prefix="",
        suffix="",
        has_negative=False,
    ),
    "flux2": TargetPreset(
        label="Flux 2",
        prefix="",
        suffix="",
        has_negative=False,
    ),
    "zimage": TargetPreset(
        label="Z-Image Turbo",
        prefix="",
        suffix="",
        has_negative=False,
    ),
    "chroma": TargetPreset(
        label="Chroma",
        prefix="",
        suffix="",
        has_negative=True,
    ),
    "qwen": TargetPreset(
        label="Qwen-Image",
        prefix="",
        suffix="",
        has_negative=True,
    ),
}


# --- Numbered-element parsing + selective extraction ---------------------
#
# Each meta-prompt contains one (or for Pony, two contiguous) numbered
# list describing the components Qwen3 should cover. The Prompt Playground
# UI surfaces these as a per-row policy table — for each element the
# user picks EXTRACT (pull from the image), OVERRIDE (use a value the
# user supplies), or AUTO (apply the meta-prompt's built-in handling for
# rows whose semantics are fixed, e.g. Pony's score-tag block).
#
# The composer wraps the meta-prompt with an "Extraction policy" preamble
# in the system turn and emits a POLICY block in the user turn listing
# the resolved policy + override value for every row. Qwen3 commits to
# the policy by reading the POLICY block; the closing user instruction
# explicitly tells it to apply OVERRIDE values to the prompt body it
# produces. We do NOT ask the model to emit a JSON commit header — when
# we did, Qwen3 sometimes treated the header as the entire response and
# stopped before generating the prompt. ``parse_output`` still strips a
# leading JSON line defensively in case a model emits one anyway.


# Rows whose policy is structurally locked to ``auto``. Pony's mandatory
# leading block (Score tags / Source tag / Rating tag) is fixed by the
# meta-prompt body — the score stack is a verbatim string, source/rating
# are derived from the image. Locking these prevents the playground from
# offering nonsensical OVERRIDE textareas for them.
_AUTO_ROWS_BY_TARGET: Final[dict[TargetModel, frozenset[int]]] = {
    "pony": frozenset({0, 1, 2}),  # 0-indexed: rows 1-3
}


class MetaElement(NamedTuple):
    """One numbered list item from a target's meta-prompt.

    ``title`` is the text between ``N. `` and ` — `; ``default_body`` is
    everything after the em-dash, including any indented continuation
    lines folded in with newlines. ``default_policy`` is what the
    playground initialises the row's policy radio to — ``auto`` for
    locked rows, ``extract`` for everything else.
    """

    title: str
    default_body: str
    default_policy: Policy


# Numbered-line head: ``N. title — first body line``. Em-dash with a
# surrounding space is the canonical separator across every _META_*
# string; hyphens never appear as the title/body separator. Title and
# body are captured non-greedily so the em-dash always anchors the split.
_NUMBERED_LINE_RX: Final[re.Pattern[str]] = re.compile(
    r"^(?P<num>\d+)\.\s+(?P<title>.+?)\s+—\s+(?P<body>.+)$"
)


def _parse_elements(meta: str) -> list[MetaElement]:
    """Walk ``meta`` line-by-line and return parsed elements.

    Continuation lines (lines that start with whitespace and are not
    themselves numbered items) are folded into the body so multi-line
    items survive intact (e.g. Pony's parenthetical under "Score tags").
    All elements default to ``policy='extract'``; per-target overrides
    in :data:`_AUTO_ROWS_BY_TARGET` are stamped on by :func:`elements_for`.
    """
    elements: list[MetaElement] = []
    lines = meta.split("\n")

    i = 0
    while i < len(lines):
        m = _NUMBERED_LINE_RX.match(lines[i])
        if not m:
            i += 1
            continue

        title = m.group("title").strip()
        body_text = m.group("body")

        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if not nxt:
                break
            if _NUMBERED_LINE_RX.match(nxt):
                break
            if not nxt.startswith((" ", "\t")):
                break
            body_text = f"{body_text}\n{nxt}"
            j += 1

        elements.append(
            MetaElement(
                title=title,
                default_body=body_text,
                default_policy="extract",
            )
        )
        i = j

    return elements


_ELEMENTS_CACHE: dict[TargetModel, list[MetaElement]] = {}


# Invalidate the parsed-elements cache whenever the YAML reloads — otherwise
# edits to a meta-prompt's numbered list would only take effect after a
# process restart. The store guards this listener against exceptions.
get_prompt_store().on_reload(_ELEMENTS_CACHE.clear)


def elements_for(target_model: TargetModel) -> list[MetaElement]:
    """Return the editable numbered-list elements for a target model.

    Order matches the order they appear in the meta-prompt; for Pony
    this concatenates the "Mandatory leading block" (1-3) and the "Tag
    body structure" (4-12) into a single 12-item list. Rows in
    :data:`_AUTO_ROWS_BY_TARGET` are returned with ``default_policy='auto'``;
    everything else is ``'extract'``.
    """
    cached = _ELEMENTS_CACHE.get(target_model)
    if cached is None:
        raw = _parse_elements(_META_BY_TARGET[target_model])
        auto_rows = _AUTO_ROWS_BY_TARGET.get(target_model, frozenset())
        cached = [
            MetaElement(
                title=el.title,
                default_body=el.default_body,
                default_policy="auto" if i in auto_rows else "extract",
            )
            for i, el in enumerate(raw)
        ]
        _ELEMENTS_CACHE[target_model] = cached
    return list(cached)


def _resolve_policy(
    el: MetaElement,
    requested: Policy | None,
) -> Policy:
    """Return the effective policy for a row.

    AUTO rows are locked — any caller-supplied policy is ignored. For
    other rows, ``requested`` (when not ``None``) wins over the row's
    ``default_policy``.
    """
    if el.default_policy == "auto":
        return "auto"
    if requested is None:
        return el.default_policy
    if requested == "auto":
        # ``auto`` is a meta-prompt-level concept; client-supplied auto
        # for a non-locked row is degenerate. Coerce back to extract so
        # the model still produces something rather than silently going
        # to default-only behaviour.
        return "extract"
    return requested


def _build_policy_block(
    elements: list[MetaElement],
    policies: list[Policy] | None,
    overrides: list[str | None] | None,
) -> str:
    """Build the user-side POLICY block.

    Always emits one line per element so the model has a stable list to
    refer back to when applying OVERRIDE values. OVERRIDE rows include
    the user-supplied value; AUTO and EXTRACT rows are bare.
    """
    lines = ["POLICY:"]
    for i, el in enumerate(elements):
        requested = policies[i] if policies and i < len(policies) else None
        resolved = _resolve_policy(el, requested)
        line = f"  {i + 1}. {el.title}: {resolved.upper()}"
        if resolved == "override":
            value = ""
            if overrides and i < len(overrides):
                raw = overrides[i]
                if raw:
                    value = raw.strip()
            if value:
                line += f" → {value}"
            else:
                # No value supplied for an override row. Tell the model
                # to fall back rather than fabricate a subject — better
                # than emitting an empty arrow that some 8B builds copy
                # literally into the output.
                line += " → (no value supplied; use the image)"
        lines.append(line)
    return "\n".join(lines) + "\n"


# --- Composer -------------------------------------------------------------


def _enabled(extras: list[ExtraOption]) -> dict[ExtraOption, bool]:
    """Resolve duplicates + the safety/uncensored mutex.

    If both ``includeSafety`` and ``includeUncensored`` arrive, prefer
    ``includeSafety`` (the more conservative default — easier to flip
    later than to undo a leak the other way).
    """
    seen: dict[ExtraOption, bool] = {}
    for x in extras:
        if x not in seen:
            seen[x] = True
    for a, b in MUTEX_PAIRS:
        if a in seen and b in seen:
            del seen[b]
    return seen


def compose_generate_prompts(
    target_model: TargetModel,
    architecture: Architecture,  # noqa: ARG001 — t2i is the only supported value today
    extras: list[ExtraOption],
    element_policies: list[Policy] | None = None,
    element_overrides: list[str | None] | None = None,
) -> tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` for image-to-prompt generation.

    The system prompt is the extraction-policy preamble + the target's
    verbatim meta-prompt + any safety / uncensored directive. Wrapping
    rather than substituting keeps the meta-prompt body identical across
    calls so a caching layer in front of the model sees a stable system
    string regardless of per-element policy choices.

    The user prompt is the resolved POLICY block (one line per element)
    followed by the fixed image instruction. ``element_policies`` and
    ``element_overrides`` are index-aligned with :func:`elements_for`;
    omit either to fall back to defaults (every row EXTRACT, except
    locked AUTO rows).
    """
    store = get_prompt_store()
    meta = _META_BY_TARGET[target_model]
    enabled = _enabled(extras)
    if enabled.get("includeSafety"):
        meta = meta + store.get("SAFETY_DIRECTIVE")
    elif enabled.get("includeUncensored"):
        meta = meta + store.get("UNCENSORED_DIRECTIVE")
    system = store.get("POLICY_PREAMBLE") + meta

    elements = elements_for(target_model)
    policy_block = _build_policy_block(elements, element_policies, element_overrides)
    user = f"{policy_block}\n{store.get('USER_INSTRUCTION')}"
    return system, user


# --- Output parsing -------------------------------------------------------


# Matches a line that introduces the negative block. Tolerates leading
# whitespace, Markdown emphasis on either side of the label
# (``**Negative:**``), and the "Negative prompt:" variant. The first
# inline run (everything after the colon on the same line) is captured;
# the trailing ``\**`` chunk lets us absorb a closing ``**`` from
# Markdown without leaking it into the negative body.
_NEGATIVE_LINE_RX: Final[re.Pattern[str]] = re.compile(
    r"^[ \t]*\**\s*[Nn]egative(?:\s+prompt)?\s*:\s*\**\s*(.*?)\s*\**\s*$",
    re.MULTILINE,
)

# Strip Markdown / labelled-block junk the model sometimes emits
# despite the "no labels" instruction in the meta-prompt.
_BLOCK_LABEL_RX: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:\**\s*)?Block\s*[12]\s*[:\-]\s*",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_markdown_fence(text: str) -> str:
    """Remove a leading/trailing triple-backtick fence if present."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop the opening fence and any language tag on the first line.
        first_nl = stripped.find("\n")
        if first_nl != -1:
            stripped = stripped[first_nl + 1 :]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    return stripped.strip()


def _strip_json_commit_header(text: str) -> str:
    """Remove a leading ``{"extracted":...,"overridden":...,"auto":...}``
    line if present.

    The composer instructs Qwen3 to emit one of these on its first line
    so the model commits to a per-element policy decision before
    generating the prompt. The header is purely a self-discipline
    mechanism for the model — callers want the prompt body, not the
    commit envelope, so we strip it on the way out. If the first line
    is not parseable JSON the text is returned untouched.
    """
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        return text
    end = stripped.find("\n")
    head = stripped[:end] if end != -1 else stripped
    head = head.strip()
    if not (head.startswith("{") and head.endswith("}")):
        return text
    try:
        parsed = json.loads(head)
    except json.JSONDecodeError:
        return text
    if not isinstance(parsed, dict):
        return text
    if end == -1:
        # Whole response was the header — nothing left.
        return ""
    return stripped[end + 1 :].lstrip("\n")


def parse_output(target_model: TargetModel, raw: str) -> tuple[str, str | None]:
    """Split a Qwen3 response into ``(positive, negative_or_none)``.

    For targets in :data:`MODELS_WITH_NEGATIVE` we look for a line that
    begins with ``Negative:`` (Markdown emphasis and "Negative prompt:"
    variants are accepted) and split there. For other targets the whole
    response is the positive prompt and ``negative`` is ``None``.

    Robust to:
    * a leading JSON line (defensive — earlier preambles asked for one,
      and stray emissions still need to be stripped if a model produces
      a header on its own)
    * leading/trailing whitespace
    * a wrapping ``⁠```⁠``⁠``⁠`` code fence
    * stray ``Block 1:`` / ``Block 2:`` labels
    * the model failing to emit a Negative block (returns ``None``)
    """
    text = _strip_markdown_fence(raw)
    text = _strip_json_commit_header(text)
    text = _BLOCK_LABEL_RX.sub("", text).strip()

    if target_model not in MODELS_WITH_NEGATIVE:
        return text, None

    match = _NEGATIVE_LINE_RX.search(text)
    if not match:
        return text, None

    positive = text[: match.start()].rstrip()
    inline_tail = match.group(1).strip()
    after_line = text[match.end() :].strip()

    if inline_tail and after_line:
        negative = f"{inline_tail}\n{after_line}".strip()
    else:
        negative = (inline_tail or after_line).strip()

    return positive.strip(), (negative or None)


__all__ = [
    "TargetModel",
    "Architecture",
    "ExtraOption",
    "Policy",
    "MODELS_WITH_NEGATIVE",
    "MUTEX_PAIRS",
    "EXTRA_OPTION_LABELS",
    "MetaElement",
    "TargetPreset",
    "TARGET_PRESETS",
    "compose_generate_prompts",
    "elements_for",
    "parse_output",
]
