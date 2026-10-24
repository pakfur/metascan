"""The prompt-writer training dataset builder (taste learning project 4):
preference pairs from variant groups, edit pairs, hindsight examples,
category scope, holdout and readiness -- synthetic rows in an isolated
temp DB, a temp prompt store."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from metascan.core.prompt_store import PromptStore
from metascan.core.writer_dataset import (
    DatasetCounts,
    PrefExample,
    SftExample,
    build_examples,
    caption_resolver,
    holdout_key,
    preview,
    preview_mix,
    readiness,
    sft_without_holdout,
    split_holdout,
    write_jsonl,
)

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
T0 = "2026-10-01 09:00:00"
T1 = "2026-10-10 09:00:00"

_YAML = """\
T2I_CAPTION_PREAMBLE: |-
  PREAMBLE-TEXT
META_KREA2: |-
  KREA-GUIDE
META_QWEN: |-
  QWEN-GUIDE
META_SDXL: |-
  SDXL-GUIDE
META_ZIMAGE: |-
  ZIMAGE-GUIDE
UNCENSORED_DIRECTIVE: |-
  UNCENSORED-DIRECTIVE
SAFETY_DIRECTIVE: |-
  SAFETY-DIRECTIVE
"""


def _media(path: str) -> Media:
    return Media(
        file_path=Path(path),
        file_size=1,
        width=8,
        height=8,
        format="png",
        created_at=datetime.now(),
        modified_at=datetime.now(),
    )


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


@pytest.fixture
def store(tmp_path):
    p = tmp_path / "meta_prompt.yml"
    p.write_text(_YAML, encoding="utf-8")
    return PromptStore(p)


def _img(
    db: DatabaseManager,
    path: str,
    *,
    outcome: Optional[str] = None,
    writer: Optional[str] = "base",
    group: Optional[str] = "g1",
    index: int = 0,
    prompt: str = "prompt A",
    caption: str = "a red fox in snow",
    model: str = "krea2",
    draft: Optional[str] = None,
    edited: Optional[int] = None,
    scorecard: Optional[str] = None,
    created_at: str = T0,
    resolved: Optional[str] = None,
    direction: Optional[str] = None,
    prompt_seed: Optional[int] = None,
) -> None:
    db.save_media(_media(path))
    image_id = db.create_t2i_image(
        file_path=path,
        model=model,
        caption=caption,
        prompt_used=prompt,
        writer_id=writer,
        writer_model="qwen3vl-8b" if writer not in (None, "manual") else None,
        variant_group=group,
        variant_index=index,
        draft_prompt=draft if draft is not None else prompt,
        edited=edited if edited is not None else 0,
        scorecard_id=scorecard,
        resolved_caption=resolved,
        direction=direction,
        prompt_seed=prompt_seed,
    )
    with db.lock, db._get_connection() as conn:
        conn.execute(
            "UPDATE t2i_images SET created_at = ? WHERE id = ?",
            (created_at, image_id),
        )
        conn.commit()
    if outcome == "seen":
        db.mark_seen(path, NOW)
    elif outcome in ("favorite", "pass"):
        db.set_verdict(path, outcome, None, NOW)


def _build(db, scope, store, now, **kw):
    kw.setdefault("content_mode", "default")
    return build_examples(db, scope, store, now, **kw)


def _key(model="krea2", description="a red fox in snow"):
    return holdout_key(model, description)


def _pair_group(db, group="g1", fav="/g/fav.png", seen="/g/seen.png", **kw):
    _img(db, fav, outcome="favorite", group=group, index=0, prompt="WINNER", **kw)
    _img(db, seen, outcome="seen", group=group, index=1, prompt="LOSER", **kw)


def test_variant_group_yields_one_pair_in_order(db, store):
    _pair_group(db)
    sft, prefs, counts = _build(db, "all", store, NOW)
    assert len(prefs) == 1
    p = prefs[0]
    assert (p.chosen, p.rejected, p.source) == ("WINNER", "LOSER", "variant")
    assert p.target_model == "krea2"
    assert p.group == _key()
    # System prompt as at inference: preamble + the target model's guide.
    assert p.system.startswith("PREAMBLE-TEXT")
    assert "KREA-GUIDE" in p.system
    # User turn: the caption in the DESCRIPTION wrapper; no angle line.
    assert p.user.startswith("DESCRIPTION:\na red fox in snow")
    assert p.user.endswith("Write the prompt now.")
    assert "ANGLE" not in p.user
    assert "CATEGORIES" not in p.user  # no categories on these images
    assert counts.pref_pairs == 1 and counts.edit_pairs == 0
    assert counts.content_mode == "default"
    assert sft == []  # nothing edited, no hindsight briefs


def test_unseen_variant_and_all_favorite_groups_make_no_pair(db, store):
    _img(db, "/g/a.png", outcome="favorite", index=0, prompt="A")
    _img(db, "/g/b.png", outcome=None, index=1, prompt="B")  # never seen
    _img(db, "/h/a.png", outcome="favorite", group="g2", index=0, prompt="C")
    _img(db, "/h/b.png", outcome="favorite", group="g2", index=1, prompt="D")
    _, prefs, _ = _build(db, "all", store, NOW)
    assert prefs == []


def test_variant_with_a_favorite_seed_is_a_winner(db, store):
    # Two seeds of variant 0: one favorite, one passed -> still the winner.
    _img(db, "/g/a1.png", outcome="pass", index=0, prompt="WIN")
    _img(db, "/g/a2.png", outcome="favorite", index=0, prompt="WIN")
    _img(db, "/g/b1.png", outcome="pass", index=1, prompt="LOSE")
    _img(db, "/g/b2.png", outcome="seen", index=1, prompt="LOSE")
    _, prefs, _ = _build(db, "all", store, NOW)
    assert [(p.chosen, p.rejected) for p in prefs] == [("WIN", "LOSE")]


def test_manual_writer_excluded_everywhere(db, store):
    _img(db, "/g/fav.png", outcome="favorite", writer="manual", prompt="MAN")
    _img(db, "/g/seen.png", outcome="seen", index=1, prompt="LOSER")
    _img(
        db,
        "/m/edit.png",
        writer="manual",
        group=None,
        prompt="EDITED",
        draft="DRAFT",
        edited=1,
    )
    sft, prefs, counts = _build(db, "all", store, NOW)
    assert prefs == [] and sft == []
    assert counts.pref_pairs == counts.edit_pairs == 0


def test_scorecard_images_excluded(db, store):
    _pair_group(db, scorecard="sc-1")
    _img(
        db,
        "/s/edit.png",
        group=None,
        prompt="EDITED",
        draft="DRAFT",
        edited=1,
        scorecard="sc-1",
    )
    sft, prefs, _ = _build(db, "all", store, NOW)
    assert prefs == [] and sft == []


def test_scorecard_loser_drops_only_itself(db, store):
    _img(db, "/g/fav.png", outcome="favorite", index=0, prompt="WIN")
    _img(db, "/g/sc.png", outcome="seen", index=1, prompt="SC", scorecard="x")
    _img(db, "/g/ok.png", outcome="seen", index=2, prompt="OK")
    _, prefs, _ = _build(db, "all", store, NOW)
    assert [(p.chosen, p.rejected) for p in prefs] == [("WIN", "OK")]


def test_videos_are_not_training_data(db, store):
    _pair_group(db)
    db.save_media(_media("/v/src.png"))
    db.save_media(_media("/v/clip.mp4"))
    db.create_i2v_video(
        source_path="/v/src.png", file_path="/v/clip.mp4", writer_id="base"
    )
    db.set_verdict("/v/clip.mp4", "favorite", None, NOW)
    _, prefs, counts = _build(db, "all", store, NOW)
    assert len(prefs) == 1 and counts.pref_pairs == 1


def test_edit_pairs(db, store):
    _img(
        db,
        "/e/1.png",
        group=None,
        prompt="EDITED TEXT",
        draft="DRAFT TEXT",
        edited=1,
        model="qwen",
    )
    # Whitespace-only difference is not an edit, nor is edited=0.
    _img(db, "/e/2.png", group=None, prompt="same  text", draft="same text", edited=1)
    _img(db, "/e/3.png", group=None, prompt="X", draft="Y", edited=0)
    sft, prefs, counts = _build(db, "all", store, NOW)
    assert counts.edit_pairs == 1
    edit_prefs = [p for p in prefs if p.source == "edit"]
    assert [(p.chosen, p.rejected) for p in edit_prefs] == [
        ("EDITED TEXT", "DRAFT TEXT")
    ]
    assert "QWEN-GUIDE" in edit_prefs[0].system
    assert [(s.chosen, s.source) for s in sft] == [("EDITED TEXT", "edit")]
    assert sft[0].user == edit_prefs[0].user


def test_edit_pair_counted_once_per_variant(db, store):
    for i in range(3):  # three seeds of one edited variant
        _img(db, f"/e/{i}.png", prompt="EDITED", draft="DRAFT", edited=1)
    _, prefs, counts = _build(db, "all", store, NOW)
    assert counts.edit_pairs == 1 and len(prefs) == 1


def test_content_mode_appends_directive(db, store):
    _pair_group(db)
    _, prefs, _ = build_examples(db, "all", store, NOW, content_mode="uncensored")
    assert prefs[0].system.endswith("UNCENSORED-DIRECTIVE")


def test_category_scope_filters_and_all_scope_names_categories(db, store):
    _pair_group(db, group="g1", fav="/a/fav.png", seen="/a/seen.png")
    _pair_group(
        db, group="g2", fav="/b/fav.png", seen="/b/seen.png", caption="a blue boat"
    )
    cat = db.create_category("Landscapes")
    other = db.create_category("Portraits")
    db.add_media_categories(["/a/fav.png", "/a/seen.png"], cat.id, "generation")
    db.add_media_categories(["/b/seen.png"], other.id, "generation")
    # A viewer-added category filters a scope but is not in the line.
    viewer = db.create_category("Later")
    db.add_media_categories(["/a/fav.png"], viewer.id, "viewer")
    g1, g2 = _key(), _key(description="a blue boat")

    _, prefs, counts = _build(db, str(cat.id), store, NOW)
    assert [p.group for p in prefs] == [g1]
    assert counts.pref_pairs == 1
    # A category scope adds no CATEGORIES line (every example carries it).
    assert "CATEGORIES" not in prefs[0].user

    _, prefs, _ = _build(db, "all", store, NOW)
    by_group = {p.group: p for p in prefs}
    assert "CATEGORIES: Landscapes\n" in by_group[g1].user
    assert "Later" not in by_group[g1].user
    assert "CATEGORIES: Portraits\n" in by_group[g2].user
    assert by_group[g1].categories == ["Landscapes"]
    assert by_group[g1].user.endswith("Write the prompt now.")

    _, prefs, _ = _build(db, str(other.id), store, NOW)
    assert [p.group for p in prefs] == [g2]
    _, prefs, _ = _build(db, str(viewer.id), store, NOW)
    assert [p.group for p in prefs] == [g1]
    assert prefs[0].categories == [] and "CATEGORIES" not in prefs[0].user


def test_unknown_scope_raises(db, store):
    with pytest.raises(ValueError):
        _build(db, "999", store, NOW)
    with pytest.raises(ValueError):
        _build(db, "nope", store, NOW)


def test_deleted_generations_count_via_tombstones(db, store):
    _pair_group(db)
    cat = db.create_category("Landscapes")
    db.add_media_categories(["/g/fav.png"], cat.id, "viewer")
    assert db.delete_media(Path("/g/seen.png"))
    assert db.delete_media(Path("/g/fav.png"))
    _, prefs, _ = _build(db, str(cat.id), store, NOW)
    assert [(p.chosen, p.rejected) for p in prefs] == [("WINNER", "LOSER")]


def test_unknown_target_model_and_blank_caption_skipped(db, store):
    _pair_group(db, group="g1", model="mystery")
    _pair_group(db, group="g2", fav="/b/f.png", seen="/b/s.png", caption="  ")
    _, prefs, _ = _build(db, "all", store, NOW)
    assert prefs == []


def test_hindsight_examples(db, store):
    _img(db, "/h/fav.png", outcome="favorite", group=None, prompt="THE PROMPT")
    _img(db, "/h/seen.png", outcome="seen", group=None, prompt="NOT FAV")
    _img(
        db,
        "/h/man.png",
        outcome="favorite",
        group=None,
        writer="manual",
        prompt="MANUAL",
    )
    db.set_hindsight_brief("/h/fav.png", "a fox in the snow", "qwen3vl-8b")
    db.set_hindsight_brief("/h/seen.png", "something", "qwen3vl-8b")
    db.set_hindsight_brief("/h/man.png", "manual brief", "qwen3vl-8b")
    sft, prefs, counts = _build(db, "all", store, NOW)
    assert prefs == []
    assert counts.hindsight == 1
    (ex,) = sft
    assert (ex.chosen, ex.source, ex.target_model) == (
        "THE PROMPT",
        "hindsight",
        "krea2",
    )
    assert ex.user.startswith("DESCRIPTION:\na fox in the snow")


def test_hindsight_library_item_needs_a_known_family(db, store):
    for path, model_name in (
        ("/lib/a.png", "krea2_dev.safetensors"),
        ("/lib/b.png", "mystery"),
    ):
        db.save_media(_media(path))
        with db.lock, db._get_connection() as conn:
            conn.execute(
                "UPDATE media SET data = json_set(data, '$.prompt', ?) "
                "WHERE file_path = ?",
                (f"LIB PROMPT {path}", path),
            )
            conn.execute(
                "INSERT INTO indices (index_type, index_key, file_path) "
                "VALUES ('model', ?, ?)",
                (model_name, path),
            )
            conn.commit()
        db.set_verdict(path, "favorite", None, NOW)
        db.set_hindsight_brief(path, f"brief {path}", "qwen3vl-8b")
    sft, _, counts = _build(db, "all", store, NOW)
    assert [(s.chosen, s.target_model) for s in sft] == [
        ("LIB PROMPT /lib/a.png", "krea2")
    ]
    assert counts.hindsight == 1


def test_counts_per_target_model_and_span(db, store):
    _pair_group(db, group="g1")
    _pair_group(db, group="g2", fav="/b/f.png", seen="/b/s.png", model="sd")
    _img(db, "/e/1.png", group=None, prompt="E", draft="D", edited=1, model="sd")
    _, _, counts = _build(db, "all", store, NOW)
    assert counts.by_target_model == {"krea2": 1, "sd": 2}
    assert counts.pref_pairs == 2 and counts.edit_pairs == 1
    assert counts.new_since_last == 3  # no previous adapter
    assert counts.date_span[0] <= counts.date_span[1]
    assert counts.date_span[1].startswith("2026-10-23")
    d = counts.to_dict()
    assert d["by_target_model"] == {"krea2": 1, "sd": 2}
    json.dumps(d)


def test_new_since_last_uses_the_newest_ready_adapter_of_the_scope(db, store):
    _img(db, "/o/f.png", group="old", prompt="W1", created_at=T0)
    _img(db, "/o/s.png", group="old", index=1, prompt="L1", created_at=T0)
    # Judge the old group before the cutoff.
    old = datetime(2026, 10, 2, tzinfo=timezone.utc)
    db.set_verdict("/o/f.png", "favorite", None, old)
    db.mark_seen("/o/s.png", old)
    _pair_group(db, group="new", created_at=T1)
    aid = db.create_writer_adapter(
        "taste-all-v1",
        "all",
        "t2i",
        "qwen3vl-8b",
        status="ready",
        data_cutoff="2026-10-05T00:00:00+00:00",
    )
    # A ready adapter of another scope and a failed one of this scope don't count.
    db.create_writer_adapter(
        "taste-x-v1", "7", "t2i", "qwen3vl-8b", status="ready", data_cutoff="2026-12-01"
    )
    db.create_writer_adapter(
        "taste-all-v2",
        "all",
        "t2i",
        "qwen3vl-8b",
        status="failed",
        data_cutoff="2026-12-01",
    )
    assert aid
    _, _, counts = _build(db, "all", store, NOW)
    assert counts.pref_pairs == 2
    assert counts.new_since_last == 1


def test_split_holdout_is_seeded_disjoint_and_by_group():
    prefs = []
    for g in range(20):
        for k in range(2):  # two pairs per group share a chosen prompt
            prefs.append(
                PrefExample(
                    system="s",
                    user=f"u{g}",
                    chosen=f"c{g}",
                    rejected=f"r{g}-{k}",
                    target_model="krea2",
                    categories=[],
                    source="variant",
                    group=f"g{g}",
                    as_of="2026-10-01T00:00:00+00:00",
                )
            )
    prefs.append(PrefExample("s", "u0", "edited", "c0", "krea2", [], "edit", "g0", "x"))
    train, hold = split_holdout(prefs, fraction=0.1, seed=0)
    train2, hold2 = split_holdout(prefs, fraction=0.1, seed=0)
    assert [p.to_dict() for p in hold] == [p.to_dict() for p in hold2]
    assert len({p.group for p in hold}) == 2
    assert {p.group for p in train}.isdisjoint({p.group for p in hold})
    assert len(train) + len(hold) == len(prefs)
    # The edit pair travels with its group.
    edit_side = hold if "g0" in {p.group for p in hold} else train
    assert any(p.source == "edit" for p in edit_side)
    # Input order does not change which groups are held out.
    _, hold_rev = split_holdout(list(reversed(prefs)), fraction=0.1, seed=0)
    assert {p.group for p in hold_rev} == {p.group for p in hold}

    sft = [
        SftExample("s", "u", "c0", "krea2", [], "hindsight", "g0"),
        SftExample("s", "u", "zz", "krea2", [], "hindsight", "solo"),
    ]
    held = {p.group for p in hold}
    kept = sft_without_holdout(sft, hold)
    assert all(s.group not in held for s in kept)
    assert any(s.group == "solo" for s in kept)


def test_split_holdout_small_sets():
    one = [PrefExample("s", "u", "c", "r", "krea2", [], "variant", "g", "x")]
    train, hold = split_holdout(one)
    assert hold == [] and train == one
    assert split_holdout([]) == ([], [])


def test_write_jsonl_and_preview(tmp_path):
    prefs = [PrefExample("SYS", "USR", "C", "R", "qwen", ["Cat"], "variant", "g", "x")]
    sft = [SftExample("SYS", "USR", "C", "qwen", [], "edit", "k")]
    p = tmp_path / "out" / "prefs.jsonl"
    assert write_jsonl(prefs, p) == 1
    line = json.loads(p.read_text(encoding="utf-8").strip())
    assert line == {
        "system": "SYS",
        "user": "USR",
        "chosen": "C",
        "rejected": "R",
        "target_model": "qwen",
        "categories": ["Cat"],
        "source": "variant",
        "group": "g",
    }
    q = tmp_path / "sft.jsonl"
    write_jsonl(sft, q)
    assert json.loads(q.read_text(encoding="utf-8")) == {
        "system": "SYS",
        "user": "USR",
        "chosen": "C",
        "target_model": "qwen",
        "categories": [],
        "source": "edit",
        "group": "k",
    }
    many = sft * 15
    assert len(preview(many)) == 10
    assert preview(many, 3)[0]["chosen"] == "C"


def _pref(i, source):
    return PrefExample("S", f"u{i}", f"c{i}", f"r{i}", "qwen", [], source, f"g{i}")


def _sft(i, source):
    return SftExample("S", f"s{i}", f"t{i}", "qwen", [], source, f"h{i}")


def test_preview_mix_balances_kinds_and_labels_them():
    prefs = [_pref(i, "variant") for i in range(20)] + [
        _pref(100 + i, "edit") for i in range(3)
    ]
    sft = [_sft(100 + i, "edit") for i in range(3)] + [
        _sft(i, "hindsight") for i in range(20)
    ]
    out = preview_mix(prefs, sft)
    assert len(out) == 10
    kinds = [e["kind"] for e in out]
    # Round robin: every kind shows up; edits (only 3) are all shown.
    assert kinds.count("edit") == 3
    assert kinds.count("preference") == 4 and kinds.count("hindsight") == 3
    assert kinds[:3] == ["preference", "edit", "hindsight"]
    # An edit is shown as its DPO pair (draft = rejected), not twice.
    edits = [e for e in out if e["kind"] == "edit"]
    assert all("rejected" in e for e in edits)
    assert len({e["user"] for e in edits}) == 3


def test_preview_mix_fills_from_the_kinds_that_exist():
    sft = [_sft(i, "hindsight") for i in range(4)] + [_sft(9, "edit")]
    out = preview_mix([_pref(0, "variant")], sft, n=10)
    assert [e["kind"] for e in out].count("hindsight") == 4
    # An edit with only its SFT form still shows (no rejected side).
    edit = [e for e in out if e["kind"] == "edit"]
    assert len(edit) == 1 and "rejected" not in edit[0]
    assert len(out) == 6
    assert preview_mix([], []) == []


def test_readiness():
    def counts(pref, edit, new):
        return DatasetCounts(pref, edit, 0, {}, new, ("", ""))

    assert readiness(counts(60, 39, 99), None) == {
        "trainable": False,
        "enough_new": False,
        "missing": 1,
    }
    assert readiness(counts(60, 40, 100), None) == {
        "trainable": True,
        "enough_new": True,
        "missing": 0,
    }
    assert readiness(counts(300, 0, 199), "2026-10-01") == {
        "trainable": True,
        "enough_new": False,
        "missing": 0,
    }
    assert readiness(counts(300, 0, 200), "2026-10-01")["enough_new"] is True


def test_nothing_logged_with_example_text(db, store, caplog):
    _pair_group(db)
    _img(db, "/e/1.png", group=None, prompt="EDITED", draft="DRAFT", edited=1)
    db.set_hindsight_brief("/g/fav.png", "SECRET-BRIEF", "qwen3vl-8b")
    with caplog.at_level(logging.DEBUG):
        sft, prefs, _ = _build(db, "all", store, NOW)
        preview(prefs)
    assert sft and prefs
    text = caplog.text
    for secret in ("WINNER", "LOSER", "EDITED", "DRAFT", "SECRET-BRIEF", "red fox"):
        assert secret not in text


def test_hindsight_briefs_db_roundtrip(db):
    db.save_media(_media("/x/a.png"))
    db.set_hindsight_brief("/x/a.png", "first", "m1")
    db.set_hindsight_brief("/x/a.png", "second", "m2")
    got = db.get_hindsight_briefs()
    assert got["/x/a.png"]["brief"] == "second"
    assert got["/x/a.png"]["model"] == "m2"
    assert db.get_hindsight_briefs(["/x/other.png"]) == {}
    db.delete_media(Path("/x/a.png"))
    assert db.get_hindsight_briefs() == {}


# ---- fix round 1: turns as at inference, fallback, holdout key ------------


def test_stored_resolved_caption_and_direction_are_the_turn(db, store):
    _pair_group(
        db,
        caption="__PERSON__ in snow",
        resolved="a tall woman in snow",
        direction="slow, tender",
    )
    _, prefs, _ = _build(db, "all", store, NOW)
    (p,) = prefs
    assert p.user == (
        "DESCRIPTION:\na tall woman in snow\n\nDIRECTION:\nslow, tender\n\n"
        "Write the prompt now."
    )
    assert p.group == _key(description="a tall woman in snow")


def test_sides_with_different_directions_make_no_pair(db, store):
    _img(db, "/g/a.png", outcome="favorite", prompt="A", direction="one")
    _img(db, "/g/b.png", outcome="seen", index=1, prompt="B", direction="two")
    _, prefs, _ = _build(db, "all", store, NOW)
    assert prefs == []


def test_old_rows_with_tokens_are_re_resolved_or_skipped(db, store):
    seen = []

    def resolve(caption, seed, model):
        seen.append((caption, seed, model))
        return f"resolved {seed}"

    _pair_group(db, group="g1", caption="__X__ fox", prompt_seed=7)
    _pair_group(
        db, group="g2", fav="/b/f.png", seen="/b/s.png", caption="__X__ owl"
    )  # tokens, no prompt seed: skipped
    _, prefs, _ = _build(db, "all", store, NOW, resolve=resolve)
    assert [p.user.split("\n")[1] for p in prefs] == ["resolved 7"]
    assert seen == [("__X__ fox", 7, "krea2")]  # cached per caption + seed
    # Without a resolver a tokened caption cannot be described.
    _, prefs, _ = _build(db, "all", store, NOW)
    assert prefs == []


def test_caption_resolver_resolves_like_the_runner():
    from metascan.core.t2i_characters import CharacterConfig, Library

    library = Library(config=CharacterConfig(), lists={"weather": ("rain",)})
    resolve = caption_resolver(library, {})
    assert resolve("a fox in __WEATHER__", 3, "krea2") == "a fox in rain"
    assert resolve("a fox", 3, "nope") is None


def test_fallback_is_never_a_target(db, store):
    # Fallback winner: no pair. Fallback loser: a pair.
    _img(db, "/f/w.png", outcome="favorite", writer="fallback", prompt="FB")
    _img(db, "/f/l.png", outcome="seen", index=1, prompt="L")
    _img(db, "/k/w.png", outcome="favorite", group="g2", prompt="W")
    _img(
        db,
        "/k/l.png",
        outcome="seen",
        group="g2",
        index=1,
        writer="fallback",
        prompt="FBL",
    )
    # An edit of a fallback draft keeps the owner's text as the target.
    _img(
        db,
        "/e/1.png",
        group=None,
        writer="fallback",
        prompt="MINE",
        draft="FBDRAFT",
        edited=1,
        caption="an edit",
    )
    # A favorited fallback image is not a hindsight target.
    _img(
        db,
        "/h/1.png",
        outcome="favorite",
        group=None,
        writer="fallback",
        prompt="FBH",
        caption="hind",
    )
    db.set_hindsight_brief("/h/1.png", "a brief", "qwen3vl-8b")
    sft, prefs, counts = _build(db, "all", store, NOW)
    pairs = sorted((p.chosen, p.rejected, p.source) for p in prefs)
    assert pairs == [("MINE", "FBDRAFT", "edit"), ("W", "FBL", "variant")]
    assert [s.chosen for s in sft] == ["MINE"]
    assert counts.hindsight == 0


def test_holdout_groups_by_model_and_description(db, store):
    # The same caption generated in two batches is one holdout group.
    _pair_group(db, group="b1", fav="/1/f.png", seen="/1/s.png")
    _pair_group(db, group="b2", fav="/2/f.png", seen="/2/s.png")
    for i in range(3):
        _pair_group(
            db,
            group=f"o{i}",
            fav=f"/o{i}/f.png",
            seen=f"/o{i}/s.png",
            caption=f"other {i}",
        )
    # A hindsight example of a favorite in the shared caption joins its group.
    db.set_hindsight_brief("/1/f.png", "a brief", "qwen3vl-8b")
    sft, prefs, _ = _build(db, "all", store, NOW)
    assert len({p.group for p in prefs}) == 4
    hint = next(s for s in sft if s.source == "hindsight")
    assert hint.group == _key()
    for seed in range(20):
        train, hold = split_holdout(prefs, fraction=0.25, seed=seed)
        shared = [p for p in prefs if p.group == _key()]
        assert all(p in hold for p in shared) or all(p in train for p in shared)
        if any(p in hold for p in shared):
            assert hint not in sft_without_holdout(sft, hold)


def test_malformed_tombstones_warn_with_a_count_only(db, store, caplog):
    with db.lock, db._get_connection() as conn:
        for path in ("/t/x.png", "/t/y.png"):
            # Category names that are not JSON: unreadable.
            conn.execute(
                "INSERT INTO media_tombstones (file_path, deleted_at, is_favorite, "
                "passed, reasons, categories, lineage) "
                "VALUES (?, '2026-10-01', 1, 0, '[]', 'oops', ?)",
                (path, '{"t2i": {"caption": "SECRET"}}'),
            )
        conn.commit()
    with caplog.at_level(logging.DEBUG):
        _build(db, "all", store, NOW)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "2 malformed" in warnings[0].getMessage()
    assert "SECRET" not in caplog.text


def test_tombstones_carry_resolved_caption_and_direction(db, store):
    _pair_group(db, resolved="a resolved fox", direction="calm")
    db.delete_media(Path("/g/fav.png"))
    db.delete_media(Path("/g/seen.png"))
    (t,) = [x for x in db.list_tombstones() if x["file_path"] == "/g/fav.png"]
    assert t["lineage"]["t2i"]["resolved_caption"] == "a resolved fox"
    assert t["lineage"]["t2i"]["direction"] == "calm"
    _, prefs, _ = _build(db, "all", store, NOW)
    assert "a resolved fox" in prefs[0].user and "DIRECTION:\ncalm" in prefs[0].user


def test_content_mode_is_required(db, store):
    with pytest.raises(TypeError):
        build_examples(db, "all", store, NOW)  # type: ignore[call-arg]


def _variant_pairs(groups):
    return [
        PrefExample("s", f"u{g}", f"c{g}", f"r{g}", "krea2", [], "variant", g, "x")
        for g in groups
    ]


def test_split_holdout_is_stable_when_groups_are_added():
    # A run continued from an earlier adapter sees a superset of its groups:
    # no group may cross the split, or the new holdout would contain pairs
    # the earlier adapter trained on.
    base = [f"group-{i}" for i in range(300)]
    more = base + [f"later-{i}" for i in range(700)]
    _, hold_a = split_holdout(_variant_pairs(base))
    _, hold_b = split_holdout(_variant_pairs(more))
    held_a = {p.group for p in hold_a}
    held_b = {p.group for p in hold_b}
    assert held_a == held_b & set(base)
    # Removing groups never moves the remaining ones either.
    _, hold_c = split_holdout(_variant_pairs(base[::2]))
    assert {p.group for p in hold_c} == held_a & set(base[::2])


def test_split_holdout_holds_about_ten_percent_of_a_large_set():
    groups = [f"g-{i}" for i in range(5000)]
    _, hold = split_holdout(_variant_pairs(groups))
    share = len({p.group for p in hold}) / len(groups)
    assert 0.085 <= share <= 0.115
