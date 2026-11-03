"""The tray launcher's decisions. The tray and the server are not started."""

import socket

import pytest

from installer.launcher import (
    browser_url,
    fresh_path,
    last_lines,
    pick_port,
    port_is_free,
)


def test_fresh_path_puts_the_registry_first_and_drops_repeats():
    path = fresh_path(
        current="C:\\old;C:\\Windows\\",
        machine="C:\\Windows;C:\\Tools",
        user="C:\\Users\\me\\WinGet\\Links;;",
    )
    assert path == "C:\\Windows;C:\\Tools;C:\\Users\\me\\WinGet\\Links;C:\\old"


def test_pick_port_takes_the_preferred_one_when_free():
    assert pick_port(8700, lambda port: True) == 8700


def test_pick_port_moves_past_blocked_ports():
    assert pick_port(8700, lambda port: port >= 8702) == 8702


def test_pick_port_gives_up():
    assert pick_port(8700, lambda port: False, tries=3) is None


def test_port_is_free_sees_a_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert port_is_free("127.0.0.1", port) is False


def test_port_is_free_sees_a_server_on_every_interface():
    """Windows would let 127.0.0.1 bind beside it, and take its traffic."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("0.0.0.0", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert port_is_free("127.0.0.1", port) is False


@pytest.mark.parametrize(
    "host, expected",
    [
        ("127.0.0.1", "http://127.0.0.1:8700"),
        ("0.0.0.0", "http://localhost:8700"),
        ("::", "http://localhost:8700"),
        ("::1", "http://[::1]:8700"),
    ],
)
def test_browser_url(host, expected):
    assert browser_url(host, 8700) == expected


def test_last_lines(tmp_path):
    log = tmp_path / "launcher.log"
    log.write_text("one\ntwo\nthree\n", encoding="utf-8")
    assert last_lines(log, 2) == "two\nthree"


def test_last_lines_of_a_missing_log(tmp_path):
    assert last_lines(tmp_path / "none.log") == ""
