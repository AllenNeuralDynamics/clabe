"""Finding the workflows an experiment file offers."""

import importlib.util
import logging
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from .. import ui
from ._workflow import Workflow

if TYPE_CHECKING:
    from ..launcher import Launcher

logger = logging.getLogger(__name__)

#: A function of the launcher (sync or async), which `Launcher.run_experiment` also accepts: it runs as a workflow of
#: one step.
ExperimentCallable = Callable[["Launcher"], None | Awaitable[None]]


def as_workflow(target: "Workflow | ExperimentCallable") -> Workflow:
    """Returns ``target`` as a workflow: itself, or, for a function of the launcher, a workflow whose only step
    (``experiment``) calls it with the launcher."""
    if isinstance(target, Workflow):
        return target
    workflow = Workflow(getattr(target, "__name__", None) or "experiment")

    def experiment_step(launcher: "Launcher") -> Any:
        return target(launcher)

    workflow.step(experiment_step, name="experiment", title=workflow.name)
    # Set after declaring the step: inspect.signature follows __wrapped__, and the step's parameter is `launcher`.
    experiment_step.__wrapped__ = target  # type: ignore[attr-defined]  # provenance points at the real function
    return workflow


def load_module(path: Path) -> ModuleType:
    """Imports the Python file at ``path``; its directory is put on ``sys.path`` so sibling imports resolve.

    A file inside a package (a folder with an ``__init__.py``) is imported as a module of that package, so its relative
    imports resolve: the folder above the outermost package is put on ``sys.path`` instead.
    """
    path = Path(path).resolve()
    package = path.parent
    parts = [path.stem]
    while (package / "__init__.py").is_file():
        parts.insert(0, package.name)
        package = package.parent
    if len(parts) > 1:
        if str(package) not in sys.path:
            sys.path.insert(0, str(package))
        return importlib.import_module(".".join(parts))
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
    """The workflows a module offers (its ``Workflow`` objects), sorted by ``order``.

    Ties keep declaration order. A workflow bound to several names is listed once.
    """
    found: list[Workflow] = []
    for value in vars(module).values():
        if isinstance(value, Workflow) and not any(value is seen for seen in found):
            found.append(value)
    return sorted(found, key=lambda workflow: workflow.order)


def select_workflow(path: Path, *, name: str | None = None) -> Workflow:
    """Loads ``path`` and returns one of its workflows.

    ``name`` selects directly; a single workflow is returned as is; otherwise the user is asked through the active
    [`clabe.ui`][clabe.ui] session.

    Raises:
        ValueError: If workflow names are not unique within the file.
        SystemExit: If none is found, ``name`` matches nothing, or the user cancels.
    """
    workflows = collect_workflows(load_module(path))
    by_name = {workflow.name: workflow for workflow in workflows}
    if len(by_name) != len(workflows):
        raise ValueError(f"Workflow names must be unique within {path}.")
    if not workflows:
        raise SystemExit(f"No workflows found in {path}")
    if name is not None:
        if name not in by_name:
            raise SystemExit(f"No experiment named '{name}' in {path}. Available: {', '.join(by_name)}")
        selected = by_name[name]
    elif len(workflows) == 1:
        selected = workflows[0]
    else:
        choice = ui.pick("Select experiment to run", list(by_name), allow_none=False, field="experiment")
        if choice is None:
            raise SystemExit("No experiment selected; exiting.")
        selected = by_name[choice]
    logger.info("Selected experiment: %s", selected.name)
    return selected


class StepInfo(BaseModel):
    """A step as listed by `list_experiments`."""

    name: str
    title: str
    barrier: bool
    optional: str | None
    conditional: bool = False


class ExperimentInfo(BaseModel):
    """A workflow offered by an experiment file, as listed by `list_experiments`."""

    file: str
    name: str
    description: str | None = None
    steps: list[StepInfo]


class ListingError(BaseModel):
    """An experiment file that could not be imported."""

    file: str
    error: str


class ExperimentListing(BaseModel):
    """What an experiment file or directory offers (see ``clabe list``)."""

    experiments: list[ExperimentInfo]
    errors: list[ListingError]


def list_experiments(path: str | Path) -> ExperimentListing:
    """Lists the workflows offered by a file, or by every ``*.py`` file in a directory (not recursively).

    Files that fail to import are reported in ``errors``; nothing runs except the files' imports.
    """
    path = Path(path)
    files = sorted(p for p in path.glob("*.py") if p.name != "__init__.py") if path.is_dir() else [path]
    experiments, errors = [], []
    for file in files:
        try:
            module = load_module(file)
        except Exception as exc:  # noqa: BLE001 -- any import failure is reported, not raised
            errors.append(ListingError(file=str(file), error=f"{type(exc).__name__}: {exc}"))
            continue
        for workflow in collect_workflows(module):
            steps = [
                StepInfo(
                    name=s.name,
                    title=s.title,
                    barrier=s.barrier,
                    optional=s.optional,
                    conditional=s.when is not None,
                )
                for s in workflow.steps
            ]
            experiments.append(
                ExperimentInfo(file=str(file), name=workflow.name, description=workflow.description, steps=steps)
            )
    return ExperimentListing(experiments=experiments, errors=errors)
