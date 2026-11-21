import importlib.util
import logging
import sys
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Protocol

from ..ui import Frontend, PickRequest, default_frontend

if TYPE_CHECKING:
    from ._base import Launcher

logger = logging.getLogger(__name__)
ExperimentCallable = Callable[["Launcher"], None | Awaitable[None]]


@dataclass
class ExperimentMetadata:
    """Metadata associated with a clabe "experiment" callable.

    Attributes:
        name: Human-readable name for the experiment.
        func: The underlying callable.
        order: Sort key controlling position when multiple experiments are
            listed (lower sorts first). Ties keep declaration order.
        exit_prompt_timeout: Seconds the launcher waits at the "Press Enter to
            exit..." prompt once the experiment finishes. ``None`` waits until
            Enter is pressed; ``0`` exits without prompting.
    """

    name: str
    func: ExperimentCallable
    order: int = 0
    exit_prompt_timeout: float | None = None


class _IExperiment(Protocol):
    """Protocol for callables that accept a `Launcher` as first argument."""

    def __call__(self, launcher: "Launcher", /, *args: Any, **kwargs: Any) -> Any: ...

    __name__: str


def experiment(
    *,
    name: str | None = None,
    order: int = 0,
    exit_prompt_timeout: float | None = None,
) -> Callable[[_IExperiment], _IExperiment]:
    """Decorator to mark a function as a CLABE experiment.

    The decorated function must accept a single `Launcher` argument and may be
    either synchronous or asynchronous.

    Args:
        name: Human-readable name for the experiment. Defaults to the
            function's ``__name__``.
        order: Sort key controlling where this experiment appears when a
            module defines more than one (lower sorts first). Experiments with
            the same ``order`` keep their declaration order.
        exit_prompt_timeout: Seconds to wait at the "Press Enter to exit..."
            prompt once the experiment finishes, before exiting on its own.
            ``None`` (default) waits until Enter is pressed; ``0`` exits without
            prompting. It has no effect on a frontend that cannot cancel its
            prompts.

    Raises:
        ValueError: If ``exit_prompt_timeout`` is negative.

    Example:
        ```python
        from pathlib import Path

        from clabe.launcher import Launcher
        from clabe.launcher import experiment


        @experiment(name="super_duper_experiment", order=-1, exit_prompt_timeout=60)
        async def vr_foraging_with_photometry(launcher: Launcher) -> None:
            ...
        ```
    """
    if exit_prompt_timeout is not None and exit_prompt_timeout < 0:
        raise ValueError(f"exit_prompt_timeout must be None or >= 0, got {exit_prompt_timeout}.")

    def decorator(func: _IExperiment) -> _IExperiment:
        exp_name = name or func.__name__
        metadata = ExperimentMetadata(
            name=exp_name,
            func=func,  # type: ignore[arg-type]
            order=order,
            exit_prompt_timeout=exit_prompt_timeout,
        )
        func.__clabe_experiment_metadata__ = metadata
        return func

    return decorator


def get_experiment_metadata(experiment: Callable[..., Any]) -> ExperimentMetadata | None:
    """Return the :class:`ExperimentMetadata` attached by :func:`experiment`, or ``None``.

    Args:
        experiment: The experiment callable.

    Returns:
        The metadata, or ``None`` if the callable is not decorated with :func:`experiment`.
    """
    metadata = getattr(experiment, "__clabe_experiment_metadata__", None)
    return metadata if isinstance(metadata, ExperimentMetadata) else None


def get_experiment_name(experiment: _IExperiment) -> str | None:
    """Return the human-readable name for an experiment callable, or ``None``.

    Checks for a :class:`ExperimentMetadata` instance attached by the
    :func:`experiment` decorator first, then falls back to ``__name__``.
    Returns ``None`` when neither is available.

    Args:
        experiment: The experiment callable.

    Returns:
        The experiment name, or ``None`` if it cannot be determined.
    """
    metadata: ExperimentMetadata | None = getattr(experiment, "__clabe_experiment_metadata__", None)
    if metadata is not None:
        return metadata.name
    return getattr(experiment, "__name__", None) or None


def collect_clabe_experiments(module: ModuleType) -> Iterable[ExperimentMetadata]:
    """Yield all `@experiment` experiments defined in the target module.

    Experiments are yielded sorted by their ``order`` (lower first); ties keep
    the module's declaration order.
    """

    discovered: list[ExperimentMetadata] = []
    for value in vars(module).values():
        metadata = getattr(value, "__clabe_experiment_metadata__", None)
        if isinstance(metadata, ExperimentMetadata):
            logger.debug("Discovered CLABE experiment: %s in module %s", metadata.name, module.__name__)
            discovered.append(metadata)
    yield from sorted(discovered, key=lambda e: e.order)


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


def _select_experiment(
    file_path: Path, frontend: Frontend | None = None, experiment_name: str | None = None
) -> ExperimentMetadata:
    """Select an experiment callable from a Python module.

    Loads the module at ``file_path``, discovers all callables decorated with
    :func:`experiment`, and returns the associated :class:`ExperimentMetadata`.

    If ``experiment_name`` is given, the matching experiment is returned
    directly (no prompt), which allows non-interactive/scripted runs. Otherwise,
    if a single experiment is found it is returned directly; when multiple
    experiments are available, the provided ``frontend`` is used to prompt the
    user to choose one. If no frontend is supplied the default frontend is used.

    Args:
        file_path: Filesystem path to the Python module to inspect.
        frontend: Optional frontend used to interactively choose an experiment
            when more than one is discovered.
        experiment_name: Optional name of the experiment to select directly,
            bypassing the interactive prompt.

    Returns:
        ExperimentMetadata: The metadata for the selected experiment.

    Raises:
        ValueError: If experiment names are not unique within the module.
        SystemExit: If no experiments are found, ``experiment_name`` does not
            match any discovered experiment, or the user cancels selection.
    """

    if frontend is None:
        frontend = default_frontend()
    module = _load_module_from_path(file_path)
    experiments = list(collect_clabe_experiments(module))

    if len({e.name for e in experiments}) != len(experiments):
        raise ValueError("Experiment names must be unique within a module.")

    if not experiments:
        msg = f"No @experiment experiments found in {file_path}"
        raise SystemExit(msg)

    if experiment_name is not None:
        callable_str_converter = {e.name: e for e in experiments}
        if experiment_name not in callable_str_converter:
            available = ", ".join(callable_str_converter)
            msg = f"No experiment named '{experiment_name}' in {file_path}. Available: {available}"
            raise SystemExit(msg)
        selected = callable_str_converter[experiment_name]
    elif len(experiments) == 1:
        selected = experiments[0]
    else:
        callable_str_converter = {e.name: e for e in experiments}
        choice = frontend.prompt_pick(
            PickRequest(
                label="Select experiment to run",
                options=list(callable_str_converter.keys()),
                allow_none=False,
                field="experiment",
            )
        )
        if choice is None:
            raise SystemExit("No experiment selected; exiting.")
        selected = callable_str_converter[choice]
    logger.info("Selected experiment: %s", selected.name)
    return selected
