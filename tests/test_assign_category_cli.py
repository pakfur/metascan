"""Tests for scripts/assign_category.py (standalone bulk-category CLI)."""

import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "assign_category.py"
_spec = importlib.util.spec_from_file_location("assign_category", _SCRIPT)
assert _spec is not None and _spec.loader is not None
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


def _mock_client(
    media: List[str],
    categories: List[Dict[str, Any]],
    record: List[httpx.Request],
    removed: int = 0,
) -> httpx.Client:
    cats = list(categories)

    def handler(request: httpx.Request) -> httpx.Response:
        record.append(request)
        path = request.url.path
        if request.method == "GET" and path == "/api/media":
            return httpx.Response(200, json=[{"file_path": p} for p in media])
        if path == "/api/categories":
            if request.method == "GET":
                return httpx.Response(200, json=cats)
            name = json.loads(request.content)["name"]
            cat = {"id": 100 + len(cats), "name": name}
            cats.append(dict(cat, item_count=0, created_at="x"))
            return httpx.Response(200, json=cat)
        if path.endswith("/items"):
            paths = json.loads(request.content)["file_paths"]
            if request.method == "POST":
                return httpx.Response(200, json={"added": len(paths)})
            return httpx.Response(
                200, json={"status": "deleted", "removed": len(paths)}
            )
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")


@pytest.mark.parametrize(
    "path,folder,expected",
    [
        ("/lib/scifi/a.png", "/lib/sci", False),
        ("/lib/sci/deep/a.png", "/lib/sci/", True),
        ("C:\\Lib\\Sci\\a.png", "c:/lib/sci", True),
        ("/Lib/Sci/a.png", "/lib/sci", False),
    ],
)
def test_is_under(path, folder, expected):
    assert cli.is_under(path, folder) is expected


def test_dry_run_makes_no_writes(capsys):
    calls: List[httpx.Request] = []
    client = _mock_client(
        media=["/lib/sci/a.png", "/lib/other/b.png"], categories=[], record=calls
    )
    assert (
        cli.main(["/lib/sci", "--category", "Sci-Fi", "--dry-run"], client=client) == 0
    )
    assert all(c.method == "GET" for c in calls)
    assert (
        "1 items under /lib/sci (dry run, nothing changed)" in capsys.readouterr().out
    )


def test_creates_category_and_adds_in_chunks(capsys):
    calls: List[httpx.Request] = []
    media = [f"/lib/sci/{i}.png" for i in range(1200)]
    client = _mock_client(media=media, categories=[], record=calls)
    assert cli.main(["/lib/sci", "--category", "Sci-Fi"], client=client) == 0
    posts = [c for c in calls if c.method == "POST" and c.url.path.endswith("/items")]
    assert [len(json.loads(c.content)["file_paths"]) for c in posts] == [500, 500, 200]
    assert all(json.loads(c.content)["source"] == "bulk" for c in posts)
    assert "1200 items under /lib/sci; added 1200" in capsys.readouterr().out


def test_existing_category_matched_case_insensitively():
    calls: List[httpx.Request] = []
    cats = [{"id": 7, "name": "Sci-Fi", "item_count": 0, "created_at": "x"}]
    client = _mock_client(media=["/lib/sci/a.png"], categories=cats, record=calls)
    assert cli.main(["/lib/sci", "--category", "sci-fi"], client=client) == 0
    assert not any(
        c.method == "POST" and c.url.path == "/api/categories" for c in calls
    )
    assert any(c.url.path == "/api/categories/7/items" for c in calls)


def test_remove_sends_delete(capsys):
    calls: List[httpx.Request] = []
    cats = [{"id": 7, "name": "Sci-Fi", "item_count": 1, "created_at": "x"}]
    client = _mock_client(media=["/lib/sci/a.png"], categories=cats, record=calls)
    assert (
        cli.main(["/lib/sci", "--category", "Sci-Fi", "--remove"], client=client) == 0
    )
    dels = [c for c in calls if c.method == "DELETE"]
    assert len(dels) == 1 and json.loads(dels[0].content) == {
        "file_paths": ["/lib/sci/a.png"]
    }
    assert "1 items under /lib/sci; removed 1" in capsys.readouterr().out


def test_no_create_unknown_category_exits_2():
    client = _mock_client(media=["/lib/a.png"], categories=[], record=[])
    assert cli.main(["/lib", "--category", "Nope", "--no-create"], client=client) == 2


def test_server_unreachable_exits_1(capsys):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = httpx.Client(transport=httpx.MockTransport(boom), base_url="http://test")
    assert cli.main(["/lib", "--category", "X"], client=client) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err and len(err.strip().splitlines()) == 1


def test_bearer_header_sent_when_env_set(monkeypatch):
    monkeypatch.setenv("METASCAN_API_KEY", "sekrit")
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    # No injected client: main builds its own, so patch the transport it uses.
    real = httpx.Client
    monkeypatch.setattr(
        cli.httpx,
        "Client",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    cli.main(["/lib", "--category", "X", "--dry-run"])
    assert seen and all(r.headers["authorization"] == "Bearer sekrit" for r in seen)


@pytest.mark.parametrize("extra", [[], ["--dry-run"], ["--remove"]])
def test_zero_matches_warns_exits_3_and_creates_nothing(capsys, extra):
    calls: List[httpx.Request] = []
    client = _mock_client(media=["/lib/other/b.png"], categories=[], record=calls)
    code = cli.main(["/lib/typo", "--category", "Sci-Fi", *extra], client=client)
    assert code == 3
    assert all(c.method == "GET" for c in calls)
    assert not any(c.url.path == "/api/categories" for c in calls)
    err = capsys.readouterr().err
    assert "no library items under /lib/typo" in err
