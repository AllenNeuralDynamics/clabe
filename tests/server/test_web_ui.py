"""Smoke test of the web UI: a real run, served with the built UI, driven through a real browser.

Skipped unless the UI is built (``python scripts/build_web.py``) and Playwright is installed with a browser
(``playwright install chromium``; set ``CLABE_TEST_BROWSER_CHANNEL=msedge`` to use an installed Edge instead).
"""

import enum
import os
import threading

import pytest
from pydantic import BaseModel, Field

from clabe import ui
from clabe.server import RunServer
from clabe.server._app import STATIC_DIR
from clabe.ui import NumberRequest, PathRequest, TextRequest
from clabe.workflow import GoBack, Run, RunStatus, Workflow
from clabe.workflow.events import PromptClosed

playwright_api = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(not (STATIC_DIR / "index.html").is_file(), reason="the web UI is not built")


class Arena(enum.Enum):
    """The arena of the demo session."""

    OPEN = "open"
    LINEAR = "linear"


class Settings(BaseModel):
    """A form with the common field types."""

    trials: int = Field(default=10, ge=1, le=500)
    arena: Arena = Arena.OPEN
    notes: str = ""


@pytest.fixture
def page():
    channel = os.environ.get("CLABE_TEST_BROWSER_CHANNEL") or None
    with playwright_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel=channel)
        except playwright_api.Error as exc:
            pytest.skip(f"no browser for Playwright: {exc}")
        page = browser.new_page()
        page.set_default_timeout(15_000)
        yield page
        browser.close()


def test_the_web_ui_drives_a_run(page, tmp_path):
    (tmp_path / "session_1").mkdir()
    went_back = threading.Event()
    wf = Workflow("web-demo")

    @wf.step
    def subject():
        return ui.text("Subject")

    @wf.step
    def calibration(subject):
        return ui.group(
            "Calibration",
            rig=TextRequest(label="Rig"),
            gain=NumberRequest(label="Gain", minimum=0, maximum=1, step=0.1, widget="slider"),
            data=PathRequest(label="Data directory", kind="dir", start=str(tmp_path)),
        )

    @wf.step
    def stage(subject):
        return ui.pick("Stage", ["stage_1", "stage_2"])

    @wf.step
    def settings(stage):
        return ui.form(Settings)

    @wf.step
    def confirm(settings):
        if not went_back.is_set():
            went_back.set()
            raise GoBack("stage")
        return ui.confirm("Start the session?")

    run = Run(wf)
    server = RunServer(run, port=18290, token="t0k")
    url = server.start()
    try:
        run.start()
        page.goto(url)
        page.get_by_label("Subject").fill("mouse-7")
        page.get_by_role("button", name="Submit").click()

        page.get_by_role("heading", name="Calibration").wait_for()
        page.get_by_label("Rig").fill("rig-3")
        page.get_by_label("Gain").fill("0.3")  # a range input
        page.get_by_role("button", name="Browse…").click()  # the remote file browser, starting at the item's start
        page.get_by_role("button", name="📁 session_1").click()
        page.get_by_role("button", name="Use this folder").click()
        page.get_by_role("button", name="Submit").click()

        page.get_by_role("button", name="stage_2").click()

        page.get_by_role("heading", name="Settings").wait_for()
        page.get_by_label("Trials").fill("900")  # out of bounds: rejected, the prompt stays open
        page.get_by_role("button", name="Submit").click()
        page.locator(".prompt .error").wait_for()
        page.get_by_label("Trials").fill("120")
        page.get_by_label("Arena").select_option(label="linear")
        page.get_by_role("button", name="Submit").click()

        # the confirm step went back to stage: answer it and the form again, then confirm
        page.get_by_role("button", name="stage_1").click()
        page.get_by_role("heading", name="Settings").wait_for()
        page.get_by_label("Trials").fill("120")
        page.get_by_label("Arena").select_option(label="linear")
        page.get_by_role("button", name="Submit").click()
        page.get_by_role("heading", name="Start the session?").wait_for()
        page.get_by_role("button", name="Yes").click()

        assert run.wait(15)
        page.get_by_role("heading", name="Run completed").wait_for()
    finally:
        if not run.wait(0):
            run.abort("test teardown")
        server.stop()

    assert run.status is RunStatus.COMPLETED
    assert run.output("subject") == "mouse-7"
    assert run.output("calibration") == {"rig": "rig-3", "gain": 0.3, "data": tmp_path / "session_1"}
    assert run.output("stage") == "stage_1"
    assert run.output("settings") == Settings(trials=120, arena=Arena.LINEAR)
    closed = [e for e in run.events.since(0) if isinstance(e, PromptClosed) and e.outcome == "answered"]
    assert all(e.answered_by.startswith("web-") for e in closed)
