from typing import Annotated, Optional

from pydantic import BaseModel, Field

from clabe.ui._requests import PathRequest
from clabe.ui._validation import first_error, resolve_field_default, resolve_field_type, validate_path


def test_first_error_returns_the_first_failure():
    validators = [lambda v: None, lambda v: "bad" if v == "x" else None, lambda v: "worse"]
    assert first_error(validators, "x") == "bad"
    assert first_error([], "x") is None


def test_validate_path_checks_existence_kind_and_extension(tmp_path):
    rig = tmp_path / "rig.json"
    rig.write_text("{}")
    assert validate_path(PathRequest(label="p"), rig) is None
    assert "does not exist" in validate_path(PathRequest(label="p"), tmp_path / "missing.json")
    assert "Expected a file" in validate_path(PathRequest(label="p", kind="file"), tmp_path)
    assert "Expected a directory" in validate_path(PathRequest(label="p", kind="dir"), rig)
    assert "extension" in validate_path(PathRequest(label="p", extensions=["csv"]), rig)


def test_resolve_field_type_strips_annotated_and_optional():
    assert resolve_field_type(str | None) == (str, True)
    assert resolve_field_type(Optional[str]) == (str, True)  # noqa: UP045 -- the typing.Optional spelling
    assert resolve_field_type(Annotated[int, "meta"]) == (int, False)
    assert resolve_field_type(str) == (str, False)


def test_resolve_field_default_prefers_initial_then_default_then_factory():
    class Model(BaseModel):
        plain: int = 3
        listed: list[int] = Field(default_factory=lambda: [1])
        required: int

    fields = Model.model_fields
    assert resolve_field_default(fields["plain"], 7) == 7
    assert resolve_field_default(fields["plain"], None) == 3
    assert resolve_field_default(fields["listed"], None) == [1]
    assert resolve_field_default(fields["required"], None) is None
