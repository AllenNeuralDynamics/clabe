"""End of run: the closing screen, where the local exit prompt and a remote Close race."""

import threading
import time
from unittest.mock import patch

import pytest
import requests

from clabe.launcher import Launcher
from clabe.server import free_port
from clabe.ui import HeadlessClient, PromptCancelledError
from clabe.workflow import Rejected, Run, Workflow
from clabe.workflow.events import RunClosed, RunClosing


class _BlockingFrontend:
    """A client whose prompts block until cancelled."""

    interactive = True

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self.labels: list[str] = []

    def ask(self, request):
        self.labels.append(request.label)
        if not self._cancelled.wait(10):
            raise AssertionError("prompt was never cancelled")
        self._cancelled.clear()
        raise PromptCancelledError()

    def cancel(self):
        self._cancelled.set()

    def notify(self, message, level=None):
        pass

    def header(self, text):
        pass

    def close(self):
        pass


def _finished_run() -> Run:
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    run = Run(wf)
    run.keep_open()
    run.start()
    assert run.wait(5)
    return run


def _close_when_shown(run: Run, client: str = "pc-2") -> threading.Thread:
    """Closes ``run``'s session as a remote client once its closing screen is shown."""

    def close() -> None:
        deadline = time.monotonic() + 5
        while run.state().closing is None and time.monotonic() < deadline:
            time.sleep(0.01)
        run.close(client=client)

    thread = threading.Thread(target=close, daemon=True)
    thread.start()
    return thread


def _closed_by(run: Run) -> str | None:
    closed = [event for event in run.events.since(0) if isinstance(event, RunClosed)]
    assert len(closed) == 1
    return closed[0].closed_by


def test_a_remote_close_ends_the_local_exit_prompt(mock_base_launcher):
    frontend = _BlockingFrontend()
    mock_base_launcher.client = frontend
    run = _finished_run()
    mock_base_launcher._served_run = run
    thread = _close_when_shown(run)
    started = time.monotonic()
    mock_base_launcher._exit(0, timeout=None)
    thread.join(5)
    assert time.monotonic() - started < 5
    assert frontend.labels == ["Press Enter to exit..."]
    assert _closed_by(run) == "pc-2"
    closing = [event for event in run.events.since(0) if isinstance(event, RunClosing)]
    assert closing[0].deadline is None


def test_the_local_exit_prompt_closes_first(mock_base_launcher, mock_frontend):
    run = _finished_run()
    mock_base_launcher._served_run = run
    mock_base_launcher._exit(0, timeout=None)
    mock_frontend._ask_text_mock.assert_called_once()
    assert _closed_by(run) == "local"
    assert isinstance(run.close(client="pc-2"), Rejected)  # the first close won


def test_a_zero_timeout_skips_the_prompt_only_after_a_success(mock_base_launcher, mock_frontend):
    with patch("clabe.launcher._base.ui.text") as exit_prompt:
        mock_base_launcher._exit(0, timeout=0)
        exit_prompt.assert_not_called()
        mock_base_launcher._exit(-1, timeout=0)  # a failure or abort: the user should see what went wrong
        exit_prompt.assert_called_once()
        assert exit_prompt.call_args.args[0] == "Press Enter to exit..."


def test_the_closing_screen_counts_down(mock_base_launcher):
    mock_base_launcher.client = _BlockingFrontend()
    run = _finished_run()
    mock_base_launcher._served_run = run
    mock_base_launcher._exit(0, timeout=0.2)
    closing = run.state().closing
    assert closing is not None and closing.deadline is not None and closing.closed_by == "timeout"
    assert closing.deadline > closing.since


def test_headless_waits_for_a_browser_up_to_the_timeout(mock_base_launcher):
    mock_base_launcher.client = HeadlessClient()
    run = _finished_run()
    mock_base_launcher._served_run = run
    thread = _close_when_shown(run)
    started = time.monotonic()
    mock_base_launcher._exit(0, timeout=30)
    thread.join(5)
    assert time.monotonic() - started < 5
    assert _closed_by(run) == "pc-2"


@pytest.mark.parametrize("timeout", [None, 0])
def test_headless_exits_at_once_without_a_timeout(mock_base_launcher, timeout):
    mock_base_launcher.client = HeadlessClient()
    run = _finished_run()
    mock_base_launcher._served_run = run
    started = time.monotonic()
    mock_base_launcher._exit(0, timeout=timeout)
    assert time.monotonic() - started < 1
    assert run.state().closing is None


def test_the_server_stays_up_until_a_browser_closes_the_session(mock_base_launcher):
    """End to end: the run server outlives the run, shows the closing screen and stops once it is closed."""
    port = free_port("127.0.0.1", 18290)
    mock_base_launcher._settings = mock_base_launcher.settings.model_copy(update={"serve": True, "port": port})
    mock_base_launcher.client = _BlockingFrontend()
    wf = Workflow("demo", exit_prompt_timeout=None)
    wf.step(lambda: 1, name="a")
    base = f"http://127.0.0.1:{port}/api"
    closed = []

    def close_from_a_browser() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                state = requests.get(f"{base}/state", timeout=1).json()
            except requests.ConnectionError:
                time.sleep(0.05)
                continue
            if state.get("closing"):
                assert requests.get(f"{base}/export", timeout=5).status_code == 200  # still served while closing
                closed.append(requests.post(f"{base}/close", json={"client": "browser"}, timeout=5).json())
                return
            time.sleep(0.05)

    browser = threading.Thread(target=close_from_a_browser, daemon=True)
    with (
        patch("clabe.daemon._announce.announce", return_value=None),
        patch.object(Launcher, "copy_logs", lambda self, *a, **k: None),
        patch.object(Launcher, "validate", return_value=None),
    ):
        browser.start()
        mock_base_launcher.run_experiment(wf)
    browser.join(5)
    assert closed == [{"ok": True, "reason": None}]
    with pytest.raises(requests.ConnectionError):
        requests.get(f"{base}/state", timeout=1)
