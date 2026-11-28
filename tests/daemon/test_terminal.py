"""A run's terminal UI in a hidden console: windows connect, type, leave and come back; it ends with the run."""

import json
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from clabe.daemon import _terminal
from clabe.daemon._terminal import TerminalConnection

pytestmark = pytest.mark.skipif(not _terminal.available(), reason="needs Windows and pywinpty")

_PROGRAM = textwrap.dedent(
    """
    import sys
    from textual.app import App
    from textual.widgets import Input, Label

    class Program(App):
        def compose(self):
            yield Label("ready", id="said")
            yield Input()

        def on_input_submitted(self, event):
            if event.value == "quit":
                self.exit(return_code=3)
            self.query_one("#said", Label).update("got:" + event.value)
            event.input.value = ""

    app = Program()
    app.run()
    sys.exit(app.return_code or 0)
    """
)
"""A terminal UI (it draws itself again when its console is resized, as the run's does)."""


class _Screen:
    """What a connection receives, collected on a thread."""

    def __init__(self, connection: TerminalConnection) -> None:
        self.text = ""
        self._thread = threading.Thread(target=self._follow, args=(connection,), daemon=True)
        self._thread.start()

    def _follow(self, connection: TerminalConnection) -> None:
        for chunk in connection.output():
            self.text += chunk

    def wait_for(self, needle: str, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while needle not in self.text:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.05)
        return True


@pytest.fixture
def hosted(tmp_path):
    """A program under a terminal host, as the daemon starts a run; yields the host process and the address."""
    program = tmp_path / "program.py"
    program.write_text(_PROGRAM, encoding="utf-8")
    address = tmp_path / "terminal.json"
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {"argv": [sys.executable, str(program)], "cwd": str(tmp_path), "token": "t0k", "address_file": str(address)}
        ),
        encoding="utf-8",
    )
    host = subprocess.Popen([sys.executable, "-m", "clabe.daemon._terminal", str(spec)])
    deadline = time.monotonic() + 30
    while not address.is_file() and time.monotonic() < deadline:
        time.sleep(0.1)
    yield host, json.loads(address.read_text(encoding="utf-8"))["port"]
    host.kill()


def test_a_window_types_into_the_run_leaves_and_comes_back(hosted):
    host, port = hosted
    first = TerminalConnection(port, "t0k")
    first.resize(100, 30)
    screen = _Screen(first)
    assert screen.wait_for("ready")
    first.write("hello\r")
    assert screen.wait_for("got:hello")
    first.close()  # the window closes: the run goes on

    time.sleep(0.5)
    assert host.poll() is None
    again = TerminalConnection(port, "t0k")
    again.resize(100, 30)  # the same size: the console is drawn again for the new window
    screen = _Screen(again)
    assert screen.wait_for("got:hello")
    again.write("quit\r")
    assert host.wait(20) == 3  # the host ends with the run, with its exit code


def test_a_wrong_token_sees_nothing(hosted):
    _, port = hosted
    intruder = TerminalConnection(port, "wrong")
    assert list(intruder.output()) == []
