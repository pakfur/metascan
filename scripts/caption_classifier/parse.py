"""Turn a llama-server chat completion into one classification record.

Each single-letter answer's probabilities are read from ``top_logprobs`` at
the token that carries the letter. The tokenizer may merge the letter with
the characters before it (``"B`` instead of ``B``), so the letter is found by
character offset in the answer text, and a candidate token counts toward a
letter when it starts with the same characters as the chosen token up to the
letter. The letter fields all come before the free-text ``issues`` part, so
only that ASCII prefix has to line up with the token stream.
"""

from __future__ import annotations

import bisect
import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .rubric import ACT_BY_LETTER, EMOTION, KISS, PARTNER


class ParseError(ValueError):
    """The model's answer cannot be turned into a record."""


LETTER_FIELDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("partner", tuple(PARTNER)),
    ("kiss", tuple(KISS)),
    ("emotion", tuple(EMOTION)),
    ("act", tuple(ACT_BY_LETTER)),
)


def _value_offset(content: str, field: str) -> int:
    marker = f'"{field}":"'
    index = content.find(marker)
    if index < 0:
        raise ParseError(f"field {field!r} missing from the answer")
    return index + len(marker)


def letter_distribution(
    token: Mapping[str, Any], prefix: str, letters: Sequence[str]
) -> Dict[str, float]:
    """Probabilities over ``letters`` at ``token``, renormalised to sum to 1."""
    logprobs: Dict[str, float] = {}
    candidates = list(token.get("top_logprobs") or [])
    candidates.append({"token": token["token"], "logprob": token["logprob"]})
    for cand in candidates:
        logprobs.setdefault(str(cand["token"]), float(cand["logprob"]))
    mass = {letter: 0.0 for letter in letters}
    for text, logprob in logprobs.items():
        if len(text) > len(prefix) and text.startswith(prefix):
            letter = text[len(prefix)]
            if letter in mass:
                mass[letter] += math.exp(logprob)
    total = sum(mass.values())
    if total <= 0:
        raise ParseError("no candidate token carries an allowed letter")
    return {letter: round(p / total, 4) for letter, p in mass.items()}


def field_distributions(
    content: str, tokens: Sequence[Mapping[str, Any]]
) -> Dict[str, Dict[str, float]]:
    """One renormalised letter distribution per field in ``LETTER_FIELDS``."""
    offsets = {field: _value_offset(content, field) for field, _ in LETTER_FIELDS}
    usable: List[Mapping[str, Any]] = [t for t in tokens if t.get("token")]
    starts: List[int] = []
    pos = 0
    for tok in usable:
        starts.append(pos)
        pos += len(tok["token"])
    text = "".join(str(t["token"]) for t in usable)
    end = offsets[LETTER_FIELDS[-1][0]] + 1
    if text[:end] != content[:end]:
        raise ParseError("token stream does not match the answer text")
    out: Dict[str, Dict[str, float]] = {}
    for field, letters in LETTER_FIELDS:
        offset = offsets[field]
        index = bisect.bisect_right(starts, offset) - 1
        prefix = content[starts[index] : offset]
        out[field] = letter_distribution(usable[index], prefix, letters)
    return out
