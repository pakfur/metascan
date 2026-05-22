"""Tests for the YAML-backed :class:`PromptStore`.

Covers load, missing-key handling, reload notification, and the file
watcher's end-to-end behaviour (watchdog Observer firing into a fresh
parse). Each test uses its own temporary YAML file so they can run in
parallel without colliding with the production ``data/meta_prompt.yml``.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from metascan.core.prompt_store import PromptStore, PromptStoreError


def _write_yaml(path: Path, mapping: dict[str, str]) -> None:
    """Write ``mapping`` as a YAML file using literal block scalars.

    Avoids the pyyaml ``str`` dump's flow style and quoting heuristics so
    the resulting file looks like the one we ship and exercises the same
    parse path.
    """
    lines: list[str] = []
    for k, v in mapping.items():
        lines.append(f"{k}: |-")
        for body_line in v.split("\n"):
            lines.append(f"  {body_line}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_load_returns_mapped_values(tmp_path: Path) -> None:
    p = tmp_path / "prompts.yml"
    _write_yaml(p, {"FOO": "hello world", "BAR": "line one\nline two"})
    store = PromptStore(p)
    assert store.get("FOO") == "hello world"
    assert store.get("BAR") == "line one\nline two"
    assert sorted(store.keys()) == ["BAR", "FOO"]
    assert "FOO" in store


def test_missing_key_raises_keyerror_with_available_list(tmp_path: Path) -> None:
    p = tmp_path / "prompts.yml"
    _write_yaml(p, {"FOO": "a"})
    store = PromptStore(p)
    with pytest.raises(KeyError) as exc:
        store.get("NOPE")
    # Message should mention both the missing key and the available ones
    # so typos surface fast.
    msg = str(exc.value)
    assert "NOPE" in msg and "FOO" in msg


def test_missing_file_raises_store_error(tmp_path: Path) -> None:
    with pytest.raises(PromptStoreError):
        PromptStore(tmp_path / "does-not-exist.yml")


def test_malformed_yaml_raises_store_error(tmp_path: Path) -> None:
    p = tmp_path / "bad.yml"
    p.write_text("FOO: : :\n", encoding="utf-8")
    with pytest.raises(PromptStoreError):
        PromptStore(p)


def test_top_level_must_be_mapping(tmp_path: Path) -> None:
    p = tmp_path / "list.yml"
    p.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(PromptStoreError):
        PromptStore(p)


def test_non_string_values_are_rejected(tmp_path: Path) -> None:
    p = tmp_path / "typed.yml"
    p.write_text("FOO: 42\n", encoding="utf-8")
    with pytest.raises(PromptStoreError):
        PromptStore(p)


def test_reload_fires_listeners(tmp_path: Path) -> None:
    p = tmp_path / "prompts.yml"
    _write_yaml(p, {"FOO": "v1"})
    store = PromptStore(p)

    fired = threading.Event()
    seen: list[str] = []

    def listener() -> None:
        seen.append(store.get("FOO"))
        fired.set()

    store.on_reload(listener)

    _write_yaml(p, {"FOO": "v2"})
    store.reload()

    assert fired.is_set()
    assert seen == ["v2"]
    assert store.get("FOO") == "v2"


def test_reload_failure_keeps_previous_values(tmp_path: Path) -> None:
    p = tmp_path / "prompts.yml"
    _write_yaml(p, {"FOO": "v1"})
    store = PromptStore(p)

    # Corrupt the file with invalid YAML, then call reload — store should
    # log + keep the previous map rather than blowing away ``FOO``.
    p.write_text("FOO: : bad\n", encoding="utf-8")
    store.reload()
    assert store.get("FOO") == "v1"


def test_listener_exception_does_not_break_subsequent_listeners(tmp_path: Path) -> None:
    p = tmp_path / "prompts.yml"
    _write_yaml(p, {"FOO": "v1"})
    store = PromptStore(p)

    calls: list[str] = []

    def broken() -> None:
        calls.append("broken")
        raise RuntimeError("boom")

    def healthy() -> None:
        calls.append("healthy")

    store.on_reload(broken)
    store.on_reload(healthy)
    store.reload()

    assert calls == ["broken", "healthy"]


def test_file_watcher_triggers_reload(tmp_path: Path) -> None:
    p = tmp_path / "prompts.yml"
    _write_yaml(p, {"FOO": "v1"})
    store = PromptStore(p)
    store.start_watching()
    try:
        fired = threading.Event()

        def listener() -> None:
            if store.get("FOO") == "v2":
                fired.set()

        store.on_reload(listener)

        # watchdog's polling/inotify lag varies by platform — give it a
        # generous deadline, then assert.
        _write_yaml(p, {"FOO": "v2"})
        assert fired.wait(timeout=5.0), "file watcher did not fire reload"
        assert store.get("FOO") == "v2"
    finally:
        store.stop_watching()


def test_start_watching_is_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "prompts.yml"
    _write_yaml(p, {"FOO": "v1"})
    store = PromptStore(p)
    try:
        store.start_watching()
        # Second call must not raise and must not spawn another observer.
        store.start_watching()
        assert store._observer is not None  # type: ignore[attr-defined]
    finally:
        store.stop_watching()
    # After stop, watching state is reset.
    assert store._observer is None  # type: ignore[attr-defined]


def test_active_yaml_has_every_required_key() -> None:
    """The production ``data/meta_prompt.yml`` must define every key the
    refactored modules read at runtime — guard against accidental deletes.
    """
    from metascan.core.prompt_store import get_prompt_store

    store = get_prompt_store()
    required = {
        "POLICY_PREAMBLE",
        "USER_INSTRUCTION",
        "SAFETY_DIRECTIVE",
        "UNCENSORED_DIRECTIVE",
        "META_FLUX1",
        "META_FLUX2",
        "META_ZIMAGE",
        "META_CHROMA",
        "META_QWEN",
        "META_SDXL",
        "META_PONY",
        "TAGGING_SYSTEM_PROMPT",
        "TAGGING_USER_PROMPT",
        "TAGGING_GRAMMAR",
    }
    missing = required - set(store.keys())
    assert not missing, f"data/meta_prompt.yml missing keys: {sorted(missing)}"
