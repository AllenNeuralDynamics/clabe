"""Recording what each command an app runs was, for provenance (``command_finished`` events in a run)."""

import datetime
import hashlib
import logging
import os
import shutil
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._base import Command

logger = logging.getLogger(__name__)

#: Files larger than this are listed without a hash (bulk data is not provenance).
MAX_HASHED_BYTES = 64 * 1024 * 1024

#: Configuration files that sit next to an executable and change what it does.
_SIBLING_CONFIGS = ("Bonsai.config",)

_hash_cache: dict[tuple[str, int, int], str] = {}
_hash_lock = threading.Lock()


def file_sha256(path: Path) -> str | None:
    """The SHA-256 of a file (cached by path, size and modification time); ``None`` when too large or unreadable."""
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size > MAX_HASHED_BYTES:
        return None
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    with _hash_lock:
        if key in _hash_cache:
            return _hash_cache[key]
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    with _hash_lock:
        _hash_cache[key] = digest.hexdigest()
    return _hash_cache[key]


def _candidate_paths(command: "Command[Any]") -> Iterator[Path]:
    """Files a command names: its arguments (also ``name=path`` values), its inputs, configs next to the program."""
    for arg in command.cmd[1:]:
        for text in (arg, arg.partition("=")[2]):
            if text and len(text) < 1024:
                yield Path(text)
    yield from command.inputs.values()
    program = _resolve(command.cmd[0]) if command.cmd else None
    if program is not None:
        yield from (program.parent / name for name in _SIBLING_CONFIGS)


def _resolve(program: str) -> Path | None:
    """The program's file, found as given or on the PATH."""
    path = Path(program)
    if path.is_file():
        return path.resolve()
    found = shutil.which(program)
    return Path(found).resolve() if found else None


def describe(command: "Command[Any]") -> dict[str, Any]:
    """The program and the files of a command, with their hashes."""
    program = _resolve(command.cmd[0]) if command.cmd else None
    files: dict[str, str | None] = {}
    for path in _candidate_paths(command):
        try:
            if path.is_file():
                files.setdefault(str(path.resolve()), file_sha256(path))
        except OSError:
            continue
    if program is not None:
        files.pop(str(program), None)
    return {
        "executable": str(program) if program is not None else (command.cmd[0] if command.cmd else ""),
        "executable_sha256": file_sha256(program) if program is not None else None,
        "files": files,
    }


@contextmanager
def recorded(command: "Command[Any]", executor: Any) -> Iterator[None]:
    """Records the command as a ``command_finished`` event of the running step, however it ends."""
    from ..workflow._records import ACTIVE_STEP

    context = ACTIVE_STEP.get()
    started = time.monotonic()
    started_at = datetime.datetime.now(datetime.UTC)
    error: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        error = exc
        raise
    finally:
        if context is not None:
            try:
                _record(context, command, executor, time.monotonic() - started, error, started_at)
            except Exception:
                logger.warning("Could not record the provenance of %s", command.cmd[:1], exc_info=True)


def _record(
    context: Any,
    command: "Command[Any]",
    executor: Any,
    duration: float,
    error: BaseException | None,
    started_at: datetime.datetime,
) -> None:
    """Appends the event."""
    from ..launcher._manifest import _mask_argv
    from ..workflow.events import CommandFinished

    result = command._result
    exit_code = result.exit_code if result is not None else getattr(error, "exit_code", None)
    times = (result.started_at, result.ended_at) if result is not None and result.started_at else (None, None)
    context.run.events.append(
        CommandFinished(
            step=context.step.name,
            attempt=context.attempt,
            command=_mask_argv([str(a) for a in command.cmd]),
            cwd=str(getattr(executor, "cwd", None) or os.getcwd()),
            machine=getattr(executor, "host", None),
            exit_code=exit_code,
            duration_s=round(duration, 3),
            error=None if error is None else f"{type(error).__name__}: {error}",
            started_at=times[0] or started_at,
            ended_at=times[1] or datetime.datetime.now(datetime.UTC),
            **describe(command),
        )
    )
