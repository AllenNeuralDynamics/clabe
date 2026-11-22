import logging
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .. import checks
from ..runnable import runnable
from ..services import Service

logger = logging.getLogger(__name__)

_DEPRECATED = (
    "ResourceMonitor and Constraint are deprecated and will be removed in a future release: write checks as "
    "functions that raise (see clabe.checks) and run them with steps.preflight(...) in a workflow, or "
    "checks.run(...) elsewhere."
)


class ResourceMonitor(Service):
    """Deprecated: runs a list of [`Constraint`][clabe.resource_monitor.Constraint]s.

    Use [`clabe.checks`][clabe.checks] instead: ``steps.preflight(*checks)`` in a workflow, ``checks.run(*checks)``
    elsewhere — both run every check and report every failure at once.
    """

    def __init__(self, constrains: list["Constraint"] | None = None) -> None:
        """Initializes the monitor.

        Args:
            constrains: The constraints to start with.
        """
        warnings.warn(_DEPRECATED, DeprecationWarning, stacklevel=2)
        self.constraints = constrains or []

    @runnable(name="Resource monitor")
    def run(self) -> bool:
        """Evaluates the constraints; returns True when all pass.

        Raises:
            RuntimeError: On the first failing constraint, with its failure message.
        """
        for constraint in self.constraints:
            if not constraint():
                raise RuntimeError(constraint.on_fail())
        return True

    def add_constraint(self, constraint: "Constraint") -> None:
        """Adds a constraint."""
        self.constraints.append(constraint)

    def remove_constraint(self, constraint: "Constraint") -> None:
        """Removes a constraint."""
        self.constraints.remove(constraint)

    def evaluate_constraints(self) -> bool:
        """Evaluates the constraints, logging the first failure; returns whether all passed."""
        for constraint in self.constraints:
            if not constraint():
                logger.error(constraint.on_fail())
                return False
        return True


@dataclass(frozen=True)
class Constraint:
    """Deprecated: a predicate with stored arguments and a failure message. Use a check (see `clabe.checks`).

    Attributes:
        name: The constraint's name.
        constraint: The predicate, called with ``args`` and ``kwargs``.
        args: Positional arguments of the predicate.
        kwargs: Keyword arguments of the predicate.
        fail_msg_handler: Builds the failure message from the same arguments.
    """

    name: str
    constraint: Callable[..., bool]
    args: list = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)
    fail_msg_handler: Callable[..., str] | None = field(default=None)

    def __post_init__(self) -> None:
        """Warns that constraints are deprecated."""
        warnings.warn(_DEPRECATED, DeprecationWarning, stacklevel=3)

    def __call__(self) -> bool:
        """Evaluates the predicate."""
        return self.constraint(*self.args, **self.kwargs)

    def on_fail(self) -> str:
        """The failure message."""
        if self.fail_msg_handler:
            return self.fail_msg_handler(*self.args, **self.kwargs)
        return f"Constraint {self.name} failed."

    @classmethod
    def from_check(cls, name: str, check: Callable[[], None], **kwargs: Any) -> "Constraint":
        """A constraint passing when the (argument-less) ``check`` does not raise; fails with its message."""

        def passes(**_: Any) -> bool:
            return _failure(check) is None

        def message(**_: Any) -> str:
            return _failure(check) or f"Constraint {name} failed."

        return cls(name=name, constraint=passes, kwargs=kwargs, fail_msg_handler=message)


def _failure(check: Callable[[], None]) -> str | None:
    """The message of ``check``'s failure, or None when it passes."""
    try:
        checks.run(check)
    except checks.ChecksFailed as error:
        return str(error)
    return None
