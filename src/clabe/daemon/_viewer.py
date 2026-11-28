"""A window on the rig showing the terminal UI of the run the daemon hosts (`clabe daemon view`).

The window only shows the run: it sends the run the keys pressed and its size, and draws what the run draws.
Closing it stops the run (the daemon asks it to abort, then ends it); the run going on without a window happens
only when a window dies without closing (killed), and opening a window again (the desktop icon) shows it again.
"""

import logging
import shutil
import sys
import threading
import time
from collections.abc import Callable
from typing import Self

from ._local import DaemonRefused, LocalDaemon
from ._terminal import TerminalConnection

logger = logging.getLogger(__name__)

#: Seconds to wait for a starting run's terminal UI.
START_TIMEOUT = 60.0

_CTRL_CLOSE, _CTRL_LOGOFF, _CTRL_SHUTDOWN = 2, 5, 6


def connect(daemon: LocalDaemon, timeout: float = START_TIMEOUT) -> TerminalConnection:
    """Connects to the current run's terminal UI, waiting for a starting one."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            port, token = daemon.terminal()
            return TerminalConnection(port, token)
        except DaemonRefused as exc:
            if exc.status != 409 or time.monotonic() > deadline:  # 409: its host is starting
                raise
        except OSError:
            if time.monotonic() > deadline:
                raise
        time.sleep(0.25)


def relay(
    connection: TerminalConnection,
    write: Callable[[str], None],
    size: Callable[[], tuple[int, int]],
    *,
    poll: float = 0.25,
) -> None:
    """Draws what the run draws with ``write`` and keeps the run at this window's ``size``, until the run ends."""
    current = size()
    connection.resize(*current)  # also redraws the run's screen, for a window that just opened
    ended = threading.Event()

    def follow_size() -> None:
        nonlocal current
        while not ended.wait(poll):
            if (new := size()) != current:
                current = new
                try:
                    connection.resize(*new)
                except OSError:
                    return

    threading.Thread(target=follow_size, name="clabe-view-size", daemon=True).start()
    try:
        for text in connection.output():
            write(text)
    finally:
        ended.set()


class _Console:
    """This process's console, switched to pass keys and escape sequences through untouched."""

    def __init__(self) -> None:
        import ctypes

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._input = self._kernel32.GetStdHandle(-10)
        self._output = self._kernel32.GetStdHandle(-11)
        self._saved: list[tuple[int, int]] = []
        self._handler = None

    def _set(self, handle: int, mode: int | Callable[[int], int]) -> None:
        """Sets a console handle's mode (``mode`` may compute it from the current one), remembering the old one."""
        import ctypes
        from ctypes import wintypes

        old = wintypes.DWORD()
        if self._kernel32.GetConsoleMode(handle, ctypes.byref(old)):
            self._saved.append((handle, old.value))
            self._kernel32.SetConsoleMode(handle, mode(old.value) if callable(mode) else mode)

    def __enter__(self) -> Self:
        self._kernel32.SetConsoleOutputCP(65001)
        self._kernel32.SetConsoleCP(65001)
        self._set(self._output, lambda old: old | 0x4 | 0x8)  # escape sequences; no automatic return after a newline
        self._set(self._input, 0x200 | 0x80)  # keys as escape sequences, nothing processed, no Quick Edit selection
        sys.stdout.write("\x1b[?1049h")  # the alternate screen, as the run itself uses
        sys.stdout.flush()
        return self

    def __exit__(self, *_: object) -> None:
        sys.stdout.write("\x1b[?1049l\x1b[0m")
        sys.stdout.flush()
        for handle, mode in reversed(self._saved):
            self._kernel32.SetConsoleMode(handle, mode)

    def read(self) -> str:
        """The next keys pressed (blocks)."""
        import ctypes
        from ctypes import wintypes

        buffer = ctypes.create_unicode_buffer(512)
        count = wintypes.DWORD()
        if not self._kernel32.ReadConsoleW(self._input, buffer, 512, ctypes.byref(count), None):
            raise OSError(ctypes.get_last_error(), "ReadConsoleW failed")
        return buffer.value[: count.value]

    def on_close(self, close: Callable[[], None]) -> None:
        """Calls ``close`` when the window is closed (or the user logs off): Windows ends the process right after."""
        import ctypes
        from ctypes import wintypes

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        def handler(event: int) -> bool:
            if event in (_CTRL_CLOSE, _CTRL_LOGOFF, _CTRL_SHUTDOWN):
                close()
            return True  # Ctrl+Break and the like do nothing: keys go to the run

        self._handler = handler  # kept: Windows calls it later
        self._kernel32.SetConsoleCtrlHandler(handler, True)


def view(daemon: LocalDaemon) -> int:
    """Shows the current run's terminal UI in this console until the run ends; returns its exit code.

    Closing this window stops the run.
    """
    if sys.platform != "win32":
        raise RuntimeError("The run's terminal UI is shown on Windows rigs.")
    connection = connect(daemon)
    with _Console() as console:

        def stop() -> None:
            try:
                daemon.stop(wait=False)
            except Exception as exc:  # noqa: BLE001 -- the window is closing either way
                logger.debug("Could not stop the run: %s", exc)

        console.on_close(stop)

        def type_keys() -> None:
            while True:
                try:
                    keys = console.read()
                    if keys:
                        connection.write(keys)
                except OSError:
                    return

        threading.Thread(target=type_keys, name="clabe-view-keys", daemon=True).start()

        def write(text: str) -> None:
            sys.stdout.write(text)
            sys.stdout.flush()

        def size() -> tuple[int, int]:
            columns, rows = shutil.get_terminal_size()
            return columns, rows

        relay(connection, write, size)
    connection.close()
    return _exit_code(daemon)


def _exit_code(daemon: LocalDaemon, timeout: float = 3.0) -> int:
    """The ended run's exit code (0 when it cannot be told); its host may take a moment to end after it."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            run = daemon.current()
        except Exception:  # noqa: BLE001 -- the run has ended; its code is a nicety
            return 0
        if run is not None and run.status == "exited" and run.exit_code is not None:
            return run.exit_code
        if time.monotonic() > deadline:
            return 0
        time.sleep(0.1)
