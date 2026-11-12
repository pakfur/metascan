"""The structured writer for multi-character T2I captions.

A caption with two or more character tokens (``__ALICE__`` ...) is written in
two steps instead of one free rewrite:

1. The VLM fills a JSON object under a GBNF grammar: one entry per cast
   member, in cast order with its id fixed by the grammar, holding what the
   caption says about that person -- hair, features and clothing as noun
   phrases, where they are, and ``sentences``: complete sentences in the
   model's own words about what that person alone does and how they look --
   plus the scene, the interactions (``{ALICE}`` placeholders) and free
   sentences for the background, camera, lighting, colour, textures and mood.
2. Code renders the prose (:func:`render`). Each person is introduced with
   the resolver's own draws (``CastMember.identity`` / ``traits``), named by
   one fixed handle everywhere, and only then do the interactions follow, so
   a trait can never drift to another person and no person can be added.
   Code writes only the sentences it owns (the introduction and "She wears
   ..."); every other sentence is the model's, whole, so the renderer never
   has to guess how to join a fragment to a subject.

:func:`check` lists what a reply got wrong in a form the model can act on
(words of the caption it lost, hedges, a person in the camera sentence ...);
the runner asks once more with that list and keeps the better reply. A reply that cannot be parsed, or that names a person the cast does
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
from functools import lru_cache
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from metascan.core.prompt_store import PromptStore, get_prompt_store
from metascan.core.t2i_characters import CastMember, MarkedCaption
from metascan.core.t2i_prompt import (
    CONTENT_MODES,
    content_directive,
    stock_negative,
    strip_parentheses,
)
from metascan.core.t2i_models import T2iModelProfile
from metascan.utils.app_paths import get_data_dir

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
_STRAY_BRACES_RE = re.compile(r"\{([^{}]*)\}")
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
    continue continues continued continuing keep keeps keeping
    wearing worn dressed
    includes include including consists consist consisting remains remain
    remaining serves serve serving adding adds leaving leaves creating
    creates giving gives suggesting suggests indicating indicates evoking
    evokes conveying conveys emphasizing emphasize highlighting highlights
    capturing captures framing frames details detail objects object elements
    element structure perspective figures figure subjects subject individuals
    individual people person pair duo trio group foreground background
    midground further same overall entire whole partially partly various
    several multiple numerous additional another environmental""".split()
)
# Adverbs ("casually", "lightly") carry tone, not a fact a reply must keep.
_ADVERB_MIN = 6

# Only a lost word that names something visible counts (a captioner's prose
# is full of words that do not: "anchoring", "moment", "illuminates"; round 1
# of the evaluation found four in five hits were such words). The lexicon is
# the single-word Open Images and ImageNet class names from data/vocabulary/
# (garments, objects, body parts, animals ...), plus these colours, materials
# and visible states, plus the action groups below.
_VISUAL_EXTRA = frozenset(
    """red orange yellow green blue purple violet pink magenta teal turquoise
    brown black white grey gray silver gold golden beige cream ivory navy
    maroon burgundy crimson scarlet emerald olive tan khaki colorful striped
    floral checkered plaid polka patterned lace leather silk satin denim
    velvet wool cotton linen mesh sheer fishnet latex knit sequined metallic
    painted tattooed pierced nude naked topless bottomless bare barefoot wet
    shirtless hoop""".split()
)
# Common scene actions, by meaning: a paraphrase inside a group keeps the
# fact ("sits" is kept by "seated", "watching" by "gazes"). No body-part
# nouns in a group ("eye", "hand"): the cast's own traits would keep the action.
ACTION_GROUPS: Tuple[Tuple[str, ...], ...] = (
    ("sit", "seat", "perch", "straddle"),
    ("lie", "lying", "lay", "recline", "sprawl"),
    ("stand", "standing"),
    ("kneel", "knelt", "crouch", "squat"),
    ("lean", "rest", "recline"),
    ("watch", "look", "gaze", "stare", "glance", "observe", "peer"),
    ("touch", "caress", "stroke", "rest", "brush", "graze"),
    ("hold", "grip", "grasp", "clutch", "grab", "cradle", "carry"),
    ("hug", "embrace", "cuddle", "wrap"),
    ("kiss",),
    ("reach", "extend", "stretch"),
    ("pour",),
    ("pass", "give", "offer"),
    ("smile", "grin", "beam"),
    ("laugh", "giggle", "chuckle"),
    ("read", "book"),
    ("write", "pen"),
    ("walk", "stroll"),
    ("run", "jog", "sprint"),
    ("dance",),
    ("wash", "rinse", "scrub", "clean"),
    ("massage", "rub", "knead"),
    ("bathe", "bath", "shower"),
    ("swim",),
    ("sleep", "doze", "nap"),
    ("eat", "bite", "chew"),
    ("drink", "sip"),
    ("cry", "weep", "tear"),
    ("point",),
    ("wave",),
    ("pull", "tug"),
    ("push", "press"),
    ("ride",),
)
# Single class names that say nothing about one particular picture.
_NOT_VISUAL = frozenset(
    """body light shadow scene view space front back left right top bottom side
    interaction atmosphere sense base balance palette color colour share
    sharing spread fall sporting works bears grounding anchoring bathed room
    clothing clothes angle area level surface nature shade shades matching
    landscape environment setting style pattern tone texture""".split()
)
_ACTION_OF: Dict[str, int] = {}


@lru_cache(maxsize=1)
def visual_lexicon() -> FrozenSet[str]:
    """Stems of the words a lost word must be one of to count (see above).
    Without data/vocabulary/ it is the colours, materials, states and
    actions alone."""
    words: Set[str] = set(_VISUAL_EXTRA)
    for group in ACTION_GROUPS:
        words.update(group)
    try:
        from metascan.core import vocabulary

        vocab_dir = get_data_dir() / "vocabulary"
        terms = list(
            vocabulary._read_open_images(vocab_dir / vocabulary.OPEN_IMAGES_FILENAME)
        ) + list(vocabulary._read_lines(vocab_dir / vocabulary.IMAGENET_FILENAME))
    except Exception:
        terms = []
    for term in terms:
        for part in term.lower().split(","):
            part = part.strip()
            if re.fullmatch(r"[a-z]+", part):
                words.add(part)
    not_visual = {f for w in _NOT_VISUAL for f in _form_stems(w)}
    return (
        frozenset(f for w in words if len(w) >= 3 for f in _form_stems(w)) - not_visual
    )


def _form_stems(word: str) -> Set[str]:
    """The stems of a word's common inflections, so a caption's "gazing"
    and a reply's "gazes" both land on a stem the word registered."""
    forms = {word, word + "s", word + "es", word + "ed", word + "ing"}
    if word.endswith("e"):
        forms |= {word[:-1] + "ing", word + "d"}
    if re.fullmatch(r"[a-z]*[^aeiou][aeiou][^aeiouwxy]", word) and len(word) <= 4:
        # sit -> sitting, hug -> hugging, grab -> grabbed
        forms |= {word + word[-1] + "ing", word + word[-1] + "ed"}
    return {_stem(f) for f in forms}


def _action_group(stem: str) -> Optional[int]:
    if not _ACTION_OF:
        for i, group in enumerate(ACTION_GROUPS):
            for word in group:
                for form in _form_stems(word):
                    _ACTION_OF.setdefault(form, i)
    return _ACTION_OF.get(stem)


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
            _field("sentences", "strlist"),
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


# A sentence ends at . ! or ? followed by a space and a capital or a {NAME},
# so "f/2.8" stays whole; "Dr. Smith" splits, harmlessly, since the pieces
# are written back one after the other.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z{])")


def _sentences(value: Any) -> List[str]:
    """Like :func:`_texts`, one sentence per entry: the model sometimes packs
    two into one ("... on the white sheet. A small tattoo ..."), which hid
    the first from the duplicate check."""
    out: List[str] = []
    for text in _texts(value):
        out += [t for t in _SENTENCE_SPLIT_RE.split(text) if t.strip()]
    return out


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
                "sentences": _sentences(subject.get("sentences")),
            }
        )
    clean: Dict[str, Any] = {
        "scene": _text(data.get("scene")),
        "subjects": clean_subjects,
        "interactions": _sentences(data.get("interactions")),
        "background": _sentences(data.get("background")),
        "negative_extra": _texts(data.get("negative_extra")),
        "unresolved": _texts(data.get("unresolved")),
    }
    for name in FREE_FIELDS:
        clean[name] = _text(data.get(name))
    if not clean["scene"]:
        raise StructuredError("the reply has no scene")
    lift_dress_states(clean)
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
        out += [(f"a sentence of {who}", t) for t in s["sentences"]]
    out += [("interactions", t) for t in data["interactions"]]
    out += [("background", t) for t in data["background"]]
    out += [(name, data[name]) for name in FREE_FIELDS]
    return out


def _stem(word: str) -> str:
    """A crude stem, enough to match a word's forms: looking / looks /
    looked -> look, touches -> touch, concentrate / concentrating ->
    concentrat."""
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    return word[:-1] if word.endswith("e") and len(word) > 4 else word


def leftover_warning(
    data: Dict[str, Any],
    cast: Sequence[CastMember],
    description: str,
    problems: Sequence[str],
) -> Optional[str]:
    """The step warning for what is still wrong after the retry, or None:
    the description words the prompt may have lost, by name (the owner can
    judge "may have lost 'concentrate'" at a glance; a count said nothing),
    and how many other checks still fail."""
    if not problems:
        return None
    lost = missing_words(data, cast, description)
    others = [p for p in problems if "the JSON has nothing for" not in p]
    parts = []
    if lost:
        parts.append("may have lost " + ", ".join(repr(w) for w in lost))
    if others:
        parts.append(f"{len(others)} check(s) still failing")
    return "structured prompt: " + "; ".join(parts) if parts else None


def missing_words(
    data: Dict[str, Any],
    cast: Sequence[CastMember],
    description: str,
    lexicon: Optional[FrozenSet[str]] = None,
) -> List[str]:
    """Content words of ``description`` (the marked caption) that appear
    nowhere in the reply or in what code adds itself (the cast's draws), at
    most :data:`RECALL_MAX`, in the order they appear. A word counts as kept
    when the reply holds a word sharing its first five letters ("watching"
    is kept by "watches") or a close spelling of it (a caption typo:
    "vwatching" is kept by "watching"), so a dropped fact shows and a
    rephrasing mostly does not. Only a word of :func:`visual_lexicon`
    (``lexicon`` overrides it) can be lost, and an action is kept by any
    word of its :data:`ACTION_GROUPS` group."""
    lex = visual_lexicon() if lexicon is None else lexicon
    kept_text = " ".join(t for _, t in _all_text(data))
    kept_text += " " + " ".join(
        " ".join([m.identity, *m.traits, m.hair or ""]) for m in cast
    )
    kept_words = set(re.findall(r"[a-z]+", kept_text.lower()))
    kept = {w[:5] for w in kept_words}
    kept_stems = {_stem(w) for w in kept_words}
    words: List[Tuple[str, bool]] = []
    for raw in _SENTENCE_RE.findall(description):
        # Whether the sentence is about a person: an action word counts only
        # then ("a coffee table sits in the foreground" loses no fact).
        personal = bool(_PLACEHOLDER_RE.search(raw) or _PERSON_REF_RE.search(raw))
        bare = _PLACEHOLDER_RE.sub(" ", raw).lower()
        words += [(w, personal) for w in re.findall(r"[a-z]+", bare)]
    missing: List[str] = []
    for word, personal in words:
        if len(word) < 4 or word in _RECALL_SKIP or word[:5] in kept:
            continue
        if word in _BODY_PARTS or _stem(word) in _BODY_PART_STEMS:
            continue  # framing ("from the chest up"), not a fact of its own
        if len(word) >= _ADVERB_MIN and word.endswith("ly"):
            continue  # tone, not a fact
        if _HEDGE_RE.fullmatch(word):
            continue  # dropping a hedge is what the form is told to do
        stem = _stem(word)
        if stem not in lex:
            continue  # not something visible ("anchoring", "moment")
        if stem in kept_stems:  # "looking" is kept by "looks"
            continue
        group = _action_group(stem)
        if group is not None and not personal:
            continue  # an object that "sits", "holds" or "circles"
        if group is not None and any(_action_group(k) == group for k in kept_stems):
            continue  # "sits" is kept by "seated"
        if get_close_matches(word, kept_words, n=1, cutoff=RECALL_TYPO_CUTOFF):
            continue  # a typo in the caption ("vwatching") the reply spelled right
        if word not in missing:
            missing.append(word)
        if len(missing) == RECALL_MAX:
            break
    return missing


_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]?")
_PERSON_REF_RE = re.compile(
    r"\b(she|he|her|his|him|they|them|their|both|all|each|woman|women|man|men|"
    r"couple|trio|figures?|subjects?)\b",
    re.IGNORECASE,
)
_BODY_PARTS = frozenset(
    """head face hair skin neck chest breast breasts torso back shoulder
    shoulders arm arms elbow hand hands wrist finger fingers hip hips waist
    belly stomach abdomen thigh thighs leg legs knee knees lap foot feet toe
    toes ankle calf calves buttock buttocks body bodies form forms figure
    figures lips mouth eye eyes ear ears nose cheek cheeks chin forehead""".split()
)
_ANY_PRONOUN_RE = re.compile(
    r"\b(she|he|her|his|him|hers|they|them|their)\b", re.IGNORECASE
)
LOST_SENTENCES_MAX = 3
_BODY_PART_STEMS = frozenset(_stem(w) for w in _BODY_PARTS)


def _lost_sentence_problems(description: str, lost: Sequence[str]) -> List[str]:
    """One problem per description sentence that holds a lost word, quoting
    the sentence (a lone word, often a typo, gave the model nothing to act
    on, and it sent its JSON back unchanged), naming the lost words and
    where they belong: interactions when the sentence names two or more
    people, else that person's own fields."""
    out: List[str] = []
    left = set(lost)
    for raw in _SENTENCE_RE.findall(description):
        sentence = " ".join(raw.split())
        words = set(re.findall(r"[a-z]+", sentence.lower()))
        here = [w for w in lost if w in left and w in words]
        if not sentence or not here:
            continue
        left -= set(here)
        names = list(dict.fromkeys(_PLACEHOLDER_RE.findall(sentence)))
        if len(names) >= 2 or (not names and _ANY_PRONOUN_RE.search(sentence)):
            # Two people, or people the caption calls "she" and "his".
            where = (
                "if it is something one person does to or with another, add "
                "it to interactions using their names"
            )
        elif names:
            where = f"add a sentence about it to the sentences of {{{names[0]}}}"
        else:
            where = "add it to background or the scene"
        out.append(
            f'the description says "{sentence}", but the JSON has nothing for '
            + ", ".join(repr(w) for w in here)
            + f": {where}, and keep everything else"
        )
        if len(out) == LOST_SENTENCES_MAX:
            break
    return out


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
            problems.extend(_lost_sentence_problems(description, lost))
    for where, text in _all_text(data):
        hedges = sorted({m.group(0).lower() for m in _HEDGE_RE.finditer(text)})
        if hedges:
            problems.append(
                f"{where} hedges with {', '.join(repr(h) for h in hedges)}: "
                "say what is there"
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
    if _PLACEHOLDER_RE.search(data["scene"]):
        problems.append("scene names a person: describe the people only as a group")
    # Not checked, because the renderer handles them and a retry for them
    # cannot help: another person's drawn hair in a hair field (the model
    # follows the caption text, which is where it came from; render puts
    # the person's own hair back).
    # Also not checked, for the same reason
    # was a wasted round trip (evaluation round 1): two people in one
    # position (four people on a bed often are; the handles stay distinct),
    # a person named in the lighting or colour sentence (the name is filled
    # in like anywhere else), a missing or shared garment_label (the handles
    # fall back to position, then an ordinal).
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
# Bare verbs that end in -ing, which the participle repair must not touch.
_ING_VERBS = frozenset(
    "bring cling fling ring sing sling spring sting string swing wring".split()
)


def _foreign_hair(hair: str, member: CastMember, cast: Sequence[CastMember]) -> bool:
    """True when a person's hair phrase holds another cast member's drawn
    hair value and not their own (evaluation: the 8B once gave one woman's
    "platinum blonde hair" to the other)."""
    text = hair.lower()
    if not text or (member.hair and member.hair.lower() in text):
        return False
    return any(
        m.name != member.name and m.hair and m.hair.lower() in text for m in cast
    )


def _repair_participle(text: str, name: str) -> str:
    """A person sentence the model left without its verb ("{ALICE} fanning
    her hair out") gets one: "{ALICE} is fanning her hair out"."""
    mine = "{" + name + "}"
    stripped = text.lstrip()
    if not stripped.startswith(mine + " "):
        return text
    rest = stripped[len(mine) + 1 :]
    first = rest.split(" ", 1)[0].lower()
    if first.endswith("ing") and first not in _ING_VERBS:
        return f"{mine} is {rest}"
    return text


# Share of a person sentence's content words found in one interaction above
# which the sentence only repeats it and is left out of the prompt.
REPEAT_SHARE = 0.6
# Garment phrases sometimes arrive as "wearing a red coat"; "She wears" is
# written by code, so the verb is dropped from the phrase.
_WEAR_LEAD_RE = re.compile(r"^(?:is\s+)?(?:wearing|wears|dressed in)\s+", re.IGNORECASE)


def _content(text: str) -> set:
    words = re.findall(r"[a-z]+", _PLACEHOLDER_RE.sub(" ", text).lower())
    return {_stem(w) for w in words if len(w) >= 4 and w not in _RECALL_SKIP}


def _repeats(sentence: str, interactions: Sequence[str]) -> bool:
    """True when a person sentence names another person and most of its
    content words are in one interaction (the model said the same thing in
    both places)."""
    if not _PLACEHOLDER_RE.search(sentence):
        return False
    words = _content(sentence)
    if not words:
        return False
    return any(
        len(words & _content(i)) / len(words) >= REPEAT_SHARE for i in interactions
    )


def _opens_with(sentence: str) -> Optional[str]:
    """The id a sentence opens with ("{BELLA} smiles."), else None."""
    match = re.match(r"\s*\{([A-Z][A-Z0-9]*)\}", sentence)
    return match.group(1) if match else None


# A state of dress listed as a garment or a feature ("nude", "nude skin")
# would read "She wears nude." / "with ... nude skin"; it is a sentence.
_DRESS_STATE_RE = re.compile(
    r"^(?:completely |fully |partially |mostly )?"
    r"(nude|naked|topless|shirtless|bare-chested|barefoot|bottomless)"
    r"(?: skin| body| figure)?$",
    re.IGNORECASE,
)


def lift_dress_states(data: Dict[str, Any]) -> None:
    """Move each state of dress out of a person's clothing and features into
    that person's sentences, "{ALICE} is nude.", once (in place)."""
    for s in data["subjects"]:
        states: List[str] = []
        for key in ("clothing", "features"):
            kept = []
            for item in s[key]:
                m = _DRESS_STATE_RE.match(item.strip().rstrip("."))
                if m:
                    states.append(m.group(1).lower())
                else:
                    kept.append(item)
            s[key] = kept
        said = " ".join(s["sentences"]).lower()
        for state in dict.fromkeys(states):
            if not re.search(rf"\b{re.escape(state)}\b", said):
                s["sentences"].append("{" + s["id"] + "} is " + state + ".")


def file_sentences(data: Dict[str, Any]) -> Tuple[Dict[str, List[str]], List[str]]:
    """``(sentences per person, interactions)`` as they are rendered: a
    sentence filed under one person that opens with another person's name
    is about that other person, so it moves to the interactions; a person
    sentence that only repeats an interaction is dropped."""
    interactions = list(data["interactions"])
    own: Dict[str, List[str]] = {}
    for s in data["subjects"]:
        kept: List[str] = []
        for sentence in s["sentences"]:
            opener = _opens_with(sentence)
            if opener is not None and opener != s["id"]:
                interactions.append(sentence)
            else:
                kept.append(sentence)
        own[s["id"]] = kept
    for sid, sentences in own.items():
        own[sid] = [t for t in sentences if not _repeats(t, interactions)]
    return own, interactions


def render(
    data: Dict[str, Any],
    cast: Sequence[CastMember],
    profile: T2iModelProfile,
    store: Optional[PromptStore] = None,
) -> Tuple[str, Optional[str]]:
    """``(prompt, negative)`` from a parsed reply: the scene sentence, then
    each person in cast order -- position, handle, the resolver's identity
    and traits, the hair, the clothing, then that person's own sentences --
    then the interactions, the background and the free sentences. Every
    field gets the handles put in for its ``{NAME}``s; a person sentence
    that opens with the person's own name opens with "She" / "He" instead,
    since the introduction just named them. Sentences are filed by
    :func:`file_sentences`."""
    names = handles(data, cast)

    def fill(text: str) -> str:
        text = _PLACEHOLDER_RE.sub(lambda m: names.get(m.group(1), m.group(0)), text)
        # A brace pair that is no id ("{Bob}", a person the caption names in
        # plain text) loses its braces: braces never reach the prompt.
        return _STRAY_BRACES_RE.sub(lambda m: m.group(1), text)

    seen: Set[str] = set()

    def once(sentence: str) -> str:
        """``sentence``, or "" when the prompt already says it word for word
        (the model wrote the lighting into the background too)."""
        key = " ".join(sentence.lower().split())
        if not key or key in seen:
            return ""
        seen.add(key)
        return sentence

    def own_sentence(member: CastMember, text: str) -> str:
        text = _repair_participle(text, member.name)
        subject, possessive = _PRONOUNS.get(member.noun, ("", ""))
        named = _sentence(fill(text))
        if subject:
            mine = "{" + member.name + "}"
            stripped = text.lstrip()
            short = text
            if stripped.startswith(mine + "'s "):
                short = possessive.capitalize() + stripped[len(mine) + 2 :]
            elif stripped.startswith(mine):
                short = subject + stripped[len(mine) :]
            pronoun = _sentence(fill(short))
            # Two people who do the same thing must not read as one person
            # doing it twice ("She bends forward." twice): the second keeps
            # its handle.
            if " ".join(pronoun.lower().split()) not in seen:
                return once(pronoun)
        return once(named)

    by_id = {s["id"]: s for s in data["subjects"]}
    own, interactions = file_sentences(data)
    # The scene goes first and its sentences count as said: a scene that also
    # says "All three are barefoot." keeps the background from saying it again.
    parts: List[str] = [
        once(_sentence(" ".join(raw.split())))
        for raw in _SENTENCE_RE.findall(fill(data["scene"]))
    ]
    for member in cast:
        s = by_id[member.name]
        subject, _ = _PRONOUNS.get(member.noun, ("They", "their"))
        withs = list(member.traits)
        if _foreign_hair(s["hair"], member, cast):
            # Another person's drawn hair: the reply gave it to the wrong
            # person, and the caption engine knows whose hair this is: say
            # their own instead.
            s = {**s, "hair": ""}
            own_hair = member.hair_trait or member.hair
            if own_hair and own_hair not in withs:
                withs.append(own_hair)
        if s["hair"] and member.hair and s["hair"] != member.hair:
            # The caption spelled the hair out with its style: keep its
            # phrase alone, not the drawn colour or the drawn style beside it.
            drawn = (member.hair, member.hair_trait)
            withs = [t for t in withs if t not in drawn]
        withs += [fill(t) for t in s["features"]]
        if s["hair"]:
            withs.append(fill(s["hair"]))
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
            garments = [_WEAR_LEAD_RE.sub("", fill(t)) for t in s["clothing"]]
            wears = f"{subject} wears {_join_and(garments)}."
            if " ".join(wears.lower().split()) in seen:
                # A matching outfit: the second person keeps their handle.
                wears = _sentence(f"{handle} wears {_join_and(garments)}")
            person.append(once(wears))
        person += [own_sentence(member, t) for t in own[member.name]]
        parts.append(" ".join(p for p in person if p))
    parts += [once(_sentence(fill(t))) for t in interactions]
    parts += [once(_sentence(fill(t))) for t in data["background"]]
    parts += [once(_sentence(fill(data[name]))) for name in FREE_FIELDS]
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
