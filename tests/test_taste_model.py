"""Tests for the pure-numpy taste model (synthetic data only, dim >= 32)."""

import numpy as np

from metascan.core.taste_model import (
    TasteModelParams,
    auc,
    features,
    fit,
    predict,
    split_holdout,
)


def _unit(rng: np.random.Generator, dim: int = 32) -> np.ndarray:
    v = rng.normal(size=dim)
    return v / np.linalg.norm(v)


def test_learns_separable_direction():
    rng = np.random.default_rng(0)
    d = _unit(rng)
    X = rng.normal(size=(400, 32))
    y = (X @ d > 0).astype(float)
    w, b = fit(features(X, [[]] * 400, []), y, np.ones(400), [])
    p = predict(TasteModelParams(w, b, [], 32, "m"), features(X, [[]] * 400, []))
    assert (p >= 0).all() and (p <= 1).all()
    assert auc(y, p) > 0.95


def test_pairs_move_ranking():
    rng = np.random.default_rng(1)
    centre = _unit(rng)
    off = _unit(rng) * 0.8
    n = 60
    A = centre + off + rng.normal(scale=0.05, size=(n, 32))
    B = centre - off + rng.normal(scale=0.05, size=(n, 32))
    X = features(np.vstack([A, B]), [[]] * (2 * n), [])
    y = np.zeros(2 * n)  # identical labels: only the pairs carry signal
    wt = np.ones(2 * n)
    pairs = [(i, n + i, 1.0) for i in range(n)]
    w, b = fit(X, y, wt, pairs, pair_weight=1.0)
    s = X @ w + b
    assert s[:n].mean() > s[n:].mean()
    w0, b0 = fit(X, y, wt, [], pair_weight=1.0)
    gap0 = (X @ w0)[:n].mean() - (X @ w0)[n:].mean()
    assert (s[:n].mean() - s[n:].mean()) > gap0


def test_weights_matter():
    rng = np.random.default_rng(2)
    v = _unit(rng)
    X = features(np.vstack([v, v]), [[], []], [])
    y = np.array([1.0, 0.0])
    w1, b1 = fit(X, y, np.array([5.0, 1.0]), [])
    w2, b2 = fit(X, y, np.array([1.0, 5.0]), [])
    p1 = predict(TasteModelParams(w1, b1, [], 32, "m"), X[:1])[0]
    p2 = predict(TasteModelParams(w2, b2, [], 32, "m"), X[:1])[0]
    assert p1 > 0.5 > p2


def test_category_feature_used():
    rng = np.random.default_rng(3)
    n = 300
    E = rng.normal(size=(n, 32))
    cats = [["good"] if i % 2 == 0 else ["bad"] for i in range(n)]
    y = np.array([1.0 if i % 2 == 0 else 0.0 for i in range(n)])
    X = features(E, cats, ["good", "bad"])
    assert X.shape == (n, 34)
    w, b = fit(X, y, np.ones(n), [])
    p = predict(TasteModelParams(w, b, ["good", "bad"], 32, "m"), X)
    assert auc(y, p) > 0.9


def test_features_normalizes_and_ignores_unknown():
    E = np.zeros((2, 32))
    E[0, 0] = 3.0
    X = features(E, [["a", "zzz"], []], ["a", "b"])
    assert np.isclose(np.linalg.norm(X[0, :32]), 1.0)
    assert np.all(X[1, :32] == 0)  # zero vector guarded, no NaN
    assert list(X[0, 32:]) == [1.0, 0.0]
    assert list(X[1, 32:]) == [0.0, 0.0]


def test_deterministic():
    rng = np.random.default_rng(4)
    X = features(rng.normal(size=(100, 32)), [[]] * 100, [])
    y = (rng.random(100) > 0.5).astype(float)
    a = fit(X, y, np.ones(100), [(0, 1, 1.0)], seed=0)
    b = fit(X, y, np.ones(100), [(0, 1, 1.0)], seed=0)
    assert np.array_equal(a[0], b[0]) and a[1] == b[1]


def test_zero_pairs_and_zero_weights_no_nan():
    rng = np.random.default_rng(5)
    X = features(rng.normal(size=(20, 32)), [[]] * 20, [])
    y = (rng.random(20) > 0.5).astype(float)
    w, b = fit(X, y, np.zeros(20), [(0, 1, 0.0)])
    assert np.isfinite(w).all() and np.isfinite(b)


def test_auc_none_when_one_class():
    assert auc(np.ones(5), np.arange(5.0)) is None


def test_auc_values_and_ties():
    assert auc(np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert auc(np.array([0, 1]), np.array([0.5, 0.5])) == 0.5


def test_save_load_roundtrip(tmp_path):
    rng = np.random.default_rng(6)
    p = TasteModelParams(rng.normal(size=34), 0.25, ["a", "b"], 32, "clip-x")
    path = tmp_path / "m.npz"
    p.save(path)
    q = TasteModelParams.load(path)
    assert np.array_equal(p.w, q.w)
    assert (q.b, q.categories, q.dim, q.embedding_model) == (
        0.25,
        ["a", "b"],
        32,
        "clip-x",
    )


def test_holdout_split_disjoint_and_sized():
    tr, ho = split_holdout(100, 0.15, seed=0)
    assert len(ho) == 15 and len(tr) == 85
    assert set(tr).isdisjoint(ho)
    assert sorted(np.concatenate([tr, ho])) == list(range(100))
    tr2, ho2 = split_holdout(100, 0.15, seed=0)
    assert np.array_equal(ho, ho2)


def test_train_pairs_drops_holdout_and_remaps():
    from metascan.core.taste_model import train_pairs

    tr = np.array([0, 2, 3, 5])
    out = train_pairs([(0, 2, 1.0), (2, 4, 0.5), (3, 5, 0.25)], tr)
    assert out == [(0, 1, 1.0), (2, 3, 0.25)]
