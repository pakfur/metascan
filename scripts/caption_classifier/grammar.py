"""GBNF grammar for the classifier's one-line JSON answer.

Fields come in a fixed order so every single-letter answer sits at a known
place in the token stream. Hyphens are never escaped (``\\-`` crashes
llama-server, see .claude/rules/vlm-llama.md); letter classes list every
letter explicitly.
"""

from __future__ import annotations

from typing import Iterable

from .rubric import (
    ACT_BY_LETTER,
    EMOTION,
    ISSUE_TYPES,
    KISS,
    MAX_ISSUES,
    MAX_QUOTE_CHARS,
    PARTNER,
)


def _lit(text: str) -> str:
    """A GBNF string literal for ``text``."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _letters(keys: Iterable[str]) -> str:
    return "[" + "".join(keys) + "]"


def build_grammar() -> str:
    root = " ".join(
        [
            _lit('{"partner":"'),
            "partner",
            _lit('","kiss":"'),
            "kiss",
            _lit('","emotion":"'),
            "emotion",
            _lit('","act":"'),
            "act",
            _lit('","issues":['),
            "issues",
            _lit("]}"),
        ]
    )
    issue = " ".join(
        [
            _lit('{"type":"'),
            "itype",
            _lit('","quote_a":"'),
            "quote",
            _lit('","quote_b":"'),
            "quote",
            _lit('"}'),
        ]
    )
    rules = [
        f"root ::= {root}",
        f"partner ::= {_letters(PARTNER)}",
        f"kiss ::= {_letters(KISS)}",
        f"emotion ::= {_letters(EMOTION)}",
        f"act ::= {_letters(ACT_BY_LETTER)}",
        f"issues ::= ( issue ( {_lit(',')} issue ){{0,{MAX_ISSUES - 1}}} )?",
        f"issue ::= {issue}",
        "itype ::= " + " | ".join(_lit(name) for name in ISSUE_TYPES),
        f"quote ::= qchar{{1,{MAX_QUOTE_CHARS}}}",
        r'qchar ::= [^"\\\r\n]',
    ]
    return "\n".join(rules) + "\n"


GRAMMAR = build_grammar()
