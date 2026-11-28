import os
import socket
import threading
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from aind_behavior_services import Rig, Session, Task

from clabe import ui
from clabe.launcher import Launcher
from clabe.launcher._cli import LauncherCliArgs


class MockFrontend:
    """Non-interactive client for tests; each prompt kind is answered by a mockable ``_ask_*_mock``.

    Raises after many consecutive asks of the same request, so a test whose scripted answer keeps being rejected
    fails instead of hanging.
    """

    _MAX_REPEATS = 50

    def __init__(self):
        self._render_mock = Mock()
        self._ask_text_mock = Mock(return_value="")
        self._ask_pick_mock = Mock(return_value="")
        self._ask_confirm_mock = Mock(return_value=True)
        self._ask_autocomplete_mock = Mock(return_value="")
        self._ask_path_mock = Mock(return_value=None)
        self._ask_form_mock = Mock(return_value=None)
        self._last_request = None
        self._repeats = 0

    def ask(self, request):
        if request is self._last_request:
            self._repeats += 1
            if self._repeats > self._MAX_REPEATS:
                raise AssertionError(f"{request!r} was asked {self._repeats} times: its scripted answer is rejected")
        else:
            self._last_request, self._repeats = request, 0
        kind = request.prompt_kind
        if kind in ("text", "number"):
            return self._ask_text_mock(request)
        if kind in ("confirm", "table"):
            return self._ask_confirm_mock(request)
        if kind in ("form", "group"):
            return self._ask_form_mock(request)
        if kind in ("acknowledge", "markdown", "media"):
            return None
        return getattr(self, f"_ask_{kind}_mock")(request)

    def cancel(self):
        self.cancel_prompt()

    def cancel_prompt(self):
        pass

    def notify(self, message, level=ui.MessageLevel.INFO):
        self._render_mock(message, level)

    def header(self, text):
        self._render_mock(text, ui.MessageLevel.INFO)

    def close(self):
        pass


#: Bindings made by :func:`use_client`, kept alive until the end of the test (a dropped one would unbind itself).
_held_bindings: list = []


def use_client(client):
    """Binds a session over ``client`` for the rest of the test (the autouse fixture clears it)."""
    binding = ui.bind(ui.ClientSession(client))
    binding.__enter__()
    _held_bindings.append(binding)
    return client


@pytest.fixture
def mock_frontend():
    return MockFrontend()


#: What :class:`MockClient` answers a prompt kind with when nothing is scripted for it.
_MOCK_CLIENT_DEFAULTS = {
    "text": "",
    "autocomplete": "",
    "number": 0.0,
    "confirm": True,
    "pick": None,
    "path": None,
    "form": None,
    "table": True,
    "acknowledge": None,
}


class MockClient:
    """Non-interactive :class:`~clabe.ui.Client` for tests.

    ``answers`` maps a prompt kind to its script: a list (one entry per ask, in order; running out fails the test),
    a callable ``(request) -> answer``, or a single value answered every time. An entry that is an exception (class or
    instance) is raised instead. Unscripted kinds get a harmless default.

    Records every request asked, notification and header; ``cancelled`` is set (and ``cancels`` counted) by
    :meth:`cancel`; ``on_notify``, when set, is called with each notification after it is recorded.
    """

    def __init__(self, answers=None):
        self.answers = dict(answers or {})
        self.asked: list = []
        self.notifications: list = []
        self.headers: list = []
        self.cancelled = threading.Event()
        self.cancels = 0
        self.closed = False
        self.on_notify = None

    def ask(self, request):
        self.asked.append(request)
        kind = request.prompt_kind
        script = self.answers.get(kind, _MOCK_CLIENT_DEFAULTS.get(kind))
        if isinstance(script, list):
            assert script, f"MockClient: no scripted {kind!r} answer left"
            answer = script.pop(0)
        elif callable(script) and not (isinstance(script, type) and issubclass(script, BaseException)):
            answer = script(request)
        else:
            answer = script
        if isinstance(answer, BaseException) or (isinstance(answer, type) and issubclass(answer, BaseException)):
            raise answer
        return answer

    def cancel(self):
        self.cancels += 1
        self.cancelled.set()

    def notify(self, message, level=ui.MessageLevel.INFO):
        self.notifications.append((message, level))
        if self.on_notify is not None:
            self.on_notify(message, level)

    def header(self, text):
        self.headers.append(text)

    def close(self):
        self.closed = True

    @property
    def messages(self) -> list:
        """The text of every notification, in order."""
        return [message for message, _ in self.notifications]


@pytest.fixture
def mock_client():
    return MockClient()


@pytest.fixture(autouse=True)
def no_runnable_timing():
    """A debug-mode launcher turns on timed notifications for the process; tests start without them."""
    from clabe.runnable import set_include_timing

    set_include_timing(False)
    yield
    set_include_timing(False)


@pytest.fixture(autouse=True)
def isolated_reports(tmp_path, monkeypatch):
    """Issue reports go to a temporary folder, never this machine's real one (a running hub would collect them)."""
    monkeypatch.setattr("clabe.reports.reports_dir", lambda: tmp_path / "reports")


@pytest.fixture(autouse=True)
def isolated_cache_manager(tmp_path, monkeypatch):
    """Isolate the CacheManager singleton from the real on-disk cache.

    The cache manager defaults to a shared ``.cache_manager.json`` under
    ``TMP_DIR``. Without isolation, anything that writes it (auto-sync usage, or
    running the example) leaks into later tests and makes assertions about cache
    contents flaky. This points the default at a per-test temp dir and resets
    the singleton around each test so the on-disk cache never matters.
    """
    from clabe import cache as cache_manager

    monkeypatch.setattr(cache_manager, "TMP_DIR", str(tmp_path))
    cache_manager.CacheManager._instance = None
    yield
    cache_manager.CacheManager._instance = None


@pytest.fixture(autouse=True)
def reset_global_frontend():
    """Clear the process-wide frontend registration and session binding after each test."""
    yield
    from clabe.ui import _session

    with _session._bindings_lock:  # a run left unfinished by a failed test must not stay bound
        _session._bindings.clear()
    _held_bindings.clear()


@pytest.fixture
def mock_session():
    return Session(
        experiment="mock",
        subject="mock_subject",
        session_name="mock_session",
    )


@pytest.fixture
def mock_rig():
    return Rig(rig_name="mock_rig", version="0.0.0", data_directory="mock_data_dir", computer_name="mock_computer")


@pytest.fixture
def mock_task():
    return Task(version="0.0.0", task_parameters={}, name="mock_task")


@pytest.fixture
def mock_base_launcher(mock_rig, mock_session, mock_task, mock_frontend, tmp_path: Path):
    os.environ["COMPUTERNAME"] = "TEST_COMPUTER"
    launcher_args = LauncherCliArgs()
    # Ensure directories exist for os.chdir
    with (
        patch("clabe.launcher._base.GitRepository") as mock_git,
        patch("os.chdir"),
        patch("pathlib.Path.mkdir"),
        patch("clabe.logging.add_file_handler"),
        patch("clabe.launcher.Launcher._ensure_directory_structure"),
        patch("clabe.launcher.Launcher.validate", return_value=True),
        patch("os.environ", {"COMPUTERNAME": "TEST_COMPUTER"}),
    ):
        mock_git.return_value.working_dir = tmp_path / "repo"
        launcher = Launcher(
            client=mock_frontend,
            settings=launcher_args,
        )
        launcher.temp_dir.mkdir(parents=True, exist_ok=True)

        return launcher


_FAKE_OPEN_EPHYS_GUI = """
import json, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer

time.sleep(DELAY)
if EXIT_CODE is not None:
    sys.exit(EXIT_CODE)


class Handler(BaseHTTPRequestHandler):
    def _reply(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._reply({"mode": "IDLE"})

    def do_PUT(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        self._reply({})
        if self.path == "/api/quit":
            threading.Thread(target=server.shutdown).start()

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", PORT), Handler)
server.serve_forever()
print("quit")
"""


@pytest.fixture
def fake_open_ephys_gui(tmp_path: Path):
    """Factory for a script standing in for the Open Ephys GUI: serves /api/status and exits on PUT /api/quit.

    Returns the script path (used as the signal chain, run with the Python interpreter as executable) and its port.
    """

    def make(delay: float = 0.0, exit_code: int | None = None) -> tuple[Path, int]:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        script = tmp_path / f"fake_open_ephys_{port}.py"
        script.write_text(
            _FAKE_OPEN_EPHYS_GUI.replace("DELAY", repr(delay))
            .replace("EXIT_CODE", repr(exit_code))
            .replace("PORT", str(port))
        )
        return script, port

    return make


@pytest.fixture
def end_launched_run():
    """A function ending the run a test's daemon launched (give it the daemon's ``Host``); call it in teardown."""
    return _end_launched_run


def _end_launched_run(host) -> None:
    """Ends the run ``host`` launched, and waits until it lets go of clabe's single-session lock.

    Stopping a daemon leaves its run going (as on a rig), and the run holds the machine-wide lock: a test that left it
    running would make every later run on this machine refuse to start.
    """
    import time

    from clabe.launcher._session_lock import SessionAlreadyRunningError, single_session_lock

    current = host.current()
    process = getattr(current, "process", None)
    if process is not None and process.poll() is None:
        current.kill()  # the run and all it started
        process.wait(10)
    deadline = time.monotonic() + 15
    while True:
        try:
            with single_session_lock():
                return
        except SessionAlreadyRunningError:
            if time.monotonic() >= deadline:
                raise AssertionError("A clabe run still holds the session lock after its test ended") from None
            time.sleep(0.1)
