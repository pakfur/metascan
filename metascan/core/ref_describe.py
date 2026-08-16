"""Reference-image describe: grammars, validators, prompt accessors.

Pure module; the VLM calls live in backend/api/storyboard.py's describe
routes. System prompts are YAML-backed via the prompt store.

NOTE: access the *_SYSTEM prompts as module attributes at call time
(``rd.REF_DESCRIBE_SUBJECT_SYSTEM``). A module-scope
``from ... import REF_DESCRIBE_SUBJECT_SYSTEM`` freezes a snapshot and
defeats the YAML hot-reload — each fresh attribute access re-resolves
against the live prompt store, exactly as ``vlm_prompts.py`` documents
for its own ``TAGGING_*`` attributes.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

_COMMON = r"""nullable ::= string | "null"
string ::= "\"" char* "\""
char ::= [^"\\\x7F\x00-\x1F] | "\\" (["\\bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])
ws ::= [ \t\n]*
"""

SUBJECT_DESCRIBE_GRAMMAR = (
    r"""root ::= "{{" ws "\"description\"" ws ":" ws string ws "," ws "\"voice\"" ws ":" ws nullable ws "}}"
"""
    + _COMMON
).format()

SETTING_DESCRIBE_GRAMMAR = (
    r"""root ::= "{{" ws "\"setting\"" ws ":" ws string ws "," ws "\"lighting\"" ws ":" ws nullable ws "," ws "\"mood\"" ws ":" ws nullable ws "}}"
"""
    + _COMMON
).format()

SUBJECT_USER_PROMPT = "Describe the subject shown in the reference image(s)."
SETTING_USER_PROMPT = "Describe the setting shown in the reference image."

_PROMPT_KEYS = frozenset({"REF_DESCRIBE_SUBJECT_SYSTEM", "REF_DESCRIBE_SETTING_SYSTEM"})


def __getattr__(name: str) -> str:
    if name in _PROMPT_KEYS:
        from metascan.core.prompt_store import get_prompt_store

        return get_prompt_store().get(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class DescribeError(ValueError):
    """The VLM's describe response could not be validated."""


def _loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as e:
        raise DescribeError(f"response is not valid JSON: {e}") from e


def _clean(v: Any) -> Optional[str]:
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return None


def validate_subject_describe(raw: str) -> Dict[str, Any]:
    data = _loads(raw)
    if not isinstance(data, dict):
        raise DescribeError("response is not a JSON object")
    description = _clean(data.get("description"))
    if not description:
        raise DescribeError("empty description")
    return {"description": description, "voice": _clean(data.get("voice"))}


def validate_setting_describe(raw: str) -> Dict[str, Any]:
    data = _loads(raw)
    if not isinstance(data, dict):
        raise DescribeError("response is not a JSON object")
    setting = _clean(data.get("setting"))
    if not setting:
        raise DescribeError("empty setting")
    return {
        "setting": setting,
        "lighting": _clean(data.get("lighting")),
        "mood": _clean(data.get("mood")),
    }
