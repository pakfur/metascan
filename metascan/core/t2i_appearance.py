"""Plausibility-constrained person sampler for the T2I caption engine.

``data/t2i_captions/person_appearance.yaml`` describes how a person's
appearance is drawn: attributes sampled in file order (each may depend only
on attributes above it), weights from population ``profiles`` or per-value
tables, ``compose`` rules for derived phrases and a placeholder language
(``{attr}``, ``{attr.field}``, ``{composed}``, ``|a``, ``|cap``). The file's
own header documents the format.

Its ``engine`` section says what the caption engine
(:mod:`metascan.core.t2i_characters`) takes from a sampled person::

    engine:
      genders: {female: woman, male: man}   # a name's gender -> gender value id
      slots:                                 # slot -> template
        age: "{apparent_age.years}-year-old"
        hair: "{hair_token}"                 # __HAIR__ and the ref handle
        ...
      head: [age, ethnicity]                 # in front of the noun
      with: [skin, eyes, face, hairstyle, body]   # after "with"
      spells: {hair: [hairstyle]}            # a caption that writes __HAIR__
                                             # drops these from the intro
      distinct: hair                         # kept different across a cast

A person is drawn from a ``random.Random`` seeded with a sha256 of the
caption key, the character's name and a salt, so the same caption always
draws the same people (``random.Random`` seeded with an int is stable across
processes and Python versions). Pure: no I/O. :func:`parse` validates the
config and screens every text with the screen it is given (the adult-only
screen of :mod:`metascan.core.t2i_wildcards`), and :meth:`Appearance.person`
screens every rendered slot again, so nothing that fails the screen can
reach a prompt.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

FILENAME = "person_appearance.yaml"
UNSET = "_unset"
PH = re.compile(r"\{([a-z_]+)(?:\.([a-z_]+))?((?:\|[a-z]+)*)\}")
_DISABLED = "_disabled"
# A person whose attributes leave some later attribute with no valid value is
# drawn again with the next salt; a config that keeps failing gives up.
MAX_ATTEMPTS = 8
# Salts tried for a person whose distinct slot (hair) another cast member has.
DISTINCT_TRIES = 16
_MAX_DEPTH = 6

Screen = Callable[[str, str], Optional[str]]
State = Dict[str, Dict[str, Any]]


class AppearanceError(ValueError):
    """The config is unusable; the message lists why."""


def article(word: str) -> str:
    w = word.lower()
    vowel = w[:1] in "aeiou" and not w.startswith(("uni", "use", "eu", "one"))
    return ("an " if vowel or w.startswith(("hour", "honest")) else "a ") + word


def _join(texts: Sequence[str]) -> str:
    ts = [t for t in texts if t]
    if not ts:
        return ""
    if len(ts) == 1:
        return ts[0]
    return ", ".join(ts[:-1]) + " and " + ts[-1]


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\s+([,.])", r"\1", text)).strip()


class Sampler:
    """Draws one person per :meth:`sample` from a validated config."""

    def __init__(self, cfg: Mapping[str, Any], rng: random.Random) -> None:
        self.cfg = cfg
        self.attrs: Dict[str, Any] = cfg["attributes"]
        self.order = list(self.attrs)
        self.profiles: Dict[str, Any] = cfg.get("profiles") or {}
        self.rng = rng

    # ------------------------------------------------------------- matching
    def identities(self, state: State, attr: str) -> Set[str]:
        """The ids a sampled attribute matches (multi-pick / mixed -> several)."""
        s = state.get(attr)
        if s is None:
            return {UNSET}
        ids = set(s["ids"])
        if attr == "ancestry":
            ids |= set(s.get("parents", []))
        return ids

    def matches(self, state: State, attr: str, wanted: Sequence[str]) -> bool:
        ids = self.identities(state, attr)
        groups = self.attrs.get(attr, {}).get("groups") or {}
        for w in wanted:
            if w in ids or (w in groups and ids & set(groups[w])):
                return True
        return False

    def when_ok(self, state: State, when: Optional[Mapping[str, Any]]) -> bool:
        return all(self.matches(state, a, w) for a, w in (when or {}).items())

    # -------------------------------------------------------------- weights
    def profile_table(self, state: State, attr: str) -> Optional[Dict[str, float]]:
        def table(pid: str) -> Optional[Dict[str, float]]:
            p = self.profiles.get(pid) or {}
            found = p.get(attr, (self.profiles.get("_default") or {}).get(attr))
            return dict(found) if found is not None else None

        anc = state["ancestry"]
        parents = anc.get("parents")
        if not parents:
            return table(anc["ids"][0])
        merged: Dict[str, float] = {}
        for pid in parents:  # average the normalised distributions
            t = table(pid) or {}
            tot = sum(t.values()) or 1
            for k, v in t.items():
                merged[k] = merged.get(k, 0) + v / tot / len(parents)
        return merged

    def base_weight(
        self, state: State, name: str, attr: Mapping[str, Any], vid: str, v: Any
    ) -> float:
        if "weights_from" in attr:
            t = self.profile_table(state, name)
            if t is not None:
                return float(t.get(vid, 0))
        if "weights_by" in attr:
            wb = attr["weights_by"]
            for key, row in wb["table"].items():
                if self.matches(state, wb["attr"], [key]):
                    return float(row.get(vid, 0))
            return 0.0
        return float(v.get("weight", 1))

    def weight(
        self, state: State, name: str, attr: Mapping[str, Any], vid: str, v: Any
    ) -> float:
        if v.get(_DISABLED):
            return 0.0
        for a, allowed in (v.get("requires") or {}).items():
            if not self.matches(state, a, allowed):
                return 0.0
        w = self.base_weight(state, name, attr, vid, v)
        for a, table in (v.get("mult") or {}).items():
            for key, f in table.items():
                if self.matches(state, a, [key]):
                    w *= f
        for adj in v.get("adjust") or []:
            if self.when_ok(state, adj.get("when")):
                w *= adj["factor"]
        return w

    def choose(self, weighted: Sequence[Tuple[str, float]]) -> Optional[str]:
        tot = sum(w for _, w in weighted)
        if tot <= 0:
            return None
        r = self.rng.uniform(0, tot)
        for k, w in weighted:
            r -= w
            if r <= 0:
                return k
        return weighted[-1][0]

    # ----------------------------------------------------------------- text
    def pick_text(self, state: State, attr_name: str, v: Mapping[str, Any]) -> str:
        t: Any = v.get("text", "")
        if isinstance(t, dict):
            by = self.attrs[attr_name].get("text_by", "gender")
            chosen = None
            for k, val in t.items():
                if k != "default" and self.matches(state, by, [k]):
                    chosen = val
                    break
            t = chosen if chosen is not None else t.get("default", "")
        if isinstance(t, list):
            t = self.rng.choice(t) if t else ""
        return str(t)

    def field(self, v: Mapping[str, Any], f: str) -> str:
        x = v.get(f, "")
        if isinstance(x, list):
            if len(x) == 2 and all(isinstance(i, int) for i in x):
                return str(self.rng.randint(x[0], x[1]))
            return str(self.rng.choice(x)) if x else ""
        return str(x)

    # ------------------------------------------------------------- sampling
    def sample(self, fixed: Optional[Mapping[str, str]] = None) -> State:
        """One person. Raises :class:`RuntimeError` when an attribute is left
        with no valid value (the caller draws again with another salt)."""
        fixed = fixed or {}
        state: State = {}
        for name in self.order:
            attr = self.attrs[name]
            if not self.when_ok(state, attr.get("applies_when")) and name not in fixed:
                continue
            vals = attr["values"]
            if name == "ancestry":
                self._sample_ancestry(state, attr, fixed.get(name))
                continue
            anc = state.get("ancestry", {})
            blend = anc.get("blend") or {}
            if (
                anc.get("parents")
                and name in blend.get("ordinal", [])
                and name not in fixed
            ):
                vid = self._ordinal_blend(state, name, attr, anc)
                state[name] = {
                    "ids": [vid],
                    "texts": [self.pick_text(state, name, vals[vid])],
                }
                continue
            if name in fixed:
                ids = [i for i in fixed[name].split(",") if i in vals]
            else:
                cands = [
                    (vid, self.weight(state, name, attr, vid, v))
                    for vid, v in vals.items()
                ]
                ids = self._pick(cands, attr.get("pick"), vals)
            if not ids:
                raise RuntimeError(f"no valid value for '{name}'")
            state[name] = {
                "ids": ids,
                "texts": [self.pick_text(state, name, vals[i]) for i in ids],
            }
        return state

    def _pick(
        self,
        cands: Sequence[Tuple[str, float]],
        pick: Optional[Mapping[str, Any]],
        vals: Mapping[str, Any],
    ) -> List[str]:
        if not pick:
            c = self.choose(cands)
            return [c] if c else []
        k = self.rng.randint(pick.get("min", 1), pick.get("max", 1))
        out: List[str] = []
        pool = dict(cands)
        while len(out) < k:
            c = self.choose([(i, w) for i, w in pool.items() if w > 0])
            if c is None:
                break
            out.append(c)
            pool.pop(c)
            for ex in vals[c].get("excludes", []):
                pool.pop(ex, None)
            for other in list(pool):
                if c in vals[other].get("excludes", []):
                    pool.pop(other)
        return out

    def _sample_ancestry(
        self, state: State, attr: Mapping[str, Any], fixed: Optional[str]
    ) -> None:
        vals = attr["values"]
        vid = fixed if fixed in vals else None
        if vid is None:
            vid = self.choose(
                [
                    (k, self.weight(state, "ancestry", attr, k, v))
                    for k, v in vals.items()
                ]
            )
        if vid is None:
            raise RuntimeError("no valid value for 'ancestry'")
        v = vals[vid]
        entry: Dict[str, Any] = {"ids": [vid]}
        if "blend" in v:
            pure = [
                (k, float(x.get("weight", 1)))
                for k, x in vals.items()
                if "blend" not in x and not x.get(_DISABLED)
            ]
            parents: List[str] = []
            while len(parents) < v["blend"].get("count", 2):
                p = self.choose([(k, w) for k, w in pure if k not in parents])
                if p is None:
                    raise RuntimeError("no parent ancestry for a blend")
                parents.append(p)
            entry["parents"], entry["blend"] = parents, v["blend"]
            names = {
                chr(97 + i): self.pick_text(state, "ancestry", vals[p])
                for i, p in enumerate(parents)
            }
            entry["texts"] = [str(v["text"]).format(**names)]
        else:
            entry["texts"] = [self.pick_text(state, "ancestry", v)]
        state["ancestry"] = entry

    def _ordinal_blend(
        self,
        state: State,
        name: str,
        attr: Mapping[str, Any],
        anc: Mapping[str, Any],
    ) -> str:
        order = list(attr["values"])
        idx: List[int] = []
        for p in anc["parents"]:
            sub = dict(state, ancestry={"ids": [p], "texts": [""]})
            cands = [
                (vid, self.weight(sub, name, attr, vid, v))
                for vid, v in attr["values"].items()
            ]
            chosen = self.choose(cands)
            if chosen is None:
                raise RuntimeError(f"no valid value for '{name}'")
            idx.append(order.index(chosen))
        lo, hi = min(idx), max(idx)
        j = int(anc["blend"].get("ordinal_jitter", 0))
        mid = (lo + hi) / 2
        i = int(mid) + (self.rng.random() < mid - int(mid))  # unbiased .5 rounding
        i += self.rng.randint(-j, j)
        return str(order[max(0, min(len(order) - 1, i))])

    # --------------------------------------------------------------- render
    def compose(self, state: State) -> Dict[str, str]:
        """Every ``compose`` field for ``state``: the first matching rule wins."""
        composed: Dict[str, str] = {}
        for cname, rules in (self.cfg.get("compose") or {}).items():
            for rule in rules:
                if self.when_ok(state, rule.get("when")):
                    t = rule.get("text", "")
                    t = self.rng.choice(t) if isinstance(t, list) else t
                    composed[cname] = self.fmt(state, composed, str(t))
                    break
        return composed

    def render(self, state: State, text: str, composed: Mapping[str, str]) -> str:
        return _tidy(self.fmt(state, composed, text))

    def fmt(
        self, state: State, composed: Mapping[str, str], text: str, depth: int = 0
    ) -> str:
        def rep(m: "re.Match[str]") -> str:
            name, fld = m.group(1), m.group(2)
            filters = m.group(3).split("|")[1:]
            if name in composed and not fld:
                r = composed[name]
            elif name in state:
                s = state[name]
                if fld:
                    r = self.field(self.attrs[name]["values"][s["ids"][0]], fld)
                else:
                    r = _join(s["texts"])
            elif name in self.attrs:
                r = ""  # an unset attribute
            else:
                return m.group(0)
            r = self.fmt(state, composed, r, depth + 1) if depth < _MAX_DEPTH else r
            for f in filters:
                if f == "a" and r:
                    r = article(r)
                elif f == "cap":
                    r = r[:1].upper() + r[1:]
            return r

        return PH.sub(rep, text)

    def summary(self, state: State) -> Dict[str, str]:
        """attribute -> value id (several joined with ``; ``)."""
        out = {k: "; ".join(v["ids"]) for k, v in state.items()}
        parents = state.get("ancestry", {}).get("parents")
        if parents:
            out["ancestry_parents"] = "; ".join(parents)
        return out


# --------------------------------------------------------------------- engine


@dataclass(frozen=True)
class Engine:
    genders: Mapping[str, str]  # "female" / "male" -> gender value id
    slots: Mapping[str, str]  # slot -> template
    head: Tuple[str, ...]
    with_: Tuple[str, ...]
    spells: Mapping[str, Tuple[str, ...]]  # token slot -> with-slots it replaces
    distinct: Optional[str]


@dataclass(frozen=True)
class Person:
    slots: Dict[str, str]  # slot -> text (empty values left out)
    attributes: Dict[str, str]  # attribute -> value id(s)
    clashed: bool = False  # the distinct slot matches another cast member's
    warnings: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Appearance:
    """A parsed, validated, screened ``person_appearance.yaml``."""

    cfg: Mapping[str, Any]
    engine: Engine
    screen: Optional[Screen] = None

    def _rng(self, key: str, name: str, salt: int) -> random.Random:
        digest = hashlib.sha256(f"{key}|{name}|appearance|{salt}".encode()).digest()
        return random.Random(int.from_bytes(digest[:8], "big"))

    def _draw(
        self, key: str, name: str, salt: int, fixed: Mapping[str, str]
    ) -> Optional[Tuple[State, Sampler]]:
        for attempt in range(MAX_ATTEMPTS):
            sampler = Sampler(
                self.cfg, self._rng(key, name, salt * MAX_ATTEMPTS + attempt)
            )
            try:
                return sampler.sample(fixed), sampler
            except RuntimeError:
                continue
        return None

    def _render(
        self, state: State, sampler: Sampler
    ) -> Tuple[Dict[str, str], List[str]]:
        composed = sampler.compose(state)
        slots: Dict[str, str] = {}
        rejected: List[str] = []
        for slot, template in self.engine.slots.items():
            text = sampler.render(state, template, composed)
            if not text:
                continue
            reason = self.screen(slot, text) if self.screen else None
            if reason is not None:
                rejected.append(f"{FILENAME}: a drawn '{slot}' was rejected: {reason}")
                continue
            slots[slot] = text
        return slots, rejected

    def person(
        self,
        key: str,
        name: str,
        gender: str,
        taken: Optional[Set[str]] = None,
    ) -> Optional[Person]:
        """The person for character ``name`` of the caption ``key``; ``gender``
        is ``female`` or ``male``. ``taken`` holds the casefolded distinct-slot
        values of the cast drawn so far: the person is drawn again, keeping
        the ancestry, until theirs differs (``clashed`` when it never does).
        None when the config can draw nobody."""
        fixed: Dict[str, str] = {}
        gender_id = self.engine.genders.get(gender)
        if gender_id is not None and "gender" in self.cfg["attributes"]:
            fixed["gender"] = gender_id
        first = self._draw(key, name, 0, fixed)
        if first is None:
            return None
        state, sampler = first
        slots, warnings = self._render(state, sampler)
        distinct = self.engine.distinct
        if (
            distinct is None
            or not taken
            or slots.get(distinct, "").casefold() not in taken
        ):
            return Person(slots, sampler.summary(state), False, tuple(warnings))
        again = dict(fixed)
        if "ancestry" in state:
            again["ancestry"] = state["ancestry"]["ids"][0]
        for salt in range(1, DISTINCT_TRIES):
            drawn = self._draw(key, name, salt, again)
            if drawn is None:
                continue
            other_slots, other_warnings = self._render(*drawn)
            if other_slots.get(distinct, "").casefold() not in taken:
                return Person(
                    other_slots,
                    drawn[1].summary(drawn[0]),
                    False,
                    tuple(other_warnings),
                )
        return Person(
            slots, sampler.summary(state), bool(slots.get(distinct)), tuple(warnings)
        )


# ----------------------------------------------------------------- validation


def validate(cfg: Mapping[str, Any]) -> List[str]:
    """Every problem with the attribute / profile / compose structure."""
    errs: List[str] = []
    attrs = cfg.get("attributes")
    if not isinstance(attrs, dict) or not attrs:
        return ["'attributes' must be a non-empty mapping"]
    for name, attr in attrs.items():
        if not isinstance(attr, dict) or not isinstance(attr.get("values"), dict):
            errs.append(f"{name}: needs a 'values' mapping")
    if errs:
        return errs
    if "ancestry" not in attrs:
        return ["'attributes' needs an 'ancestry' attribute"]
    order = list(attrs)
    compose = cfg.get("compose") or {}

    def check_ref(where: str, pos: Optional[int], a: str, keys: Any) -> None:
        if a not in attrs:
            errs.append(f"{where}: unknown attribute '{a}'")
            return
        if pos is not None and order.index(a) >= pos:
            errs.append(f"{where}: depends on '{a}' which is sampled later")
        valid = set(attrs[a]["values"]) | set(attrs[a].get("groups") or {}) | {UNSET}
        for k in keys:
            if k not in valid:
                errs.append(f"{where}: '{k}' is not a value/group of '{a}'")

    for pos, (name, attr) in enumerate(attrs.items()):
        vals = attr["values"]
        for g, members in (attr.get("groups") or {}).items():
            if g in vals:
                errs.append(f"{name}: group '{g}' collides with a value id")
            for m in members:
                if m not in vals:
                    errs.append(f"{name}.groups.{g}: unknown '{m}'")
        for a, keys in (attr.get("applies_when") or {}).items():
            check_ref(f"{name}.applies_when", pos, a, keys)
        if "weights_by" in attr:
            wb = attr["weights_by"]
            check_ref(f"{name}.weights_by", pos, wb["attr"], wb["table"].keys())
            for row, tbl in wb["table"].items():
                for vid in tbl:
                    if vid not in vals:
                        errs.append(f"{name}.weights_by.{row}: unknown value '{vid}'")
        for vid, v in vals.items():
            w = f"{name}.{vid}"
            if not isinstance(v, dict):
                errs.append(f"{w}: must be a mapping")
                continue
            for a, keys in (v.get("requires") or {}).items():
                check_ref(w + ".requires", pos, a, keys)
            for a, tbl in (v.get("mult") or {}).items():
                check_ref(w + ".mult", pos, a, tbl.keys())
            for adj in v.get("adjust") or []:
                for a, keys in adj["when"].items():
                    check_ref(w + ".adjust", pos, a, keys)
            for ex in v.get("excludes", []):
                if ex not in vals:
                    errs.append(f"{w}.excludes: unknown '{ex}'")
            for m in PH.finditer(json.dumps(v.get("text", ""))):
                if (
                    m.group(1) not in attrs
                    and m.group(1) not in compose
                    and m.group(1) not in ("a", "b")
                ):
                    errs.append(f"{w}.text: unknown placeholder '{m.group(1)}'")
    profs = cfg.get("profiles") or {}
    for pid, p in profs.items():
        if pid != "_default" and pid not in attrs["ancestry"]["values"]:
            errs.append(f"profiles.{pid}: not an ancestry value")
        for a, tbl in p.items():
            if a not in attrs:
                errs.append(f"profiles.{pid}: unknown attribute '{a}'")
                continue
            for vid in tbl:
                if vid not in attrs[a]["values"]:
                    errs.append(f"profiles.{pid}.{a}: unknown value '{vid}'")
    for vid, v in attrs["ancestry"]["values"].items():
        if "blend" not in v and vid not in profs:
            errs.append(f"ancestry.{vid}: no profile")
    for cname, rules in compose.items():
        for r in rules:
            for a, keys in (r.get("when") or {}).items():
                check_ref(f"compose.{cname}", None, a, keys)
    return errs


def _placeholders_ok(
    where: str, text: str, cfg: Mapping[str, Any], errs: List[str]
) -> None:
    attrs = cfg["attributes"]
    compose = cfg.get("compose") or {}
    for m in PH.finditer(text):
        if m.group(1) not in attrs and m.group(1) not in compose:
            errs.append(f"{where}: unknown placeholder '{m.group(1)}'")


def _names(value: Any, where: str, errs: List[str]) -> Tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
        errs.append(f"{where}: must be a list of slot names")
        return ()
    return tuple(value)


def _engine(cfg: Mapping[str, Any], errs: List[str]) -> Optional[Engine]:
    raw = cfg.get("engine")
    if not isinstance(raw, dict):
        errs.append("'engine' must be a mapping (see the module docs)")
        return None
    slots = raw.get("slots")
    if not isinstance(slots, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in slots.items()
    ):
        errs.append("engine.slots: must map slot names to template text")
        return None
    for slot, template in slots.items():
        _placeholders_ok(f"engine.slots.{slot}", template, cfg, errs)
    head = _names(raw.get("head", []), "engine.head", errs)
    with_ = _names(raw.get("with", []), "engine.with", errs)
    for where, names in (("engine.head", head), ("engine.with", with_)):
        for n in names:
            if n not in slots:
                errs.append(f"{where}: '{n}' is not an engine slot")
    spells: Dict[str, Tuple[str, ...]] = {}
    for token, replaced in (raw.get("spells") or {}).items():
        spells[str(token)] = _names(replaced, f"engine.spells.{token}", errs)
        for n in spells[str(token)]:
            if n not in slots:
                errs.append(f"engine.spells.{token}: '{n}' is not an engine slot")
    genders_raw = raw.get("genders") or {}
    genders: Dict[str, str] = {}
    gender_vals = cfg["attributes"].get("gender", {}).get("values", {})
    for g, vid in genders_raw.items():
        if g not in ("female", "male"):
            errs.append(f"engine.genders: '{g}' is not female or male")
        elif vid not in gender_vals:
            errs.append(f"engine.genders.{g}: '{vid}' is not a gender value")
        else:
            genders[g] = str(vid)
    distinct = raw.get("distinct")
    if distinct is not None and distinct not in slots:
        errs.append(f"engine.distinct: '{distinct}' is not an engine slot")
        distinct = None
    return Engine(genders, dict(slots), head, with_, spells, distinct)


def _screen_text(
    value: Any, slot: str, screen: Screen, where: str, warnings: List[str]
) -> Tuple[Any, bool]:
    """``value`` (a text, list of texts or map of those) without the texts the
    screen rejects; the flag says whether anything is left."""
    if isinstance(value, dict):
        out_map: Dict[str, Any] = {}
        for k, v in value.items():
            kept, ok = _screen_text(v, slot, screen, f"{where}.{k}", warnings)
            if ok:
                out_map[k] = kept
        return out_map, bool(out_map)
    if isinstance(value, list):
        kept_list = []
        for i, item in enumerate(value):
            reason = screen(slot, str(item))
            if reason is None:
                kept_list.append(item)
            else:
                warnings.append(f"{where}[{i}]: rejected: {reason}")
        return kept_list, bool(kept_list)
    reason = screen(slot, str(value))
    if reason is not None:
        warnings.append(f"{where}: rejected: {reason}")
        return "", False
    return value, True


_STRUCTURAL = frozenset(
    {"requires", "mult", "adjust", "excludes", "blend", "weight", _DISABLED}
)


def _screen_config(
    cfg: Dict[str, Any], screen: Screen, errs: List[str], warnings: List[str]
) -> None:
    """Screen every value text and field in place. A value left with no text
    is disabled (weight 0), and so is a value whose ``years`` range starts
    under 18; a compose or engine text that fails is an error (they are the
    frame of every prompt)."""
    for name, attr in cfg["attributes"].items():
        for vid, v in attr["values"].items():
            where = f"{FILENAME}: {name}.{vid}"
            for key in list(v):
                raw = v[key]
                if key in _STRUCTURAL or raw is None:
                    continue
                if isinstance(raw, (bool, int, float)):
                    continue
                if (
                    isinstance(raw, list)
                    and raw
                    and all(isinstance(i, int) for i in raw)
                ):
                    if key == "years" and min(raw) < 18:
                        warnings.append(f"{where}.{key}: rejected: age under 18")
                        v[_DISABLED] = True
                    continue
                kept, ok = _screen_text(raw, name, screen, f"{where}.{key}", warnings)
                v[key] = kept
                if not ok and key == "text":
                    v[_DISABLED] = True
    for cname, rules in (cfg.get("compose") or {}).items():
        for i, rule in enumerate(rules):
            texts = rule.get("text", "")
            for t in texts if isinstance(texts, list) else [texts]:
                reason = screen(str(cname), str(t))
                if reason is not None:
                    errs.append(f"compose.{cname}[{i}]: rejected: {reason}")
    for slot, template in ((cfg.get("engine") or {}).get("slots") or {}).items():
        reason = screen(str(slot), str(template))
        if reason is not None:
            errs.append(f"engine.slots.{slot}: rejected: {reason}")


def parse(data: Any, screen: Optional[Screen] = None) -> Tuple[Appearance, List[str]]:
    """``(appearance, warnings)`` for a loaded YAML document.

    Raises :class:`AppearanceError` listing every structural problem. Texts
    the ``screen`` rejects are dropped with a warning (a value left with no
    text is never drawn); never mutates ``data``."""
    if not isinstance(data, dict):
        raise AppearanceError("must be a mapping")
    cfg: Dict[str, Any] = copy.deepcopy(data)
    errs = validate(cfg)
    if errs:
        raise AppearanceError("; ".join(errs))
    warnings: List[str] = []
    engine = _engine(cfg, errs)
    if screen is not None and not errs:
        _screen_config(cfg, screen, errs, warnings)
    if errs or engine is None:
        raise AppearanceError("; ".join(errs))
    return Appearance(cfg, engine, screen), warnings
