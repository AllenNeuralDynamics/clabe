from enum import Enum
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from clabe import ui
from clabe.ui import (
    AcknowledgeRequest,
    AutoCompleteRequest,
    Client,
    ClientSession,
    ConfirmRequest,
    FieldRequest,
    MessageLevel,
    NumberRequest,
    PathRequest,
    PickRequest,
    PromptCancelledError,
    TextRequest,
    make_client,
)
from tests.conftest import MockClient, MockFrontend


class Color(Enum):
    RED = 1
    BLUE = 2


class Model(BaseModel):
    count: int = Field(gt=0)
    enabled: bool = False
    color: Color = Color.RED
    where: Path = Path("data")


# --- ClientSession --------------------------------------------------------


def test_a_client_session_is_a_session():
    assert isinstance(ClientSession(MockClient()), ui.Session)


def test_the_test_clients_are_clients():
    assert isinstance(MockClient(), Client)
    assert isinstance(MockFrontend(), Client)


def test_ask_returns_the_validated_answer():
    client = MockClient({"text": [""]})
    session = ClientSession(client)

    assert session.ask(TextRequest(label="name", default="mouse")) == "mouse"
    assert client.asked == [TextRequest(label="name", default="mouse")]


def test_a_rejected_answer_is_reported_and_asked_again():
    client = MockClient({"pick": ["zzz", "b"]})
    session = ClientSession(client)

    assert session.ask(PickRequest(label="p", options=["a", "b"], allow_none=False)) == "b"
    assert len(client.asked) == 2
    assert client.notifications == [("'zzz' is not one of the options.", MessageLevel.ERROR)]


def test_validators_run_locally():
    client = MockClient({"text": ["bad", "good"]})
    session = ClientSession(client)
    request = TextRequest(label="x", validators=[lambda v: "no!" if v == "bad" else None])

    assert session.ask(request) == "good"
    assert client.notifications == [("no!", MessageLevel.ERROR)]


def test_numbers_are_coerced():
    assert ClientSession(MockClient({"number": ["1.5"]})).ask(NumberRequest(label="n")) == 1.5


def test_paths_are_validated_and_returned_as_paths(tmp_path):
    missing, existing = tmp_path / "missing.txt", tmp_path / "there.txt"
    existing.write_text("")
    client = MockClient({"path": [str(missing), str(existing)]})

    assert ClientSession(client).ask(PathRequest(label="p")) == existing
    assert [level for _, level in client.notifications] == [MessageLevel.ERROR]


def test_acknowledge_returns_none():
    assert ClientSession(MockClient()).ask(AcknowledgeRequest(message="read me")) is None


def test_field_requests_are_resolved_before_asking():
    client = MockClient({"text": ["0", "3"], "confirm": [True], "autocomplete": ["BLUE"], "path": [""]})
    session = ClientSession(client)

    assert session.ask(FieldRequest(model=Model, field_name="count")) == 3
    assert session.ask(FieldRequest(model=Model, field_name="enabled")) is True
    assert session.ask(FieldRequest(model=Model, field_name="color")) is Color.BLUE
    assert session.ask(FieldRequest(model=Model, field_name="where")) == Path("data")
    assert not any(isinstance(request, FieldRequest) for request in client.asked)
    assert [type(r) for r in client.asked] == [
        TextRequest,
        TextRequest,
        ConfirmRequest,
        AutoCompleteRequest,
        PathRequest,
    ]
    assert [level for _, level in client.notifications] == [MessageLevel.ERROR]  # "0" is not > 0


def test_a_cancelled_prompt_propagates():
    session = ClientSession(MockClient({"text": PromptCancelledError}))

    with pytest.raises(PromptCancelledError):
        session.ask(TextRequest(label="x"))


def test_notify_and_header_go_to_the_client():
    client = MockClient()
    session = ClientSession(client)

    session.notify("hello", MessageLevel.WARNING)
    session.header("Title")

    assert client.notifications == [("hello", MessageLevel.WARNING)]
    assert client.headers == ["Title"]
    assert session.client is client


def test_bound_client_session_answers_ui_ask():
    client = MockClient({"confirm": [False]})

    with ui.bind(ClientSession(client)):
        assert ui.confirm("sure?") is False
        ui.notify("hi")

    assert client.notifications == [("hi", MessageLevel.INFO)]


# --- make_client ------------------------------------------------------------


def test_make_client_builds_a_console_client():
    from clabe.ui import ConsoleClient

    client = make_client("console")
    assert isinstance(client, Client)
    assert isinstance(client, ConsoleClient)


def test_make_client_rejects_unknown_backends():
    with pytest.raises(ValueError):
        make_client("nope")
