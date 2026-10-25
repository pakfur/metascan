"""REST tests for /api/judgments: the grade, ★ favorite, reasons, seen."""

import pytest

from tests._taste_api_fixtures import client, seeded  # noqa: F401


def test_unknown_reason_is_422(client, seeded):
    client.patch("/api/judgments//p/a.png", json={"grade": "fail"})
    r = client.patch("/api/judgments//p/a.png", json={"reason": "zzz", "on": True})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unknown_reason"


def test_reason_roundtrip(client, seeded):
    client.patch("/api/judgments//p/a.png", json={"grade": "pass"})
    r = client.patch("/api/judgments//p/a.png", json={"reason": "light", "on": True})
    assert r.status_code == 200 and r.json()["reasons"] == ["light"]
    r = client.patch("/api/judgments//p/a.png", json={"reason": "light", "on": False})
    assert r.status_code == 200 and r.json()["reasons"] == []


def test_reason_without_on_is_422(client, seeded):
    client.patch("/api/judgments//p/a.png", json={"grade": "pass"})
    r = client.patch("/api/judgments//p/a.png", json={"reason": "light"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "missing_on"


def test_seen_and_get(client, seeded):
    assert client.post("/api/judgments/seen", json={"file_path": "/p/a.png"}).json()[
        "seen_at"
    ]
    got = client.get("/api/judgments//p/a.png").json()
    assert got["seen_at"]
    # Seen is not a grade.
    assert (got["favorite"], got["grade"], got["judged_at"]) == (False, None, None)


def test_unknown_media_404(client):
    assert client.get("/api/judgments//nope.png").status_code == 404
    assert (
        client.post("/api/judgments/seen", json={"file_path": "/nope.png"}).status_code
        == 404
    )
    for body in (
        {"grade": "pass"},
        {"grade": None},
        {"favorite": True},
        {"reason": "light", "on": True},
    ):
        r = client.patch("/api/judgments//nope.png", json=body)
        assert r.status_code == 404, body


def test_sticky_category_via_grade(client, seeded):
    cid = client.post("/api/categories", json={"name": "Anime"}).json()["id"]
    r = client.patch(
        "/api/judgments//p/a.png",
        json={"grade": "fail", "sticky_category_id": cid},
    )
    assert r.json()["categories"] == [{"id": cid, "name": "Anime"}]


def test_unknown_sticky_category_404(client, seeded):
    r = client.patch(
        "/api/judgments//p/a.png",
        json={"grade": "pass", "sticky_category_id": 999},
    )
    assert r.status_code == 404 and r.json()["detail"]["code"] == "unknown_category"
    assert client.get("/api/judgments//p/a.png").json()["grade"] is None


def test_sticky_broadcasts_once(client, seeded, monkeypatch):
    cid = client.post("/api/categories", json={"name": "Anime"}).json()["id"]
    sent = []
    monkeypatch.setattr(
        "backend.api.judgments.ws_manager.broadcast_sync", lambda *a: sent.append(a)
    )
    body = {"grade": "pass", "sticky_category_id": cid}
    client.patch("/api/judgments//p/a.png", json=body)
    client.patch("/api/judgments//p/a.png", json=body)
    assert sent == [("categories", "categories_changed", {})]


class _FakeFaiss:
    def __init__(self, loaded, vec_for, loaded_at=1_700_000_000.0):
        self.is_loaded = loaded
        self.meta = {"model_key": "small"}
        self.loaded_at = loaded_at
        self._vec_for = vec_for

    def get_embedding(self, key):
        return self._vec_for.get(key)


def test_faiss_embedding_lookup(monkeypatch, caplog):
    import numpy as np

    from backend.services.judgments_service import faiss_embedding_lookup

    vec = np.ones(32, dtype=np.float32)
    fake = _FakeFaiss(True, {"/p/a.png": vec})
    monkeypatch.setattr("backend.api.similarity._faiss_manager", fake)
    got = faiss_embedding_lookup("/p/a.png")
    # The vector, its model and when the in-memory index was loaded: the
    # database reads it only for a file marked before that.
    assert got is not None and got[0] is vec
    assert got[1:] == ("small", 1_700_000_000.0)
    assert faiss_embedding_lookup("/p/missing.png") is None
    fake.loaded_at = None  # not loaded from disk: no evidence it holds a mark
    with caplog.at_level("WARNING"):
        assert faiss_embedding_lookup("/p/a.png") is None
    assert not caplog.records  # an expected answer, not a failed lookup
    fake.loaded_at = 1_700_000_000.0
    fake.is_loaded = False
    assert faiss_embedding_lookup("/p/a.png") is None


def test_faiss_embedding_lookup_never_constructs_or_loads(monkeypatch):
    from backend.services.judgments_service import faiss_embedding_lookup

    def boom(*a, **k):
        raise AssertionError("must not construct/load")

    monkeypatch.setattr("backend.api.similarity._faiss_manager", None)
    monkeypatch.setattr("backend.api.similarity._get_faiss_manager", boom)
    monkeypatch.setattr("backend.api.similarity._get_embedding_queue", boom)
    monkeypatch.setattr("backend.api.similarity.FaissIndexManager", boom)
    assert faiss_embedding_lookup("/p/a.png") is None


def test_faiss_embedding_lookup_failure_logs_warning(monkeypatch, caplog):
    from backend.services.judgments_service import faiss_embedding_lookup

    class Broken:
        is_loaded = True
        meta = {"model_key": "small"}
        loaded_at = 1_700_000_000.0

        def get_embedding(self, key):
            raise RuntimeError("bad index")

    monkeypatch.setattr("backend.api.similarity._faiss_manager", Broken())
    with caplog.at_level("WARNING"):
        assert faiss_embedding_lookup("/p/a.png") is None
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_seen_records_the_score_shown(client, seeded):
    from backend.dependencies import get_db

    r = client.post(
        "/api/judgments/seen", json={"file_path": "/p/a.png", "score_shown": 0.42}
    )
    assert r.status_code == 200 and r.json()["seen_at"]
    events = get_db().list_judgment_events("/p/a.png")
    seen = [e for e in events if e["event"] == "seen"]
    assert len(seen) == 1 and seen[0]["score_shown"] == 0.42


def test_seen_without_score_stores_null(client, seeded):
    from backend.dependencies import get_db

    client.post("/api/judgments/seen", json={"file_path": "/p/a.png"})
    seen = [
        e for e in get_db().list_judgment_events("/p/a.png") if e["event"] == "seen"
    ]
    assert len(seen) == 1 and seen[0]["score_shown"] is None


# ----------------------------------------------------------------------
# Pass / fail grades and ★ favorite on the wire
# ----------------------------------------------------------------------


def _events(path):
    from backend.dependencies import get_db

    return [e["event"] for e in get_db().list_judgment_events(path)]


@pytest.fixture
def hook_calls(monkeypatch):
    """Every scorecard-hook call any route makes, by path."""
    calls = []

    async def fake(db, file_path, broadcast):
        calls.append(file_path)

    monkeypatch.setattr("backend.services.scorecard.note_scorecard_judgment", fake)
    return calls


def test_patch_grade_roundtrip(client, seeded, hook_calls):
    r = client.patch("/api/judgments//p/a.png", json={"grade": "fail"})
    assert r.status_code == 200
    body = r.json()
    assert (body["grade"], body["favorite"], body["reasons"]) == ("fail", False, [])
    assert body["judged_at"] and body["confirmed_at"]
    got = client.get("/api/judgments//p/a.png").json()
    assert (got["grade"], got["favorite"]) == ("fail", False)
    assert "verdict" not in got and "is_favorite" not in got
    assert hook_calls == ["/p/a.png"]


def test_patch_favorite_roundtrip(client, seeded, hook_calls):
    r = client.patch("/api/judgments//p/a.png", json={"favorite": True})
    assert r.status_code == 200
    assert (r.json()["favorite"], r.json()["grade"]) == (True, None)
    r = client.patch("/api/judgments//p/a.png", json={"favorite": False})
    assert (r.json()["favorite"], r.json()["grade"]) == (False, None)
    assert _events("/p/a.png") == ["favorite", "unfavorite"]
    # ★ is not a grade: it never reaches the scorecard.
    assert hook_calls == []


def test_grade_and_favorite_are_independent_on_the_wire(client, seeded):
    client.patch("/api/judgments//p/a.png", json={"favorite": True})
    r = client.patch("/api/judgments//p/a.png", json={"grade": "fail"})
    assert (r.json()["favorite"], r.json()["grade"]) == (True, "fail")
    r = client.patch("/api/judgments//p/a.png", json={"favorite": False})
    assert (r.json()["favorite"], r.json()["grade"]) == (False, "fail")
    r = client.patch("/api/judgments//p/b.png", json={"grade": "pass"})
    assert (r.json()["favorite"], r.json()["grade"]) == (False, "pass")


def test_null_grade_clears(client, seeded, hook_calls):
    client.patch("/api/judgments//p/a.png", json={"grade": "pass"})
    r = client.patch("/api/judgments//p/a.png", json={"grade": None})
    assert r.status_code == 200 and r.json()["grade"] is None
    assert _events("/p/a.png") == ["grade_pass", "grade_clear"]
    assert hook_calls == ["/p/a.png", "/p/a.png"]


def test_reason_without_grade_is_no_grade(client, seeded):
    r = client.patch("/api/judgments//p/a.png", json={"reason": "light", "on": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_grade"
    # ★ alone is not a grade either.
    client.patch("/api/judgments//p/a.png", json={"favorite": True})
    r = client.patch("/api/judgments//p/a.png", json={"reason": "light", "on": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_grade"
    client.patch("/api/judgments//p/a.png", json={"grade": "fail"})
    r = client.patch("/api/judgments//p/a.png", json={"reason": "light", "on": True})
    assert r.status_code == 200 and r.json()["reasons"] == ["light"]


def test_reason_patch_never_reaches_the_scorecard(client, seeded, hook_calls):
    client.patch("/api/judgments//p/a.png", json={"grade": "pass"})
    client.patch("/api/judgments//p/a.png", json={"reason": "light", "on": True})
    assert hook_calls == ["/p/a.png"]


def test_patch_rejects_old_verdict_field(client, seeded, hook_calls):
    for body in (
        {"verdict": "favorite"},
        {"verdict": "pass"},
        {"verdict": None},
        {"grade": "pass", "verdict": "favorite"},
    ):
        r = client.patch("/api/judgments//p/a.png", json=body)
        assert r.status_code == 422, body
    got = client.get("/api/judgments//p/a.png").json()
    assert (got["favorite"], got["grade"]) == (False, None)
    assert _events("/p/a.png") == [] and hook_calls == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"grade": "maybe"},
        {"grade": "pass", "favorite": True},
        {"grade": "pass", "reason": "light", "on": True},
        {"grade": "pass", "on": True},
        {"favorite": None},
        {"favorite": True, "score_shown": 0.5},
        {"favorite": True, "reason": "light", "on": True},
        {"reason": "light", "on": True, "sticky_category_id": 1},
        {"reason": "light"},
        {"on": True},
        {"score_shown": 0.5},
        {"sticky_category_id": 1},
    ],
)
def test_patch_takes_exactly_one_shape(client, seeded, body):
    r = client.patch("/api/judgments//p/a.png", json=body)
    assert r.status_code == 422, body
    assert _events("/p/a.png") == []


def test_legacy_favorite_patch_touches_favorite_only(client, seeded, hook_calls):
    from backend.dependencies import get_db

    client.patch("/api/judgments//p/a.png", json={"grade": "fail"})
    client.patch("/api/judgments//p/a.png", json={"reason": "light", "on": True})
    hook_calls.clear()
    before = client.get("/api/judgments//p/a.png").json()
    r = client.patch("/api/media//p/a.png", json={"is_favorite": True})
    assert r.status_code == 200 and r.json()["is_favorite"] is True
    after = client.get("/api/judgments//p/a.png").json()
    assert (after["favorite"], after["grade"], after["reasons"]) == (
        True,
        "fail",
        ["light"],
    )
    assert (after["judged_at"], after["confirmed_at"]) == (
        before["judged_at"],
        before["confirmed_at"],
    )
    client.patch("/api/media//p/a.png", json={"is_favorite": False})
    after = client.get("/api/judgments//p/a.png").json()
    assert (after["favorite"], after["grade"], after["reasons"]) == (
        False,
        "fail",
        ["light"],
    )
    assert _events("/p/a.png") == ["grade_fail", "reason_on", "favorite", "unfavorite"]
    assert hook_calls == []
    assert get_db().list_judgment_events("/p/a.png")[2]["score_shown"] is None


def test_sticky_category_applies_on_grade_not_favorite(client, seeded):
    cid = client.post("/api/categories", json={"name": "Anime"}).json()["id"]
    r = client.patch(
        "/api/judgments//p/a.png",
        json={"favorite": True, "sticky_category_id": cid},
    )
    assert r.status_code == 422
    r = client.patch("/api/judgments//p/a.png", json={"favorite": True})
    assert r.json()["categories"] == []
    # Clearing a grade with the sticky category set links nothing.
    r = client.patch(
        "/api/judgments//p/b.png", json={"grade": None, "sticky_category_id": cid}
    )
    assert r.status_code == 200 and r.json()["categories"] == []
    r = client.patch(
        "/api/judgments//p/a.png", json={"grade": "pass", "sticky_category_id": cid}
    )
    assert r.json()["categories"] == [{"id": cid, "name": "Anime"}]
    assert _events("/p/a.png") == ["favorite", "grade_pass", "category_on"]


def test_rapid_key_sequence_through_the_routes(client, seeded, hook_calls):
    """Review Focus 3 on the wire: P, a reason, X, X, F, P -- each toggling
    against the previous answer -- end in one state, events in key order."""
    state = client.get("/api/judgments//p/a.png").json()

    def press(key):
        nonlocal state
        if key == "F":
            body = {"favorite": not state["favorite"]}
        else:
            target = {"P": "pass", "X": "fail"}[key]
            body = {"grade": None if state["grade"] == target else target}
        r = client.patch("/api/judgments//p/a.png", json=body)
        assert r.status_code == 200
        state = r.json()

    press("P")
    state = client.patch(
        "/api/judgments//p/a.png", json={"reason": "concept", "on": True}
    ).json()
    press("X")
    assert (state["grade"], state["reasons"]) == ("fail", [])
    press("X")
    press("F")
    press("P")
    assert (state["favorite"], state["grade"], state["reasons"]) == (True, "pass", [])
    got = client.get("/api/judgments//p/a.png").json()
    assert {k: got[k] for k in state} == state
    assert _events("/p/a.png") == [
        "grade_pass",
        "reason_on",
        "grade_fail",
        "grade_clear",
        "favorite",
        "grade_pass",
    ]
    # One scorecard check per grade change, none for the ★ or the reason.
    assert hook_calls == ["/p/a.png"] * 4


def test_keys_on_a_hidden_scorecard_image_end_consistent(client, seeded):
    """Review Focus 3: P on a hidden (blind) scorecard image, then ★ during
    the grading, then X. Every grade PATCH runs the real scorecard check
    once, the ★ none; nothing sets ★ but the ★ itself, and the state, the
    event log and the T2I row agree. The grade reveals the image; ★ never
    hides or reveals it."""
    from unittest import mock

    from backend.dependencies import get_db
    from metascan.core.writer_adapters import adapter_writer_id

    db = get_db()
    adapter = db.create_writer_adapter(
        name="taste-all-v1", scope="all", kind="t2i", base_model="m", status="untested"
    )
    db.create_scorecard("card-1", adapter, "krea2", preset_id=None, total=40)
    db.add_scorecard_batch("batch-0", "card-1", 0)
    for path, writer in (
        ("/p/a.png", "base"),
        ("/p/b.png", adapter_writer_id(adapter)),
    ):
        db.create_t2i_image(
            file_path=path, batch_id="batch-0", writer_id=writer, scorecard_id="card-1"
        )
    assert client.get("/api/judgments//p/a.png").json()["scorecard"]["blind"] is True
    sent = []
    with mock.patch(
        "backend.ws.manager.ws_manager.broadcast_sync",
        side_effect=lambda c, e, d: sent.append((c, e, d)),
    ):
        r = client.patch("/api/judgments//p/a.png", json={"grade": "pass"})
        assert (r.json()["grade"], r.json()["favorite"]) == ("pass", False)
        assert (
            client.get("/api/judgments//p/a.png").json()["scorecard"]["blind"] is False
        )
        r = client.patch("/api/judgments//p/a.png", json={"favorite": True})
        assert (r.json()["grade"], r.json()["favorite"]) == ("pass", True)
        r = client.patch("/api/judgments//p/a.png", json={"grade": "fail"})
        assert (r.json()["grade"], r.json()["favorite"]) == ("fail", True)
        assert (
            client.get("/api/judgments//p/a.png").json()["scorecard"]["blind"] is False
        )
        # ★ alone on the partner: still hidden, and no scorecard check.
        r = client.patch("/api/judgments//p/b.png", json={"favorite": True})
        assert (r.json()["grade"], r.json()["favorite"]) == (None, True)
        assert (
            client.get("/api/judgments//p/b.png").json()["scorecard"]["blind"] is True
        )
    judged = [s for s in sent if s[1] == "scorecard_judged"]
    assert judged == [("training", "scorecard_judged", {"adapter_id": adapter})] * 2
    assert _events("/p/a.png") == ["grade_pass", "favorite", "grade_fail"]
    rows = {r["file_path"]: r for r in db.list_t2i_images()}
    row = rows[next(p for p in rows if p.endswith("a.png"))]
    assert (row["grade"], row["is_favorite"]) == ("fail", True)
