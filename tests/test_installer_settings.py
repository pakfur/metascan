"""The Windows installer's answers reach config.json, and nothing else moves."""

import json

import pytest

from installer import apply_settings
from installer.apply_settings import (
    comfy_base_url,
    merge_settings,
    scan_directories,
)


@pytest.mark.parametrize(
    "host, port, expected",
    [
        ("127.0.0.1", 8188, "http://127.0.0.1:8188"),
        ("  gpu-box ", 9000, "http://gpu-box:9000"),
        ("http://gpu-box/", 8188, "http://gpu-box:8188"),
        ("HTTPS://gpu-box", 8188, "http://gpu-box:8188"),
        ("", 8188, "http://127.0.0.1:8188"),
        ("::1", 8188, "http://[::1]:8188"),
    ],
)
def test_comfy_base_url(host, port, expected):
    assert comfy_base_url(host, port) == expected


def test_scan_directories_use_the_forward_slash_form():
    rows = scan_directories(
        [
            {"filepath": "D:\\Media\\", "search_subfolders": False},
            {"filepath": "E:\\", "search_subfolders": True},
        ]
    )
    assert rows == [
        {"filepath": "D:/Media", "search_subfolders": False},
        {"filepath": "E:/", "search_subfolders": True},
    ]


def test_scan_directories_drop_blanks_and_repeats():
    rows = scan_directories(
        [{"filepath": "D:/Media"}, {"filepath": "d:\\media"}, {"filepath": " "}]
    )
    assert rows == [{"filepath": "D:/Media", "search_subfolders": True}]


def test_merge_keeps_every_other_key():
    config = {
        "theme": "dark",
        "comfy": {"base_url": "http://old:1", "in_flight": 5},
        "upscale": {"max_count": 3},
        "similarity": {"device": "cuda", "clip_model": "small"},
    }
    merged = merge_settings(
        config,
        {
            "directories": [{"filepath": "D:\\Media"}],
            "comfy_host": "gpu-box",
            "comfy_port": 9000,
            "original_root": "E:\\Originals",
        },
        "large",
    )
    assert merged["theme"] == "dark"
    assert merged["directories"] == [
        {"filepath": "D:/Media", "search_subfolders": True}
    ]
    assert merged["comfy"] == {"base_url": "http://gpu-box:9000", "in_flight": 5}
    assert merged["upscale"] == {"max_count": 3, "original_root": "E:\\Originals"}
    assert merged["similarity"] == {"device": "cuda", "clip_model": "large"}
    assert config["comfy"]["base_url"] == "http://old:1"


def test_merge_without_a_recommendation_keeps_the_clip_model():
    merged = merge_settings({"similarity": {"clip_model": "medium"}}, {}, None)
    assert merged["similarity"]["clip_model"] == "medium"


def test_the_default_original_folder_stays_empty():
    merged = merge_settings({}, {"original_root": ""}, None)
    assert merged["upscale"]["original_root"] == ""


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"theme": "dark", "directories": []}), encoding="utf-8")
    monkeypatch.setattr(apply_settings, "get_config_path", lambda: path)
    monkeypatch.setattr(apply_settings, "recommended_clip_model", lambda: "medium")
    return path


def test_main_reads_the_wizards_bom_and_writes_the_config(
    config_path, tmp_path, capsys
):
    original = tmp_path / "originals"
    settings = tmp_path / "wizard.json"
    payload = {
        "directories": [{"filepath": "D:\\Médias"}],
        "comfy_host": "127.0.0.1",
        "comfy_port": 8188,
        "original_root": str(original),
    }
    settings.write_text(json.dumps(payload), encoding="utf-8-sig")

    assert apply_settings.main(["--settings", str(settings)]) == 0

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["theme"] == "dark"
    assert saved["directories"][0]["filepath"] == "D:/Médias"
    assert saved["similarity"]["clip_model"] == "medium"
    assert original.is_dir()
    assert capsys.readouterr().out.strip() == "clip_model=medium"


def test_main_without_settings_leaves_the_config_alone(config_path, capsys):
    """An update: provision.ps1 only asks which CLIP model is configured."""
    before = config_path.read_bytes()
    assert apply_settings.main([]) == 0
    assert config_path.read_bytes() == before
    assert capsys.readouterr().out.strip() == "clip_model=small"
