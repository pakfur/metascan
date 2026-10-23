"""Pure-numpy taste model: logistic regression with a pairwise ranking term.

Runs inside the one-shot training worker, so it depends on numpy only and is
fully deterministic (zero init, full-batch Adam).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

Pair = Tuple[int, int, float]


def _sigmoid(z: np.ndarray) -> np.ndarray:
    out = np.empty_like(z, dtype=np.float64)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[~pos])
    out[~pos] = ez / (1.0 + ez)
    return out


def _balanced(sw: np.ndarray, y: np.ndarray) -> np.ndarray:
    """``sw`` rescaled so both classes sum to half the total weight; a
    single-class (or weightless) input is returned unchanged."""
    pos = y > 0.5
    pos_total = float(sw[pos].sum())
    neg_total = float(sw[~pos].sum())
    if pos_total <= 0 or neg_total <= 0:
        return sw
    half = (pos_total + neg_total) / 2.0
    out: np.ndarray = np.where(pos, sw * (half / pos_total), sw * (half / neg_total))
    return out


@dataclass
class TasteModelParams:
    w: np.ndarray
    b: float
    categories: List[str]
    dim: int
    embedding_model: str

    def save(self, path: Path) -> None:
        with open(path, "wb") as f:
            np.savez(
                f,
                w=np.asarray(self.w, dtype=np.float64),
                b=np.float64(self.b),
                categories=np.array(self.categories, dtype=str),
                dim=np.int64(self.dim),
                embedding_model=np.array(self.embedding_model, dtype=str),
            )

    @classmethod
    def load(cls, path: Path) -> "TasteModelParams":
        with np.load(path, allow_pickle=False) as z:
            return cls(
                w=np.asarray(z["w"], dtype=np.float64),
                b=float(z["b"]),
                categories=[str(c) for c in z["categories"].tolist()],
                dim=int(z["dim"]),
                embedding_model=str(z["embedding_model"]),
            )


def features(
    embeddings: np.ndarray,
    row_categories: Sequence[Sequence[str]],
    categories: Sequence[str],
) -> np.ndarray:
    """L2-normalised embeddings with a multi-hot over ``categories`` appended."""
    emb = np.asarray(embeddings, dtype=np.float64)
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    emb = emb / norms
    index: Dict[str, int] = {c: i for i, c in enumerate(categories)}
    hot = np.zeros((emb.shape[0], len(index)), dtype=np.float64)
    for r, names in enumerate(row_categories):
        for name in names:
            j = index.get(name)
            if j is not None:
                hot[r, j] = 1.0
    return np.hstack([emb, hot])


def fit(
    X: np.ndarray,
    y: np.ndarray,
    weights: np.ndarray,
    pairs: List[Pair],
    *,
    l2: float = 1e-3,
    pair_weight: float = 1.0,
    steps: int = 400,
    lr: float = 0.05,
    seed: int = 0,
    balance: bool = True,
) -> Tuple[np.ndarray, float]:
    """Full-batch Adam on weighted logistic + weighted pairwise logistic loss.

    Each loss term is divided by its own total weight, so ``pair_weight`` is
    independent of how many pairs there are. Initialisation is zero; ``seed``
    is accepted for interface stability (the optimiser has no randomness).

    ``balance`` rescales the sample weights so each class carries the same
    total weight (relative weights within a class are kept): a library with
    thousands of migrated favorites and a few dozen negatives would
    otherwise learn "everything is a favorite" and score near 1.
    """
    del seed
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    sw = np.asarray(weights, dtype=np.float64)
    if balance:
        sw = _balanced(sw, y)
    n, d = X.shape
    sw_total = float(sw.sum())

    if pairs:
        pi = np.array([p[0] for p in pairs], dtype=np.int64)
        pj = np.array([p[1] for p in pairs], dtype=np.int64)
        pw = np.array([p[2] for p in pairs], dtype=np.float64)
    else:
        pi = pj = np.zeros(0, dtype=np.int64)
        pw = np.zeros(0, dtype=np.float64)
    pw_total = float(pw.sum())
    use_pairs = pair_weight > 0 and pw_total > 0

    w = np.zeros(d)
    b = 0.0
    m_w, v_w = np.zeros(d), np.zeros(d)
    m_b = v_b = 0.0
    beta1, beta2, eps = 0.9, 0.999, 1e-8

    for t in range(1, steps + 1):
        gw = 2.0 * l2 * w
        gb = 0.0
        if sw_total > 0:
            r = sw * (_sigmoid(X @ w + b) - y) / sw_total
            gw = gw + X.T @ r
            gb += float(r.sum())
        if use_pairs:
            diff = X[pi] - X[pj]
            # d/dw log(1 + exp(-(s_i - s_j))) = -sigmoid(-(s_i - s_j)) * diff
            c = -pair_weight * pw * _sigmoid(-(diff @ w)) / pw_total
            gw = gw + diff.T @ c
        m_w = beta1 * m_w + (1 - beta1) * gw
        v_w = beta2 * v_w + (1 - beta2) * gw * gw
        m_b = beta1 * m_b + (1 - beta1) * gb
        v_b = beta2 * v_b + (1 - beta2) * gb * gb
        c1, c2 = 1 - beta1**t, 1 - beta2**t
        w = w - lr * (m_w / c1) / (np.sqrt(v_w / c2) + eps)
        b = b - lr * (m_b / c1) / (np.sqrt(v_b / c2) + eps)

    return w, float(b)


def predict(params: TasteModelParams, X: np.ndarray) -> np.ndarray:
    z = np.asarray(X, dtype=np.float64) @ params.w + params.b
    return _sigmoid(z)


def split_holdout(
    n: int, fraction: float = 0.15, seed: int = 0
) -> Tuple[np.ndarray, np.ndarray]:
    """Return (train_idx, holdout_idx), both sorted and disjoint."""
    rng = np.random.default_rng(seed)
    perm = rng.permutation(n)
    k = int(round(n * fraction))
    return np.sort(perm[k:]), np.sort(perm[:k])


def train_pairs(pairs: List[Pair], train_idx: np.ndarray) -> List[Pair]:
    """Drop pairs touching a non-train row; remap the rest to train positions."""
    pos = {int(r): i for i, r in enumerate(train_idx)}
    return [(pos[a], pos[b], w) for a, b, w in pairs if a in pos and b in pos]


def auc(y: np.ndarray, scores: np.ndarray) -> Optional[float]:
    """Rank-based AUC (Mann-Whitney U, average ranks for ties)."""
    y = np.asarray(y) > 0.5
    s = np.asarray(scores, dtype=np.float64)
    n_pos = int(y.sum())
    n_neg = int(y.size - n_pos)
    if n_pos == 0 or n_neg == 0:
        return None
    order = np.argsort(s, kind="mergesort")
    sorted_s = s[order]
    ranks = np.empty(s.size, dtype=np.float64)
    i = 0
    while i < s.size:
        j = i
        while j + 1 < s.size and sorted_s[j + 1] == sorted_s[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    u = ranks[y].sum() - n_pos * (n_pos + 1) / 2.0
    return float(u / (n_pos * n_neg))
