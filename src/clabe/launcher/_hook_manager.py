import logging
import typing as t
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._base import BaseLauncher
else:
    BaseLauncher = Any

logger = logging.getLogger(__name__)

TInput = t.TypeVar("TInput")
TOutput = t.TypeVar("TOutput")
TLauncher = t.TypeVar("TLauncher", bound=BaseLauncher)


class HookManager(t.Generic[TInput, TOutput]):
    def __init__(self):
        self._observables: list[t.Callable[[TInput], TOutput]] = []
        self._has_run: bool = False

    def has_run(self) -> bool:
        """Check if callables have been run."""
        return self._has_run

    def register(self, hook: t.Callable[[TInput], TOutput]) -> None:
        """Register a new hook."""
        self._observables.append(hook)

    def remove(self, hook: t.Callable[[TInput], TOutput]) -> None:
        """Remove a registered hook."""
        if hook in self._observables:
            self._observables.remove(hook)

    def clear(self) -> None:
        """Clear all registered callables."""
        self._observables.clear()

    def run(self, value: TInput) -> None:
        """Run all registered callables"""
        if self._has_run:
            logger.warning("Callables have already been run. Skipping execution.")
            return
        for observer in self._observables:
            logger.debug(f"Running observer: {observer.__name__} with value: {value}")
            observer(value)
