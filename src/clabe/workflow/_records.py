"""Recording step outputs and artifacts, for reproducibility."""

import contextvars
import dataclasses
import datetime
import hashlib
import json
import os
import shutil
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from . import codecs

if TYPE_CHECKING:
    from ._run import Run
    from ._workflow import Step


@dataclasses.dataclass(frozen=True)
class _StepContext:
    """The step attempt a piece of code runs for."""

    run: "Run"
    step: "Step"
    attempt: int


#: The step attempt executing in the current context (set by the run around each step call and its codec).
ACTIVE_STEP: contextvars.ContextVar[_StepContext | None] = contextvars.ContextVar("clabe_active_step", default=None)


def _canonical_json(value: Any) -> bytes:
    """``value`` as canonical JSON (sorted keys, no whitespace, no NaN), UTF-8 encoded; raises if it has none."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return text.encode("utf-8")


@dataclasses.dataclass(frozen=True)
class Encoded:
    """A step output as recorded.

    Attributes:
        codec: Name of the codec that encoded it.
        output: The encoded (JSON-able) output; ``None`` when lossy or omitted.
        sha256: Hash of the canonical JSON of ``output`` (of ``repr`` when lossy); ``None`` when omitted.
        lossy: The codec failed, or its result has no canonical JSON form; ``repr`` stands in for the output.
        repr: Truncated ``repr`` of the output when lossy, else ``None``.
        error: Why encoding failed, else ``None``.
    """

    codec: str
    output: Any
    sha256: str | None
    lossy: bool = False
    repr: str | None = None
    error: str | None = None


def _safe_text(value: Any) -> str:
    """:func:`codecs.text` of ``value`` (always UTF-8 encodable), or its type name if even ``repr`` fails."""
    try:
        text = codecs.text(value)
    except Exception:  # noqa: BLE001 -- the last-resort rendering must not fail
        return f"<{type(value).__name__}>"
    return text.encode("utf-8", errors="backslashreplace").decode("utf-8")


def encode_output(value: Any, codec: codecs.Codec | None) -> Encoded:
    """Encodes a step output with ``codec`` (default :func:`codecs.auto`); any failure falls back to ``repr``.

    The output is serialized once, canonically, to check it has a JSON form and to hash it. Never raises.
    """
    codec = codecs.auto if codec is None else codec
    try:
        name = codecs.codec_name(codec)
    except Exception:  # noqa: BLE001 -- a codec with a broken name must not fail its step
        name = "?"
    try:
        output = codec(value)
        if output is codecs.OMIT:
            return Encoded(name, None, None)
        return Encoded(name, output, hashlib.sha256(_canonical_json(output)).hexdigest())
    except Exception as exc:  # noqa: BLE001 -- recording never fails a step
        rendered = _safe_text(value)
        sha256 = hashlib.sha256(_canonical_json(rendered)).hexdigest()  # safe: an encodable str
        return Encoded(name, None, sha256, lossy=True, repr=rendered, error=f"{type(exc).__name__}: {exc}")


def output_line(
    *, run_id: str, step: str, index: int, attempt: int, inputs: dict[str, int], encoded: Encoded, artifacts: list[str]
) -> dict[str, Any]:
    """Builds one ``outputs.jsonl`` line."""
    return {
        "ts": datetime.datetime.now(datetime.UTC).isoformat(),
        "run_id": run_id,
        "step": step,
        "index": index,
        "attempt": attempt,
        "inputs": inputs,
        "codec": encoded.codec,
        "output": encoded.output,
        "sha256": encoded.sha256,
        "lossy": encoded.lossy,
        "repr": encoded.repr,
        "artifacts": artifacts,
    }


def _check_name(name: str) -> PurePosixPath:
    """``name`` as a relative path, or ``ValueError`` if it could escape the step's artifacts folder."""
    path = PurePosixPath(name.replace("\\", "/"))
    if not name or path.is_absolute() or PureWindowsPath(name).anchor or ".." in path.parts or not path.parts:
        raise ValueError(f"Invalid artifact name {name!r}: it must be a relative path without '..'.")
    return path


def write_artifact(directory: Path, source: Any, name: str | None) -> PurePosixPath:
    """Writes ``source`` under ``directory`` and returns its name (relative to ``directory``)."""
    if isinstance(source, os.PathLike):
        source = Path(source)
        name = name or source.name
    elif name is None:
        raise ValueError("An artifact name is required unless the source is a Path.")
    relative = _check_name(name)
    destination = directory / relative
    if isinstance(source, BaseModel):
        source = source.model_dump_json(indent=2)
    elif not isinstance(source, (Path, bytes, str)):
        raise TypeError(f"Cannot export a {type(source).__name__}; pass a Path, bytes, str or pydantic model.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:  # exclusive creation: of two exports of one name, even concurrent ones, the second fails
        if isinstance(source, Path):
            if source.is_dir():
                shutil.copytree(source, destination)  # refuses an existing destination
            else:
                with source.open("rb") as reader, destination.open("xb") as writer:
                    shutil.copyfileobj(reader, writer)
                shutil.copystat(source, destination)
        elif isinstance(source, bytes):
            with destination.open("xb") as writer:
                writer.write(source)
        else:
            with destination.open("x", encoding="utf-8") as writer:
                writer.write(source)
    except FileExistsError:
        raise FileExistsError(f"Artifact {relative} was already exported by this step attempt.") from None
    return relative


def export_artifact(source: "os.PathLike[str] | bytes | str | BaseModel", name: str | None = None) -> Path:
    """Saves a file into the current step's artifacts and records it with the step's output.

    Only valid inside a running step (including code the step calls on the same thread or in its tasks, and the
    step's codec). Code on a new ``threading.Thread`` does not see the step; ``asyncio.to_thread`` does.
    Artifacts land in ``<record_dir>/artifacts/<step>/<attempt>/<name>``.

    Args:
        source: A ``Path`` to copy (file or directory), ``bytes``, ``str`` (text content — use a ``Path`` to copy
            a file) or a pydantic model (saved as JSON).
        name: Name under the step's artifacts, may include sub-folders. Defaults to the file name of a ``Path``.

    Returns:
        The absolute path of the saved artifact.

    Raises:
        RuntimeError: Called outside a running step, or the run keeps no records.
        ValueError: Missing or unsafe ``name``.
        FileExistsError: ``name`` was already exported by this step attempt.
        TypeError: ``source`` is of an unsupported type.
    """
    context = ACTIVE_STEP.get()
    if context is None:
        raise RuntimeError("export_artifact() can only be called from inside a running step.")
    return context.run._export_artifact(context.step, context.attempt, source, name)
