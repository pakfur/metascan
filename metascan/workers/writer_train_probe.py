"""Probe: can this Python's TRL use the SFT adapter as the DPO reference?

Stdlib only -- safe to import from the FastAPI process (the ``writer_training``
feature gate). The check itself runs in a subprocess so trl / torch are
never imported here::

    ok, reason = probe_trl_reference_support(python="/path/to/python")

``ok`` is True when ``trl.DPOConfig`` has both ``model_adapter_name`` and
``ref_adapter_name`` (the writer worker loads the SFT adapter twice and
lets TRL swap to the reference adapter). ``reason`` is "" when ok, else a
short owner-facing explanation.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Optional, Tuple

REQUIRED_DPO_FIELDS = ("model_adapter_name", "ref_adapter_name")

_CHECK = """
import dataclasses, json
try:
    import trl
except Exception as e:
    print(json.dumps({"installed": False, "error": type(e).__name__}))
else:
    names = {f.name for f in dataclasses.fields(trl.DPOConfig)}
    print(json.dumps({"installed": True,
                      "version": getattr(trl, "__version__", "?"),
                      "missing": sorted(set(%r) - names)}))
""" % (
    list(REQUIRED_DPO_FIELDS),
)

_PIN_HINT = "install the trl version pinned in requirements-train.txt"


def probe_trl_reference_support(
    python: Optional[str] = None, timeout: float = 120.0
) -> Tuple[bool, str]:
    try:
        proc = subprocess.run(
            [python or sys.executable, "-c", _CHECK],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"could not run the TRL probe: {e}"
    return parse_trl_probe(proc.returncode, proc.stdout)


# The check's source, for callers that run it themselves (asyncio).
TRL_CHECK = _CHECK


def parse_trl_probe(returncode: Optional[int], stdout: str) -> Tuple[bool, str]:
    """``(ok, reason)`` from the check's exit code and stdout."""
    lines = (stdout or "").strip().splitlines()
    try:
        info = json.loads(lines[-1]) if lines else None
    except ValueError:
        info = None
    if returncode != 0 or not isinstance(info, dict):
        return False, f"the TRL probe failed (exit {returncode})"
    if not info.get("installed"):
        return False, f"trl is not installed ({_PIN_HINT})"
    missing = info.get("missing") or []
    if missing:
        return False, (
            f"trl {info.get('version')} lacks DPOConfig.{', '.join(missing)} "
            f"({_PIN_HINT})"
        )
    return True, ""
