"""Exceptions a step raises to steer its run."""


class GoBack(Exception):
    """Raised by a step to go back to an earlier step.

    Raising ``GoBack`` with the step's own name runs the step again; a step that does so without asking anything
    first will loop.

    Args:
        to: Name of the step to go back to. ``None`` goes back to the nearest earlier step that has run and
            is not locked by a completed barrier.
    """

    def __init__(self, to: str | None = None) -> None:
        super().__init__(to)
        self.to = to


class GoBackRejected(RuntimeError):
    """Raised when a step's :class:`GoBack` cannot be honoured (no earlier step, a barrier, or not run yet)."""


class Abort(Exception):
    """Raised by a step to end the run as aborted (not failed)."""

    def __init__(self, reason: str = "Aborted.") -> None:
        super().__init__(reason)
        self.reason = reason
