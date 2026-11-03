"""The server the Windows launcher runs: ``python -m installer.serve``.

It is uvicorn without a console to press Ctrl+C in. The launcher names a Windows
event and its own process id in the environment; the server stops cleanly (the
database and the workers need that) when the event is set or the launcher is
gone. Stdin would be simpler, but a thread blocked reading it hangs every child
process that inherits it. Host and port come from METASCAN_HOST / METASCAN_PORT.
"""

import ctypes
import os
import sys
import threading
from ctypes import wintypes

import uvicorn

from backend.config import get_server_config
from installer.serve_protocol import LAUNCHER_PID_ENV, STOP_EVENT_ENV

# Open connections get this long once a stop is asked for.
GRACEFUL_SHUTDOWN_S = 5

SYNCHRONIZE = 0x00100000
INFINITE = 0xFFFFFFFF


def _wait_for_stop(server: uvicorn.Server, event_name: str, launcher_pid: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.WaitForMultipleObjects.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.BOOL,
        wintypes.DWORD,
    ]

    event = kernel32.OpenEventW(SYNCHRONIZE, False, event_name)
    launcher = kernel32.OpenProcess(SYNCHRONIZE, False, launcher_pid)
    handles = [handle for handle in (event, launcher) if handle]
    if not handles:
        # The launcher went before this got here.
        server.should_exit = True
        return
    array = (wintypes.HANDLE * len(handles))(*handles)
    kernel32.WaitForMultipleObjects(len(handles), array, False, INFINITE)
    server.should_exit = True


def main() -> None:
    settings = get_server_config()
    server = uvicorn.Server(
        uvicorn.Config(
            "backend.main:app",
            host=settings.host,
            port=settings.port,
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
        )
    )
    event_name = os.environ.get(STOP_EVENT_ENV)
    launcher_pid = os.environ.get(LAUNCHER_PID_ENV)
    if event_name and launcher_pid:
        threading.Thread(
            target=_wait_for_stop,
            args=(server, event_name, int(launcher_pid)),
            daemon=True,
        ).start()
    server.run()
    if server.started is False:
        sys.exit(1)  # the port was taken, or startup failed


if __name__ == "__main__":
    main()
