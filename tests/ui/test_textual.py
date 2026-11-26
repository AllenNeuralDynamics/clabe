import queue

import pytest

from clabe import __version__
from clabe.ui._requests import PathRequest
from clabe.ui._textual import _LauncherApp, _linkify


class TestLinkify:
    def test_existing_absolute_path_becomes_link(self, tmp_path):
        target = tmp_path / "Logs"
        target.mkdir()
        text = _linkify(f"Copied logs to {target}", "")
        assert any("link file:" in str(span.style) for span in text.spans)
        assert text.plain == f"Copied logs to {target}"

    def test_existing_relative_path_is_resolved_and_linked(self, tmp_path, monkeypatch):
        (tmp_path / "sub" / "Logs").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        text = _linkify("wrote sub/Logs done", "")
        assert any("link file:" in str(span.style) for span in text.spans)

    def test_nonexistent_pathlike_not_linked(self):
        text = _linkify("compare a/b/c with x", "")
        assert not any("link " in str(span.style) for span in text.spans)

    def test_urls_are_not_linkified(self):
        text = _linkify("fetched from http://host/api/v2/names ok", "")
        assert not any("link " in str(span.style) for span in text.spans)
        assert text.plain == "fetched from http://host/api/v2/names ok"

    def test_plain_text_unchanged(self):
        text = _linkify("nothing to see here", "green")
        assert text.plain == "nothing to see here"
        assert not any("link " in str(span.style) for span in text.spans)


class TestBindings:
    def test_ctrl_c_exits(self):
        actions = {binding.key: binding.action for binding in _LauncherApp.BINDINGS}
        assert actions["ctrl+c"] == "cancel"


@pytest.mark.asyncio
async def test_header_shows_version_and_footer_present():
    from textual.widgets import Footer, Header

    app = _LauncherApp()
    async with app.run_test():
        assert app.sub_title == f"v{__version__}"
        assert len(app.query(Header)) == 1
        assert len(app.query(Footer)) == 1


@pytest.mark.asyncio
async def test_set_experiment_updates_header():
    app = _LauncherApp()
    async with app.run_test():
        app.set_experiment("demo_experiment")
        assert "demo_experiment" in app.sub_title
        assert __version__ in app.sub_title


class TestAskPath:
    """Integration tests against the real textual-fspicker screens (no mocking).

    Both tests below pin down bugs that were found and fixed while building this
    feature (verified by reverting each fix and confirming the test fails), not
    regressions in a previously shipped behavior -- ``PathRequest``/``ask_path``
    is new.
    """

    @pytest.mark.asyncio
    async def test_backspace_navigates_past_start(self, tmp_path):
        # A plain DirectoryTree-rooted browser can't walk above its own root --
        # the original gap this feature was built to close. textual-fspicker's
        # DirectoryNavigation can: Backspace must reach start's parent, not stop
        # at start.
        start = tmp_path / "sub"
        start.mkdir()
        app = _LauncherApp(show_logs=False)
        async with app.run_test() as pilot:
            reply: queue.Queue = queue.Queue()
            await app.ask_path(PathRequest(label="Pick", start=str(start), kind="dir"), reply)
            await pilot.pause()
            nav = app.screen.query_one("DirectoryNavigation")
            assert nav.location == start
            await pilot.press("backspace")
            await pilot.pause()
            assert nav.location == tmp_path
            await pilot.press("escape")
            await pilot.pause()
            assert reply.get_nowait() is None

    @pytest.mark.asyncio
    async def test_nonexistent_start_does_not_crash(self, tmp_path):
        # textual-fspicker lists `location` unconditionally and raises
        # FileNotFoundError if it doesn't exist -- a real crash hit during
        # development when start pointed at an output directory that hadn't
        # been created yet.
        missing = tmp_path / "not_created_yet" / "out.json"
        app = _LauncherApp(show_logs=False)
        async with app.run_test() as pilot:
            reply: queue.Queue = queue.Queue()
            await app.ask_path(PathRequest(label="Pick", start=str(missing), kind="file", must_exist=False), reply)
            await pilot.pause()
            nav = app.screen.query_one("DirectoryNavigation")
            assert nav.location.is_dir()  # resolved to a real, existing ancestor -- not the missing path itself
            await pilot.press("escape")
            await pilot.pause()
            assert reply.get_nowait() is None

    @pytest.mark.asyncio
    async def test_select_directory_returns_browsed_dir(self, tmp_path):
        app = _LauncherApp(show_logs=False)
        async with app.run_test() as pilot:
            reply: queue.Queue = queue.Queue()
            await app.ask_path(PathRequest(label="Pick", start=str(tmp_path), kind="dir"), reply)
            await pilot.pause()
            await pilot.click("#select")
            await pilot.pause()
            assert reply.get_nowait() == tmp_path

    @pytest.mark.asyncio
    async def test_file_open_returns_typed_file(self, tmp_path):
        target = tmp_path / "rig.json"
        target.write_text("{}")
        app = _LauncherApp(show_logs=False)
        async with app.run_test() as pilot:
            reply: queue.Queue = queue.Queue()
            await app.ask_path(PathRequest(label="Pick", start=str(tmp_path), kind="file", extensions=[".json"]), reply)
            await pilot.pause()
            app.screen.query("Input").first().value = "rig.json"
            await pilot.click("#select")
            await pilot.pause()
            assert reply.get_nowait() == target

    @pytest.mark.asyncio
    async def test_kind_any_folder_choice_can_return_a_directory(self, tmp_path):
        # Regression: FileOpen (what kind="any" used to map to directly) treats
        # picking a directory as "cd into it" and never dismisses with one -- a
        # PathRequest(kind="any") (what prompt_field uses for a plain Path field,
        # e.g. the output_dir demo) could never resolve to a directory in the
        # Textual frontend. The disambiguation screen must let "Folder" reach a
        # real SelectDirectory that can.
        app = _LauncherApp(show_logs=False)
        async with app.run_test() as pilot:
            reply: queue.Queue = queue.Queue()
            await app.ask_path(PathRequest(label="Pick", start=str(tmp_path), kind="any"), reply)
            await pilot.pause()
            await pilot.click("#kind-dir")
            await pilot.pause()
            assert app.screen.query_one("DirectoryNavigation") is not None
            await pilot.click("#select")
            await pilot.pause()
            assert reply.get_nowait() == tmp_path

    @pytest.mark.asyncio
    async def test_kind_any_file_choice_returns_a_file(self, tmp_path):
        target = tmp_path / "rig.json"
        target.write_text("{}")
        app = _LauncherApp(show_logs=False)
        async with app.run_test() as pilot:
            reply: queue.Queue = queue.Queue()
            await app.ask_path(PathRequest(label="Pick", start=str(tmp_path), kind="any"), reply)
            await pilot.pause()
            await pilot.click("#kind-file")
            await pilot.pause()
            app.screen.query("Input").first().value = "rig.json"
            await pilot.click("#select")
            await pilot.pause()
            assert reply.get_nowait() == target

    @pytest.mark.asyncio
    async def test_kind_any_cancel_at_disambiguation_returns_none(self, tmp_path):
        app = _LauncherApp(show_logs=False)
        async with app.run_test() as pilot:
            reply: queue.Queue = queue.Queue()
            await app.ask_path(PathRequest(label="Pick", start=str(tmp_path), kind="any"), reply)
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert reply.get_nowait() is None


class TestCancelPending:
    @pytest.mark.asyncio
    async def test_cancels_inline_text_prompt(self):
        from textual.containers import Vertical

        from clabe.ui._requests import TextRequest
        from clabe.ui._textual import _PROMPT_CANCELLED

        app = _LauncherApp()
        async with app.run_test() as pilot:
            reply: queue.Queue = queue.Queue()
            await app.ask_text(TextRequest(label="Press Enter to exit..."), reply)
            await pilot.pause()
            await app.cancel_pending()
            assert reply.get_nowait() is _PROMPT_CANCELLED
            assert app._pending is None
            assert len(app.query_one("#clabe-prompt", Vertical).children) == 0

    @pytest.mark.asyncio
    async def test_cancels_acknowledge_modal(self):
        from clabe.ui._requests import AcknowledgeRequest
        from clabe.ui._textual import _PROMPT_CANCELLED

        app = _LauncherApp()
        async with app.run_test() as pilot:
            reply: queue.Queue = queue.Queue()
            await app.ask_acknowledge(AcknowledgeRequest(message="Read me"), reply)
            await pilot.pause()
            assert len(app.screen_stack) == 2
            await app.cancel_pending()
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert reply.get_nowait() is _PROMPT_CANCELLED
            assert reply.empty()

    @pytest.mark.asyncio
    async def test_no_pending_prompt_is_a_no_op(self):
        app = _LauncherApp()
        async with app.run_test():
            await app.cancel_pending()
            assert app._pending is None


def test_unwrap_prompt_cancelled_raises():
    from clabe.ui import PromptCancelledError
    from clabe.ui._textual import _PROMPT_CANCELLED, _unwrap

    with pytest.raises(PromptCancelledError):
        _unwrap(_PROMPT_CANCELLED)


class TestTextualClient:
    """Dispatch and echo of the TUI client, without starting the app."""

    @pytest.fixture
    def client(self, monkeypatch):
        from clabe.ui import TextualClient

        client = TextualClient()
        written = []
        monkeypatch.setattr(client, "_ensure", lambda: None)
        monkeypatch.setattr(client, "_write_user", written.append)
        client.written = written
        return client

    def test_ask_dispatches_on_the_prompt_kind_and_echoes(self, client, monkeypatch):
        from clabe.ui import ConfirmRequest, TextRequest

        monkeypatch.setattr(client, "_ask_text", lambda request: "mouse")
        monkeypatch.setattr(client, "_ask_confirm", lambda request: True)
        assert client.ask(TextRequest(label="Subject")) == "mouse"
        assert client.ask(ConfirmRequest(label="Go?")) is True
        echoed = [line.plain for line in client.written]
        assert any("Subject: mouse" in line for line in echoed)

    def test_number_is_asked_as_text(self, client, monkeypatch):
        from clabe.ui import NumberRequest

        seen = []
        monkeypatch.setattr(client, "_ask_text", lambda request: seen.append(request) or "2.5")
        assert client.ask(NumberRequest(label="Gain", default=1.0)) == "2.5"
        assert (seen[0].label, seen[0].default) == ("Gain", "1")

    def test_unresolved_field_requests_are_refused(self, client):
        from pydantic import BaseModel

        from clabe.ui import FieldRequest

        class Model(BaseModel):
            note: str = ""

        with pytest.raises(ValueError, match="resolve"):
            client.ask(FieldRequest(model=Model, field_name="note"))

    def test_is_a_client(self):
        from clabe.ui import Client, TextualClient

        assert isinstance(TextualClient(), Client)
