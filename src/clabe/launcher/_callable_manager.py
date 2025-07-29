import logging
import typing as t
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from git import Optional

if TYPE_CHECKING:
    from ._base import BaseLauncher
else:
    BaseLauncher = Any

logger = logging.getLogger(__name__)

TInput = t.TypeVar("TInput")
TOutput = t.TypeVar("TOutput")
TLauncher = t.TypeVar("TLauncher", bound=BaseLauncher)


class _UnsetType:
    """A singleton class to represent an unset value."""

    __slots__ = ()
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


_UNSET = _UnsetType()


class _Promise(t.Generic[TInput, TOutput]):
    """
    A promise-like object that stores a callable and lazily evaluates its result.

    This class allows callables to be registered and their results to be accessed
    later through the .result property, enabling dependency chains between callables.
    """

    def __init__(self, callable: t.Callable[[TInput], TOutput]):
        self._fn = callable
        self._result: TOutput | _UnsetType = _UNSET

    def invoke(self, value: TInput) -> TOutput:
        """
        Execute the callable with the given value and store the result.

        Args:
            value: The input value to pass to the callable

        Returns:
            The result of the callable execution
        """
        if self.has_result():
            assert not isinstance(self._result, _UnsetType)
            return self._result
        self._result = self._fn(value)
        return self._result

    @property
    def result(self) -> TOutput:
        """
        Lazily evaluate and return the result of the callable.

        Returns:
            The result of the callable execution.

        Raises:
            RuntimeError: If the callable hasn't been executed yet.
        """
        if not self.has_result():
            raise RuntimeError("Callable has not been executed yet. Call invoke() first.")

        return self._result  # type: ignore[return-value]

    def has_result(self) -> bool:
        """Check if the callable has a result."""
        return self._result is not _UNSET

    @property
    def callable(self) -> t.Callable[[Any], TOutput]:
        """Get the underlying callable."""
        return self._fn

    def __repr__(self) -> str:
        status = "executed" if self.has_result() else "pending"
        return f"Promise(func={self._fn.__name__}, status={status})"


class _CallableManager(t.Generic[TInput, TOutput]):
    """
    Manages a collection of callables and their lazy evaluation.

    This class allows registering callables, which are wrapped in `_Promise`
    objects. It ensures that each callable is executed at most once and provides a
    mechanism to retrieve their results.
    """

    def __init__(self):
        self._callable_promises: Dict[Callable[[TInput], TOutput], _Promise[TInput, TOutput]] = {}
        self._has_run: bool = False

    def has_run(self) -> bool:
        """Check if callables have been run."""
        return self._has_run

    def register(self, callable: Callable[[TInput], TOutput]) -> _Promise[TInput, TOutput]:
        """Register a new callable and return its _Promise."""
        promise = _Promise(callable)
        self._callable_promises[callable] = promise
        return promise

    def unregister(self, callable_fn: Callable[[TInput], TOutput]) -> Optional[_Promise[TInput, TOutput]]:
        """Remove a registered callable."""
        return self._callable_promises.pop(callable_fn, None)

    def clear(self) -> None:
        """Clear all registered callables."""
        self._callable_promises.clear()

    def run(self, value: TInput) -> None:
        """Run all registered callables"""
        if self._has_run:
            logger.warning("Callable have already been run. Skipping execution.")
            return

        for callable_fn, promise in self._callable_promises.items():
            promise.invoke(value)

        self._has_run = True

    def get_result(self, callable_fn: Callable[[TInput], TOutput]) -> TOutput:
        """
        Get the result of a registered callable.

        Args:
            callable_fn: The callable to get the result for

        Returns:
            The result of the callable promise

        Raises:
            KeyError: If the callable is not found in registered promises
        """
        if callable_fn not in self._callable_promises:
            raise KeyError(f"Callable {callable_fn.__name__} not found in registered promises")
        return self._callable_promises[callable_fn].result
