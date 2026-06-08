import pytest

from clabe import __version__
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
