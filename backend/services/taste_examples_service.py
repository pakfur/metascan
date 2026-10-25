"""Taste guidance for the T2I prompt writer ("base+taste").

:meth:`TasteExamplesService.examples_for` finds up to ``k`` of the owner's
own ✓ prompts (images graded pass; ★ favorites and views never count) whose
images sit closest, by CLIP, to a caption: the caption is encoded with the
live inference worker (one bounded call, only when it is already READY -- a
guided write never starts CLIP) and searched against the **in-memory**
FAISS index only — the index is never loaded from disk under a request
(``similarity._get_faiss_manager`` would).

Every failure — no index, CLIP unavailable or slow, an index built with a
different CLIP model, no matching ✓ image — yields ``([], warning)``; this
never raises, so a guided write degrades to the unguided ``base`` writer.
Captions, prompts and examples are never logged at INFO or above.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, List, Optional, Tuple

import numpy as np

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.inference_client import STATE_READY
from metascan.core.t2i_models import MODEL_PROFILES
from metascan.core.taste_examples import (
    TASTE_EXAMPLE_MAX_CHARS,
    TASTE_EXAMPLES_K,
    truncate_example,
)

logger = logging.getLogger(__name__)

CLIP_TIMEOUT_S = 10.0
SEARCH_TOP_K = 200
WARNING_PREFIX = "taste guidance unavailable: "


class _Unavailable(Exception):
    """Internal: why guidance cannot be given (safe to show and log)."""


def _default_client() -> Optional[Any]:
    from backend.api import similarity

    return similarity._inference_client


def _default_index() -> Optional[Any]:
    from backend.api import similarity

    # The module global only; _get_faiss_manager() would load from disk.
    return similarity._faiss_manager


def _index_dim_mismatch(index: Any, vec_dim: int) -> bool:
    from fastapi import HTTPException

    from backend.api import similarity

    try:
        similarity._assert_dim_matches(index, vec_dim)
    except HTTPException:
        return True
    return False


class TasteExamplesService:
    def __init__(
        self,
        db: DatabaseManager,
        *,
        get_client: Callable[[], Optional[Any]] = _default_client,
        get_index: Callable[[], Optional[Any]] = _default_index,
        timeout_s: float = CLIP_TIMEOUT_S,
    ) -> None:
        self.db = db
        self._get_client = get_client
        self._get_index = get_index
        self._timeout_s = timeout_s

    async def examples_for(
        self, caption: str, target_model: str, k: int = TASTE_EXAMPLES_K
    ) -> Tuple[List[str], Optional[str]]:
        """``(examples, warning)``: up to ``k`` ✓ prompts (each at most
        ``TASTE_EXAMPLE_MAX_CHARS``) for ``caption``, best match first, or
        ``([], "taste guidance unavailable: <why>")``."""
        try:
            examples = await self._examples(caption, target_model, k)
        except _Unavailable as e:
            # Per-step and expected: the caller turns it into a step warning.
            logger.debug("%s%s", WARNING_PREFIX, e)
            return [], f"{WARNING_PREFIX}{e}"
        except Exception as e:  # never let guidance fail a write
            # Nothing on this path formats the caption or a prompt into an
            # exception message (DB/FAISS errors only).
            logger.warning(
                "%sunexpected %s", WARNING_PREFIX, type(e).__name__, exc_info=True
            )
            return [], f"{WARNING_PREFIX}internal error"
        if not examples:
            reason = "no passed images with a known prompt match this caption"
            logger.debug("%s%s", WARNING_PREFIX, reason)
            return [], f"{WARNING_PREFIX}{reason}"
        logger.debug("taste examples: %d for model %s", len(examples), target_model)
        return examples, None

    async def _examples(self, caption: str, target_model: str, k: int) -> List[str]:
        if k <= 0:
            return []
        if target_model not in MODEL_PROFILES:
            raise _Unavailable(f"unknown model {target_model!r}")
        index = self._get_index()
        if index is None or not getattr(index, "is_loaded", False):
            raise _Unavailable("no similarity index loaded")
        client = self._get_client()
        # READY only: a guided write never starts, restarts or waits for the
        # CLIP worker (controller ruling) -- loading/spawning counts as absent.
        if client is None or getattr(client, "state", None) != STATE_READY:
            raise _Unavailable("CLIP is not loaded")

        vec = await self._encode(client, caption)
        if _index_dim_mismatch(index, int(vec.shape[0])):
            raise _Unavailable("the index was built with a different CLIP model")

        hits = await asyncio.to_thread(index.search, vec, SEARCH_TOP_K)
        ranked = [str(path) for path, _score in hits]
        if not ranked:
            return []
        prompts = await asyncio.to_thread(
            self.db.pass_prompt_candidates, ranked, target_model
        )
        if not prompts:
            return []
        from metascan.utils.path_utils import to_posix_path

        examples: List[str] = []
        seen = set()
        for path in ranked:
            text = prompts.get(to_posix_path(path))
            if text is None:
                continue
            short = truncate_example(text, TASTE_EXAMPLE_MAX_CHARS)
            key = short.lower()
            if not short or key in seen:
                continue
            seen.add(key)
            examples.append(short)
            if len(examples) >= k:
                break
        return examples

    async def _encode(self, client: Any, caption: str) -> np.ndarray:
        """Encode ``caption`` on the already-ready worker, within the bound."""
        try:
            vec = await asyncio.wait_for(
                client.encode_text(caption, timeout=self._timeout_s),
                timeout=self._timeout_s,
            )
        except asyncio.TimeoutError:
            raise _Unavailable(f"CLIP timed out after {self._timeout_s:g} s") from None
        except Exception as e:
            raise _Unavailable(f"CLIP failed: {type(e).__name__}") from None
        return np.asarray(vec, dtype=np.float32).reshape(-1)
