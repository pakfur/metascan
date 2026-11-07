"""The structured writer for multi-character T2I captions.

A caption with two or more character tokens (``__ALICE__`` ...) is written in
two steps instead of one free rewrite:

1. The VLM fills a JSON object under a GBNF grammar: one entry per cast
   member, in cast order with its id fixed by the grammar, holding what the
   caption says about that person (hair, features, clothing, pose,
   expression, position), plus the scene, the interactions (``{ALICE}``
   placeholders) and free sentences for the background, camera, lighting,
   colour, textures and mood.
2. Code renders the prose (:func:`render`). Each person is introduced with
   the resolver's own draws (``CastMember.identity`` / ``traits``), named by
   one fixed handle everywhere, and only then do the interactions follow, so
   a trait can never drift to another person, a pronoun can never point at
   the wrong one and no person can be added.

:func:`check` lists what a reply got wrong in a form the model can act on
(hedges, a pose that involves another person, a person in the camera
sentence ...); the runner asks once more with that list and keeps the better
reply. A reply that cannot be parsed, or that names a person the cast does
not have, raises :class:`StructuredError` and the runner writes prose
instead.

Pure apart from reading the system prompt (``T2I_STRUCTURED_SYSTEM``) and
the content directives out of the hot-reloading prompt store. Nothing here
logs: problems name fields, ids and hedge words, never caption text.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from difflib import get_close_matches
from typing import Any, Dict, List, Optional, Sequence, Tuple

from metascan.core.prompt_store import PromptStore, get_prompt_store
from metascan.core.t2i_characters import CastMember, MarkedCaption
from metascan.core.t2i_prompt import (
    CONTENT_MODES,
    content_directive,
    stock_negative,
    strip_parentheses,
)
from metascan.core.t2i_models import T2iModelProfile

SYSTEM_KEY = "T2I_STRUCTURED_SYSTEM"
# Enough for four people with long clothing lists and the free sentences.
MAX_TOKENS = 1600
MIN_CAST = 2
MAX_CAST = 4
NEGATIVE_EXTRA_MAX = 4

POSITIONS: Tuple[str, ...] = ("left", "right", "center", "front", "back")
_POSITION_LEAD: Dict[str, str] = {
    "left": "On the left, ",
    "right": "On the right, ",
    "center": "In the center, ",
    "front": "In front, ",
    "back": "At the back, ",
}
FREE_FIELDS: Tuple[str, ...] = ("camera", "lighting", "colour", "textures", "mood")

_CLOSING = "Write the JSON now."

_HEDGE_RE = re.compile(
    r"\b(possibly|likely|appears? to|appearing|resembl\w*|seems?|seemingly)\b"
    r"|\bvisible at the (?:lower |upper )?edge\b",
    re.IGNORECASE,
)
_PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9]*)\}")
# A possessive in the scene, the background or an interaction ("a man on his
# bed", "{ALICE} wraps her right arm around {BELLA}") is clear; what can point
# at the wrong person is a subject or object pronoun: she, he, him, hers, and
# "her" used as an object before a noun phrase ("hands her a cup").
_PERSON_PRONOUN_RE = re.compile(
    r"\b(she|he|him|hers)\b|\bher\s+(?:a|an|the|some|one|two)\b", re.IGNORECASE
)

# Words of a description that say nothing a reply must keep: framing words a
# reply rephrases ("shows" becomes "a photograph of"), the verbs the renderer
# writes itself ("She wears ...") and function words.
_RECALL_SKIP = frozenset(
    """shows show shown showing depicts depicting features featuring image
    picture photo photograph scene setting shot view frame framed composition
    visible positioned appears there their they them this that these those
    with from into onto over under behind between while which whose where
    when also very some each other both another about around near next
    across along against toward towards upon within without through
    being been have having does doing slightly gently softly wear wears
    wearing worn dressed""".split()
)
RECALL_MAX = 8
# How close a reply's word must be to a caption word to count as it (difflib).
RECALL_TYPO_CUTOFF = 0.85

# Hair colour families: two handles from one family ("honey blonde" and
# "platinum blonde") look alike, so the renderer names those people by a
# garment instead.
_COLOUR_FAMILIES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (
        "blonde",
        (
            "blonde",
            "blond",
            "platinum",
            "honey",
            "golden",
            "flaxen",
            "sandy",
            "ash",
            "strawberry blonde",
        ),
    ),
    ("red", ("red", "ginger", "auburn", "copper", "titian")),
    (
        "brown",
        (
            "brown",
            "brunette",
            "chestnut",
            "caramel",
            "espresso",
            "mahogany",
            "chocolate",
            "hazel",
            "mocha",
            "cinnamon",
        ),
    ),
    ("black", ("black", "raven", "jet", "ebony")),
    ("grey", ("grey", "gray", "silver", "white", "salt-and-pepper")),
)


class StructuredError(ValueError):
    """The reply cannot be used: not JSON, the wrong shape, or a person the
    cast does not have. The message names the problem, never caption text."""


def hair_family(text: Optional[str]) -> Optional[str]:
    """The colour family a hair value belongs to, or None."""
    lowered = (text or "").lower()
    for family, words in _COLOUR_FAMILIES:
        if any(re.search(rf"\b{re.escape(w)}\b", lowered) for w in words):
            return family
    return None


def applies(profile: T2iModelProfile, marked: Optional[MarkedCaption]) -> bool:
    """True when this caption is written structured: the profile asks for it
    and the caption names two to four characters."""
    if not profile.structured or marked is None:
        return False
    return MIN_CAST <= len(marked.cast) <= MAX_CAST


# ---- prompts and grammar ---------------------------------------------------


def compose(
    marked: MarkedCaption,
    content_mode: str,
    direction: Optional[str] = None,
    angle: Optional[str] = None,
    store: Optional[PromptStore] = None,
) -> Tuple[str, str]:
    """``(system, user)`` for the structured call."""
    if content_mode not in CONTENT_MODES:
        raise ValueError(f"unknown content mode {content_mode!r}")
    if store is None:
        store = get_prompt_store()
    system = store.get(SYSTEM_KEY) + content_directive(content_mode, store)
    people = ", ".join(f"{{{m.name}}} a {m.noun}" for m in marked.cast)
    user = f"PEOPLE: {people}\n\nDESCRIPTION:\n{marked.text}\n\n"
    if direction is not None and direction.strip():
        user += f"DIRECTION:\n{direction.strip()}\n\n"
    if angle is not None and angle.strip():
        user += f"ANGLE: lead with {angle.strip()}\n\n"
    return system, user + _CLOSING


def retry_user(user: str, raw: str, problems: Sequence[str]) -> str:
    """The user turn that asks again, with the first reply and its problems."""
    listed = "\n".join(f"- {p}" for p in problems)
    return (
        f"{user}\n\nYour previous JSON:\n{raw.strip()}\n\n"
        f"It has these problems:\n{listed}\n\nWrite the corrected JSON now."
    )


_GRAMMAR_TAIL = r"""
position ::= "\"left\"" | "\"right\"" | "\"center\"" | "\"front\"" | "\"back\""
strlist ::= "[" ws (string (ws "," ws string){0,11})? ws "]"
string ::= "\"" char* "\""
char ::= [^"\\\x7F\x00-\x1F] | "\\" (["\\bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])
ws ::= [ \t\n]{0,20}
"""


def _field(name: str, rule: str) -> str:
    return f'"\\"{name}\\"" ws ":" ws {rule}'


def grammar(cast: Sequence[CastMember]) -> str:
    """GBNF for the reply: exactly one subject per cast member, in cast
    order, each with its id fixed, and every field present in order."""
    if not cast:
        raise ValueError("a structured grammar needs a cast")
    sep = ' ws "," ws '
    lines: List[str] = []
    subject_rules: List[str] = []
    for i, member in enumerate(cast):
        rule = f"subject{i}"
        subject_rules.append(rule)
        fields = [
            f'"\\"id\\"" ws ":" ws "\\"{member.name}\\""',
            _field("position", "position"),
            _field("hair", "string"),
            _field("features", "strlist"),
            _field("clothing", "strlist"),
            _field("garment_label", "string"),
            _field("pose", "string"),
            _field("expression", "string"),
        ]
        lines.append(f'{rule} ::= "{{" ws {sep.join(fields)} ws "}}"')
    subjects = '"[" ws ' + sep.join(subject_rules) + ' ws "]"'
    top = [
        _field("scene", "string"),
        _field("subjects", subjects),
        _field("interactions", "strlist"),
        _field("background", "strlist"),
        *(_field(name, "string") for name in FREE_FIELDS),
        _field("negative_extra", "strlist"),
        _field("unresolved", "strlist"),
    ]
    root = f'root ::= "{{" ws {sep.join(top)} ws "}}"'
    return "\n".join([root, *lines]) + _GRAMMAR_TAIL


# ---- parsing and checking --------------------------------------------------


def _text(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _texts(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [t for t in (_text(v) for v in value) if t]


def parse(raw: str, cast: Sequence[CastMember]) -> Dict[str, Any]:
    """The reply as a clean dict (strings collapsed, lists of non-empty
    strings). Raises :class:`StructuredError` when it is not usable."""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise StructuredError("the reply is not JSON") from exc
    if not isinstance(data, dict):
        raise StructuredError("the reply is not a JSON object")
    subjects = data.get("subjects")
    names = [m.name for m in cast]
    if not isinstance(subjects, list) or len(subjects) != len(names):
        raise StructuredError("the reply does not have one subject per person")
    clean_subjects: List[Dict[str, Any]] = []
    for want, subject in zip(names, subjects):
        if not isinstance(subject, dict) or subject.get("id") != want:
            raise StructuredError("the reply's people are not the caption's")
        position = subject.get("position")
        clean_subjects.append(
            {
                "id": want,
                "position": position if position in POSITIONS else "center",
                "hair": _text(subject.get("hair")),
                "features": _texts(subject.get("features")),
                "clothing": _texts(subject.get("clothing")),
                "garment_label": _text(subject.get("garment_label")),
                "pose": _text(subject.get("pose")),
                "expression": _text(subject.get("expression")),
            }
        )
    clean: Dict[str, Any] = {
        "scene": _text(data.get("scene")),
        "subjects": clean_subjects,
        "interactions": _texts(data.get("interactions")),
        "background": _texts(data.get("background")),
        "negative_extra": _texts(data.get("negative_extra")),
        "unresolved": _texts(data.get("unresolved")),
    }
    for name in FREE_FIELDS:
        clean[name] = _text(data.get(name))
    if not clean["scene"]:
        raise StructuredError("the reply has no scene")
    unknown = sorted(
        {
            m.group(1)
            for m in _PLACEHOLDER_RE.finditer(json.dumps(clean))
            if m.group(1) not in names
        }
    )
    if unknown:
        raise StructuredError("the reply names a person the caption does not have")
    return clean


def _all_text(data: Dict[str, Any]) -> List[Tuple[str, str]]:
    """``(where, text)`` for every text the renderer writes."""
    out: List[Tuple[str, str]] = [("scene", data["scene"])]
    for s in data["subjects"]:
        who = "{" + s["id"] + "}"
        out.append((f"the hair of {who}", s["hair"]))
        out += [(f"the features of {who}", t) for t in s["features"]]
        out += [(f"the clothing of {who}", t) for t in s["clothing"]]
        out.append((f"the pose of {who}", s["pose"]))
        out.append((f"the expression of {who}", s["expression"]))
    out += [("interactions", t) for t in data["interactions"]]
    out += [("background", t) for t in data["background"]]
    out += [(name, data[name]) for name in FREE_FIELDS]
    return out


def missing_words(
    data: Dict[str, Any], cast: Sequence[CastMember], description: str
) -> List[str]:
    """Content words of ``description`` (the marked caption) that appear
    nowhere in the reply or in what code adds itself (the cast's draws), at
    most :data:`RECALL_MAX`, in the order they appear. A word counts as kept
    when the reply holds a word sharing its first five letters ("watching"
    is kept by "watches") or a close spelling of it (a caption typo:
    "vwatching" is kept by "watching"), so a dropped fact shows and a
    rephrasing mostly does not."""
    kept_text = " ".join(t for _, t in _all_text(data))
    kept_text += " " + " ".join(
        " ".join([m.identity, *m.traits, m.hair or ""]) for m in cast
    )
    kept_words = set(re.findall(r"[a-z]+", kept_text.lower()))
    kept = {w[:5] for w in kept_words}
    words = re.findall(r"[a-z]+", _PLACEHOLDER_RE.sub(" ", description).lower())
    missing: List[str] = []
    for word in words:
        if len(word) < 4 or word in _RECALL_SKIP or word[:5] in kept:
            continue
        if get_close_matches(word, kept_words, n=1, cutoff=RECALL_TYPO_CUTOFF):
            continue  # a typo in the caption ("vwatching") the reply spelled right
        if word not in missing:
            missing.append(word)
        if len(missing) == RECALL_MAX:
            break
    return missing


def check(
    data: Dict[str, Any],
    cast: Sequence[CastMember],
    description: Optional[str] = None,
) -> List[str]:
    """What the reply got wrong, each worded as an instruction the model can
    follow on a retry. Empty: nothing to fix. With ``description`` (the
    marked caption) it also lists the description's words the reply lost.
    A problem may quote those words: problems go to the model, and the
    runner's warning carries only their count, so never log them."""
    problems: List[str] = []
    if description is not None:
        lost = missing_words(data, cast, description)
        if lost:
            problems.append(
                "these words of the description are missing, put what they "
                "say in the right field: " + ", ".join(repr(w) for w in lost)
            )
    for where, text in _all_text(data):
        hedges = sorted({m.group(0).lower() for m in _HEDGE_RE.finditer(text)})
        if hedges:
            problems.append(
                f"{where} hedges with {', '.join(repr(h) for h in hedges)}: "
                "say what is there"
            )
    for s in data["subjects"]:
        if _PLACEHOLDER_RE.search(s["pose"]):
            problems.append(
                f"the pose of {{{s['id']}}} involves another person: "
                "move that part to interactions"
            )
    # The scene may not name anyone, so a pronoun is the only way it can
    # refer back ("a woman watching him"); the other two can use the names.
    for where in ("interactions", "background"):
        texts = data[where]
        if any(_PERSON_PRONOUN_RE.search(t) for t in texts):
            problems.append(
                f"{where} uses she, he, her or his for a person: "
                "use the names in braces"
            )
    for name in FREE_FIELDS:
        if _PLACEHOLDER_RE.search(data[name]):
            problems.append(f"{name} mentions a person: leave people out of it")
    if _PLACEHOLDER_RE.search(data["scene"]):
        problems.append("scene names a person: describe the people only as a group")
    positions = [s["position"] for s in data["subjects"]]
    if len(set(positions)) != len(positions):
        problems.append("two people have the same position: give each their own")
    if _needs_garment_handles(cast) and all(s["clothing"] for s in data["subjects"]):
        # Only when everyone has clothing: a description without any cannot
        # give a label, and the handles then fall back to position.
        labels = [s["garment_label"].lower() for s in data["subjects"]]
        if any(not label for label in labels) or len(set(labels)) != len(labels):
            problems.append(
                "garment_label must be filled and different for each person"
            )
    return problems


def log_entry(
    caption: str,
    model_id: str,
    data: Dict[str, Any],
    problems: Sequence[str],
    *,
    first: Optional[Dict[str, Any]] = None,
    first_problems: Sequence[str] = (),
    when: Optional[datetime] = None,
) -> str:
    """One pretty-printed JSON entry for ``t2i.structured_json_log``: the
    time (local, ISO 8601), the caption as typed, the writer model, the JSON
    the prompt was rendered from and the checks it still fails; after a
    retry also the first reply and what was wrong with it."""
    stamp = (when or datetime.now()).astimezone().isoformat(timespec="seconds")
    entry: Dict[str, Any] = {
        "timestamp": stamp,
        "caption": caption,
        "writer_model": model_id,
        "json": data,
        "checks_failing": list(problems),
    }
    if first is not None:
        entry["retry"] = {"first_json": first, "first_problems": list(first_problems)}
    return json.dumps(entry, indent=2, ensure_ascii=False) + "\n"


# ---- rendering -------------------------------------------------------------


def _hair_tells_apart(cast: Sequence[CastMember]) -> bool:
    """True when every hair handle names a hair colour of its own family."""
    if any(not m.handle_has_hair for m in cast):
        return False
    families = [hair_family(m.hair) for m in cast]
    return None not in families and len(set(families)) == len(families)


def _needs_garment_handles(cast: Sequence[CastMember]) -> bool:
    """True when neither the hair nor the nouns tell the cast apart (a man
    and a woman are "the man" and "the woman")."""
    if _hair_tells_apart(cast):
        return False
    nouns = [m.noun for m in cast]
    return len(set(nouns)) != len(nouns)


_POSITION_PHRASE: Dict[str, str] = {
    "left": "on the left",
    "right": "on the right",
    "center": "in the center",
    "front": "in front",
    "back": "at the back",
}


def _ordinal(n: int) -> str:
    return ("first", "second", "third", "fourth")[n] if n < 4 else f"number {n + 1}"


def handles(data: Dict[str, Any], cast: Sequence[CastMember]) -> Dict[str, str]:
    """One fixed, distinct name per cast member: the resolver's hair handle
    when the hair tells everyone apart, else the bare noun when the nouns
    do ("the man", "the woman"), else "the woman in the navy dress", else
    "the woman on the left", and as a last resort an ordinal."""
    by_id = {s["id"]: s for s in data["subjects"]}
    out: Dict[str, str] = {}
    if _hair_tells_apart(cast):
        return {m.name: m.handle for m in cast}
    if not _needs_garment_handles(cast):
        return {m.name: f"the {m.noun}" for m in cast}
    for m in cast:
        label = re.sub(r"^(a|an|the)\s+", "", by_id[m.name]["garment_label"].lower())
        out[m.name] = f"the {m.noun} in the {label}" if label else ""
    if any(not h for h in out.values()) or len(set(out.values())) != len(out):
        out = {
            m.name: f"the {m.noun} {_POSITION_PHRASE[by_id[m.name]['position']]}"
            for m in cast
        }
    if len(set(out.values())) != len(out):
        out = {m.name: f"the {_ordinal(i)} {m.noun}" for i, m in enumerate(cast)}
    return out


def _join_and(items: Sequence[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?" else text + "."


_PRONOUNS: Dict[str, Tuple[str, str]] = {"woman": ("She", "her"), "man": ("He", "his")}
# Bare verbs that end in -ing, which a participle check must not touch.
_ING_VERBS = frozenset(
    {
        "bring",
        "cling",
        "fling",
        "ring",
        "sing",
        "sling",
        "spring",
        "sting",
        "string",
        "swing",
        "wring",
    }
)


def _finite(pose: str) -> str:
    """A pose the model wrote as a participle ("sitting on his bed") made
    to follow "She" / "He": "is sitting on his bed". A finite verb
    ("sits") is left alone."""
    first = pose.split(" ", 1)[0].lower()
    if first.endswith("ing") and first not in _ING_VERBS:
        return "is " + pose
    return pose


def render(
    data: Dict[str, Any],
    cast: Sequence[CastMember],
    profile: T2iModelProfile,
    store: Optional[PromptStore] = None,
) -> Tuple[str, Optional[str]]:
    """``(prompt, negative)`` from a parsed reply: the scene sentence, then
    each person in cast order -- position, handle, the resolver's identity
    and traits, the hair, clothing, pose and expression -- then the
    interactions, the background and the free sentences. A pose that
    involves another person is left out when there are interactions (they
    say it, and the pose would repeat it)."""
    names = handles(data, cast)

    def fill(text: str) -> str:
        return _PLACEHOLDER_RE.sub(lambda m: names.get(m.group(1), m.group(0)), text)

    by_id = {s["id"]: s for s in data["subjects"]}
    parts: List[str] = [_sentence(data["scene"])]
    for member in cast:
        s = by_id[member.name]
        subject, _ = _PRONOUNS.get(member.noun, ("They", "their"))
        withs = list(member.traits)
        if s["hair"] and member.hair and s["hair"] != member.hair:
            # The caption spelled the hair out with its style; the drawn
            # value says only the colour, so keep the fuller phrase alone.
            withs = [t for t in withs if t != member.hair]
        withs += s["features"]
        if s["hair"]:
            withs.append(s["hair"])
        handle = names[member.name]
        if handle.endswith(_POSITION_PHRASE[s["position"]]):
            # "The woman in the center is ..." already says where she is.
            intro = f"{handle[0].upper()}{handle[1:]} is {member.identity}"
        else:
            intro = _POSITION_LEAD[s["position"]] + f"{handle} is {member.identity}"
        if withs:
            intro += " with " + _join_and(withs)
        person = [intro + "."]
        if s["clothing"]:
            person.append(f"{subject} wears {_join_and(s['clothing'])}.")
        pose = s["pose"]
        if pose and not (_PLACEHOLDER_RE.search(pose) and data["interactions"]):
            person.append(f"{subject} {_finite(fill(pose)).rstrip('.')}.")
        if s["expression"]:
            person.append(f"{subject} has {s['expression'].rstrip('.')}.")
        parts.append(" ".join(person))
    parts += [_sentence(fill(t)) for t in data["interactions"]]
    parts += [_sentence(fill(t)) for t in data["background"]]
    parts += [_sentence(fill(data[name])) for name in FREE_FIELDS]
    prompt = strip_parentheses(" ".join(p for p in parts if p))
    negative = stock_negative(profile, store)
    if negative is not None:
        terms = [t.strip() for t in negative.split(",") if t.strip()]
        seen = {t.lower() for t in terms}
        for extra in data["negative_extra"][:NEGATIVE_EXTRA_MAX]:
            if extra.lower() not in seen:
                terms.append(extra)
                seen.add(extra.lower())
        negative = strip_parentheses(", ".join(terms))
    return prompt, negative
