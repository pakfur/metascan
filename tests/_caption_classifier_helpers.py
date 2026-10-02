"""Builds classifier answers and matching llama-server token streams for tests."""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _token(text: str, dist: Dict[str, float]) -> Dict[str, Any]:
    chosen = dist.get(text, 1e-6)
    return {
        "token": text,
        "logprob": math.log(chosen),
        "top_logprobs": [{"token": t, "logprob": math.log(p)} for t, p in dist.items()],
    }


def answer(
    partner: str = "A",
    kiss: str = "N",
    emotion: str = "A",
    act: str = "A",
    issues: Optional[Sequence[Dict[str, str]]] = None,
    alts: Optional[Dict[str, Dict[str, float]]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """The answer text plus one token per skeleton piece and per letter.

    ``alts`` maps a field name to its candidate letters and probabilities;
    a field without alts is certain.
    """
    alts = alts or {}
    tail = json.dumps(list(issues or []), separators=(",", ":"), ensure_ascii=False)
    pieces = [
        ('{"partner":"', None),
        (partner, "partner"),
        ('","kiss":"', None),
        (kiss, "kiss"),
        ('","emotion":"', None),
        (emotion, "emotion"),
        ('","act":"', None),
        (act, "act"),
        ('","issues":' + tail + "}", None),
    ]
    tokens = [
        _token(text, alts.get(field, {text: 1.0}) if field else {text: 1.0})
        for text, field in pieces
    ]
    return "".join(text for text, _ in pieces), tokens
