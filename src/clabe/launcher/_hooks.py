import logging
import typing as t
from typing import final, TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._base import BaseLauncher
else:
    BaseLauncher = Any

logger = logging.getLogger(__name__)

TInput = t.TypeVar("TInput")
TOutput = t.TypeVar("TOutput")
TLauncher = t.TypeVar("TLauncher", bound=BaseLauncher)


class HookManager(t.Generic[TInput, TOutput]):
    def __init__(self, hook: t.Callable):
        self._hook_reference: t.Callable = hook
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
            observer(value)


@final
class HookManagerCollection(t.Generic[TInput, TOutput]):
    def __init__(self):
        self._hook_managers: dict[t.Callable, HookManager[TInput, TOutput]] = {}

    def add_hook_manager(self, hook: t.Callable) -> None:
        """Add a new hook manager."""
        if hook not in self._hook_managers:
            self._hook_managers[hook] = HookManager(hook)

    def get_hook_manager(self, hook: t.Callable) -> HookManager[TInput, TOutput]:
        """Get a hook manager by its hook."""
        if hook not in self._hook_managers:
            raise KeyError(f"Hook manager for {hook.__name__} not found.")
        return self._hook_managers[hook]

    @classmethod
    def from_launcher(cls, launcher: TLauncher) -> "HookManagerCollection[TLauncher, TInput]":
        """Create a HookManagerCollection from a launcher."""
        collection = HookManagerCollection[TLauncher, TInput]()
        for hook in [launcher._pre_run_hook, launcher._run_hook, launcher._post_run_hook]:
            collection.add_hook_manager(hook)
        return collection
