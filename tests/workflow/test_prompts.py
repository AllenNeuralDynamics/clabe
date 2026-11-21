import json
import os
from enum import Enum
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from clabe.ui import (
    AcknowledgeRequest,
    AutoCompleteRequest,
    Choice,
    ConfirmRequest,
    FieldRequest,
    FormRequest,
    NumberRequest,
    PathRequest,
    PickRequest,
    ReadOnlyTable,
    TextRequest,
)
from clabe.workflow._prompts import AnswerRejected, answer_schema, prompt_kind, prompt_spec, validate_answer


class Mode(Enum):
    A = "a"
    B = "b"


class Config(BaseModel):
    trials: int = Field(default=10, gt=0)
    mode: Mode = Mode.A
    kind: Literal["x", "y"] = "x"
    note: str | None = None


def test_every_request_type_has_a_kind():
    assert prompt_kind(PickRequest(label="p", options=["a"])) == "pick"
    assert prompt_kind(PathRequest(label="p")) == "path"
    assert prompt_kind(ConfirmRequest(label="p")) == "confirm"
    assert prompt_kind(TextRequest(label="p")) == "text"
    assert prompt_kind(AutoCompleteRequest(label="p", options=[])) == "autocomplete"
    assert prompt_kind(NumberRequest(label="p")) == "number"
    assert prompt_kind(FormRequest(model=Config)) == "form"
    assert prompt_kind(ReadOnlyTable(columns=[], rows=[])) == "table"
    assert prompt_kind(FieldRequest(model=Config, field_name="trials")) == "field"
    assert prompt_kind(AcknowledgeRequest(message="m")) == "acknowledge"
    with pytest.raises(TypeError):
        prompt_kind(object())


def test_spec_is_json_safe_and_drops_callables():
    assert prompt_spec(TextRequest(label="Name", validators=[lambda v: None])) == {
        "label": "Name",
        "default": None,
        "multiline": False,
        "field": None,
    }
    pick = prompt_spec(PickRequest(label="p", options=["a", Choice("b", "Bee")]))
    assert pick["options"] == [{"value": "a", "label": "a"}, {"value": "b", "label": "Bee"}]
    form = prompt_spec(FormRequest(model=Config, initial=Config(trials=3)))
    assert form["model"] == "Config"
    assert form["initial"]["trials"] == 3
    json.dumps(form)


def test_answer_schemas():
    assert answer_schema(ConfirmRequest(label="?")) == {"type": "boolean"}
    assert answer_schema(PickRequest(label="p", options=["a"], allow_none=False)) == {"enum": ["a"]}
    assert answer_schema(PickRequest(label="p", options=["a"])) == {"enum": ["a", None]}
    assert answer_schema(AutoCompleteRequest(label="s", options=["a"], strict=True)) == {
        "type": "string",
        "enum": ["a"],
    }
    assert answer_schema(FormRequest(model=Config))["anyOf"][0]["title"] == "Config"
    assert answer_schema(FieldRequest(model=Config, field_name="trials"))["type"] == "integer"


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
    assert validate_answer(request_, value) == expected


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
        validate_answer(request_, value)


def test_strict_autocomplete_rejects_unknown_options_before_running_validators():
    request = AutoCompleteRequest(label="a", options=["x"], strict=True, validators=[lambda v: "custom"])
    with pytest.raises(AnswerRejected, match="offered options"):
        validate_answer(request, "y")


def test_path_answers_follow_the_path_rules(tmp_path):
    rig = tmp_path / "rig.json"
    rig.write_text("{}")
    request = PathRequest(label="p", extensions=[".json"])
    assert validate_answer(request, str(rig)) == rig
    assert validate_answer(request, None) is None
    assert validate_answer(request, "") is None
    assert validate_answer(PathRequest(label="p", default=str(rig)), "") == rig
    with pytest.raises(AnswerRejected, match="does not exist"):
        validate_answer(request, str(tmp_path / "nope.json"))


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
    assert answer_schema(request) == {}
    assert answer_schema(FieldRequest(model=WithOpaque, field_name="thing")) == {}
    answer = WithOpaque(thing=Opaque())
    assert validate_answer(request, answer) is answer


def test_spec_falls_back_to_fspath_base64_and_repr():
    assert prompt_spec(PathRequest(label="p", start=FakePath()))["start"] == "C:/x"
    table = prompt_spec(ReadOnlyTable(columns=["a", "b"], rows=[[bytes([0xFF]), Opaque()]]))
    json.dumps(table)
    assert table["rows"][0][0] == "_w=="
    assert table["rows"][0][1].startswith("<")
