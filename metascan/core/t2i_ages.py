"""The number an age value states, in digits or spelled out.

One reader for the two places that need it, so they can never disagree about
``forty-five-year-old``: the caption engine (``t2i_characters``, which words a
first mention uses for a 40+ or 70+ subject) and the list loader
(``t2i_wildcards``, whose adult-only screen reads the same vocabulary). Pure:
no I/O and no other t2i imports, so both can depend on it.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

UNIT_WORDS: Dict[str, int] = {
    word: number
    for number, word in enumerate(
        (
            "zero one two three four five six seven eight nine ten eleven twelve "
            "thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
        ).split()
    )
}
TENS_WORDS: Dict[str, int] = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fourty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
# A spelled-out number below 100. The tens-plus-unit form comes first so
# "twenty-one" is one number (21), not "twenty" and a stray "one".
NUMBER_WORD = (
    r"(?:(?:"
    + "|".join(TENS_WORDS)
    + r")(?:[\s-]+(?:"
    + "|".join(w for w, n in UNIT_WORDS.items() if 1 <= n <= 9)
    + r"))?|"
    + "|".join(sorted(UNIT_WORDS, key=len, reverse=True))
    + r")"
)
SPELLED_NUMBER_RE = re.compile(r"\b" + NUMBER_WORD + r"\b", re.IGNORECASE)

# The first number in a value: a run of one to three digits that is not part
# of a longer run ("1990" is a year, not an age), or a spelled-out number.
_STATED_AGE_RE = re.compile(
    r"(?<!\d)(\d{1,3})(?!\d)|\b(" + NUMBER_WORD + r")\b", re.IGNORECASE
)


def spelled_value(words: str) -> int:
    """The value of a spelled-out number matched by ``NUMBER_WORD``."""
    return sum(
        TENS_WORDS.get(word, UNIT_WORDS.get(word, 0))
        for word in re.split(r"[\s-]+", words.lower())
    )


def stated_age(text: str) -> Optional[int]:
    """The first number in ``text``, digits or words, or None when it has none.

    ``27-year-old`` -> 27, ``in her 50s`` -> 50, ``Seventy two year old`` -> 72,
    ``35-to-45-year-old`` -> 35 (the first number wins), ``elderly`` -> None.
    """
    found = _STATED_AGE_RE.search(text)
    if found is None:
        return None
    if found.group(1) is not None:
        return int(found.group(1))
    return spelled_value(found.group(2))
