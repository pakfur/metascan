"""Test helpers: an appearance config built from per-slot value lists.

The caption-engine tests pin wording with small hand-written slot lists
(``{"age": ("31-year-old",), "hair": ("copper red hair",), ...}``). These
helpers turn such lists into a ``person_appearance.yaml`` config whose
engine slots render exactly those values, so a test reads as before while
the people are drawn by the real sampler (``metascan.core.t2i_appearance``).

* every slot is an attribute whose values are the list's texts;
* ``<slot>.female`` / ``<slot>.male`` lists only draw for that gender, the
  plain list for a gender without its own; a slot with values for one
  gender only ``applies_when`` that gender;
* the engine lays a first mention out as the old default did: head
  ``age, ethnicity``, with ``skin, eyes, face, hair, body``; ``hair`` is the
  ``__HAIR__`` value and is kept distinct across a cast.

Never the shipped person_appearance.yaml and never user data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import yaml

from metascan.core.t2i_appearance import Appearance, parse
from metascan.core.t2i_characters import CharacterConfig, Library
from metascan.core.t2i_wildcards import _reject_reason

SLOTS: Tuple[str, ...] = ("age", "ethnicity", "skin", "eyes", "face", "hair", "body")
HEAD: Tuple[str, ...] = ("age", "ethnicity")
WITH: Tuple[str, ...] = ("skin", "eyes", "face", "hair", "body")
_GENDER = {"female": "woman", "male": "man"}


def appearance_config(
    lists: Mapping[str, Sequence[str]],
    slots: Sequence[str] = SLOTS,
    head: Sequence[str] = HEAD,
    with_: Sequence[str] = WITH,
    spells: Optional[Mapping[str, Sequence[str]]] = None,
) -> Dict[str, Any]:
    attrs: Dict[str, Any] = {
        "gender": {
            "values": {"woman": {"text": "woman"}, "man": {"text": "man"}},
        },
        "ancestry": {"values": {"any": {"text": ""}}},
    }
    used = []
    for slot in slots:
        values: Dict[str, Any] = {}
        gendered = {g: tuple(lists.get(f"{slot}.{g}", ())) for g in _GENDER}
        plain = tuple(lists.get(slot, ()))

        def add(texts: Sequence[str], genders: Sequence[str]) -> None:
            for text in texts:
                value: Dict[str, Any] = {"text": text}
                if len(genders) == 1:
                    value["requires"] = {"gender": list(genders)}
                values[f"v{len(values)}"] = value

        drawn_for = []
        for g, texts in gendered.items():
            if texts:
                add(texts, [_GENDER[g]])
                drawn_for.append(_GENDER[g])
        rest = [_GENDER[g] for g, texts in gendered.items() if not texts]
        if plain and rest:
            add(plain, rest)
            drawn_for += rest
        if not values:
            continue
        attr: Dict[str, Any] = {"values": values}
        if len(drawn_for) == 1:
            attr["applies_when"] = {"gender": drawn_for}
        attrs[slot] = attr
        used.append(slot)
    return {
        "attributes": attrs,
        "profiles": {"any": {}},
        "engine": {
            "genders": dict(_GENDER),
            "slots": {slot: "{" + slot + "}" for slot in used},
            "head": [s for s in head if s in used],
            "with": [s for s in with_ if s in used],
            "spells": {k: list(v) for k, v in (spells or {}).items()},
            "distinct": "hair" if "hair" in used else None,
        },
    }


def appearance_from_lists(
    lists: Mapping[str, Sequence[str]], **layout: Any
) -> Appearance:
    appearance, _ = parse(appearance_config(lists, **layout), _reject_reason)
    return appearance


def split_lists(
    lists: Mapping[str, Sequence[str]], slots: Sequence[str] = SLOTS
) -> Tuple[Dict[str, Tuple[str, ...]], Dict[str, Tuple[str, ...]]]:
    """(slot lists the appearance draws, every other list)."""
    mine: Dict[str, Tuple[str, ...]] = {}
    rest: Dict[str, Tuple[str, ...]] = {}
    for key, values in lists.items():
        (mine if key.split(".")[0] in slots else rest)[key] = tuple(values)
    return mine, rest


def library_from_lists(
    lists: Mapping[str, Sequence[str]],
    config: Optional[CharacterConfig] = None,
    **layout: Any,
) -> Library:
    slots = layout.get("slots", SLOTS)
    mine, rest = split_lists(lists, slots)
    return Library(
        config=config or CharacterConfig(),
        lists=rest,
        appearance=appearance_from_lists(mine, **layout),
    )


def appearance_yaml(lists: Mapping[str, Sequence[str]], **layout: Any) -> str:
    return yaml.safe_dump(appearance_config(lists, **layout), sort_keys=False)


def write_appearance(
    directory: Path, lists: Mapping[str, Sequence[str]], **layout: Any
) -> Path:
    path = Path(directory) / "person_appearance.yaml"
    path.write_text(appearance_yaml(lists, **layout), encoding="utf-8")
    return path


def write_list_files(directory: Path, files: Mapping[str, str]) -> None:
    """Write ``{"hair.txt": "auburn hair\\n...", "sky.txt": ...}`` as a test
    caption directory: the slot lists become person_appearance.yaml, every
    other file is written as it is."""
    directory = Path(directory)
    slot_lists: Dict[str, Tuple[str, ...]] = {}
    for name, text in files.items():
        stem = name[: -len(".txt")] if name.endswith(".txt") else name
        if stem.split(".")[0] in SLOTS:
            lines = [ln.strip() for ln in text.splitlines()]
            slot_lists[stem] = tuple(
                ln for ln in lines if ln and not ln.startswith("#")
            )
        else:
            (directory / name).write_text(text, encoding="utf-8")
    if slot_lists:
        write_appearance(directory, slot_lists)
