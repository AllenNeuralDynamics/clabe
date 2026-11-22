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
        page.get_by_role("button", name="session_1").click()
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


def test_back_prefills_and_forward_keeps_the_earlier_answer(page):
    wf = Workflow("forward-demo")

    @wf.step
    def subject():
        return ui.text("Subject")

    @wf.step
    def notes(subject):
        return ui.text("Notes")

    run = Run(wf)
    server = RunServer(run, port=18297)
    url = server.start()
    try:
        run.start()
        page.goto(url)
        page.get_by_label("Subject").fill("mouse-7")
        page.keyboard.press("Enter")  # the last (only) field submits

        page.get_by_label("Notes").wait_for()
        page.keyboard.press("Alt+ArrowLeft")  # Back: to the subject, prefilled with the earlier answer
        page.get_by_label("Subject").wait_for()
        assert page.get_by_label("Subject").input_value() == "mouse-7"
        page.get_by_role("button", name="Forward").click()  # keeps it

        page.get_by_label("Notes").fill("all good")
        page.get_by_role("button", name="Submit").click()
        assert run.wait(15)
    finally:
        if not run.wait(0):
            run.abort("test teardown")
        server.stop()
    assert run.status is RunStatus.COMPLETED
    assert run.output("subject") == "mouse-7" and run.output("notes") == "all good"


def test_the_closing_screen_closes_the_session(page):
    import datetime

    wf = Workflow("closing-demo")

    @wf.step
    def hello():
        ui.markdown("Hello", title="Hello")

    run = Run(wf)
    run.keep_open()  # as a launcher serving its run does: the session stays open after the run
    server = RunServer(run, port=18298)
    url = server.start()
    try:
        run.start()
        page.goto(url)
        page.get_by_role("heading", name="Hello").wait_for()
        page.keyboard.press("Enter")
        assert run.wait(15)
        run.begin_closing(datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=60))

        page.get_by_role("heading", name="Run completed").wait_for()
        # a second tab on the same run, closable by its script like a tab clabe opens (Playwright's own tabs aren't)
        with page.expect_popup() as popup:
            page.evaluate("url => window.open(url)", url)
        other = popup.value
        other.get_by_role("heading", name="Run completed").wait_for()
        page.get_by_text("Closing in").wait_for()
        page.get_by_role("button", name="Close session").click()
        assert run.wait_closed(10)
        page.get_by_text("this tab closes in").wait_for()
        page.get_by_role("button", name="Keep it open").click()
        page.get_by_text("Session closed — you can close this tab.").wait_for()
        # without "Keep it open", a tab closes itself
        other.get_by_text("this tab closes in").wait_for()
        other.wait_for_event("close", timeout=15_000)
    finally:
        if not run.wait(0):
            run.abort("test teardown")
        run.close(client="teardown")
        server.stop()
    closed = [e for e in run.events.since(0) if e.type == "run_closed"]
    assert len(closed) == 1 and closed[0].closed_by.startswith("web-")


_PNG = bytes.fromhex(  # 1x1 pixel
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f80f00000101000518d84e0000000049454e44ae426082"
)


def test_the_web_ui_shows_markdown_and_media(page, tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(_PNG)
    wf = Workflow("media-demo")

    @wf.step
    def notes():
        ui.markdown("# Checklist\n\n- **Water** is on\n\n<script>window.pwned = 1</script>", title="Before you start")

    @wf.step
    def snapshot(notes):
        ui.image(image, title="Camera 1", caption="Last frame")

    @wf.step
    def report(snapshot):
        ui.html("<h3>QC passed</h3>", title="QC")

    @wf.step
    def docs(report):
        ui.url(f"{base}/docs", title="API")

    run = Run(wf)
    server = RunServer(run, port=18295)
    url = server.start()
    base = url.rstrip("/")
    try:
        run.start()
        page.goto(url)
        page.get_by_role("heading", name="Before you start").wait_for()
        assert page.locator(".markdown strong").inner_text() == "Water"
        assert page.evaluate("window.pwned") is None  # raw HTML is not rendered
        page.get_by_role("button", name="Continue").click()

        page.get_by_role("heading", name="Camera 1").wait_for()
        page.wait_for_function("document.querySelector('.media img')?.naturalWidth === 1")
        assert page.locator("figcaption").inner_text() == "Last frame"
        page.get_by_role("button", name="Continue").click()

        page.get_by_role("heading", name="QC", exact=True).wait_for()
        page.frame_locator(".media iframe").get_by_role("heading", name="QC passed").wait_for()
        page.get_by_role("button", name="Continue").click()

        page.get_by_role("heading", name="API").wait_for()
        assert page.locator(".media iframe").get_attribute("src") == f"{base}/docs"
        assert page.get_by_role("link", name="Open in a new tab").get_attribute("href") == f"{base}/docs"
        page.get_by_role("button", name="Continue").click()
        assert run.wait(15)
    finally:
        if not run.wait(0):
            run.abort("test teardown")
        server.stop()
    assert run.status is RunStatus.COMPLETED


def test_a_failed_branch_is_retried_alone_from_the_web_ui(page):
    import asyncio

    from clabe.workflow import concurrently

    attempts = {"behavior": 0, "ephys": 0}
    wf = Workflow("branches")

    @wf.step(title="Acquire")
    async def acquire() -> dict:
        async def behavior():
            attempts["behavior"] += 1
            await asyncio.sleep(3)
            return "ok"

        async def ephys():
            attempts["ephys"] += 1
            if attempts["ephys"] == 1:
                raise RuntimeError("probe not found")
            return "ok"

        return await concurrently(behavior=behavior, ephys=ephys)

    run = Run(wf)
    server = RunServer(run, port=18296)
    url = server.start()
    try:
        run.start()
        page.goto(url)
        page.get_by_role("button", name="Retry ephys").wait_for()
        assert page.locator(".branches li.failed").inner_text().strip().endswith("ephys")
        assert page.locator(".branches li.running").inner_text().strip().endswith("behavior")
        page.get_by_role("button", name="Retry ephys").click()
        assert run.wait(15)
    finally:
        if not run.wait(0):
            run.abort("test teardown")
        server.stop()
    assert run.status is RunStatus.COMPLETED and attempts == {"behavior": 1, "ephys": 2}
