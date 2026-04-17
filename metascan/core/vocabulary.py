"""CLIP tagging vocabulary — load, normalize, encode, cache.

The vocabulary is composed of four files under ``data/vocabulary/``:

* ``oidv7-class-descriptions.csv`` — Open Images V7 ``LabelName,DisplayName``
  CSV (~20k photographic subjects/scenes/objects).
* ``imagenet_classes.txt`` — ImageNet-1k class names, one per line.
* ``aesthetics.txt`` — LLM-generated photographic aesthetic terms.
* ``nsfw.txt`` — LLM-generated adult-content descriptive terms.
* ``excluded.txt`` — optional, terms to always remove from the merged vocab.

Each term is tagged with an ``axis`` drawn from
``{"general", "aesthetic", "nsfw"}`` so downstream code can slice tags by
provenance without re-reading the source files.

Encoded embeddings are cached to ``data/vocabulary/vocab.<model_key>.npz``
(keyed on the source-file hashes and the CLIP model key) so repeated server
starts skip the ~20-60s of text encoding.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Layout
# ----------------------------------------------------------------------

OPEN_IMAGES_FILENAME = "oidv7-class-descriptions.csv"
IMAGENET_FILENAME = "imagenet_classes.txt"
AESTHETICS_FILENAME = "aesthetics.txt"
NSFW_FILENAME = "nsfw.txt"
EXCLUDED_FILENAME = "excluded.txt"


@dataclass
class Vocabulary:
    """Loaded term list + pre-computed CLIP text embeddings.

    ``embeddings`` is an L2-normalized ``(N, D)`` float32 matrix so cosine
    similarity with a query image embedding is just an inner product.
    """

    terms: List[str]
    axes: List[str]  # parallel to terms, one of "general" | "aesthetic" | "nsfw"
    embeddings: np.ndarray  # (N, D), float32, L2-normalized
    model_key: str


# ----------------------------------------------------------------------
# File loading + normalization
# ----------------------------------------------------------------------


_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(term: str) -> Optional[str]:
    """Trim, collapse whitespace, reject terms that aren't useful as tags.

    Returns None if the term should be dropped (empty, too short, too long,
    contains characters that would produce noise as a tag facet).
    """
    if term is None:
        return None
    t = term.strip().lower()
    t = _WHITESPACE_RE.sub(" ", t)
    if not t or len(t) < 2:
        return None
    if len(t) > 60:
        return None
    # Drop tokens that are just numbers or punctuation.
    if t.strip("0123456789.,-") == "":
        return None
    return t


def _read_lines(path: Path) -> Iterable[str]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            yield line


def _read_open_images(path: Path) -> Iterable[str]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        # Expected: LabelName,DisplayName — we want DisplayName.
        display_col = 1
        if header is not None:
            for i, name in enumerate(header):
                if name.strip().lower() == "displayname":
                    display_col = i
                    break
        for row in reader:
            if len(row) > display_col:
                yield row[display_col]


def _hash_file(path: Path) -> str:
    if not path.exists():
        return "missing"
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def load_vocabulary(
    vocab_dir: Path,
) -> Tuple[List[str], List[str], str]:
    """Read and merge all vocabulary source files.

    Returns ``(terms, axes, sources_fingerprint)`` where the fingerprint is
    a stable digest of the source files' contents — used as a cache key for
    the encoded matrix.
    """
    open_images = list(_read_open_images(vocab_dir / OPEN_IMAGES_FILENAME))
    imagenet = list(_read_lines(vocab_dir / IMAGENET_FILENAME))
    aesthetics = list(_read_lines(vocab_dir / AESTHETICS_FILENAME))
    nsfw = list(_read_lines(vocab_dir / NSFW_FILENAME))
    excluded_terms = {
        n for t in _read_lines(vocab_dir / EXCLUDED_FILENAME) if (n := _normalize(t))
    }

    seen: set = set()
    terms: List[str] = []
    axes: List[str] = []

    def _add_all(raw: Iterable[str], axis: str) -> None:
        for term in raw:
            norm = _normalize(term)
            if not norm or norm in seen or norm in excluded_terms:
                continue
            seen.add(norm)
            terms.append(norm)
            axes.append(axis)

    # Order matters only for which axis wins a duplicated term — put the
    # most specific labels first so generic Open Images labels don't
    # overwrite an aesthetic/nsfw classification.
    _add_all(nsfw, "nsfw")
    _add_all(aesthetics, "aesthetic")
    _add_all(imagenet, "general")
    _add_all(open_images, "general")

    fingerprint = hashlib.sha1(
        "|".join(
            _hash_file(vocab_dir / name)
            for name in (
                OPEN_IMAGES_FILENAME,
                IMAGENET_FILENAME,
                AESTHETICS_FILENAME,
                NSFW_FILENAME,
                EXCLUDED_FILENAME,
            )
        ).encode("utf-8")
    ).hexdigest()[:16]

    logger.info(
        "Loaded vocabulary: total=%d (nsfw=%d, aesthetic=%d, general=%d)",
        len(terms),
        axes.count("nsfw"),
        axes.count("aesthetic"),
        axes.count("general"),
    )
    return terms, axes, fingerprint


# ----------------------------------------------------------------------
# Encoding + cache
# ----------------------------------------------------------------------


def _cache_path(vocab_dir: Path, model_key: str) -> Path:
    return vocab_dir / f"vocab.{model_key}.npz"


def _try_load_cache(
    vocab_dir: Path, model_key: str, fingerprint: str
) -> Optional[Vocabulary]:
    path = _cache_path(vocab_dir, model_key)
    if not path.exists():
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            meta_fingerprint = str(data["fingerprint"].item())
            if meta_fingerprint != fingerprint:
                logger.info(
                    "Vocabulary cache at %s is stale (fingerprint changed).", path
                )
                return None
            terms = [str(x) for x in data["terms"].tolist()]
            axes = [str(x) for x in data["axes"].tolist()]
            embeddings = data["embeddings"].astype(np.float32, copy=False)
            return Vocabulary(
                terms=terms,
                axes=axes,
                embeddings=embeddings,
                model_key=model_key,
            )
    except Exception as e:
        logger.warning("Vocabulary cache at %s unreadable (%s)", path, e)
        return None


def _save_cache(vocab: Vocabulary, vocab_dir: Path, fingerprint: str) -> None:
    path = _cache_path(vocab_dir, vocab.model_key)
    try:
        np.savez_compressed(
            path,
            terms=np.array(vocab.terms, dtype=object),
            axes=np.array(vocab.axes, dtype=object),
            embeddings=vocab.embeddings,
            fingerprint=np.array(fingerprint),
            model_key=np.array(vocab.model_key),
        )
        logger.info(
            "Wrote vocabulary cache: %s (%d terms, dim=%d)",
            path,
            len(vocab.terms),
            vocab.embeddings.shape[1],
        )
    except Exception as e:
        logger.error("Failed to write vocabulary cache %s: %s", path, e)


def build_vocabulary(
    vocab_dir: Path,
    mgr: Any,  # duck-typed EmbeddingManager (model_key, embedding_dim, compute_text_embedding)
) -> Optional[Vocabulary]:
    """Load, encode, and cache the full tagging vocabulary.

    ``mgr`` must expose ``model_key: str``, ``embedding_dim: int``, and
    ``compute_text_embedding(text: str) -> np.ndarray | None``. In
    practice this is a ``metascan.core.embedding_manager.EmbeddingManager``.

    Returns None when the vocabulary dir is missing or empty — caller can
    treat this as "tagging disabled for this run".
    """
    if not vocab_dir.exists():
        logger.warning(
            "Vocabulary directory %s does not exist; CLIP tagging disabled.",
            vocab_dir,
        )
        return None
    terms, axes, fingerprint = load_vocabulary(vocab_dir)
    if not terms:
        logger.warning(
            "No terms found in vocabulary dir %s; CLIP tagging disabled.", vocab_dir
        )
        return None

    model_key = getattr(mgr, "model_key", "unknown")
    cached = _try_load_cache(vocab_dir, model_key, fingerprint)
    if cached is not None and cached.terms == terms and cached.axes == axes:
        logger.info(
            "Loaded cached vocabulary (%d terms, model=%s).",
            len(cached.terms),
            model_key,
        )
        return cached

    logger.info(
        "Encoding %d vocabulary terms with %s …",
        len(terms),
        model_key,
    )
    embeddings = _encode_terms(mgr, terms)
    if embeddings is None:
        return None
    vocab = Vocabulary(
        terms=terms, axes=axes, embeddings=embeddings, model_key=model_key
    )
    _save_cache(vocab, vocab_dir, fingerprint)
    return vocab


def _encode_terms(
    mgr: Any,
    terms: List[str],
) -> Optional[np.ndarray]:
    """Encode each term with CLIP and stack into a ``(N, D)`` float32 matrix.

    Terms that fail to encode are replaced with zero-rows so index positions
    stay aligned with the ``terms`` / ``axes`` lists. Zero rows naturally
    score 0 against every image, so they're harmless at matmul time.
    """
    dim = int(getattr(mgr, "embedding_dim", 0))
    if not dim:
        logger.error("EmbeddingManager has no embedding_dim; cannot encode vocab.")
        return None
    out = np.zeros((len(terms), dim), dtype=np.float32)
    failed = 0
    for i, term in enumerate(terms):
        try:
            vec = mgr.compute_text_embedding(term)
        except Exception as e:
            logger.debug("Encoding failed for term %r: %s", term, e)
            failed += 1
            continue
        if vec is None:
            failed += 1
            continue
        out[i] = vec.astype(np.float32, copy=False)
        # Log progress at coarse milestones; CLIP ViT-H-14 on CPU takes a
        # meaningful fraction of a second per term.
        if (i + 1) % 1000 == 0:
            logger.info("  encoded %d / %d terms", i + 1, len(terms))
    if failed:
        logger.warning(
            "%d / %d vocabulary terms failed to encode and are zeroed.",
            failed,
            len(terms),
        )
    return out


# ----------------------------------------------------------------------
# Tag selection
# ----------------------------------------------------------------------


def select_tags(
    image_embedding: np.ndarray,
    vocab: Vocabulary,
    *,
    top_k: int,
    threshold: float,
) -> List[Tuple[str, str, float]]:
    """Return the ``top_k`` vocabulary terms whose cosine similarity with
    ``image_embedding`` is at least ``threshold``.

    Both the image embedding and ``vocab.embeddings`` are assumed to be
    L2-normalized, so the inner product equals cosine similarity.

    Result shape: ``[(term, axis, score), ...]`` sorted by descending score.
    """
    if image_embedding.ndim != 1:
        image_embedding = image_embedding.reshape(-1)
    scores = vocab.embeddings @ image_embedding.astype(np.float32, copy=False)
    if top_k >= scores.shape[0]:
        idx_by_score = np.argsort(-scores)
    else:
        part = np.argpartition(-scores, top_k)[:top_k]
        idx_by_score = part[np.argsort(-scores[part])]
    out: List[Tuple[str, str, float]] = []
    for idx in idx_by_score:
        s = float(scores[idx])
        if s < threshold:
            break
        out.append((vocab.terms[int(idx)], vocab.axes[int(idx)], s))
    return out
