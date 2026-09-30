"""Pure caption engine for the text-to-image (t2i) feature.

A caption is a plain string that may contain ``__TOKEN__`` placeholders
(two or more underscores each side, ``[A-Z][A-Z0-9]*`` between). This module
turns them into deterministic character descriptions:

* **Character names** (``__ALICE__``, ``__ADAM__`` ...) -- the first mention
  becomes a description (``a 31-year-old West African woman with olive skin,
  ...``); later mentions become a short handle chosen by the identity style
  (``ref``: ``the copper-red-haired woman``, ``noun``: ``the woman``,
  ``name``: ``Alice``).
* **Characteristic tokens** (``__HAIR__``, ``__BREASTS__`` ...) -- stand in
  for the word itself and are replaced by the value drawn for the character
  that owns them, or by the bare lowercase word when that is safer
  (``__HAIR__brush``, ``pubic __HAIR__``, a missing list, no owner).
* **Plain wildcards** (any other token with a list) -- one seeded value,
  the same everywhere in the caption.

A character's draws are keyed on the caption text alone, never on the seed:
``sha256(f"{caption_sha}|{name}|{slot}|{salt}")`` where ``caption_sha`` is the
hex sha256 of the caption. One caption therefore always draws the same cast,
and the seed is free to change (a new image of the same scene). The seed only
picks plain wildcards: ``sha256(f"{seed}|{token}|wildcard|0")``. Each draw is
reduced modulo the list length, so results are stable across processes and
Python versions. Parentheses are never emitted (ComfyUI would read them as
weighting syntax). No I/O and no exceptions on data problems: awkward input
degrades to the bare word or an omitted slot and adds a warning.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

from metascan.core.t2i_ages import stated_age

IDENTITY_STYLES: Tuple[str, ...] = ("ref", "noun", "name")
DEFAULT_IDENTITY_STYLE = "ref"

# A bare "woman" or "man" makes image models draw the subject younger than the
# stated number, so a first mention names the age bracket in words as well: from
# MIDDLE_AGED_FROM the noun becomes "middle aged woman", from OLD_FROM "old
# woman" (a 70-year-old is still middle aged). Later mentions keep the plain
# noun.
MIDDLE_AGED_FROM = 40
OLD_FROM = 71

_FEMALE = "female"
_MALE = "male"


@dataclass(frozen=True)
class CharacterConfig:
    """Names, nouns and slot layout; ``characters.yml`` overrides any field."""

    female_names: Tuple[str, ...] = ("ALICE", "BELLA", "CLARA", "DIANNA", "EMMA")
    male_names: Tuple[str, ...] = ("ADAM", "BOB")
    noun_female: str = "woman"
    noun_male: str = "man"
    slots: Tuple[str, ...] = (
        "age",
        "ethnicity",
        "skin",
        "eyes",
        "face",
        "hair",
        "body",
    )
    head_slots: Tuple[str, ...] = ("age", "ethnicity")
    with_slots: Tuple[str, ...] = ("skin", "eyes", "face", "hair", "body")
    token_slots: Tuple[str, ...] = ("hair", "breasts", "vagina", "penis")
    token_gender: Tuple[Tuple[str, str], ...] = (
        ("breasts", "female"),
        ("vagina", "female"),
        ("penis", "male"),
    )
    body_hair_prefixes: Tuple[str, ...] = (
        "pubic",
        "body",
        "facial",
        "chest",
        "arm",
        "leg",
        "underarm",
        "armpit",
        "stomach",
    )


@dataclass(frozen=True)
class Library:
    config: CharacterConfig
    # Keys: "<slot>", "<slot>.female", "<slot>.male" and any other lowercase
    # token name (plain wildcard lists). Values are already cleaned/validated.
    lists: Mapping[str, Tuple[str, ...]]


@dataclass(frozen=True)
class ResolvedCaption:
    text: str
    characters: Dict[str, Dict[str, str]]  # NAME -> {slot: drawn value}
    warnings: List[str]


# ``(?<!_)`` keeps a long run of underscores linear: a token can only start
# at the beginning of a run, never inside one.
_TOKEN_RE = re.compile(r"(?<!_)_{2,}([A-Z][A-Z0-9]*)_{2,}")
_POSSESSIVE_RE = re.compile(r"['\u2019\u02bc][sS]\b")

# After a first mention, details stay inline ("... woman with olive skin")
# unless the next word would make that awkward.
_NON_INLINE_WORDS = frozenset(
    {
        "with",
        "and",
        "or",
        "in",
        "on",
        "at",
        "who",
        "whose",
        "wearing",
        "holding",
        "while",
        "as",
    }
)
_COMPOUND_JOINERS = frozenset({"and", ",", ", and"})
_COMPOUND_GAP_MAX = 12

_MALE_PRONOUNS = frozenset({"his", "him", "he"})
_FEMALE_PRONOUNS = frozenset({"her", "she", "hers"})
_PRONOUN_WINDOW = 6
_WORD_SCAN_LIMIT = 64  # a pronoun or body-hair prefix is never longer
_WORD_TRIM = ",;:\"'\u201c\u201d\u2018\u2019()[]{}\u2014\u2013-\u2026!?."

_SENTENCE_END = frozenset(".!?\u2026")
# Skipped when looking back for the end of the previous sentence.
_OPENERS = frozenset(" \t\r\f\v\"'\u201c\u2018([{\u00ab")
_CLOSERS = frozenset("\"'\u201d\u2019)]}\u00bb")

_MAX_HAIR_SALT = 64
_A_EXCEPTIONS = ("eu", "uk", "uni", "uru", "uga", "use")


def _caption_key(caption: str) -> str:
    """What every character draw of ``caption`` is keyed on: the sha256 of its
    text exactly as given. ``surrogatepass`` keeps a lone surrogate, which a
    JSON string may carry, from raising."""
    return hashlib.sha256(caption.encode("utf-8", "surrogatepass")).hexdigest()


def _draw(key: Union[int, str], name: str, slot: str, salt: int, size: int) -> int:
    digest = hashlib.sha256(f"{key}|{name}|{slot}|{salt}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % size


def _warn(warnings: List[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def _cap1(text: str) -> str:
    return text[:1].upper() + text[1:]


def _join_and(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _article(phrase: str) -> str:
    """``an`` before a vowel or a number spoken with one (8x, 11, 18)."""
    digits = re.match(r"\d+", phrase)
    if digits:
        run = digits.group()
        number = int(run) if len(run) <= 6 else -1
        return "an" if number in (8, 11, 18) or 80 <= number <= 89 else "a"
    lowered = phrase.lower()
    if not lowered or lowered.startswith(_A_EXCEPTIONS):
        return "a"
    return "an" if lowered[0] in "aeiou" else "a"


def _hair_stem(value: Optional[str]) -> Optional[str]:
    """``copper red hair`` -> ``copper-red-haired`` (None if not `` hair``)."""
    if not value or not value.lower().endswith(" hair"):
        return None
    words = value[:-5].split()
    return "-".join(words) + "-haired" if words else None


def _values(library: Library, slot: str, gender: Optional[str]) -> Tuple[str, ...]:
    """The list for a slot: a gendered list overrides the plain one."""
    if gender:
        specific = library.lists.get(f"{slot}.{gender}")
        if specific:
            return specific
    return library.lists.get(slot) or ()


def _name_genders(cfg: CharacterConfig) -> Dict[str, str]:
    """NAME -> gender, in canonical order (female names, then male names)."""
    genders: Dict[str, str] = {}
    for name in cfg.female_names:
        genders.setdefault(name, _FEMALE)
    for name in cfg.male_names:
        genders.setdefault(name, _MALE)
    return genders


# --- Text scanning helpers (all bounded, so the whole pass stays linear) ----


def _next_context(caption: str, pos: int) -> Tuple[str, str]:
    """What follows ``pos``: ('word', w) | ('punct', ch) | ('end', '') | ('other', '')."""
    size = len(caption)
    i = pos
    while i < size and caption[i].isspace():
        i += 1
    if i >= size:
        return "end", ""
    char = caption[i]
    if char.isalpha():
        j = i
        while j < size and caption[j].isalpha():
            j += 1
        return "word", caption[i:j].lower()
    if char.isdigit() or char == "_":
        return "other", ""
    return "punct", char


def _previous_word(caption: str, pos: int) -> str:
    """The whitespace-delimited chunk immediately before ``pos``."""
    i = pos
    while i > 0 and caption[i - 1].isspace():
        i -= 1
    floor = max(0, i - _WORD_SCAN_LIMIT)
    j = i
    while j > floor and not caption[j - 1].isspace():
        j -= 1
    return caption[j:i]


def _pronoun_gender(caption: str, pos: int) -> Optional[str]:
    """Gender of the NEAREST pronoun in the last six words of the sentence before ``pos``.

    Scans backwards from the token: the first ``his/him/he`` (male) or
    ``her/she/hers`` (female) found decides, so "he holds her __HAIR__" is
    female. No pronoun in the window means undecided.
    """
    end = pos
    for _ in range(_PRONOUN_WINDOW):
        i = end
        while i > 0 and caption[i - 1].isspace():
            i -= 1
        if i == 0:
            break
        floor = max(0, i - _WORD_SCAN_LIMIT)
        j = i
        while j > floor and not caption[j - 1].isspace():
            j -= 1
        word = caption[j:i]
        if i < end and word[-1] in ".!?":
            break  # that word closed the previous sentence
        base = word.lower().strip(_WORD_TRIM).split("'")[0].split("\u2019")[0]
        if base in _MALE_PRONOUNS:
            return _MALE
        if base in _FEMALE_PRONOUNS:
            return _FEMALE
        end = j
    return None


def _sentence_state(piece: str) -> Optional[bool]:
    """Does text ending with ``piece`` end a sentence? None if it cannot tell."""
    for char in reversed(piece):
        if char == "\n":
            return True
        if char in _OPENERS:
            continue
        return char in _SENTENCE_END
    return None


def _ends_sentence(text: str) -> bool:
    for char in reversed(text):
        if char in _CLOSERS or char.isspace():
            continue
        return char in _SENTENCE_END
    return True


class _Emitter:
    """Collects output pieces and knows whether the next one starts a sentence.

    The state is updated from each piece as it is added, so capitalisation
    costs O(1) per token instead of re-scanning everything written so far.
    """

    def __init__(self) -> None:
        self._parts: List[str] = []
        self._at_start = True

    @property
    def at_sentence_start(self) -> bool:
        return self._at_start

    def add(self, piece: str) -> None:
        if not piece:
            return
        self._parts.append(piece)
        state = _sentence_state(piece)
        if state is not None:
            self._at_start = state

    def text(self) -> str:
        return "".join(self._parts)


# --- Planning (pass 1) ------------------------------------------------------


@dataclass
class _Ref:
    match: "re.Match[str]"
    kind: str  # "char" | "slot" | "wild"
    name: str = ""  # character name, or the owner of a characteristic token
    slot: str = ""  # lowercase slot / wildcard name
    bare: bool = False  # write the lowercase word instead of a drawn value


@dataclass
class _Plan:
    refs: List[_Ref]
    named: Set[str]  # character names present in the caption
    defaults: Set[str]  # default owners created for a typed caption
    referenced: Dict[str, Set[str]]  # owner -> slots a token spelled out


def _default_owner(cfg: CharacterConfig, want: Optional[str]) -> Optional[str]:
    names = cfg.male_names if want == _MALE else cfg.female_names
    return names[0] if names else None


class _Planner:
    """Pass 1: decide what every token becomes and who owns each characteristic token."""

    def __init__(
        self,
        caption: str,
        matches: Sequence["re.Match[str]"],
        library: Library,
        genders: Dict[str, str],
        warnings: List[str],
    ) -> None:
        self.caption = caption
        self.matches = matches
        self.library = library
        self.cfg = library.config
        self.genders = genders
        self.warnings = warnings
        self.token_slots = set(self.cfg.token_slots)
        self.fixed_gender: Dict[str, str] = dict(self.cfg.token_gender)
        self.prefixes = {word.lower() for word in self.cfg.body_hair_prefixes}
        self.plan = _Plan(
            refs=[],
            named={m.group(1) for m in matches if m.group(1) in genders},
            defaults=set(),
            referenced={},
        )
        # "female" | "male" | "any" -> the latest character name seen so far
        self.current: Dict[str, str] = {}

    def run(self) -> _Plan:
        for match in self.matches:
            token = match.group(1)
            if token in self.genders:
                ref = self._character(match, token)
            elif token.lower() in self.token_slots:
                ref = self._characteristic(match, token)
            else:
                ref = self._wildcard(match, token)
            self.plan.refs.append(ref)
        return self.plan

    def _character(self, match: "re.Match[str]", token: str) -> _Ref:
        self.current[self.genders[token]] = token
        self.current["any"] = token
        return _Ref(match, "char", name=token)

    def _wildcard(self, match: "re.Match[str]", token: str) -> _Ref:
        slot = token.lower()
        bare = not self.library.lists.get(slot)
        if bare:
            _warn(
                self.warnings,
                f"no list for __{token}__ ({slot}.txt); wrote the plain word",
            )
        return _Ref(match, "wild", slot=slot, bare=bare)

    def _characteristic(self, match: "re.Match[str]", token: str) -> _Ref:
        slot = token.lower()
        bare = _Ref(match, "slot", slot=slot, bare=True)
        end = match.end()
        fused = self.caption[end : end + 1].isalpha()
        body_hair = (
            slot == "hair"
            and _previous_word(self.caption, match.start()).lower() in self.prefixes
        )
        if fused or body_hair:
            return bare  # a suffix or body-hair prefix: it is just the word
        want: Optional[str] = self.fixed_gender.get(slot)
        if want not in (_FEMALE, _MALE):
            want = _pronoun_gender(self.caption, match.start())
        owner, is_default = self._find_owner(want)
        if owner is None:
            _warn(
                self.warnings,
                f"no {want or 'matching'} character for __{token}__; "
                f"wrote the plain word '{slot}'",
            )
            return bare
        if not _values(self.library, slot, self.genders[owner]):
            _warn(
                self.warnings,
                f"no list for __{token}__ ({slot}.txt); wrote the plain word",
            )
            return bare
        if is_default:
            self.plan.defaults.add(owner)
            self.current[self.genders[owner]] = owner
            self.current["any"] = owner
        self.plan.referenced.setdefault(owner, set()).add(slot)
        return _Ref(match, "slot", name=owner, slot=slot)

    def _find_owner(self, want: Optional[str]) -> Tuple[Optional[str], bool]:
        """(owner, is_default): nearest named character before the token, else
        the first cast member of the right gender, else -- only for a typed
        caption with no cast at all -- a default owner who gets no intro."""
        nearest = self.current.get(want or "any")
        if nearest is not None:
            return nearest, False
        for name, gender in self.genders.items():
            if name in self.plan.named and (want is None or gender == want):
                return name, False
        if not self.plan.named:
            default = _default_owner(self.cfg, want)
            return default, default is not None
        return None, False


# --- Drawing ----------------------------------------------------------------


def _draw_distinct(
    key: str,
    name: str,
    slot: str,
    options: Tuple[str, ...],
    taken: Mapping[str, List[str]],
) -> Tuple[str, bool]:
    """Draw a value no earlier character has; (value, clashed).

    Increments the salt until the value is unused; if chance keeps hitting
    used values, probes the list from the first draw so a free value is
    always found when one exists.
    """
    size = len(options)
    for salt in range(_MAX_HAIR_SALT):
        value = options[_draw(key, name, slot, salt, size)]
        if value.casefold() not in taken:
            return value, False
    start = _draw(key, name, slot, 0, size)
    for step in range(size):
        value = options[(start + step) % size]
        if value.casefold() not in taken:
            return value, False
    return options[start], True


def _draw_cast(
    key: str,
    cast: Sequence[str],
    genders: Dict[str, str],
    library: Library,
    referenced: Mapping[str, Set[str]],
    warnings: List[str],
) -> Tuple[Dict[str, Dict[str, str]], Set[str]]:
    """Draw every character's slots; returns (characters, ambiguous names).

    ``key`` is the caption's (see ``_caption_key``). ``hair`` is forced
    distinct across the cast (canonical order, so stable). A name is
    *ambiguous* when it shares hair with another character, and its ``ref``
    handle then falls back to the plain noun.
    """
    cfg = library.config
    characters: Dict[str, Dict[str, str]] = {}
    hair_owners: Dict[str, List[str]] = {}
    ambiguous: Set[str] = set()
    for name in cast:
        gender = genders[name]
        extra = [
            s
            for s in cfg.token_slots
            if s in referenced.get(name, ()) and s not in cfg.slots
        ]
        drawn: Dict[str, str] = {}
        for slot in (*cfg.slots, *extra):
            options = _values(library, slot, gender)
            if not options:
                if slot in cfg.slots:
                    _warn(
                        warnings,
                        f"no '{slot}' list for {gender} characters; left out of descriptions",
                    )
                continue
            if slot == "hair":
                value, clashed = _draw_distinct(key, name, slot, options, hair_owners)
                owners = hair_owners.setdefault(value.casefold(), [])
                owners.append(name)
                if clashed:
                    ambiguous.update(owners)
                    _warn(
                        warnings,
                        "the hair list has too few distinct values for this many "
                        "characters; some share hair and are called by their noun",
                    )
            else:
                value = options[_draw(key, name, slot, 0, len(options))]
            drawn[slot] = value
        characters[name] = drawn
    return characters, ambiguous


# --- Rendering (pass 2) -----------------------------------------------------


class _Renderer:
    def __init__(
        self,
        caption: str,
        seed: int,
        style: str,
        library: Library,
        genders: Dict[str, str],
        plan: _Plan,
        characters: Dict[str, Dict[str, str]],
        ambiguous: Set[str],
    ) -> None:
        self.caption = caption
        self.seed = seed
        self.style = style
        self.library = library
        self.cfg = library.config
        self.genders = genders
        self.plan = plan
        self.characters = characters
        self.ambiguous = ambiguous
        self.deferred: List[Tuple[str, List[Tuple[str, str]]]] = []

    def noun(self, name: str) -> str:
        female = self.genders[name] == _FEMALE
        return self.cfg.noun_female if female else self.cfg.noun_male

    def head_noun(self, name: str) -> str:
        """The noun of a first mention: ``middle aged woman`` or ``old man``
        when the drawn age says so, the plain noun otherwise (no age list, or
        an age value with no number in it)."""
        noun = self.noun(name)
        value = self.characters[name].get("age")
        age = stated_age(value) if value else None
        if age is None:
            return noun
        if age >= OLD_FROM:
            return f"old {noun}"
        if age >= MIDDLE_AGED_FROM:
            return f"middle aged {noun}"
        return noun

    def handle_carries_hair(self, name: str) -> bool:
        return (
            self.style == "ref"
            and name not in self.ambiguous
            and _hair_stem(self.characters[name].get("hair")) is not None
        )

    def handle(self, name: str) -> str:
        """The later-mention phrase for the style."""
        if self.style == "name":
            return name.capitalize()
        if self.handle_carries_hair(name):
            stem = _hair_stem(self.characters[name].get("hair"))
            return f"the {stem} {self.noun(name)}"
        return f"the {self.noun(name)}"

    def inline_ok(
        self,
        match: "re.Match[str]",
        prev: Optional["re.Match[str]"],
        prev_is_char: bool,
    ) -> bool:
        end = match.end()
        if _POSSESSIVE_RE.match(self.caption, end):
            return False
        kind, word = _next_context(self.caption, end)
        if kind == "punct" or (kind == "word" and word in _NON_INLINE_WORDS):
            return False
        if prev is not None and prev_is_char:
            gap = match.start() - prev.end()
            between = (
                self.caption[prev.end() : match.start()]
                if gap <= _COMPOUND_GAP_MAX
                else ""
            )
            if between.strip() in _COMPOUND_JOINERS:
                return False  # compound subject: "X and Y", "X, Y"
        return True

    def first_mention(
        self,
        name: str,
        match: "re.Match[str]",
        prev: Optional["re.Match[str]"],
        prev_is_char: bool,
    ) -> str:
        drawn = self.characters[name]
        head = " ".join(
            [drawn[s] for s in self.cfg.head_slots if s in drawn]
            + [self.head_noun(name)]
        )
        head = f"{_article(head)} {head}"
        if self.style == "name":
            head += f" named {name.capitalize()}"
        spelled = self.plan.referenced.get(name, set())
        details = [
            (s, drawn[s])
            for s in self.cfg.with_slots
            if s in drawn and s not in spelled
        ]
        if not details:
            return head
        if self.inline_ok(match, prev, prev_is_char):
            return f"{head} with {_join_and([v for _, v in details])}"
        self.deferred.append((name, details))
        return head

    def trailing_sentence(
        self, name: str, details: List[Tuple[str, str]]
    ) -> Optional[str]:
        """``The <handle> has a, b and c.`` -- the handle already says the hair."""
        if self.handle_carries_hair(name):
            rest = [(s, v) for s, v in details if s != "hair"]
            if rest:
                return (
                    f"{_cap1(self.handle(name))} has {_join_and([v for _, v in rest])}."
                )
            # Hair was the only detail: say it, without the redundant handle.
            subject = f"the {self.noun(name)}"
            return f"{_cap1(subject)} has {_join_and([v for _, v in details])}."
        return f"{_cap1(self.handle(name))} has {_join_and([v for _, v in details])}."

    def slot_value(self, ref: _Ref) -> str:
        if ref.bare or ref.name not in self.characters:
            return ref.slot
        return self.characters[ref.name].get(ref.slot, ref.slot)

    def wildcard_value(self, ref: _Ref) -> str:
        values = self.library.lists.get(ref.slot)
        if ref.bare or not values:
            return ref.slot
        token = ref.match.group(1)
        return values[_draw(self.seed, token, "wildcard", 0, len(values))]

    def render(self) -> str:
        out = _Emitter()
        seen: Set[str] = set()
        last = 0
        prev: Optional["re.Match[str]"] = None
        prev_is_char = False
        for ref in self.plan.refs:
            match = ref.match
            out.add(self.caption[last : match.start()])
            last = match.end()
            if ref.kind == "char":
                if ref.name in seen:
                    piece = self.handle(ref.name)
                else:
                    seen.add(ref.name)
                    piece = self.first_mention(ref.name, match, prev, prev_is_char)
            elif ref.kind == "slot":
                piece = self.slot_value(ref)
            else:
                piece = self.wildcard_value(ref)
            out.add(_cap1(piece) if out.at_sentence_start else piece)
            prev, prev_is_char = match, ref.kind == "char"
        out.add(self.caption[last:])
        return self._append_trailing(out.text())

    def _append_trailing(self, text: str) -> str:
        sentences = [
            s
            for s in (self.trailing_sentence(n, d) for n, d in self.deferred)
            if s is not None
        ]
        if not sentences:
            return text
        body = text.rstrip()
        tail = text[len(body) :]
        if not _ends_sentence(body):
            body = body.rstrip(",;:") + "."  # a dangling clause mark becomes a stop
        return f"{body} {' '.join(sentences)}{tail}"


def resolve_caption(
    caption: str, seed: int, style: str, library: Library
) -> ResolvedCaption:
    """Resolve every token in ``caption`` in identity ``style``.

    Pure and deterministic: the same inputs always give the same output. The
    characters depend on the caption text alone; ``seed`` only picks the value
    of plain wildcards. A caption with no tokens is returned unchanged. Never
    raises on data problems; an unknown ``style`` is treated as ``ref`` with a
    warning.
    """
    warnings: List[str] = []
    if style not in IDENTITY_STYLES:
        _warn(
            warnings,
            f"unknown identity style {style!r}; using {DEFAULT_IDENTITY_STYLE!r}",
        )
        style = DEFAULT_IDENTITY_STYLE
    matches = list(_TOKEN_RE.finditer(caption))
    if not matches:
        return ResolvedCaption(text=caption, characters={}, warnings=warnings)

    genders = _name_genders(library.config)
    plan = _Planner(caption, matches, library, genders, warnings).run()
    cast = [name for name in genders if name in plan.named or name in plan.defaults]
    characters, ambiguous = _draw_cast(
        _caption_key(caption), cast, genders, library, plan.referenced, warnings
    )
    renderer = _Renderer(
        caption, seed, style, library, genders, plan, characters, ambiguous
    )
    return ResolvedCaption(
        text=renderer.render(), characters=characters, warnings=warnings
    )
