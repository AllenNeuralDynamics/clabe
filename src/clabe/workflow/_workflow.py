"""Declaring workflows: ordered steps wired together by parameter name."""

import inspect
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal, Self, TypeVar

from .codecs import Codec

#: Names a step may take as a parameter without another step producing them.
BUILTINS = frozenset({"launcher", "run"})

OnError = Literal["raise", "warn"]


class WorkflowDefinitionError(ValueError):
    """Raised when a workflow is declared with invalid steps or wiring."""


@dataclass(frozen=True)
class Step:
    """One declared step of a :class:`Workflow`.

    Attributes:
        name: Unique name; the step's return value is stored under it.
        func: The step function (sync or async).
        title: Human-readable title shown by frontends.
        params: The function's parameter names, resolved by name when the step runs.
        barrier: Once completed, neither this step nor any before it can run again.
        on_error: ``"raise"`` pauses the run with Retry/Go back/Abort; ``"warn"`` notifies and continues
            with ``None`` as the step's output.
        optional: When set, the run asks this question first and skips the step on "no".
        undo: Called with the step's output when a completed step is invalidated by going back.
        cancellable: An async step that may be interrupted by going back while it is busy.
        codec: How the output is recorded in ``outputs.jsonl`` (see :mod:`clabe.workflow.codecs`); ``None`` uses
            ``codecs.auto``.
    """

    name: str
    func: Callable[..., Any]
    title: str
    params: tuple[str, ...]
    barrier: bool = False
    on_error: OnError = "raise"
    optional: str | None = None
    undo: Callable[[Any], Any] | None = None
    cancellable: bool = False
    codec: Codec | None = None

    @property
    def deps(self) -> tuple[str, ...]:
        """The parameters that must be produced by earlier steps."""
        return tuple(p for p in self.params if p not in BUILTINS)

    def options(self) -> dict[str, Any]:
        """The declaration options, as accepted by :meth:`Workflow.step`."""
        return {
            "title": self.title,
            "barrier": self.barrier,
            "on_error": self.on_error,
            "optional": self.optional,
            "undo": self.undo,
            "cancellable": self.cancellable,
            "codec": self.codec,
        }


#: Attribute holding the default options a step function carries (see :func:`step_defaults`).
STEP_DEFAULTS = "__clabe_step_defaults__"

_F = TypeVar("_F", bound=Callable[..., Any])


def step_defaults(**options: Any) -> Callable[[_F], _F]:
    """Gives a step function default options, e.g. a reusable step that should be a barrier.

    Options passed when the step is declared (``wf.step(func, barrier=False)``) override these. See :class:`Step`
    for the options.
    """

    def mark(func: _F) -> _F:
        setattr(func, STEP_DEFAULTS, dict(options))
        return func

    return mark


def make_step(func: Callable[..., Any], /, **options: Any) -> Step:
    """Builds a :class:`Step` from a function: its :func:`step_defaults`, overridden by ``options``."""
    return _build_step(func, **{**getattr(func, STEP_DEFAULTS, {}), **options})


def _build_step(
    func: Callable[..., Any],
    *,
    name: str | None = None,
    title: str | None = None,
    barrier: bool = False,
    on_error: OnError = "raise",
    optional: str | None = None,
    undo: Callable[[Any], Any] | None = None,
    cancellable: bool = False,
    codec: Codec | None = None,
) -> Step:
    """Builds a :class:`Step` from a function, checking the options that do not depend on other steps."""
    step_name = name if name is not None else getattr(func, "__name__", None)
    if not isinstance(step_name, str) or not step_name.isidentifier():
        raise WorkflowDefinitionError(
            f"Step name {step_name!r} is not a valid Python identifier; pass name=... "
            "(later steps receive this step's output through a parameter of that name)."
        )
    params = []
    for param in inspect.signature(func).parameters.values():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise WorkflowDefinitionError(f"Step {step_name!r}: *args/**kwargs are not supported; name each input.")
        if param.kind is param.POSITIONAL_ONLY:
            raise WorkflowDefinitionError(
                f"Step {step_name!r}: parameter {param.name!r} is positional-only; steps are called by keyword."
            )
        params.append(param.name)
    if barrier and undo is not None:
        raise WorkflowDefinitionError(f"Step {step_name!r} cannot be both a barrier and undoable.")
    if on_error not in ("raise", "warn"):
        raise WorkflowDefinitionError(f"Step {step_name!r}: on_error must be 'raise' or 'warn', got {on_error!r}.")
    if cancellable and not inspect.iscoroutinefunction(func):
        raise WorkflowDefinitionError(f"Step {step_name!r}: only async steps can be cancellable.")
    if undo is not None:
        try:
            inspect.signature(undo).bind(None)
        except TypeError:
            raise WorkflowDefinitionError(
                f"Step {step_name!r}: undo must accept one argument (the step's output)."
            ) from None
    return Step(
        name=step_name,
        func=func,
        title=title or step_name.replace("_", " ").capitalize(),
        params=tuple(params),
        barrier=barrier,
        on_error=on_error,
        optional=optional,
        undo=undo,
        cancellable=cancellable,
        codec=codec,
    )


def _check(steps: Iterable[Step]) -> None:
    """Validates names and wiring: every dependency must be an earlier step."""
    seen: set[str] = set()
    for step in steps:
        if step.name in BUILTINS:
            raise WorkflowDefinitionError(f"Step name {step.name!r} is reserved for a built-in.")
        if step.name in seen:
            raise WorkflowDefinitionError(f"Duplicate step name {step.name!r}.")
        for dep in step.deps:
            if dep not in seen:
                raise WorkflowDefinitionError(
                    f"Step {step.name!r} needs {dep!r}, which is neither an earlier step "
                    f"nor a built-in ({', '.join(sorted(BUILTINS))})."
                )
        seen.add(step.name)


class Workflow:
    """An ordered list of steps. Declaration order is the stepper order.

    Example:
        ```python
        wf = Workflow("vr-foraging", exit_prompt_timeout=60)

        @wf.step
        def session(launcher) -> Session: ...

        @wf.step(barrier=True)
        def register(launcher, session) -> None: ...
        ```

    Step outputs are passed to later steps by reference: a step must not mutate what it receives — return a new
    value instead (e.g. ``session.model_copy(update=...)``).

    Args:
        name: Human-readable name of the workflow (what ``clabe run`` lists).
        order: Sort key when a module defines several workflows (lower first).
        exit_prompt_timeout: Seconds the launcher waits at its exit prompt; ``None`` waits, ``0`` skips it.
    """

    def __init__(self, name: str, *, order: int = 0, exit_prompt_timeout: float | None = None) -> None:
        if exit_prompt_timeout is not None and exit_prompt_timeout < 0:
            raise ValueError(f"exit_prompt_timeout must be None or >= 0, got {exit_prompt_timeout}.")
        self.name = name
        self.order = order
        self.exit_prompt_timeout = exit_prompt_timeout
        self._steps: tuple[Step, ...] = ()

    def __repr__(self) -> str:
        return f"Workflow({self.name!r}, steps={[s.name for s in self._steps]})"

    @property
    def steps(self) -> tuple[Step, ...]:
        """The declared steps, in order."""
        return self._steps

    def step(self, func: Callable[..., Any] | None = None, /, **options: Any) -> Any:
        """Declares a step. Use as ``@wf.step`` or ``@wf.step(title=..., barrier=True, ...)``.

        The decorated function is returned unchanged, so steps stay testable as plain functions.
        Every parameter must name an earlier step or a built-in (``launcher``, ``run``), even one with a
        default value: a misspelled input fails here rather than silently falling back to its default.
        See :class:`Step` for the options.
        """

        def register(f: Callable[..., Any]) -> Callable[..., Any]:
            self._set_steps([*self._steps, make_step(f, **options)])
            return f

        return register if func is None else register(func)

    def get(self, name: str) -> Step:
        """Returns the step called ``name``."""
        for step in self._steps:
            if step.name == name:
                return step
        raise KeyError(f"No step named {name!r} in workflow {self.name!r}.")

    def index(self, name: str) -> int:
        """Returns the position of the step called ``name``."""
        return self._steps.index(self.get(name))

    def dependents(self, name: str) -> list[str]:
        """Names of the steps that transitively depend on ``name``, in declaration order."""
        affected = {name}
        result = []
        for step in self._steps[self.index(name) + 1 :]:
            if affected.intersection(step.deps):
                affected.add(step.name)
                result.append(step.name)
        return result

    def variant(self, name: str) -> "Workflow":
        """Returns a copy named ``name`` with the same steps and settings, to customise further."""
        copy = Workflow(name, order=self.order, exit_prompt_timeout=self.exit_prompt_timeout)
        copy._steps = self._steps
        return copy

    def insert_before(self, anchor: str, func: Callable[..., Any], /, **options: Any) -> Self:
        """Inserts a new step right before ``anchor``. See :class:`Step` for the options."""
        steps = list(self._steps)
        steps.insert(self.index(anchor), make_step(func, **options))
        self._set_steps(steps)
        return self

    def insert_after(self, anchor: str, func: Callable[..., Any], /, **options: Any) -> Self:
        """Inserts a new step right after ``anchor``. See :class:`Step` for the options."""
        steps = list(self._steps)
        steps.insert(self.index(anchor) + 1, make_step(func, **options))
        self._set_steps(steps)
        return self

    def replace(self, name: str, func: Callable[..., Any], /, **options: Any) -> Self:
        """Replaces step ``name`` with ``func``, keeping its name and (unless overridden) its options.

        Inherited options (notably ``undo``, ``codec``, ``cancellable``) describe the old function; override them
        if the new one differs.
        """
        position = self.index(name)
        steps = list(self._steps)
        steps[position] = make_step(func, name=name, **{**steps[position].options(), **options})
        self._set_steps(steps)
        return self

    def _set_steps(self, steps: list[Step]) -> None:
        """Validates ``steps`` and replaces the declared steps; leaves them unchanged if invalid."""
        _check(steps)
        self._steps = tuple(steps)
