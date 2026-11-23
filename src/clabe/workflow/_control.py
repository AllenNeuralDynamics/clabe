"""Exceptions a step raises to steer its run."""

from collections.abc import Callable
from typing import Any


class GoBack(Exception):
    """Raised by a step to go back to an earlier step.

    Raising ``GoBack`` with the step's own name runs the step again; a step that does so without asking anything
    first will loop.

    Args:
        to: The step to go back to: its name, or its function (``GoBack(subject)``; found by identity, so it works
            for a step declared with ``name=``). ``None`` goes back to the nearest earlier step that has run and is
            not locked by a completed barrier.
    """

    def __init__(self, to: str | Callable[..., Any] | None = None) -> None:
        super().__init__(to)
        self.to = to


class GoBackRejected(RuntimeError):
    """Raised when a step's `GoBack` cannot be honoured (no earlier step, a barrier, or not run yet)."""


class Abort(Exception):
    """Raised by a step to end the run as aborted (not failed)."""

    def __init__(self, reason: str = "Aborted.") -> None:
        super().__init__(reason)
        self.reason = reason
