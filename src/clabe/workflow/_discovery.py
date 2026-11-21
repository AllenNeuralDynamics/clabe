"""Finding the workflows an experiment file offers."""

import importlib.util
import logging
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from ..ui import Frontend, PickRequest, default_frontend
from ._workflow import Workflow

if TYPE_CHECKING:
    from ..launcher import Launcher

logger = logging.getLogger(__name__)

#: A legacy experiment: a function of the launcher, sync or async.
ExperimentCallable = Callable[["Launcher"], None | Awaitable[None]]

_ATTRIBUTE = "__clabe_workflow__"


def _one_step(func: ExperimentCallable, name: str, order: int, exit_prompt_timeout: float | None) -> Workflow:
    """Wraps ``func`` in a workflow whose only step, ``experiment``, calls it with the launcher."""
    workflow = Workflow(name, order=order, exit_prompt_timeout=exit_prompt_timeout)

    def experiment_step(launcher: "Launcher") -> Any:
        return func(launcher)

    workflow.step(experiment_step, name="experiment", title=name)
    # Set after declaring the step: inspect.signature follows __wrapped__, and the step's parameter is `launcher`.
    experiment_step.__wrapped__ = func  # type: ignore[attr-defined]  # provenance points at the real function
    return workflow


def experiment(
    *, name: str | None = None, order: int = 0, exit_prompt_timeout: float | None = None
) -> Callable[[ExperimentCallable], ExperimentCallable]:
    """Marks a ``(launcher) -> None`` function (sync or async) as a one-step workflow.

    The function is returned unchanged; the workflow is attached to it (see :func:`as_workflow`).

    Args:
        name: Name of the experiment. Defaults to the function's ``__name__``.
        order: Sort key when a file offers several workflows (lower first).
        exit_prompt_timeout: Seconds the launcher waits at its exit prompt; ``None`` waits, ``0`` skips it.

    Raises:
        ValueError: If ``exit_prompt_timeout`` is negative.

    Example:
        ```python
        from clabe.launcher import Launcher, experiment


        @experiment(name="super_duper_experiment", order=-1, exit_prompt_timeout=60)
        async def vr_foraging_with_photometry(launcher: Launcher) -> None:
            ...
        ```
    """
    if exit_prompt_timeout is not None and exit_prompt_timeout < 0:
        raise ValueError(f"exit_prompt_timeout must be None or >= 0, got {exit_prompt_timeout}.")

    def decorator(func: ExperimentCallable) -> ExperimentCallable:
        setattr(func, _ATTRIBUTE, _one_step(func, name or func.__name__, order, exit_prompt_timeout))
        return func

    return decorator


def as_workflow(target: "Workflow | ExperimentCallable") -> Workflow:
    """Returns ``target`` as a workflow: itself, the one attached by ``@experiment``, or a new one-step workflow."""
    if isinstance(target, Workflow):
        return target
    attached = getattr(target, _ATTRIBUTE, None)
    if isinstance(attached, Workflow):
        return attached
    return _one_step(target, getattr(target, "__name__", None) or "experiment", 0, None)


def load_module(path: Path) -> ModuleType:
    """Imports the Python file at ``path``; its directory is put on ``sys.path`` so sibling imports resolve."""
    script_dir = str(path.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def collect_workflows(module: ModuleType) -> list[Workflow]:
    """The workflows a module offers (``Workflow`` objects and ``@experiment`` functions), sorted by ``order``.

    Ties keep declaration order. A workflow bound to several names is listed once.
    """
    found: list[Workflow] = []
    for value in vars(module).values():
        workflow = value if isinstance(value, Workflow) else getattr(value, _ATTRIBUTE, None)
        if isinstance(workflow, Workflow) and not any(workflow is seen for seen in found):
            found.append(workflow)
    return sorted(found, key=lambda workflow: workflow.order)


def select_workflow(path: Path, *, frontend: Frontend | None = None, name: str | None = None) -> Workflow:
    """Loads ``path`` and returns one of its workflows.

    ``name`` selects directly; a single workflow is returned as is; otherwise ``frontend`` (default: the default
    frontend) asks the user.

    Raises:
        ValueError: If workflow names are not unique within the file.
        SystemExit: If none is found, ``name`` matches nothing, or the user cancels.
    """
    workflows = collect_workflows(load_module(path))
    by_name = {workflow.name: workflow for workflow in workflows}
    if len(by_name) != len(workflows):
        raise ValueError(f"Workflow names must be unique within {path}.")
    if not workflows:
        raise SystemExit(f"No workflows or @experiment functions found in {path}")
    if name is not None:
        if name not in by_name:
            raise SystemExit(f"No experiment named '{name}' in {path}. Available: {', '.join(by_name)}")
        selected = by_name[name]
    elif len(workflows) == 1:
        selected = workflows[0]
    else:
        choice = (frontend or default_frontend()).prompt_pick(
            PickRequest(label="Select experiment to run", options=list(by_name), allow_none=False, field="experiment")
        )
        if choice is None:
            raise SystemExit("No experiment selected; exiting.")
        selected = by_name[choice]
    logger.info("Selected experiment: %s", selected.name)
    return selected
