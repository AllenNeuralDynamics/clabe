import json
import os
from enum import Enum
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, Secret, SecretBytes, SecretStr

from clabe.ui import (
    AcknowledgeRequest,
    AnswerRejected,
    AutoCompleteRequest,
    Choice,
    ConfirmRequest,
    FieldRequest,
    FormRequest,
    NumberRequest,
    PathRequest,
    PickRequest,
    ReadOnlyTable,
    Request,
    TextRequest,
)


class Mode(Enum):
    A = "a"
    B = "b"


class Config(BaseModel):
    trials: int = Field(default=10, gt=0)
    mode: Mode = Mode.A
    kind: Literal["x", "y"] = "x"
    note: str | None = None


def test_every_request_type_has_a_kind():
    assert PickRequest(label="p", options=["a"]).prompt_kind == "pick"
    assert PathRequest(label="p").prompt_kind == "path"
    assert ConfirmRequest(label="p").prompt_kind == "confirm"
    assert TextRequest(label="p").prompt_kind == "text"
    assert AutoCompleteRequest(label="p", options=[]).prompt_kind == "autocomplete"
    assert NumberRequest(label="p").prompt_kind == "number"
    assert FormRequest(model=Config).prompt_kind == "form"
    assert ReadOnlyTable(columns=[], rows=[]).prompt_kind == "table"
    assert FieldRequest(model=Config, field_name="trials").prompt_kind == "field"
    assert AcknowledgeRequest(message="m").prompt_kind == "acknowledge"
    assert all(
        isinstance(r, Request)
        for r in (PickRequest(label="p", options=[]), AcknowledgeRequest(message="m"), ReadOnlyTable([], []))
    )


def test_path_request_keeps_its_kind_field_next_to_its_prompt_kind():
    request = PathRequest(label="p", kind="dir")
    assert (request.kind, request.prompt_kind) == ("dir", "path")


def test_choice_stays_positional():
    assert Choice("v", "Label").display == "Label"


def test_spec_is_json_safe_and_drops_callables():
    assert TextRequest(label="Name", validators=[lambda v: None]).spec() == {
        "label": "Name",
        "default": None,
        "multiline": False,
        "field": None,
    }
    pick = PickRequest(label="p", options=["a", Choice("b", "Bee")]).spec()
    assert pick["options"] == [{"value": "a", "label": "a"}, {"value": "b", "label": "Bee"}]
    form = FormRequest(model=Config, initial=Config(trials=3)).spec()
    assert form["model"] == "Config"
    assert form["initial"]["trials"] == 3
    json.dumps(form)


def test_answer_schemas():
    assert ConfirmRequest(label="?").answer_schema() == {"type": "boolean"}
    assert PickRequest(label="p", options=["a"], allow_none=False).answer_schema() == {"enum": ["a"]}
    assert PickRequest(label="p", options=["a"]).answer_schema() == {"enum": ["a", None]}
    assert AutoCompleteRequest(label="s", options=["a"], strict=True).answer_schema() == {
        "type": "string",
        "enum": ["a"],
    }
    assert FormRequest(model=Config).answer_schema()["anyOf"][0]["title"] == "Config"
    assert FieldRequest(model=Config, field_name="trials").answer_schema()["type"] == "integer"


@pytest.mark.parametrize(
    ("request_", "value", "expected"),
    [
        (PickRequest(label="p", options=["a", "b"]), "b", "b"),
        (PickRequest(label="p", options=["a"]), None, None),
        (ConfirmRequest(label="?"), False, False),
        (ReadOnlyTable(columns=["a"], rows=[]), True, True),
        (TextRequest(label="t", default="d"), "", "d"),
        (AutoCompleteRequest(label="a", options=["x"], strict=True), "x", "x"),
        (NumberRequest(label="n"), "2.5", 2.5),
        (NumberRequest(label="n", default=1.0), "", 1.0),
        (FormRequest(model=Config), {"trials": 3}, Config(trials=3)),
        (FormRequest(model=Config), None, None),
        (FieldRequest(model=Config, field_name="mode"), "b", Mode.B),
        (FieldRequest(model=Config, field_name="trials"), "7", 7),
        (FieldRequest(model=Config, field_name="trials"), "", 10),
        (FieldRequest(model=Config, field_name="trials", initial=4), "", 4),
        (FieldRequest(model=Config, field_name="note"), "", None),
        (AcknowledgeRequest(message="m"), None, None),
    ],
)
def test_valid_answers_are_coerced(request_, value, expected):
    assert request_.validate_answer(value) == expected


@pytest.mark.parametrize(
    ("request_", "value"),
    [
        (PickRequest(label="p", options=["a"]), "z"),
        (PickRequest(label="p", options=["a"], allow_none=False), None),
        (ConfirmRequest(label="?"), "yes"),
        (TextRequest(label="t", validators=[lambda v: "too short" if len(v) < 3 else None]), "ab"),
        (AutoCompleteRequest(label="a", options=["x"], strict=True), "y"),
        (NumberRequest(label="n"), "abc"),
        (FormRequest(model=Config), {"trials": 0}),
        (FieldRequest(model=Config, field_name="trials"), "-1"),
    ],
)
def test_invalid_answers_are_rejected(request_, value):
    with pytest.raises(AnswerRejected):
        request_.validate_answer(value)


def test_strict_autocomplete_rejects_unknown_options_before_running_validators():
    request = AutoCompleteRequest(label="a", options=["x"], strict=True, validators=[lambda v: "custom"])
    with pytest.raises(AnswerRejected, match="offered options"):
        request.validate_answer("y")


def test_path_answers_follow_the_path_rules(tmp_path):
    rig = tmp_path / "rig.json"
    rig.write_text("{}")
    request = PathRequest(label="p", extensions=[".json"])
    assert request.validate_answer(str(rig)) == rig
    assert request.validate_answer(None) is None
    assert request.validate_answer("") is None
    assert PathRequest(label="p", default=str(rig)).validate_answer("") == rig
    with pytest.raises(AnswerRejected, match="does not exist"):
        request.validate_answer(str(tmp_path / "nope.json"))


class Opaque:
    pass


class WithOpaque(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    thing: Opaque


class FakePath(os.PathLike):
    def __fspath__(self):
        return "C:/x"


def test_answer_schema_of_a_model_pydantic_cannot_describe_accepts_anything():
    request = FormRequest(model=WithOpaque)
    assert request.answer_schema() == {}
    assert FieldRequest(model=WithOpaque, field_name="thing").answer_schema() == {}
    answer = WithOpaque(thing=Opaque())
    assert request.validate_answer(answer) is answer


def test_spec_falls_back_to_fspath_base64_and_repr():
    assert PathRequest(label="p", start=FakePath()).spec()["start"] == "C:/x"
    table = ReadOnlyTable(columns=["a", "b"], rows=[[bytes([0xFF]), Opaque()]]).spec()
    json.dumps(table)
    assert table["rows"][0][0] == "_w=="
    assert table["rows"][0][1].startswith("<")


# --- FieldRequest.resolve ------------------------------------------------------


class Fields(BaseModel):
    enabled: bool = True
    kind: Literal["x", 2] = 2
    mode: Mode = Mode.B
    folder: Path = Path("data")
    trials: int = Field(default=10, gt=0)
    note: str | None = None
    flag: bool | None = None
    titled: str = Field(default="v", title="Custom title")


def _resolve(field_name: str, **kwargs):
    return FieldRequest(model=Fields, field_name=field_name, **kwargs).resolve()


def test_resolving_a_bool_field_asks_a_confirm():
    request, convert = _resolve("enabled")
    assert request == ConfirmRequest(label="Enabled", default=True, field="enabled")
    assert convert(request.validate_answer(False)) is False
    assert _resolve("flag")[0].default is False


def test_resolving_a_literal_field_asks_a_strict_autocomplete_and_returns_the_literal_value():
    request, convert = _resolve("kind")
    assert isinstance(request, AutoCompleteRequest)
    assert (request.options, request.default, request.strict, request.field) == (["x", "2"], "2", True, "kind")
    assert convert(request.validate_answer("2")) == 2
    assert convert(request.validate_answer("x")) == "x"
    with pytest.raises(AnswerRejected):
        request.validate_answer("z")


def test_resolving_an_enum_field_asks_a_strict_autocomplete_on_member_names():
    request, convert = _resolve("mode")
    assert isinstance(request, AutoCompleteRequest)
    assert (request.options, request.default, request.strict) == (["A", "B"], "B", True)
    assert convert(request.validate_answer("A")) is Mode.A
    assert _resolve("mode", initial=Mode.A)[0].default == "A"


def test_resolving_a_path_field_asks_for_any_path_that_need_not_exist(tmp_path):
    request, convert = _resolve("folder")
    assert request == PathRequest(
        label="Folder", start="data", default="data", must_exist=False, kind="any", field="folder"
    )
    missing = tmp_path / "missing"
    assert convert(request.validate_answer(str(missing))) == missing


def test_resolving_a_constrained_int_field_asks_validated_text_and_returns_an_int():
    request, convert = _resolve("trials")
    assert isinstance(request, TextRequest)
    assert (request.label, request.default, request.field) == ("Trials", "10", "trials")
    assert convert(request.validate_answer("7")) == 7
    assert convert(request.validate_answer("")) == 10
    with pytest.raises(AnswerRejected, match="greater than 0"):
        request.validate_answer("0")
    with pytest.raises(AnswerRejected):
        request.validate_answer("abc")


def test_resolving_an_optional_text_field_returns_none_for_an_empty_answer():
    request, convert = _resolve("note")
    assert isinstance(request, TextRequest) and request.default is None
    assert convert(request.validate_answer("")) is None
    assert convert(request.validate_answer("hi")) == "hi"


def test_resolving_uses_the_field_title_as_label():
    assert _resolve("titled")[0].label == "Custom title"


class Secrets(BaseModel):
    token: SecretStr = SecretStr("hidden")
    key: SecretBytes | None = None
    pin: Secret[int] = Secret[int](1234)
    numbers: list[int] = [1, 2]
    plain: str = "p"


@pytest.mark.parametrize(("field_name", "answer"), [("token", "t"), ("key", "k"), ("pin", "7")])
def test_resolving_a_secret_field_converts_to_the_secret_type(field_name, answer):
    request, convert = FieldRequest(model=Secrets, field_name=field_name).resolve()
    value = convert(request.validate_answer(answer))
    assert isinstance(value, (SecretStr, SecretBytes, Secret))


def test_an_empty_answer_on_the_text_route_returns_the_real_default():
    request, convert = FieldRequest(model=Secrets, field_name="numbers").resolve()
    assert convert(request.validate_answer("")) == [1, 2]

    request, convert = FieldRequest(model=Secrets, field_name="token").resolve()
    secret = convert(request.validate_answer(""))
    assert isinstance(secret, SecretStr) and secret.get_secret_value() == "hidden"
    assert convert(request.validate_answer("new")).get_secret_value() == "new"


class TestAutoCompleteOptionsFunction:
    @staticmethod
    def _search(typed: str) -> list[str]:
        if typed == "boom":
            raise RuntimeError("down")
        return [name for name in ("mouse-7", "mouse-8", "rat-1") if typed in name]

    def test_spec_lists_the_function_result_for_empty_text_and_says_it_is_dynamic(self):
        spec = AutoCompleteRequest(label="Subject", options=self._search).spec()
        assert spec["options"] == ["mouse-7", "mouse-8", "rat-1"] and spec["dynamic_options"] is True
        assert AutoCompleteRequest(label="Subject", options=["a"]).spec()["dynamic_options"] is False

    def test_suggestions_call_the_function_with_the_typed_text(self):
        request = AutoCompleteRequest(label="Subject", options=self._search)
        assert request.dynamic and request.suggestions("mouse") == ["mouse-7", "mouse-8"]
        assert AutoCompleteRequest(label="Subject", options=["a", "b"]).suggestions("zzz") == ["a", "b"]

    def test_strict_accepts_what_the_function_returns_for_the_value(self):
        request = AutoCompleteRequest(label="Subject", options=self._search, strict=True)
        assert request.answer_schema() == {"type": "string"}
        assert request.validate_answer("rat-1") == "rat-1"
        with pytest.raises(AnswerRejected):
            request.validate_answer("dog")
        with pytest.raises(AnswerRejected):
            request.validate_answer("boom")  # a failing function accepts nothing

    def test_a_failing_function_still_opens_the_prompt(self):
        def broken(typed: str) -> list[str]:
            raise RuntimeError("down")

        assert AutoCompleteRequest(label="Subject", options=broken).spec()["options"] == []
