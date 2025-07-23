import logging
import typing as t

logger = logging.getLogger(__name__)


class HookManager:
    def __init__(self, hook: t.Callable):
        self._hook_reference: t.Callable = hook
        self._observables: list[t.Callable[..., t.Any]] = []
        self._has_run: bool = False

    def has_run(self) -> bool:
        """Check if callables have been run."""
        return self._has_run

    def register(self, hook: t.Callable[..., t.Any]) -> None:
        """Register a new hook."""
        self._observables.append(hook)

    def remove(self, hook: t.Callable[..., t.Any]) -> None:
        """Remove a registered hook."""
        if hook in self._observables:
            self._observables.remove(hook)

    def clear(self) -> None:
        """Clear all registered callables."""
        self._observables.clear()

    def run(self, *, on_error_resume: bool = False) -> None:
        """Run all registered callables"""
        if self._has_run:
            logger.warning("Callables have already been run. Skipping execution.")
            return
        for observer in self._observables:
            try:
                observer()
            except Exception as e:
                if on_error_resume:
                    logger.error(f"Hook: {observer.__name__} - Error occurred in callable {observer.__name__}: {e}")
                else:
                    raise e
