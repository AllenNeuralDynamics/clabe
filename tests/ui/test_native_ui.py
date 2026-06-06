import io

import pytest
from rich.console import Console

from clabe.ui import AutoCompleteRequest, ConfirmRequest, ConsoleFrontend, PickRequest, TextRequest, _keys


@pytest.fixture
def frontend():
    return ConsoleFrontend(console=Console(file=io.StringIO(), force_terminal=False))


@pytest.fixture
def terminal_frontend():
    return ConsoleFrontend(console=Console(file=io.StringIO(), force_terminal=True))


class TestConsoleFrontend:
    def test_prompt_text(self, frontend, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a, **k: "Some notes")
        assert frontend.prompt_text(TextRequest(label="Notes")) == "Some notes"

    def test_prompt_text_uses_default_on_empty(self, frontend, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a, **k: "")
        assert frontend.prompt_text(TextRequest(label="Notes", default="fallback")) == "fallback"

    def test_prompt_confirm_yes(self, frontend, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
        assert frontend.prompt_confirm(ConfirmRequest(label="Continue?")) is True

    def test_prompt_confirm_no(self, frontend, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
        assert frontend.prompt_confirm(ConfirmRequest(label="Continue?")) is False

    def test_prompt_pick(self, frontend, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a, **k: "1")
        result = frontend.prompt_pick(PickRequest(label="Choose", options=["item1", "item2"], allow_none=False))
        assert result == "item1"

    def test_prompt_pick_none(self, frontend, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a, **k: "0")
        result = frontend.prompt_pick(PickRequest(label="Choose", options=["item1", "item2"], allow_none=True))
        assert result is None


def _keys_returning(*sequence):
    """Returns a callable that yields the given keys in order on each call."""
    iterator = iter(sequence)
    return lambda: next(iterator)


class TestConsoleFrontendInteractive:
    def test_pick_arrow_navigation(self, terminal_frontend, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.DOWN, _keys.ENTER))
        result = terminal_frontend.prompt_pick(
            PickRequest(label="Choose", options=["item1", "item2"], allow_none=False)
        )
        assert result == "item2"

    def test_pick_default_then_enter(self, terminal_frontend, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.ENTER))
        result = terminal_frontend.prompt_pick(
            PickRequest(label="Choose", options=["item1", "item2"], default="item2", allow_none=False)
        )
        assert result == "item2"

    def test_pick_none_row(self, terminal_frontend, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.ENTER))
        result = terminal_frontend.prompt_pick(
            PickRequest(label="Choose", options=["item1", "item2"], allow_none=True)
        )
        assert result is None

    def test_autocomplete_enter_selects_highlighted(self, terminal_frontend, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning("a", "l", _keys.ENTER))
        result = terminal_frontend.prompt_autocomplete(
            AutoCompleteRequest(label="Subject", options=["alpha", "beta"])
        )
        assert result == "alpha"

    def test_autocomplete_enter_on_first_suggestion(self, terminal_frontend, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.ENTER))
        result = terminal_frontend.prompt_autocomplete(
            AutoCompleteRequest(label="Experimenter", options=["alex.kim", "bruno.cruz"])
        )
        assert result == "alex.kim"

    def test_autocomplete_arrow_then_enter(self, terminal_frontend, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.DOWN, _keys.ENTER))
        result = terminal_frontend.prompt_autocomplete(
            AutoCompleteRequest(label="Experimenter", options=["alex.kim", "bruno.cruz"])
        )
        assert result == "bruno.cruz"

    def test_autocomplete_free_text_when_no_match(self, terminal_frontend, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning("z", "z", _keys.ENTER))
        result = terminal_frontend.prompt_autocomplete(
            AutoCompleteRequest(label="Subject", options=["alpha", "beta"])
        )
        assert result == "zz"

    def test_autocomplete_tab_completes_match(self, terminal_frontend, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning("b", _keys.TAB, _keys.ENTER))
        result = terminal_frontend.prompt_autocomplete(
            AutoCompleteRequest(label="Subject", options=["beta", "gamma"])
        )
        assert result == "beta"
