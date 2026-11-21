"""Turning prompt requests into run prompts: kind, JSON-safe spec, answer schema and answer validation."""

import dataclasses
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import pydantic_core
from pydantic import PydanticInvalidForJsonSchema, PydanticSchemaGenerationError, TypeAdapter, ValidationError

from ..ui import (
    AcknowledgeRequest,
    AutoCompleteRequest,
    ConfirmRequest,
    FieldRequest,
    FormRequest,
    NumberRequest,
    PathRequest,
    PickRequest,
    ReadOnlyTable,
    TextRequest,
)
from ..ui._validation import first_error, resolve_field_default, resolve_field_type, validate_path

logger = logging.getLogger(__name__)

_KINDS: dict[type, str] = {
    PickRequest: "pick",
    PathRequest: "path",
    ConfirmRequest: "confirm",
    TextRequest: "text",
    AutoCompleteRequest: "autocomplete",
    NumberRequest: "number",
    FormRequest: "form",
    ReadOnlyTable: "table",
    FieldRequest: "field",
    AcknowledgeRequest: "acknowledge",
}

#: The ``Frontend`` method that asks each kind of prompt.
FRONTEND_METHODS: dict[str, str] = {
    "pick": "prompt_pick",
    "path": "prompt_path",
    "confirm": "prompt_confirm",
    "text": "prompt_text",
    "autocomplete": "prompt_autocomplete",
    "number": "prompt_number",
    "form": "prompt_form",
    "table": "prompt_read_only_table",
    "field": "prompt_field",
    "acknowledge": "prompt_acknowledge",
}


class AnswerRejected(ValueError):
    """An answer does not satisfy its prompt; the message is shown to whoever answered."""


def prompt_kind(request: Any) -> str:
    """Returns the prompt kind of a request object (e.g. ``"pick"`` for a :class:`PickRequest`)."""
    try:
        return _KINDS[type(request)]
    except KeyError:
        raise TypeError(f"Unsupported prompt request: {type(request).__name__}") from None


def _jsonable_fallback(value: Any) -> str:
    """Renders a value pydantic cannot serialize: paths as their filesystem string, anything else as its repr."""
    return os.fspath(value) if isinstance(value, os.PathLike) else repr(value)


def prompt_spec(request: Any) -> dict[str, Any]:
    """Returns a JSON-safe description of what to show for ``request`` (callables dropped, models named)."""
    kind = prompt_kind(request)
    spec = {
        f.name: getattr(request, f.name) for f in dataclasses.fields(request) if f.name not in ("validators", "model")
    }
    if kind in ("form", "field"):
        spec["model"] = request.model.__name__
    if kind == "pick":
        spec["options"] = [{"value": c.value, "label": c.display} for c in request.choices()]
    return pydantic_core.to_jsonable_python(spec, fallback=_jsonable_fallback, bytes_mode="base64")


def _field_adapter(request: FieldRequest) -> TypeAdapter:
    """Builds a validator for the requested model field, including its constraints (e.g. ``gt=0``)."""
    info = request.model.model_fields[request.field_name]
    annotation = Annotated[(info.annotation, *info.metadata)] if info.metadata else info.annotation
    return TypeAdapter(annotation)


def answer_schema(request: Any) -> dict[str, Any]:
    """Returns the JSON Schema a valid answer to ``request`` must satisfy.

    Models pydantic cannot describe (e.g. arbitrary-type fields) get ``{}`` (any answer); answers are still
    validated by :func:`validate_answer`.
    """
    kind = prompt_kind(request)
    if kind in ("form", "field"):
        try:
            return _model_schema(request)
        except (PydanticInvalidForJsonSchema, PydanticSchemaGenerationError) as exc:
            logger.debug("No answer schema for %s prompt on %s: %s", kind, request.model.__name__, exc)
            return {}
    if kind == "pick":
        values: list[Any] = [c.value for c in request.choices()]
        return {"enum": values + [None] if request.allow_none else values}
    if kind in ("confirm", "table"):
        return {"type": "boolean"}
    if kind == "text":
        return {"type": "string"}
    if kind == "autocomplete":
        return {"type": "string", "enum": request.suggestions()} if request.strict else {"type": "string"}
    if kind == "number":
        return {"type": "number"}
    if kind == "path":
        return {"type": ["string", "null"]}
    return {"type": "null"}


def _model_schema(request: FormRequest | FieldRequest) -> dict[str, Any]:
    """Builds the answer schema of a form or field prompt; raises pydantic's schema errors."""
    if isinstance(request, FormRequest):
        return {"anyOf": [request.model.model_json_schema(), {"type": "null"}]}
    return _field_adapter(request).json_schema()


def _message(exc: ValidationError) -> str:
    """Formats the first pydantic error as ``location: message``."""
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"])
    return f"{location}: {error['msg']}" if location else error["msg"]


def _pick(request: PickRequest, value: Any) -> Any:
    """Accepts one of the option values, or ``None`` when the request allows it."""
    if value is None:
        if request.allow_none:
            return None
        raise AnswerRejected("Please choose one of the options.")
    if value not in [c.value for c in request.choices()]:
        raise AnswerRejected(f"{value!r} is not one of the options.")
    return value


def _boolean(request: Any, value: Any) -> bool:
    """Accepts a strict boolean."""
    if not isinstance(value, bool):
        raise AnswerRejected("Expected yes or no.")
    return value


def _text(request: TextRequest | AutoCompleteRequest, value: Any) -> str:
    """Accepts a string, falling back to the default when empty, then runs the validators."""
    if not isinstance(value, str):
        raise AnswerRejected("Expected text.")
    if value == "" and request.default is not None:
        value = request.default
    error = first_error(request.validators, value)
    if error is not None:
        raise AnswerRejected(error)
    return value


def _autocomplete(request: AutoCompleteRequest, value: Any) -> str:
    """Like :func:`_text`, but a strict request only accepts one of its suggestions (checked first)."""
    if not isinstance(value, str):
        raise AnswerRejected("Expected text.")
    if value == "" and request.default is not None:
        value = request.default
    if request.strict and value not in request.suggestions():
        raise AnswerRejected("Please choose one of the offered options.")
    return _text(request, value)


def _number(request: NumberRequest, value: Any) -> float:
    """Accepts a number or numeric text; an empty answer falls back to the default."""
    if value in ("", None) and request.default is not None:
        return request.default
    if isinstance(value, bool):
        raise AnswerRejected("Please enter a valid number.")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise AnswerRejected("Please enter a valid number.") from None


def _path(request: PathRequest, value: Any) -> Path | None:
    """Accepts ``None`` (cancelled) or a path satisfying the request; an empty answer falls back to the default."""
    if value == "":
        value = request.default
    if value is None:
        return None
    path = Path(value)
    error = validate_path(request, path)
    if error is not None:
        raise AnswerRejected(error)
    return path


def _form(request: FormRequest, value: Any) -> Any:
    """Accepts ``None`` (cancelled), a model instance, or data that validates as the model."""
    if value is None or isinstance(value, request.model):
        return value
    try:
        return request.model.model_validate(value)
    except ValidationError as exc:
        raise AnswerRejected(_message(exc)) from None


def _field(request: FieldRequest, value: Any) -> Any:
    """Validates a single model field value; an empty answer falls back to the default, else ``None`` if optional."""
    if value == "":
        info = request.model.model_fields[request.field_name]
        default = resolve_field_default(info, request.initial)
        if default is not None:
            value = default
        elif resolve_field_type(info.annotation)[1]:
            value = None
    try:
        return _field_adapter(request).validate_python(value)
    except ValidationError as exc:
        raise AnswerRejected(_message(exc)) from None


_VALIDATORS: dict[str, Callable[[Any, Any], Any]] = {
    "pick": _pick,
    "confirm": _boolean,
    "table": _boolean,
    "text": _text,
    "autocomplete": _autocomplete,
    "number": _number,
    "path": _path,
    "form": _form,
    "field": _field,
    "acknowledge": lambda request, value: None,
}


def validate_answer(request: Any, value: Any) -> Any:
    """Validates ``value`` against ``request`` and returns it coerced to the type the step expects.

    Raises:
        AnswerRejected: The answer is not acceptable; the prompt should stay open.
    """
    return _VALIDATORS[prompt_kind(request)](request, value)
