"""Markdown and media prompts: their specs, and how the console and the TUI show (or cannot show) them."""

import io
import logging
import queue
from pathlib import Path

import pytest
from rich.console import Console

from clabe import ui
from clabe.ui import ClientSession, ConsoleClient, GroupRequest, MarkdownRequest, MediaRequest, TextRequest
from clabe.ui._textual import _LauncherApp
from clabe.ui._textual_form import _AcknowledgeScreen
from tests.conftest import MockClient, use_client


class TestMarkdownRequest:
    def test_the_spec_carries_the_rendered_html_without_raw_html(self):
        spec = MarkdownRequest("# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n<script>alert(1)</script>").spec()
        assert "<h1>Title</h1>" in spec["html"] and "<table>" in spec["html"]
        assert "<script>" not in spec["html"] and "&lt;script&gt;" in spec["html"]

    def test_unsafe_links_are_not_rendered(self):
        assert 'href="javascript' not in MarkdownRequest("[x](javascript:alert(1))").spec()["html"]

    def test_continuing_is_the_answer(self):
        assert ClientSession(MockClient()).ask(MarkdownRequest("hi")) is None


class TestMediaRequest:
    def test_where_the_media_is(self, tmp_path):
        image = tmp_path / "frame.png"
        image.write_bytes(b"png")
        assert MediaRequest(image).location == "file" and MediaRequest(image).local_file() == image.resolve()
        assert MediaRequest(str(image)).spec()["source"] == str(image.resolve())
        assert MediaRequest("https://example.org/a.png").location == "url"
        assert MediaRequest("<b>hi</b>", "html").location == "inline"
        assert MediaRequest("report.html", "html").location == "file"

    def test_a_url_media_needs_a_url(self):
        with pytest.raises(ValueError, match="http"):
            MediaRequest("C:/report.html", "url")
        with pytest.raises(ValueError, match="media must be one of"):
            MediaRequest("x", "video")  # type: ignore[arg-type]

    def test_it_cannot_be_grouped(self):
        with pytest.raises(ValueError, match="cannot be part of a group"):
            GroupRequest(items={"a": TextRequest(label="a"), "b": MediaRequest("https://example.org")})

    def test_the_session_helpers_ask_media(self):
        client = MockClient()
        use_client(client)
        ui.image("frame.png", caption="Camera 1")
        ui.html("<p>QC</p>")
        ui.url("https://example.org")
        ui.markdown("**bold**", title="Notes")
        kinds = [(r.prompt_kind, getattr(r, "media", None)) for r in client.asked]
        assert kinds == [("media", "image"), ("media", "html"), ("media", "url"), ("markdown", None)]


class TestConsole:
    def _client(self, monkeypatch, out: io.StringIO) -> ConsoleClient:
        monkeypatch.setattr("builtins.input", lambda *a, **k: "")
        return ConsoleClient(console=Console(file=out, force_terminal=False, width=80))

    def test_markdown_is_rendered(self, monkeypatch):
        out = io.StringIO()
        self._client(monkeypatch, out).ask(MarkdownRequest("# Checklist\n\n- **water** on", title="Before you start"))
        text = out.getvalue()
        assert "Before you start" in text and "Checklist" in text and "water" in text and "**" not in text

    def test_media_is_a_warning_naming_the_file(self, monkeypatch, caplog):
        out = io.StringIO()
        with caplog.at_level(logging.WARNING):
            self._client(monkeypatch, out).ask(MediaRequest(Path("frame.png"), caption="Camera 1"))
        assert "cannot show the image" in caplog.text and "frame.png" in caplog.text
        assert "frame.png" in out.getvalue() and "Camera 1" in out.getvalue()


class TestTextual:
    @pytest.mark.asyncio
    async def test_markdown_is_shown_in_the_dialog(self):
        from textual.widgets import Markdown

        app = _LauncherApp()
        async with app.run_test() as pilot:
            reply: queue.Queue = queue.Queue()
            await app.ask_acknowledge(MarkdownRequest("# Hello", title="Notes"), reply)
            await pilot.pause()
            assert isinstance(app.screen, _AcknowledgeScreen)
            assert len(app.screen.query(Markdown)) == 1
            await pilot.press("enter")
            await pilot.pause()
            assert reply.get_nowait() is True

    @pytest.mark.asyncio
    async def test_media_names_what_it_cannot_show(self):
        from textual.widgets import Label

        app = _LauncherApp()
        async with app.run_test() as pilot:
            reply: queue.Queue = queue.Queue()
            await app.ask_acknowledge(MediaRequest("https://example.org/qc", "url"), reply)
            await pilot.pause()
            body = str(app.screen.query_one("#ack-body", Label).render())
            assert "cannot show this url" in body and "https://example.org/qc" in body

    def test_the_client_warns_for_an_image(self, monkeypatch, caplog, opened):
        from clabe.ui import TextualClient

        client = TextualClient()
        monkeypatch.setattr(client, "_ask_acknowledge", lambda request, shown=None: None)
        with caplog.at_level(logging.WARNING):
            client._ask_media(MediaRequest("frame.png"))
        assert "TUI cannot show the image" in caplog.text and opened == []

    @pytest.mark.asyncio
    async def test_the_dialog_says_what_was_opened(self):
        from textual.widgets import Label

        app = _LauncherApp()
        async with app.run_test() as pilot:
            reply: queue.Queue = queue.Queue()
            await app.ask_acknowledge(MediaRequest("https://example.org/qc", "url"), reply, "https://example.org/qc")
            await pilot.pause()
            body = str(app.screen.query_one("#ack-body", Label).render())
            assert "Opened in your browser: https://example.org/qc" in body


@pytest.fixture
def opened(monkeypatch) -> list[str]:
    """What the terminal clients open in the browser (nothing is opened)."""
    targets: list[str] = []
    monkeypatch.setattr("clabe._process.open_in_browser", targets.append)
    return targets


def _cases(tmp_path: Path) -> list[tuple[MediaRequest, str]]:
    """A local HTML file, a URL and inline HTML, each with what the browser should open."""
    report = tmp_path / "qc report.html"
    report.write_text("<h1>QC</h1>")
    return [
        (MediaRequest(report, "html"), report.resolve().as_uri()),
        (MediaRequest("https://example.org/qc", "url"), "https://example.org/qc"),
        (MediaRequest("<h1>Inline</h1>", "html"), "inline"),
    ]


class TestTerminalsOpenPagesInTheBrowser:
    @pytest.mark.parametrize("index", [0, 1, 2])
    def test_the_console_opens_it_once_and_still_waits(self, monkeypatch, tmp_path, opened, index):
        request, expected = _cases(tmp_path)[index]
        waited: list[str] = []
        out = io.StringIO()
        client = ConsoleClient(console=Console(file=out, force_terminal=False, width=200))
        monkeypatch.setattr(client._console, "input", lambda prompt, **k: waited.append(prompt) or "")
        client.ask(request)
        assert len(opened) == 1 and len(waited) == 1
        _check_target(opened[0], expected)
        assert "Opened in your browser" in out.getvalue()

    @pytest.mark.parametrize("index", [0, 1, 2])
    def test_the_tui_opens_it_once_and_still_waits(self, monkeypatch, tmp_path, opened, index):
        from clabe.ui import TextualClient

        request, expected = _cases(tmp_path)[index]
        shown: list[tuple] = []
        client = TextualClient()
        monkeypatch.setattr(client, "_ask_acknowledge", lambda request, what=None: shown.append((request, what)))
        client._ask_media(request)
        assert len(opened) == 1 and shown == [(request, opened[0])]
        _check_target(opened[0], expected)

    def test_an_image_or_a_missing_file_opens_nothing(self, monkeypatch, tmp_path, opened):
        monkeypatch.setattr("builtins.input", lambda *a, **k: "")
        client = ConsoleClient(console=Console(file=io.StringIO(), force_terminal=False))
        client.ask(MediaRequest(tmp_path / "frame.png"))
        client.ask(MediaRequest(tmp_path / "missing.html", "html"))
        assert opened == []

    def test_a_failure_to_open_is_logged_and_the_prompt_still_waits(self, monkeypatch, tmp_path, caplog):
        def fail(target: str) -> None:
            raise OSError("no browser")

        monkeypatch.setattr("clabe._process.open_in_browser", fail)
        waited: list[str] = []
        client = ConsoleClient(console=Console(file=io.StringIO(), force_terminal=False))
        monkeypatch.setattr(client._console, "input", lambda prompt, **k: waited.append(prompt) or "")
        with caplog.at_level(logging.WARNING):
            client.ask(MediaRequest("https://example.org/qc", "url"))
        assert "Could not open the url" in caplog.text and "no browser" in caplog.text and len(waited) == 1


def _check_target(target: str, expected: str) -> None:
    """``target`` is ``expected``; for inline HTML, a temporary .html file holding it."""
    if expected != "inline":
        assert target == expected
        return
    from urllib.parse import urlparse
    from urllib.request import url2pathname

    page = Path(url2pathname(urlparse(target).path))
    assert target.startswith("file:") and page.suffix == ".html"
    assert page.read_text(encoding="utf-8") == "<h1>Inline</h1>"
    page.unlink()
