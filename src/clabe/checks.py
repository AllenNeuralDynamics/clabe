"""Reusable checks: plain functions that raise, with a readable message, when something is wrong.

A check is any function returning nothing and raising when its condition does not hold. Like a step, it takes
what it needs by parameter name — ``rig``, ``session``, ``launcher`` or any other step's output — so the same
check runs inside a workflow and outside one:

    from clabe import checks, steps

    rig_ready = [
        checks.free_space(200e9),                          # on the rig's data drive
        checks.exists(r"\\\\allen\\aind\\scratch"),         # the share data is copied to
        checks.that(lambda rig: rig.cameras, "The rig has no cameras."),
    ]
    wf.step(steps.preflight(*rig_ready))                  # in a workflow: one step running them all

    checks.run(*rig_ready, rig=rig)                      # anywhere else

Every check runs, so one failure lists everything that is wrong (see `ChecksFailed`). Write your own as plain
functions and keep them in a module of your package, next to your steps, for others to import.
"""

import inspect
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .workflow import Abort, GoBack

__all__ = ["ChecksFailed", "exists", "free_space", "inputs", "run", "that"]

#: Where a check looks: a fixed path, or a function of the ``rig`` returning one.
PathSource = str | os.PathLike | Callable[[Any], str | os.PathLike]


class ChecksFailed(RuntimeError):
    """One or more checks failed. Its message lists each failure; ``failures`` holds ``(check name, error)``."""

    def __init__(self, failures: list[tuple[str, Exception]]) -> None:
        self.failures = failures
        if len(failures) == 1:
            message = str(failures[0][1])
        else:
            message = f"{len(failures)} checks failed:\n" + "\n".join(f"- {error}" for _, error in failures)
        super().__init__(message)


def free_space(minimum_bytes: float, *, directory: PathSource | None = None) -> Callable[..., None]:
    """Fails when the drive holding ``directory`` has less than ``minimum_bytes`` free.

    The directory need not exist yet: the drive is the one of its nearest existing parent, so mounted folders and
    network shares are measured where the data will actually go.

    Args:
        minimum_bytes: Free space required.
        directory: Where the data goes: a path, or a function of the ``rig``; by default ``rig.data_directory``.
    """

    def check(target: Path) -> None:
        existing = next((p for p in (target, *target.parents) if os.path.exists(p)), None)
        if existing is None:
            raise FileNotFoundError(f"Neither {target} nor any of its parents exist.")
        free = shutil.disk_usage(existing).free
        if free < minimum_bytes:
            raise RuntimeError(f"Only {free / 1e9:.1f} GB free on {existing}; {minimum_bytes / 1e9:.1f} GB needed.")

    return _on_path(directory, check, "free_space")


def exists(path: PathSource) -> Callable[..., None]:
    """Fails when ``path`` (a file, a directory or a network share) does not exist or cannot be reached.

    Args:
        path: The path, or a function of the ``rig`` returning it.
    """

    def check(target: Path) -> None:
        if not os.path.exists(target):
            raise FileNotFoundError(f"{target} does not exist or cannot be reached.")

    return _on_path(path, check, "exists")


def that(condition: Callable[..., Any], message: str, *, name: str | None = None) -> Callable[..., None]:
    """A check from a predicate: fails with ``message`` when ``condition`` returns something false.

    The check takes the same parameters as ``condition``, so ``that(lambda rig: rig.cameras, "...")`` gets the rig.

    Args:
        condition: The predicate.
        message: The failure message.
        name: The check's name; by default the predicate's.
    """

    def check(**inputs: Any) -> None:
        if not condition(**inputs):
            raise RuntimeError(message)

    check.__signature__ = inspect.signature(condition).replace(return_annotation=None)  # type: ignore[attr-defined]
    check_name = name or getattr(condition, "__name__", "that")
    check.__name__ = check.__qualname__ = "that" if check_name == "<lambda>" else check_name
    return check


def inputs(*checks: Callable[..., Any]) -> list[str]:
    """The names the checks take, in order and without repeats: what `run` (or a step running them) must be given.

    Parameters with a default are left to their default.
    """
    names: dict[str, None] = {}
    for check in checks:
        names.update(dict.fromkeys(_required(check)))
    return list(names)


def run(*checks: Callable[..., Any], **values: Any) -> None:
    """Runs every check, each with the ``values`` it takes, and raises `ChecksFailed` if any failed.

    A check asking the workflow to go back or abort (`GoBack`, `Abort`) does so at once.

    Raises:
        ChecksFailed: Listing every check that failed.
        TypeError: When a check takes a value that was not given.
    """
    missing = [name for name in inputs(*checks) if name not in values]
    if missing:
        raise TypeError(f"The checks need {', '.join(missing)}; pass them as keywords.")
    failures: list[tuple[str, Exception]] = []
    for check in checks:
        try:
            check(**{name: values[name] for name in _required(check)})
        except (GoBack, Abort):
            raise
        except Exception as error:  # noqa: BLE001 -- a failing check is reported with the others, not raised alone
            failures.append((getattr(check, "__name__", repr(check)), error))
    if failures:
        raise ChecksFailed(failures)


def _required(check: Callable[..., Any]) -> list[str]:
    """The parameters of ``check`` without a default; checks are called by keyword."""
    names = []
    for param in inspect.signature(check).parameters.values():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise TypeError(f"Check {check!r}: *args/**kwargs are not supported; name each input.")
        if param.kind is param.POSITIONAL_ONLY:
            raise TypeError(
                f"Check {check!r}: parameter {param.name!r} is positional-only; checks are called by keyword."
            )
        if param.default is param.empty:
            names.append(param.name)
    return names


def _on_path(source: PathSource | None, check: Callable[[Path], None], name: str) -> Callable[..., None]:
    """A check named ``name`` running ``check`` on a fixed path, or on one taken from the ``rig``."""
    if source is None or callable(source):
        resolve: Callable[[Any], str | os.PathLike] = source if source is not None else (lambda rig: rig.data_directory)

        def from_rig(rig: Any) -> None:
            check(Path(resolve(rig)))

        built: Callable[..., None] = from_rig
    else:
        fixed = Path(source)

        def at_path() -> None:
            check(fixed)

        built = at_path
    built.__name__ = built.__qualname__ = name
    return built
