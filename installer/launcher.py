"""What the Metascan shortcut runs: a tray icon that owns the server.

The Start menu and desktop shortcuts run this with pythonw, so there is no
console. It starts ``installer.serve`` hidden, opens the browser once the server
answers, and stays in the tray so the owner can open or stop Metascan. A second
click on a shortcut only opens the browser.

    pythonw installer\\launcher.py [--background]

``--background`` skips the browser (the sign-in shortcut uses it).

METASCAN_PORT picks the port (default 8700), and METASCAN_HOST the bind address.
The default is this PC only, unlike run.bat, so no firewall prompt appears; set
it to 0.0.0.0 to reach Metascan from other devices.
"""

import argparse
import ctypes
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Iterable, List, Optional

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from metascan.utils.app_paths import get_data_dir  # noqa: E402
from metascan.utils.log_files import rotating_file_handler  # noqa: E402
from installer.serve_protocol import LAUNCHER_PID_ENV, STOP_EVENT_ENV  # noqa: E402

# metascan.iss names the same mutex (AppMutex), so Setup and Uninstall wait for
# Metascan to be stopped.
MUTEX_NAME = "MetascanLauncher"
STATE_FILE = "launcher.json"
LOG_FILE = "launcher.log"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8700
# A blocked port is usually a WSL leftover (docs/first_time_setup.md), so the
# next free one is better than an error the owner cannot act on.
PORT_TRIES = 20
CONNECT_PROBE_S = 0.5

POLL_S = 0.5
# A first start can run a database migration, so there is no cap on starting:
# after this long the tray only says it is taking a while.
SLOW_START_S = 60
STOP_WAIT_S = 30
TAIL_LINES = 15

CREATE_NO_WINDOW = 0x08000000
MB_OK = 0x0
MB_ICONERROR = 0x10
MB_ICONWARNING = 0x30
ERROR_ALREADY_EXISTS = 183

logger = logging.getLogger("metascan.launcher")


def fresh_path(current: str, machine: str, user: str) -> str:
    """PATH as a new sign-in would have it, then anything else ``current`` has.

    Explorer can hold a PATH from before the installer added FFmpeg or
    ExifTool, and a server without them logs an error for every video.
    """
    seen = set()
    entries: List[str] = []
    for entry in f"{machine};{user};{current}".split(";"):
        entry = entry.strip()
        key = entry.rstrip("\\/").casefold()
        if entry and key not in seen:
            seen.add(key)
            entries.append(entry)
    return ";".join(entries)


def _registry_path(root: int, key: str) -> str:
    import winreg

    try:
        with winreg.OpenKey(root, key) as handle:
            value, _ = winreg.QueryValueEx(handle, "Path")
    except OSError:
        return ""
    return os.path.expandvars(str(value))


def _server_path() -> str:
    import winreg

    return fresh_path(
        os.environ.get("PATH", ""),
        _registry_path(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
        _registry_path(winreg.HKEY_CURRENT_USER, "Environment"),
    )


def port_is_free(host: str, port: int) -> bool:
    """Nothing answers on the port, and this process may bind it.

    Binding alone is not enough. Windows lets 127.0.0.1 bind beside a server on
    0.0.0.0, and then takes that server's localhost traffic.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(CONNECT_PROBE_S)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return False
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def pick_port(
    preferred: int,
    is_free: Callable[[int], bool],
    tries: int = PORT_TRIES,
) -> Optional[int]:
    """The first free port from ``preferred`` up, or None."""
    for port in range(preferred, preferred + tries):
        if is_free(port):
            return port
    return None


def browser_url(host: str, port: int) -> str:
    """Where to open: a wildcard bind is reached through localhost."""
    if host in ("0.0.0.0", "::", ""):
        host = "localhost"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}"


def last_lines(path: Path, count: int = TAIL_LINES) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-count:])


def _message(text: str, flags: int = MB_OK | MB_ICONERROR) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, "Metascan", flags)


def _hold_mutex() -> bool:
    """True when this is the only launcher. The handle lives as long as the
    process, and Windows releases it however the process ends."""
    kernel32 = ctypes.windll.kernel32
    kernel32.SetLastError(0)
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return bool(handle) and kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def _make_stop_event(name: str) -> Optional[int]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    return kernel32.CreateEventW(None, True, False, name)


def _set_event(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.SetEvent(handle)


def _open_running_instance(state_path: Path) -> None:
    """Another launcher owns the server: send the owner to it."""
    try:
        url = json.loads(state_path.read_text(encoding="utf-8"))["url"]
    except (OSError, ValueError, KeyError):
        return  # it is still starting, and opens the browser itself
    webbrowser.open(url)


class Server:
    """The hidden server process and its output."""

    def __init__(self, host: str, port: int, log_path: Path) -> None:
        self.host = host
        self.port = port
        self.url = browser_url(host, port)
        self.log_path = log_path
        self.process: Optional["subprocess.Popen[bytes]"] = None
        self.stopping = False
        self.stop_event_name = f"MetascanStop-{os.getpid()}"
        self.stop_event = _make_stop_event(self.stop_event_name)

    def start(self) -> None:
        env = dict(os.environ)
        env["METASCAN_HOST"] = self.host
        env["METASCAN_PORT"] = str(self.port)
        env["PATH"] = _server_path()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONUTF8"] = "1"
        env[STOP_EVENT_ENV] = self.stop_event_name
        env[LAUNCHER_PID_ENV] = str(os.getpid())
        # python.exe beside this pythonw: with CREATE_NO_WINDOW it has a hidden
        # console, which its workers inherit, so none of them flashes a window.
        python = Path(sys.executable).with_name("python.exe")
        self.process = subprocess.Popen(
            [str(python), "-m", "installer.serve"],
            cwd=str(APP_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for raw in self.process.stdout:
            logger.info("%s", raw.decode("utf-8", "replace").rstrip())

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def answers(self) -> bool:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/api/background", timeout=2
            ):
                return True
        except urllib.error.HTTPError:
            return True  # an API key answers 401, and it is up
        except OSError:
            return False

    def stop(self) -> None:
        """Ask the server to stop, then make sure it and its workers did."""
        self.stopping = True
        process = self.process
        if process is None or process.poll() is not None:
            return
        if self.stop_event:
            _set_event(self.stop_event)
        try:
            process.wait(STOP_WAIT_S)
        except subprocess.TimeoutExpired:
            logger.warning("The server did not stop in time: ending it")
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                creationflags=CREATE_NO_WINDOW,
                capture_output=True,
            )


def _tray_image() -> object:
    from PIL import Image

    return Image.open(APP_ROOT / "icon.png")


def run_tray(server: Server, open_browser: bool, state_path: Path) -> None:
    import pystray

    def open_ui(icon: object = None, item: object = None) -> None:
        webbrowser.open(server.url)

    def open_logs(icon: object = None, item: object = None) -> None:
        os.startfile(str(server.log_path.parent))  # type: ignore[attr-defined]

    def quit_app(icon: "pystray.Icon", item: object = None) -> None:
        icon.title = "Metascan: stopping..."
        server.stop()
        icon.stop()

    def supervise(icon: "pystray.Icon") -> None:
        icon.visible = True
        started = time.monotonic()
        slow_said = False
        try:
            server.start()
        except OSError as exc:
            _message(f"Metascan could not start.\n\n{exc}")
            icon.stop()
            return
        while server.running() and not server.answers():
            if not slow_said and time.monotonic() - started > SLOW_START_S:
                slow_said = True
                icon.notify("Still starting. The first start can take a while.")
            time.sleep(POLL_S)
        if server.running():
            icon.title = f"Metascan: {server.url}"
            state_path.write_text(json.dumps({"url": server.url}), encoding="utf-8")
            if open_browser:
                open_ui()
            while server.running():
                time.sleep(POLL_S)
        if not server.stopping:
            detail = last_lines(server.log_path)
            _message(
                "Metascan stopped unexpectedly.\n\n"
                f"{detail}\n\nThe full log is in {server.log_path.parent}."
            )
            icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open Metascan", open_ui, default=True),
        pystray.MenuItem("Open log folder", open_logs),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Stop Metascan", quit_app),
    )
    pystray.Icon("Metascan", _tray_image(), "Metascan: starting...", menu=menu).run(
        setup=supervise
    )


def _configure_logging(log_path: Path) -> None:
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = rotating_file_handler(log_path)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Start Metascan in the tray")
    parser.add_argument("--background", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    state_path = get_data_dir() / STATE_FILE
    if not _hold_mutex():
        if not args.background:
            _open_running_instance(state_path)
        return 0

    state_path.unlink(missing_ok=True)
    log_path = APP_ROOT / "logs" / LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _configure_logging(log_path)

    host = os.environ.get("METASCAN_HOST", DEFAULT_HOST)
    preferred = int(os.environ.get("METASCAN_PORT", DEFAULT_PORT))
    port = pick_port(preferred, lambda p: port_is_free(host, p))
    if port is None:
        _message(
            f"Metascan could not start: ports {preferred} to "
            f"{preferred + PORT_TRIES - 1} are all in use.\n\n"
            "If Metascan also runs in WSL, stop it there and try again."
        )
        return 1

    server = Server(host, port, log_path)
    try:
        run_tray(server, not args.background, state_path)
    finally:
        server.stop()
        state_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
