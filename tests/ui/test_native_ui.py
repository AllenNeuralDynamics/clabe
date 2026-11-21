import io
import os
import threading
from pathlib import Path

import pytest
from pydantic import BaseModel
from rich.console import Console

from clabe.ui import (
    AcknowledgeRequest,
    AutoCompleteRequest,
    ConfirmRequest,
    ConsoleFrontend,
    FieldRequest,
    PathRequest,
    PickRequest,
    PromptCancelledError,
    TextRequest,
    _keys,
)


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

    def test_prompt_field_empty_answer_to_pep604_optional_is_none(self, frontend, monkeypatch):
        class Model(BaseModel):
            note: str | None = None

        monkeypatch.setattr("builtins.input", lambda *a, **k: "")
        assert frontend.prompt_field(FieldRequest(model=Model, field_name="note")) is None


class TestConsoleFrontendPath:
    def test_prompt_path_non_interactive_accepts_existing_file(self, frontend, tmp_path, monkeypatch):
        target = tmp_path / "rig.json"
        target.write_text("{}")
        monkeypatch.setattr("builtins.input", lambda *a, **k: str(target))
        result = frontend.prompt_path(PathRequest(label="Rig", start=str(tmp_path)))
        assert result == target

    def test_prompt_path_reprompts_until_existing(self, frontend, tmp_path, monkeypatch):
        target = tmp_path / "rig.json"
        target.write_text("{}")
        answers = iter([str(tmp_path / "missing.json"), str(target)])
        monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
        result = frontend.prompt_path(PathRequest(label="Rig", start=str(tmp_path)))
        assert result == target

    def test_prompt_path_rejects_wrong_kind(self, frontend, tmp_path, monkeypatch):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        target = tmp_path / "rig.json"
        target.write_text("{}")
        answers = iter([str(subdir), str(target)])
        monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
        result = frontend.prompt_path(PathRequest(label="Rig", start=str(tmp_path), kind="file"))
        assert result == target

    def test_prompt_path_rejects_wrong_extension(self, frontend, tmp_path, monkeypatch):
        wrong = tmp_path / "notes.txt"
        wrong.write_text("x")
        target = tmp_path / "rig.json"
        target.write_text("{}")
        answers = iter([str(wrong), str(target)])
        monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
        result = frontend.prompt_path(PathRequest(label="Rig", start=str(tmp_path), extensions=[".json"]))
        assert result == target

    def test_prompt_path_extension_match_is_case_insensitive(self, frontend, tmp_path, monkeypatch):
        # Regression: pickers offered files by lower-casing only the candidate's
        # suffix, while _validate_path compared unmodified case -- a real
        # Rig1.JSON would be shown as selectable and then rejected forever.
        target = tmp_path / "Rig1.JSON"
        target.write_text("{}")
        monkeypatch.setattr("builtins.input", lambda *a, **k: str(target))
        result = frontend.prompt_path(PathRequest(label="Rig", start=str(tmp_path), extensions=[".json"]))
        assert result == target

    def test_prompt_path_extension_normalizes_uppercase_in_request(self, frontend, tmp_path, monkeypatch):
        target = tmp_path / "rig.json"
        target.write_text("{}")
        monkeypatch.setattr("builtins.input", lambda *a, **k: str(target))
        result = frontend.prompt_path(PathRequest(label="Rig", start=str(tmp_path), extensions=[".JSON"]))
        assert result == target

    def test_prompt_path_typed_reprompt_shows_rejected_candidate_as_default(self, frontend, tmp_path, monkeypatch):
        # Regression: on retry, the non-interactive fallback only ever offered the
        # original `request.default` again, discarding the just-rejected answer
        # FrontendBase.prompt_path had already threaded through as the new `start`.
        rejected = tmp_path / "sub"
        rejected.mkdir()
        captured_defaults: list[str] = []
        from rich.prompt import Prompt

        original_ask = Prompt.ask

        def _spy_ask(*args, **kwargs):
            captured_defaults.append(kwargs.get("default"))
            return original_ask(*args, **kwargs)

        monkeypatch.setattr(Prompt, "ask", staticmethod(_spy_ask))
        target = tmp_path / "rig.json"
        target.write_text("{}")
        answers = iter([str(rejected), str(target)])
        monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
        frontend.prompt_path(PathRequest(label="Rig", start=str(tmp_path), kind="file"))
        assert captured_defaults[1] == str(rejected)

    def test_prompt_path_empty_answer_cancels(self, frontend, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a, **k: "")
        result = frontend.prompt_path(PathRequest(label="Rig", must_exist=False))
        assert result is None


def _keys_returning(*sequence):
    """Returns a callable that yields the given keys in order on each call."""
    iterator = iter(sequence)
    return lambda cancel=None: next(iterator)


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
        result = terminal_frontend.prompt_pick(PickRequest(label="Choose", options=["item1", "item2"], allow_none=True))
        assert result is None

    def test_autocomplete_enter_selects_highlighted(self, terminal_frontend, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning("a", "l", _keys.ENTER))
        result = terminal_frontend.prompt_autocomplete(AutoCompleteRequest(label="Subject", options=["alpha", "beta"]))
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
        result = terminal_frontend.prompt_autocomplete(AutoCompleteRequest(label="Subject", options=["alpha", "beta"]))
        assert result == "zz"

    def test_autocomplete_tab_completes_match(self, terminal_frontend, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning("b", _keys.TAB, _keys.ENTER))
        result = terminal_frontend.prompt_autocomplete(AutoCompleteRequest(label="Subject", options=["beta", "gamma"]))
        assert result == "beta"

    def test_path_tab_completion_drills_into_matching_file(self, terminal_frontend, tmp_path, monkeypatch):
        pickme = tmp_path / "pickme"
        pickme.mkdir()
        (pickme / "alpha.json").write_text("{}")
        (pickme / "beta.json").write_text("{}")
        monkeypatch.setattr(_keys, "read_key", _keys_returning("p", _keys.TAB, "a", _keys.TAB, _keys.ENTER))
        result = terminal_frontend.prompt_path(
            PathRequest(label="Rig", start=str(tmp_path) + os.sep, kind="file", extensions=[".json"])
        )
        assert result == pickme / "alpha.json"

    def test_path_empty_query_falls_back_to_default(self, terminal_frontend, monkeypatch):
        # Regression: every other prompt type falls back to `default` on an empty
        # answer (per PathRequest.default's own docstring); the interactive path
        # browser instead returned None unconditionally, indistinguishable from Escape.
        default = "x.json"
        keys = tuple([_keys.BACKSPACE] * len(default)) + (_keys.ENTER,)
        monkeypatch.setattr(_keys, "read_key", _keys_returning(*keys))
        result = terminal_frontend.prompt_path(PathRequest(label="Rig", default=default, must_exist=False))
        assert result == Path(default)

    def test_path_escape_cancels(self, terminal_frontend, tmp_path, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.ESCAPE))
        result = terminal_frontend.prompt_path(PathRequest(label="Rig", start=str(tmp_path)))
        assert result is None

    def test_path_interrupt_raises(self, terminal_frontend, tmp_path, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.INTERRUPT))
        with pytest.raises(KeyboardInterrupt):
            terminal_frontend.prompt_path(PathRequest(label="Rig", start=str(tmp_path)))


class TestConsoleCancelPrompt:
    @pytest.fixture
    def tty_frontend(self, terminal_frontend, monkeypatch):
        monkeypatch.setattr("clabe.ui._console._stdin_is_tty", lambda: True)
        return terminal_frontend

    def test_text_prompt_reads_keys_on_a_terminal(self, tty_frontend, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning("a", "x", _keys.BACKSPACE, "b", _keys.ENTER))
        assert tty_frontend.prompt_text(TextRequest(label="Notes")) == "ab"

    def test_cancelled_text_prompt_raises(self, tty_frontend, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning("a", _keys.CANCELLED))
        with pytest.raises(PromptCancelledError):
            tty_frontend.prompt_text(TextRequest(label="Notes"))

    def test_cancelled_confirm_raises(self, tty_frontend, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.CANCELLED))
        with pytest.raises(PromptCancelledError):
            tty_frontend.prompt_confirm(ConfirmRequest(label="Sure?"))

    def test_cancelled_acknowledge_raises(self, tty_frontend, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.CANCELLED))
        with pytest.raises(PromptCancelledError):
            tty_frontend.prompt_acknowledge(AcknowledgeRequest(message="Read me"))

    def test_cancelled_pick_raises(self, tty_frontend, monkeypatch):
        monkeypatch.setattr(_keys, "read_key", _keys_returning(_keys.DOWN, _keys.CANCELLED))
        with pytest.raises(PromptCancelledError):
            tty_frontend.prompt_pick(PickRequest(label="Choose", options=["a", "b"], allow_none=False))

    def test_cancel_prompt_stops_a_waiting_read(self, tty_frontend, monkeypatch):
        def read_key(cancel=None):
            assert cancel is not None and cancel.wait(5)
            return _keys.CANCELLED

        monkeypatch.setattr(_keys, "read_key", read_key)
        threading.Timer(0.05, tty_frontend.cancel_prompt).start()
        with pytest.raises(PromptCancelledError):
            tty_frontend.prompt_text(TextRequest(label="Notes"))

    def test_cancel_while_idle_does_not_hit_the_next_prompt(self, tty_frontend, monkeypatch):
        tty_frontend.cancel_prompt()
        monkeypatch.setattr(_keys, "read_key", lambda cancel=None: _keys.CANCELLED if cancel.is_set() else _keys.ENTER)
        assert tty_frontend.prompt_text(TextRequest(label="Notes")) == ""


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
