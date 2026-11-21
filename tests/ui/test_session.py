import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from clabe import ui
from clabe.ui import (
    AcknowledgeRequest,
    AutoCompleteRequest,
    Choice,
    ConfirmRequest,
    FieldRequest,
    FormRequest,
    MessageLevel,
    NumberRequest,
    PathRequest,
    PickRequest,
    ReadOnlyTable,
    TextRequest,
)


class FakeSession:
    """Records what it is asked and told; answers every prompt with ``answer``."""

    def __init__(self, answer=None) -> None:
        self.answer = answer
        self.asked: list = []
        self.notes: list = []
        self.headers: list = []

    def ask(self, request):
        self.asked.append(request)
        return self.answer

    def notify(self, message, level=MessageLevel.INFO):
        self.notes.append((message, level))

    def header(self, text):
        self.headers.append(text)


class _Model(BaseModel):
    name: str = "x"


def _validator(value: str) -> str | None:
    return None


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (lambda: ui.confirm("sure?", default=True, field="f"), ConfirmRequest(label="sure?", default=True, field="f")),
        (
            lambda: ui.text("name", default="a", multiline=True, validators=[_validator], field="f"),
            TextRequest(label="name", default="a", multiline=True, validators=[_validator], field="f"),
        ),
        (lambda: ui.number("n", default=1.0, field="f"), NumberRequest(label="n", default=1.0, field="f")),
        (
            lambda: ui.pick("p", ["a", Choice("b", "B")], default="a", allow_none=False, none_label="-", help="h"),
            PickRequest(
                label="p", options=["a", Choice("b", "B")], default="a", allow_none=False, none_label="-", help="h"
            ),
        ),
        (
            lambda: ui.autocomplete("a", ["x"], default="x", strict=True, validators=[_validator], field="f", help="h"),
            AutoCompleteRequest(
                label="a", options=["x"], default="x", strict=True, validators=[_validator], field="f", help="h"
            ),
        ),
        (
            lambda: ui.path("p", start="s", default="d", must_exist=False, kind="dir", extensions=[".j"], help="h"),
            PathRequest(label="p", start="s", default="d", must_exist=False, kind="dir", extensions=[".j"], help="h"),
        ),
        (
            lambda: ui.form(_Model, title="T", initial=_Model(), field="f"),
            FormRequest(model=_Model, title="T", initial=_Model(), field="f"),
        ),
        (
            lambda: ui.field(_Model, "name", initial="y"),
            FieldRequest(model=_Model, field_name="name", initial="y"),
        ),
        (
            lambda: ui.table(["a"], [[1]], title="T", prompt="ok?", confirm_label="Y", cancel_label="N", field="f"),
            ReadOnlyTable(
                columns=["a"], rows=[[1]], title="T", prompt="ok?", confirm_label="Y", cancel_label="N", field="f"
            ),
        ),
    ],
)
def test_helpers_build_the_request_and_return_the_session_answer(call, expected):
    session = FakeSession(answer=Path("answer"))
    with ui.bind(session):
        assert call() == Path("answer")
    assert session.asked == [expected]


def test_acknowledge_builds_the_request_and_returns_nothing():
    session = FakeSession(answer="ignored")
    with ui.bind(session):
        assert ui.acknowledge("read", title="T", button_label="Got it", field="f") is None
    assert session.asked == [AcknowledgeRequest(message="read", title="T", button_label="Got it", field="f")]


def test_helper_defaults_match_the_request_defaults():
    session = FakeSession()
    with ui.bind(session):
        ui.confirm("c")
        ui.pick("p", ["a"])
        ui.path("p")
        ui.table(["a"], [])
        ui.acknowledge("m")
    assert session.asked == [
        ConfirmRequest(label="c"),
        PickRequest(label="p", options=["a"]),
        PathRequest(label="p"),
        ReadOnlyTable(columns=["a"], rows=[]),
        AcknowledgeRequest(message="m"),
    ]


def test_ask_without_a_session_raises():
    assert ui.current_session() is None
    with pytest.raises(ui.NoSessionError):
        ui.ask(TextRequest(label="name"))
    with pytest.raises(ui.NoSessionError):
        ui.confirm("sure?")


def test_ask_uses_the_bound_session():
    session = FakeSession(answer="mouse")
    with ui.bind(session):
        assert ui.current_session() is session
        assert ui.ask(TextRequest(label="name")) == "mouse"


def test_notify_and_header_reach_the_bound_session():
    session = FakeSession()
    with ui.bind(session):
        ui.notify("hello", MessageLevel.WARNING)
        ui.header("Title")
    assert session.notes == [("hello", MessageLevel.WARNING)]
    assert session.headers == ["Title"]


def test_notify_and_header_log_without_a_session(caplog):
    with caplog.at_level(logging.DEBUG, logger="clabe.ui"):
        ui.notify("careful", MessageLevel.WARNING)
        ui.notify("done", MessageLevel.SUCCESS)
        ui.header("Title")
    records = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    assert ("clabe.ui", logging.WARNING, "careful") in records
    assert ("clabe.ui", logging.INFO, "done") in records
    assert ("clabe.ui", logging.INFO, "Title") in records


def test_nested_bind_restores_the_previous_session():
    outer, inner = FakeSession(), FakeSession()
    with ui.bind(outer):
        with ui.bind(inner):
            assert ui.current_session() is inner
        assert ui.current_session() is outer
        with ui.bind(None):
            assert ui.current_session() is None
        assert ui.current_session() is outer
    assert ui.current_session() is None


def test_overlapping_binds_exiting_the_older_first_keep_the_newer():
    import contextlib

    a, r = FakeSession(), FakeSession()
    outer, inner = contextlib.ExitStack(), contextlib.ExitStack()
    outer.enter_context(ui.bind(a))  # e.g. the main thread
    inner.enter_context(ui.bind(r))  # e.g. a run, from its own thread
    outer.close()  # A's block ends first
    assert ui.current_session() is r
    inner.close()
    assert ui.current_session() is None  # A is not restored after its block ended


def test_overlapping_binds_exiting_the_newer_first_restore_the_older():
    import contextlib

    a, r = FakeSession(), FakeSession()
    outer, inner = contextlib.ExitStack(), contextlib.ExitStack()
    outer.enter_context(ui.bind(r))
    inner.enter_context(ui.bind(a))
    outer.close()  # R's binding is removed though it is not the latest
    assert ui.current_session() is a
    inner.close()
    assert ui.current_session() is None  # a finished R does not stay bound


def test_a_none_binding_means_no_session_while_it_is_the_latest():
    import contextlib

    a = FakeSession()
    with ui.bind(a):
        hidden = contextlib.ExitStack()
        hidden.enter_context(ui.bind(None))
        assert ui.current_session() is None
        with pytest.raises(ui.NoSessionError):
            ui.ask(TextRequest(label="x"))
        hidden.close()
        assert ui.current_session() is a


def test_bind_restores_on_error():
    session = FakeSession()
    with pytest.raises(RuntimeError), ui.bind(session):
        raise RuntimeError("boom")
    assert ui.current_session() is None


def test_bind_is_process_wide():
    import threading

    session, seen = FakeSession(), []
    with ui.bind(session):
        thread = threading.Thread(target=lambda: seen.append(ui.current_session()))
        thread.start()
        thread.join(5)
    assert seen == [session]


def test_activity_uses_the_shared_activity_indicator(monkeypatch):
    indicator = MagicMock()
    monkeypatch.setattr("clabe.runnable.get_activity_indicator", lambda: indicator)
    with ui.bind(FakeSession()):
        ui.activity("working")
    indicator.activity.assert_called_once_with("working")


def test_prompt_cancelled_error_is_the_same_class_everywhere():
    from clabe.ui._session import PromptCancelledError

    assert ui.PromptCancelledError is PromptCancelledError
