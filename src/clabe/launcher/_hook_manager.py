import logging
import typing as t
from typing import TYPE_CHECKING, Any, Callable, Dict
import logging
import typing as t
from typing import Any, Optional
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
    
    This class allows hooks to be registered and their results to be accessed
    later through the .result property, enabling dependency chains between hooks.
    """
    
    def __init__(self, hook_func: t.Callable[[TInput], TOutput]):
        self._fn = hook_func
        self._result: TOutput | _UnsetType = _UNSET

    def invoke(self, value: TInput) -> TOutput:
        """
        Execute the hook function with the given value and store the result.
        
        Args:
            value: The input value to pass to the hook function
            
        Returns:
            The result of the hook function execution
        """
        if not self.has_result():
            assert not isinstance(self._result, _UnsetType)
            return self._result
        self._result = self._fn(value)
        return self._result
    
    @property
    def result(self) -> TOutput:
        """
        Lazily evaluate and return the result of the hook function.
        
        Returns:
            The result of the hook function execution.
            
        Raises:
            RuntimeError: If the hook hasn't been executed yet.
        """
        if not self.has_result():
            raise RuntimeError("Callable has not been executed yet. Call invoke() first.")
        
        return self._result  # type: ignore[return-value]
    
    def has_result(self) -> bool:
        """Check if the hook has a result."""
        return not self._result is _UNSET
    
    @property
    def hook_func(self) -> t.Callable[[Any], TOutput]:
        """Get the underlying hook function."""
        return self._fn
    
    def __repr__(self) -> str:
        status = "executed" if self.has_result() else "pending"
        return f"Promise(func={self._fn.__name__}, status={status})"

class _HookManager(t.Generic[TInput, TOutput]):
    def __init__(self):
        self._hook_promises: Dict[Callable[[TInput], TOutput], _Promise[TInput, TOutput]] = {}
        self._has_run: bool = False

    def has_run(self) -> bool:
        """Check if callables have been run."""
        return self._has_run

    def register(self, hook_promise: Callable[[TInput], TOutput]) -> _Promise[TInput, TOutput]:
        """Register a new hook promise and return it."""
        promise = _Promise(hook_promise)
        self._hook_promises[hook_promise] = promise
        return promise

    def unregister(self, callable_fn: Callable[[TInput], TOutput]) -> Optional[_Promise[TInput, TOutput]]:
        """Remove a registered hook promise by its callable."""
        return self._hook_promises.pop(callable_fn, None)

    def clear(self) -> None:
        """Clear all registered hook promises."""
        self._hook_promises.clear()

    def run(self, value: TInput) -> None:
        """Run all registered hook promises"""
        if self._has_run:
            logger.warning("Hook promises have already been run. Skipping execution.")
            return
        
        for callable_fn, promise in self._hook_promises.items():
            promise.invoke(value)
        
        self._has_run = True

    def get_result(self, callable_fn: Callable[[TInput], TOutput]) -> TOutput:
        """
        Get the result of a registered hook promise.
        
        Args:
            callable_fn: The callable to get the result for
            
        Returns:
            The result of the hook promise
            
        Raises:
            KeyError: If the callable is not found in registered promises
        """
        if callable_fn not in self._hook_promises:
            raise KeyError(f"Callable {callable_fn.__name__} not found in registered promises")
        return self._hook_promises[callable_fn].result

