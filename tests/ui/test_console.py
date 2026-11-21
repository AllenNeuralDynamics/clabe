import io
import os
import threading
from enum import Enum
from pathlib import Path

import pytest
from pydantic import BaseModel, Field
from rich.console import Console

from clabe.ui import (
    AcknowledgeRequest,
    AutoCompleteRequest,
    ClientSession,
    ConfirmRequest,
    ConsoleClient,
    FieldRequest,
    FormRequest,
    NumberRequest,
    PathRequest,
    PickRequest,
    PromptCancelledError,
    ReadOnlyTable,
    TextRequest,
    _keys,
)


@pytest.fixture
def client():
    return ConsoleClient(console=Console(file=io.StringIO(), force_terminal=False))


@pytest.fixture
def session(client):
    return ClientSession(client)


@pytest.fixture
def terminal_client():
    return ConsoleClient(console=Console(file=io.StringIO(), force_terminal=True))


def _answers(monkeypatch, *values):
    """Feeds ``values`` to ``input()`` in order."""
    iterator = iter(values)
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(iterator))


class TestConsoleClient:
    def test_text(self, session, monkeypatch):
        _answers(monkeypatch, "Some notes")
        assert session.ask(TextRequest(label="Notes")) == "Some notes"

    def test_text_uses_default_on_empty(self, session, monkeypatch):
        _answers(monkeypatch, "")
        assert session.ask(TextRequest(label="Notes", default="fallback")) == "fallback"

    def test_confirm(self, session, monkeypatch):
        _answers(monkeypatch, "y", "n")
        assert session.ask(ConfirmRequest(label="Continue?")) is True
        assert session.ask(ConfirmRequest(label="Continue?")) is False

    def test_pick(self, session, monkeypatch):
        _answers(monkeypatch, "1", "0")
        assert session.ask(PickRequest(label="Choose", options=["item1", "item2"], allow_none=False)) == "item1"
        assert session.ask(PickRequest(label="Choose", options=["item1", "item2"], allow_none=True)) is None

    def test_number_is_typed_text_coerced_by_validation(self, client, session, monkeypatch):
        _answers(monkeypatch, "2.5", "abc", "3", "")
        assert client.ask(NumberRequest(label="Gain")) == "2.5"
        assert session.ask(NumberRequest(label="Gain")) == 3.0  # "abc" is rejected and asked again
        assert session.ask(NumberRequest(label="Gain", default=1.5)) == 1.5

    def test_field_resolves_to_a_primitive(self, session, monkeypatch):
        class Model(BaseModel):
            note: str | None = None
            count: int = Field(default=1, gt=0)

        _answers(monkeypatch, "", "0", "4")
        assert session.ask(FieldRequest(model=Model, field_name="note")) is None
        assert session.ask(FieldRequest(model=Model, field_name="count")) == 4

    def test_unresolved_field_requests_are_refused(self, client):
        class Model(BaseModel):
            note: str = ""

        with pytest.raises(ValueError, match="resolve"):
            client.ask(FieldRequest(model=Model, field_name="note"))

    def test_form_asks_each_field(self, client, session, monkeypatch):
        class Mode(Enum):
            FAST = "fast"
            SLOW = "slow"

        class Config(BaseModel):
            name: str
            trials: int = Field(default=10, gt=0)
            mode: Mode = Mode.FAST
            debug: bool = False

        # name, trials (0 rejected, then 5), mode (by member name), debug
        _answers(monkeypatch, "mouse", "0", "5", "SLOW", "y")
        result = session.ask(FormRequest(model=Config))
        assert result == Config(name="mouse", trials=5, mode=Mode.SLOW, debug=True)
        assert "greater than 0" in client._console.file.getvalue()

    def test_form_uses_the_initial_values_as_defaults(self, session, monkeypatch):
        class Config(BaseModel):
            name: str
            trials: int = 10

        _answers(monkeypatch, "", "")
        assert session.ask(FormRequest(model=Config, initial=Config(name="init", trials=3))) == Config(
            name="init", trials=3
        )

    def test_table_prints_and_confirms(self, client, session, monkeypatch):
        _answers(monkeypatch, "y")
        request = ReadOnlyTable.from_records([{"App": "Bonsai", "Timeout": 2}], title="Plan", prompt="Run these?")
        assert session.ask(request) is True
        output = client._console.file.getvalue()
        assert "Bonsai" in output and "Run these?" in output

    def test_notify_and_header(self, client):
        client.notify("hello")
        client.header("Title")
        output = client._console.file.getvalue()
        assert "hello" in output and "Title" in output


class TestConsolePath:
    def test_accepts_an_existing_file(self, session, tmp_path, monkeypatch):
        target = tmp_path / "rig.json"
        target.write_text("{}")
        _answers(monkeypatch, str(target))
        assert session.ask(PathRequest(label="Rig", start=str(tmp_path))) == target

    def test_asks_again_until_the_path_exists(self, session, tmp_path, monkeypatch):
        target = tmp_path / "rig.json"
        target.write_text("{}")
        _answers(monkeypatch, str(tmp_path / "missing.json"), str(target))
        assert session.ask(PathRequest(label="Rig", start=str(tmp_path))) == target

    def test_rejects_the_wrong_kind(self, session, tmp_path, monkeypatch):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        target = tmp_path / "rig.json"
        target.write_text("{}")
        _answers(monkeypatch, str(subdir), str(target))
        assert session.ask(PathRequest(label="Rig", start=str(tmp_path), kind="file")) == target

    def test_rejects_the_wrong_extension(self, session, tmp_path, monkeypatch):
        wrong = tmp_path / "notes.txt"
        wrong.write_text("x")
        target = tmp_path / "rig.json"
        target.write_text("{}")
        _answers(monkeypatch, str(wrong), str(target))
        assert session.ask(PathRequest(label="Rig", start=str(tmp_path), extensions=[".json"])) == target

    def test_extension_match_is_case_insensitive(self, session, tmp_path, monkeypatch):
        # Regression: a picker offering Rig1.JSON must not have it rejected by validation over case.
        target = tmp_path / "Rig1.JSON"
        target.write_text("{}")
        _answers(monkeypatch, str(target))
        assert session.ask(PathRequest(label="Rig", start=str(tmp_path), extensions=[".json"])) == target

    def test_extension_in_the_request_is_normalized(self, session, tmp_path, monkeypatch):
        target = tmp_path / "rig.json"
        target.write_text("{}")
        _answers(monkeypatch, str(target))
        assert session.ask(PathRequest(label="Rig", start=str(tmp_path), extensions=[".JSON"])) == target

    def test_empty_answer_is_none(self, session, monkeypatch):
        _answers(monkeypatch, "")
        assert session.ask(PathRequest(label="Rig", must_exist=False)) is None


def _keys_returning(*sequence):
    """Returns a callable that yields the given keys in order on each call."""
    iterator = iter(sequence)
    return lambda cancel=None: next(iterator)


class TestConsoleInteractive:
    def test_pick_arrow_navigation(self, terminal_client, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.DOWN, _keys.ENTER))
        result = terminal_client.ask(PickRequest(label="Choose", options=["item1", "item2"], allow_none=False))
        assert result == "item2"

    def test_pick_default_then_enter(self, terminal_client, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.ENTER))
        request = PickRequest(label="Choose", options=["item1", "item2"], default="item2", allow_none=False)
        assert terminal_client.ask(request) == "item2"

    def test_pick_none_row(self, terminal_client, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.ENTER))
        assert terminal_client.ask(PickRequest(label="Choose", options=["item1", "item2"], allow_none=True)) is None

    def test_autocomplete_enter_selects_highlighted(self, terminal_client, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning("a", "l", _keys.ENTER))
        assert terminal_client.ask(AutoCompleteRequest(label="Subject", options=["alpha", "beta"])) == "alpha"

    def test_autocomplete_enter_on_first_suggestion(self, terminal_client, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.ENTER))
        request = AutoCompleteRequest(label="Experimenter", options=["alex.kim", "bruno.cruz"])
        assert terminal_client.ask(request) == "alex.kim"

    def test_autocomplete_arrow_then_enter(self, terminal_client, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.DOWN, _keys.ENTER))
        request = AutoCompleteRequest(label="Experimenter", options=["alex.kim", "bruno.cruz"])
        assert terminal_client.ask(request) == "bruno.cruz"

    def test_autocomplete_free_text_when_no_match(self, terminal_client, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning("z", "z", _keys.ENTER))
        assert terminal_client.ask(AutoCompleteRequest(label="Subject", options=["alpha", "beta"])) == "zz"

    def test_autocomplete_tab_completes_match(self, terminal_client, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning("b", _keys.TAB, _keys.ENTER))
        assert terminal_client.ask(AutoCompleteRequest(label="Subject", options=["beta", "gamma"])) == "beta"

    def test_path_tab_completion_drills_into_matching_file(self, terminal_client, tmp_path, monkeypatch):
        pickme = tmp_path / "pickme"
        pickme.mkdir()
        (pickme / "alpha.json").write_text("{}")
        (pickme / "beta.json").write_text("{}")
        monkeypatch.setattr(_keys, "read_key", _keys_returning("p", _keys.TAB, "a", _keys.TAB, _keys.ENTER))
        request = PathRequest(label="Rig", start=str(tmp_path) + os.sep, kind="file", extensions=[".json"])
        assert terminal_client.ask(request) == pickme / "alpha.json"

    def test_path_empty_query_falls_back_to_default(self, terminal_client, monkeypatch):
        default = "x.json"
        keys = tuple([_keys.BACKSPACE] * len(default)) + (_keys.ENTER,)
        monkeypatch.setattr(_keys, "read_key", _keys_returning(*keys))
        assert terminal_client.ask(PathRequest(label="Rig", default=default, must_exist=False)) == Path(default)

    def test_path_escape_cancels(self, terminal_client, tmp_path, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.ESCAPE))
        assert terminal_client.ask(PathRequest(label="Rig", start=str(tmp_path))) is None

    def test_path_interrupt_raises(self, terminal_client, tmp_path, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.INTERRUPT))
        with pytest.raises(KeyboardInterrupt):
            terminal_client.ask(PathRequest(label="Rig", start=str(tmp_path)))


class TestConsoleCancel:
    @pytest.fixture
    def tty_client(self, terminal_client, monkeypatch):
        monkeypatch.setattr("clabe.ui._console._stdin_is_tty", lambda: True)
        return terminal_client

    def test_text_reads_keys_on_a_terminal(self, tty_client, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning("a", "x", _keys.BACKSPACE, "b", _keys.ENTER))
        assert tty_client.ask(TextRequest(label="Notes")) == "ab"

    @pytest.mark.parametrize(
        "request_",
        [
            TextRequest(label="Notes"),
            ConfirmRequest(label="Sure?"),
            AcknowledgeRequest(message="Read me"),
            NumberRequest(label="Gain"),
        ],
    )
    def test_cancelled_prompts_raise(self, tty_client, monkeypatch, request_):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.CANCELLED))
        with pytest.raises(PromptCancelledError):
            tty_client.ask(request_)

    def test_cancelled_pick_raises(self, tty_client, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.DOWN, _keys.CANCELLED))
        with pytest.raises(PromptCancelledError):
            tty_client.ask(PickRequest(label="Choose", options=["a", "b"], allow_none=False))

    def test_cancel_stops_a_waiting_read(self, tty_client, monkeypatch):
        def read_key(cancel=None):
            assert cancel is not None and cancel.wait(5)
            return _keys.CANCELLED

        monkeypatch.setattr(_keys, "read_key", read_key)
        threading.Timer(0.05, tty_client.cancel).start()
        with pytest.raises(PromptCancelledError):
            tty_client.ask(TextRequest(label="Notes"))

    def test_cancel_while_idle_does_not_hit_the_next_prompt(self, tty_client, monkeypatch):
        tty_client.cancel()
        monkeypatch.setattr(_keys, "read_key", lambda cancel=None: _keys.CANCELLED if cancel.is_set() else _keys.ENTER)
        assert tty_client.ask(TextRequest(label="Notes")) == ""


class TestReadKeyCancel:
    def test_windows_read_returns_cancelled(self, monkeypatch):
        import sys
        import types

        monkeypatch.setitem(sys.modules, "msvcrt", types.SimpleNamespace(kbhit=lambda: False))
        cancel = threading.Event()
        threading.Timer(0.05, cancel.set).start()
        assert _keys._read_key_windows(cancel) == _keys.CANCELLED

    def test_windows_read_returns_key_when_pressed(self, monkeypatch):
        import sys
        import types

        monkeypatch.setitem(sys.modules, "msvcrt", types.SimpleNamespace(kbhit=lambda: True, getwch=lambda: "q"))
        assert _keys._read_key_windows(threading.Event()) == "q"
