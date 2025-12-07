import importlib.util
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Awaitable, Callable, Iterable, Optional, Protocol, Union

from ..ui import DefaultUIHelper, IUiHelper
from ._base import Launcher

logger = logging.getLogger(__name__)
ExperimentCallable = Callable[[Launcher], Union[None, Awaitable[None]]]


@dataclass
class ExperimentMetadata:
    """Metadata associated with a clabe "experiment" callable.

    Attributes:
        name: Human-readable name for the experiment.
        func: The underlying callable.
    """

    name: str
    func: ExperimentCallable


class _IExperiment(Protocol):
    """Protocol for callables that accept a `Launcher` as first argument."""

    def __call__(self, launcher: Launcher, *args: Any, **kwargs: Any) -> Any: ...

    __name__: str


class _ITaggedExperiment(_IExperiment, Protocol):
    """Protocol for experiments tagged with metadata."""

    __clabe_experiment_metadata__: ExperimentMetadata


def experiment(
    *,
    name: Optional[str] = None,
) -> Callable[[_IExperiment], _IExperiment]:
    """Decorator to mark a function as a CLABE experiment.

    The decorated function must accept a single `Launcher` argument and may be
    either synchronous or asynchronous.

    Example:
        ```python
        from pathlib import Path

        from clabe.launcher import Launcher
        from clabe.launcher import experiment


        @experiment(name="super_duper_experiment")
        async def vr_foraging_with_photometry(launcher: Launcher) -> None:
            ...
        ```
    """

    def decorator(func: _IExperiment) -> _IExperiment:
        exp_name = name or func.__name__
        metadata = ExperimentMetadata(
            name=exp_name,
            func=func,  # type: ignore[arg-type]
        )
        setattr(func, "__clabe_experiment_metadata__", metadata)
        return func

    return decorator


def collect_clabe_experiments(module: ModuleType) -> Iterable[ExperimentMetadata]:
    """Yield all `@experiment` experiments defined in the target module."""

    for _, value in vars(module).items():
        metadata = getattr(value, "__clabe_experiment_metadata__", None)
        if isinstance(metadata, ExperimentMetadata):
            logger.debug("Discovered CLABE experiment: %s in module %s", metadata.name, module.__name__)
            yield metadata


def _load_module_from_path(path: Path):
    """Load a module from a filesystem path.

    The directory containing the script is prepended to ``sys.path`` so that
    local imports (for example ``from sibling import foo`` or
    ``from package.module import bar`` rooted at that directory) resolve when
    the module is loaded via its file path.
    """

    script_dir = path.parent
    script_dir_str = str(script_dir)
    if script_dir_str not in sys.path:
        sys.path.insert(0, script_dir_str)

    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        msg = f"Cannot load module from {path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def _select_experiment(file_path: Path, ui_helper: IUiHelper | None = None) -> ExperimentMetadata:
    """Select an experiment callable from a Python module.

    Loads the module at ``file_path``, discovers all callables decorated with
    :func:`experiment`, and returns the associated :class:`ExperimentMetadata`.

    If a single experiment is found it is returned directly. When multiple
    experiments are available, the provided ``ui_helper`` is used to prompt the
    user to choose one. If no UI helper is supplied a :class:`DefaultUIHelper`
    instance is used.

    Args:
        file_path: Filesystem path to the Python module to inspect.
        ui_helper: Optional helper used to interactively choose an experiment
            when more than one is discovered.

    Returns:
        ExperimentMetadata: The metadata for the selected experiment.

    Raises:
        ValueError: If experiment names are not unique within the module.
        SystemExit: If no experiments are found or the user cancels
            selection.
    """

    if ui_helper is None:
        ui_helper = DefaultUIHelper()
    module = _load_module_from_path(file_path)
    experiments = list(collect_clabe_experiments(module))

    if len(set(e.name for e in experiments)) != len(experiments):
        raise ValueError("Experiment names must be unique within a module.")

    if not experiments:
        msg = f"No @experiment experiments found in {file_path}"
        raise SystemExit(msg)

    if len(experiments) == 1:
        selected = experiments[0]
    else:
        callable_str_converter = {e.name: e for e in experiments}
        choice = ui_helper.prompt_pick_from_list(list(callable_str_converter.keys()), "Select experiment to run")
        if choice is None:
            raise SystemExit("No experiment selected; exiting.")
        selected = callable_str_converter[choice]
    logging.info("Selected experiment: %s", selected.name)
    return selected
