"""Answer validation shared by every frontend and by the run (server-side)."""

import types
import typing
from collections.abc import Sequence
from pathlib import Path
from typing import Any, get_args, get_origin

from pydantic_core import PydanticUndefined

from ._requests import PathRequest, Validator


def first_error(validators: Sequence[Validator], value: str) -> str | None:
    """Returns the first validation error for ``value``, or ``None`` when every validator passes."""
    for validator in validators:
        error = validator(value)
        if error is not None:
            return error
    return None


def validate_path(request: PathRequest, path: Path) -> str | None:
    """Returns an error message when ``path`` violates the request's constraints, else ``None``."""
    if request.must_exist and not path.exists():
        return f"Path does not exist: {path}"
    if request.kind == "file" and path.exists() and not path.is_file():
        return f"Expected a file, got a directory: {path}"
    if request.kind == "dir" and path.exists() and not path.is_dir():
        return f"Expected a directory, got a file: {path}"
    exts = request.normalized_extensions()
    if request.kind != "dir" and exts is not None and path.suffix.lower() not in exts:
        return f"Expected a file with extension {', '.join(sorted(exts))}"
    return None


def resolve_field_type(annotation: Any) -> tuple[Any, bool]:
    """Strips ``Annotated`` and optional (``Optional[X]`` / ``X | None``) wrappers.

    Args:
        annotation: A model field's type annotation.

    Returns:
        tuple[Any, bool]: The inner type and whether the annotation allowed ``None``.
    """
    if get_origin(annotation) is typing.Annotated:
        annotation = get_args(annotation)[0]
    if get_origin(annotation) in (typing.Union, types.UnionType):
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0], True
    return annotation, False


def resolve_field_default(field_info: Any, initial: Any) -> Any:
    """Returns the value a field prompt starts from.

    Args:
        field_info: The pydantic ``FieldInfo`` of the field.
        initial: An explicit starting value; wins when not ``None``.

    Returns:
        Any: ``initial``, else the field's declared default, else its default factory's value, else ``None``.
    """
    if initial is not None:
        return initial
    raw = getattr(field_info, "default", PydanticUndefined)
    if raw is not PydanticUndefined:
        return raw
    factory = getattr(field_info, "default_factory", None)
    return factory() if factory is not None else None
