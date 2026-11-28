"""A run's terminal UI, kept in a hidden console that windows on this machine show and leave.

The daemon starts a run that shows the terminal UI under a terminal host: a small process of its own (so it outlives
a daemon restart) that runs ``clabe run`` in a pseudo console and serves it on a localhost port. A window
(`clabe daemon view`) connects to it, shows what the run draws and sends it the keys pressed; closing the window
leaves the run where it is. The host ends with the run.

The protocol, over TCP: the window sends the host's token on a line, then JSON lines (``{"input": text}`` or
``{"size": [columns, rows]}``); the host sends what the run draws, as UTF-8.
"""

import json
import logging
import os
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

#: The size of the console before a window gives its own.
DEFAULT_SIZE = (120, 30)

#: Seconds between the two size changes that make a terminal UI redraw for a window that just connected.
REDRAW_PAUSE = 0.3


def available() -> bool:
    """Whether this machine can host a terminal UI (Windows, with pywinpty)."""
    if sys.platform != "win32":
        return False
    try:
        import winpty  # noqa: F401
    except ImportError:
        return False
    return True


class _Host:
    """Runs ``argv`` in a pseudo console and serves it to the windows that connect."""

    def __init__(self, argv: list[str], *, cwd: str, token: str, size: tuple[int, int] = DEFAULT_SIZE) -> None:
        from winpty import PtyProcess

        self.token = token
        self.size = size
        self.pty = PtyProcess.spawn(argv, cwd=cwd, env=dict(os.environ), dimensions=(size[1], size[0]))
        self._lock = threading.Lock()
        self._windows: list[socket.socket] = []
        self.listener = socket.create_server(("127.0.0.1", 0))

    def serve(self, address_file: Path) -> int:
        """Serves until the run ends; returns its exit code."""
        port = self.listener.getsockname()[1]
        address = {"port": port, "pid": os.getpid(), "run_pid": self.pty.pid}
        address_file.write_text(json.dumps(address), encoding="utf-8")
        threading.Thread(target=self._accept, name="clabe-terminal-accept", daemon=True).start()
        self._relay()
        self.listener.close()
        with self._lock:
            for window in self._windows:
                _close(window)
        return self.pty.exitstatus if self.pty.exitstatus is not None else 1

    def _relay(self) -> None:
        """Sends what the run draws to every window, until the run ends (a window that fails is dropped)."""
        while True:
            try:
                text = self.pty.read(65536)
            except EOFError:
                return
            data = text.encode("utf-8", errors="replace")
            with self._lock:
                for window in list(self._windows):
                    try:
                        window.sendall(data)
                    except OSError:
                        self._windows.remove(window)
                        _close(window)

    def _accept(self) -> None:
        """Takes the windows that connect."""
        while True:
            try:
                window, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve_window, args=(window,), daemon=True).start()

    def _serve_window(self, window: socket.socket) -> None:
        """Checks a window's token, then applies what it sends."""
        lines = _lines(window)
        if next(lines, None) != self.token:
            _close(window)
            return
        with self._lock:
            self._windows.append(window)
        for line in lines:
            try:
                message = json.loads(line)
                if "input" in message:
                    self.pty.write(str(message["input"]))
                elif "size" in message:
                    self.resize(int(message["size"][0]), int(message["size"][1]))
            except (ValueError, TypeError, KeyError, IndexError, OSError) as exc:
                logger.debug("Ignoring %r from a window: %s", line, exc)
        with self._lock:
            if window in self._windows:
                self._windows.remove(window)
        _close(window)

    def resize(self, columns: int, rows: int) -> None:
        """Gives the console a window's size; the same size redraws it (a window that just connected sees it)."""
        columns, rows = max(columns, 20), max(rows, 5)
        if (columns, rows) == self.size:  # a terminal UI redraws when its size changes: change it, and back
            self.pty.setwinsize(rows, columns - 1)
            time.sleep(REDRAW_PAUSE)  # sooner, it would see the same size as before, and not redraw
        self.pty.setwinsize(rows, columns)
        self.size = (columns, rows)


def _lines(connection: socket.socket) -> Iterator[str]:
    """The lines ``connection`` sends, until it closes."""
    buffer = b""
    while True:
        try:
            chunk = connection.recv(65536)
        except OSError:
            return
        if not chunk:
            return
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            yield line.decode("utf-8", errors="replace").rstrip("\r")


def _close(connection: socket.socket) -> None:
    """Closes ``connection``, ignoring that it already is."""
    try:
        connection.close()
    except OSError:
        pass


class TerminalConnection:
    """A window's connection to a run's terminal host.

    Args:
        port: The host's port on this machine.
        token: The host's token.
    """

    def __init__(self, port: int, token: str, *, timeout: float = 5.0) -> None:
        self._socket = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self._socket.settimeout(None)
        self._send_lock = threading.Lock()
        self._send(token)

    def _send(self, line: str) -> None:
        """Sends one line."""
        with self._send_lock:
            self._socket.sendall(line.encode("utf-8") + b"\n")

    def write(self, text: str) -> None:
        """Types ``text`` into the run."""
        self._send(json.dumps({"input": text}))

    def resize(self, columns: int, rows: int) -> None:
        """Gives the run this window's size (the same size redraws it)."""
        self._send(json.dumps({"size": [columns, rows]}))

    def output(self) -> Iterator[str]:
        """What the run draws, until it ends (or the host goes away)."""
        import codecs

        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            try:
                chunk = self._socket.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            if text := decoder.decode(chunk):
                yield text

    def close(self) -> None:
        """Leaves the run."""
        _close(self._socket)


def main(argv: list[str] | None = None) -> int:
    """``python -m clabe.daemon._terminal <spec.json>``: hosts the run the spec describes; exits with its code.

    The spec (JSON) holds ``argv``, ``cwd``, ``token`` and ``address_file`` (where the host writes its port).
    """
    spec = json.loads(Path((argv or sys.argv[1:])[0]).read_text(encoding="utf-8"))
    host = _Host(list(spec["argv"]), cwd=spec["cwd"], token=spec["token"])
    return host.serve(Path(spec["address_file"]))


if __name__ == "__main__":
    raise SystemExit(main())
