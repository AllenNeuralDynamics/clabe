"""Report an issue from the terminal UI (F3)."""

import asyncio
import queue

import pytest
from textual.widgets import RichLog

from clabe.ui._requests import ConfirmRequest, TextRequest
from clabe.ui._textual import TextualClient, _LauncherApp
from clabe.ui._textual_form import _ReportScreen


def _session_text(app: _LauncherApp) -> str:
    return "\n".join(str(line.text) for line in app.query_one("#clabe-user-log", RichLog).lines)


async def _settle(pilot, condition, timeout: float = 5.0) -> None:
    """Waits (pausing the app) until ``condition()`` holds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        assert loop.time() < deadline, "timed out"
        await pilot.pause(0.02)


def test_f3_reports_an_issue():
    actions = {binding.key: binding.action for binding in _LauncherApp.BINDINGS}
    assert actions["f3"] == "report"


@pytest.mark.asyncio
async def test_f3_files_the_comment_and_says_it_was_reported():
    filed = []
    app = _LauncherApp()
    app.reporter = lambda comment: filed.append(comment) or "abc123"
    async with app.run_test() as pilot:
        await pilot.press("f3")
        await pilot.pause()
        assert isinstance(app.screen, _ReportScreen)
        await pilot.press(*"camera froze")
        await pilot.press("enter")
        await _settle(pilot, lambda: "abc123" in _session_text(app))
        assert filed == ["camera froze"]
        assert not isinstance(app.screen, _ReportScreen)


@pytest.mark.asyncio
async def test_escape_closes_the_dialog_without_reporting():
    filed = []
    app = _LauncherApp()
    app.reporter = filed.append
    async with app.run_test() as pilot:
        await pilot.press("f3")
        await pilot.pause()
        await pilot.press("x", "escape")
        await pilot.pause()
        assert not isinstance(app.screen, _ReportScreen)
        assert filed == []


@pytest.mark.asyncio
async def test_the_comment_never_answers_the_prompt_underneath():
    app = _LauncherApp()
    app.reporter = lambda comment: "id1"
    async with app.run_test() as pilot:
        text_reply: queue.Queue = queue.Queue()
        await app.ask_text(TextRequest(label="Name?"), text_reply)
        await pilot.pause()
        await pilot.press("f3")
        await pilot.pause()
        await pilot.press(*"oops", "enter")
        await _settle(pilot, lambda: "id1" in _session_text(app))
        assert text_reply.empty()  # the text prompt still waits for its own answer
        assert app._kind == "text"

        await app.cancel_pending()
        confirm_reply: queue.Queue = queue.Queue()
        await app.ask_confirm(ConfirmRequest(label="Go?"), confirm_reply)
        await pilot.pause()
        await pilot.press("f3")
        await pilot.pause()
        await pilot.press("y", "n", "1")  # typed into the comment, not the Yes/No question
        assert confirm_reply.empty()


@pytest.mark.asyncio
async def test_a_failing_report_is_shown():
    def fail(comment: str) -> str:
        raise OSError("disk full")

    app = _LauncherApp()
    app.reporter = fail
    async with app.run_test() as pilot:
        await pilot.press("f3")
        await pilot.pause()
        await pilot.press("enter")
        await _settle(pilot, lambda: "disk full" in _session_text(app))
        assert "Could not save the issue report" in _session_text(app)


@pytest.mark.asyncio
async def test_without_a_run_there_is_nothing_to_report():
    app = _LauncherApp()
    async with app.run_test() as pilot:
        await pilot.press("f3")
        await pilot.pause()
        assert not isinstance(app.screen, _ReportScreen)
        assert "Nothing to report on yet" in _session_text(app)


def test_the_client_hands_its_reporter_to_the_app():
    client = TextualClient()

    def reporter(comment: str) -> str:
        return "id"

    client.set_reporter(reporter)
    assert client._reporter is reporter


@pytest.mark.asyncio
async def test_the_context_bar_shows_what_the_run_is_about():
    from textual.widgets import Static

    from clabe.workflow import RunContext

    app = _LauncherApp()
    async with app.run_test() as pilot:
        bar = app.query_one("#clabe-context", Static)
        assert not bar.display  # nothing to show before a run
        app.set_context(RunContext(workflow="vr-foraging", hostname="PC-1", subject="123456", experimenter=["Ana"]))
        await pilot.pause()
        text = str(bar.render())
        assert bar.display
        for part in ("vr-foraging", "Subject 123456", "Experimenters Ana", "Rig —", "Host PC-1"):
            assert part in text
