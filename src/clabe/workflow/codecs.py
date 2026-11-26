"""Codecs: how a step's output is recorded in ``outputs.jsonl``.

A codec is any callable ``value -> JSON-able``; pass it as ``@wf.step(codec=...)``. It runs inside the step's
context, so it may call [`export_artifact`][clabe.workflow.export_artifact] and return the artifact's path.
"""

from collections.abc import Callable
from typing import Any

import pydantic_core
from pydantic import BaseModel

#: A codec turns a step output into something JSON-serializable.
Codec = Callable[[Any], Any]

_TEXT_LIMIT = 10_000


class _Omit:
    """Marker returned by `omit`: record no value."""

    def __repr__(self) -> str:
        return "OMIT"


#: Returned by `omit`: the output line records no value.
OMIT = _Omit()


def model_dump(value: BaseModel) -> Any:
    """A pydantic model's own JSON serialization (``model_dump(mode="json")``)."""
    return value.model_dump(mode="json")


def jsonable(value: Any) -> Any:
    """Pydantic's generic JSON serialization of any value."""
    return pydantic_core.to_jsonable_python(value)


def auto(value: Any) -> Any:
    """The default: `model_dump` for pydantic models, `jsonable` for anything else."""
    return model_dump(value) if isinstance(value, BaseModel) else jsonable(value)


def text(value: Any) -> str:
    """``repr(value)``, truncated to 10 000 characters."""
    rendered = repr(value)
    return rendered if len(rendered) <= _TEXT_LIMIT else rendered[: _TEXT_LIMIT - 1] + "…"


def omit(value: Any) -> _Omit:
    """Records no value (credentials, huge objects); lineage and artifacts are still recorded."""
    return OMIT


_NATIVE: tuple[tuple[Codec, str], ...] = (
    (auto, "auto"),
    (model_dump, "model_dump"),
    (jsonable, "jsonable"),
    (text, "text"),
    (omit, "omit"),
)


def codec_name(codec: Codec) -> str:
    """The name recorded for ``codec``: its native name, or ``module.qualname`` for a custom callable.

    Callables without a ``__qualname__`` (e.g. ``functools.partial``) are named after their type, which is stable
    across runs.
    """
    for native, name in _NATIVE:
        if codec is native:
            return name
    qualname = getattr(codec, "__qualname__", None)
    if not isinstance(qualname, str):
        return f"{type(codec).__module__}.{type(codec).__qualname__}"
    return f"{getattr(codec, '__module__', None) or type(codec).__module__}.{qualname}"
