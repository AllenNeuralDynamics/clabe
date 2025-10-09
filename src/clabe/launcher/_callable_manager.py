import functools
import logging
import typing as t
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

if TYPE_CHECKING:
    from ._base import Launcher
else:
    Launcher = Any

logger = logging.getLogger(__name__)

TException = t.TypeVar("TException", bound=Exception)

P = t.ParamSpec("P")
R = t.TypeVar("R")
_T = t.TypeVar("_T")


class _UnsetType:
    """A singleton class to represent an unset value."""

    __slots__ = ()
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


_UNSET = _UnsetType()


class Promise(t.Generic[P, R]):
    """
    A promise-like object that stores a callable and lazily evaluates its result.

    This class allows callables to be registered and their results to be accessed
    later through the .result property, enabling dependency chains between callables.
    """

    def __init__(self, callable: t.Callable[P, R]):
        self._fn = callable
        self._result: R | _UnsetType = _UNSET

    def invoke(self, *args: P.args, **kwargs: P.kwargs) -> R:
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
        self._result = self._fn(*args, **kwargs)
        return self._result

    def as_callable(self) -> t.Callable[..., R]:
        """Return a callable that returns the stored result, ignoring any arguments."""
        return lambda *args, **kwargs: self.result

    @property
    def result(self) -> R:
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
    def callable(self) -> t.Callable[P, R]:
        """Get the underlying callable."""
        return self._fn

    def __repr__(self) -> str:
        status = "executed" if self.has_result() else "pending"
        return f"Promise(func={self._fn.__name__}, status={status})"

    @classmethod
    def from_value(cls, value: _T) -> "Promise[t.Any, _T]":
        """Create a Promise from a resolved value."""

        # P is unconstrained here since we don't care about the input types
        # as a result we will just use Any for the hinting.Any
        # We will also use a new TypeVar just in case someone uses this method
        # from an instance.
        def _any_input(*args: t.Any, **kwargs: t.Any):
            return value

        promise = Promise[t.Any, _T](_any_input)
        promise._result = value
        return promise


class _CallableManager(t.Generic[P, R]):
    """
    Manages a collection of callables and their lazy evaluation.

    This class allows registering callables, which are wrapped in `_Promise`
    objects. It ensures that each callable is executed at most once and provides a
    mechanism to retrieve their results.
    """

    def __init__(self):
        self._callable_promises: Dict[Callable[P, R], Promise[P, R]] = {}
        self._has_run: bool = False

    def has_run(self) -> bool:
        """Check if callables have been run."""
        return self._has_run

    def register(self, callable: Callable[P, R]) -> Promise[P, R]:
        """Register a new callable and return its _Promise."""
        promise = Promise(callable)
        self._callable_promises[callable] = promise
        return promise

    def unregister(self, callable_fn: Callable[P, R]) -> Optional[Promise[P, R]]:
        """Remove a registered callable."""
        return self._callable_promises.pop(callable_fn, None)

    def clear(self) -> None:
        """Clear all registered callables."""
        self._callable_promises.clear()

    def run(self, *args: P.args, **kwargs: P.kwargs) -> None:
        """Run all registered callables"""
        if self._has_run:
            logger.warning("Callables have already been run. Skipping execution.")
            return

        for callable_fn, promise in self._callable_promises.items():
            promise.invoke(*args, **kwargs)

        self._has_run = True

    def get_result(self, callable_fn: Callable[P, R]) -> R:
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
            fn_name = getattr(callable_fn, "__name__", repr(callable_fn))
            raise KeyError(f"Callable {fn_name} not found in registered promises")
        return self._callable_promises[callable_fn].result


def ignore_errors(
    exception_types: t.Union[t.Type[BaseException], t.Tuple[t.Type[BaseException], ...]] = Exception,
    default_return: t.Any = None,
) -> t.Callable[[t.Callable[P, R]], t.Callable[P, Optional[R]]]:
    """
    A decorator that implements try-catch for the wrapped function.

    Args:
        exception_types: Exception type(s) to catch (default: Exception)
        default_return: Value to return if exception is caught (default: None)

    Returns:
        The decorated function with exception handling
    """

    def decorator(func: t.Callable) -> t.Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except exception_types as e:
                fn_name = getattr(func, "__name__", repr(func))
                logger.warning(f"Exception in {fn_name}: {e}")
                return default_return

        return wrapper

    return decorator


class _TryResult(t.Generic[R, TException]):
    """A wrapper for the result of a function that may raise an exception."""

    def __init__(self, result: R | TException):
        """Initialize with either a result or an exception.
        Args:
            result: The result of the function or an exception instance.
        """
        self._result = result

    @property
    def has_exception(self) -> bool:
        """Check if the result is an exception."""
        return isinstance(self._result, BaseException)

    @property
    def result(self) -> R:
        """Get the result if it's not an exception, else raise an error."""
        if self.has_exception:
            raise RuntimeError("Result is an exception, not a valid result.")
        return self._result  # type: ignore[return-value]

    def raise_from_exception(self) -> None:
        """Raise the stored exception if it exists."""
        if self.has_exception:
            assert isinstance(self._result, BaseException)
            raise self._result  # type: ignore[raise-value]

    @property
    def exception(self) -> Optional[TException]:
        """Get the exception if it exists, else return None."""
        if self.has_exception:
            return self._result  # type: ignore[return-value]
        return None

    def __repr__(self) -> str:
        if self.has_exception:
            return f"_TryResult(exception={self._result})"
        return f"_TryResult(result={self._result})"


class _MaybeResult(t.Generic[R]):
    """A wrapper for the result of a function that may not return a value"""

    def __init__(self, result: R | _UnsetType = _UNSET):
        """Initialize with either a result or None.
        Args:
            result: The result of the function or _UNSET if not set.
        """
        self._result = result

    @property
    def has_result(self) -> bool:
        """Check if the result is an exception."""
        return self._result is not _UNSET

    @property
    def result(self) -> R:
        """Get the result if the function returned a value, else raise an error."""
        if not self.has_result:
            raise RuntimeError("Result is not set.")
        return self._result  # type: ignore[return-value]


def try_catch(
    exception_types: t.Union[t.Type[TException], t.Tuple[t.Type[TException], ...]] = Exception,  # type: ignore[assignment]
) -> t.Callable[[t.Callable[P, R]], t.Callable[P, _TryResult[R, TException]]]:
    """
    A decorator that implements try-catch for the wrapped function.

    Args:
        exception_types: Exception type(s) to catch (default: Exception)

    Returns:
        The decorated function with exception handling that returns a _TryResult
    """

    def decorator(func: t.Callable[P, R]) -> t.Callable[P, _TryResult[R, TException]]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> _TryResult[R, TException]:
            try:
                return _TryResult(func(*args, **kwargs))
            except exception_types as e:
                fn_name = getattr(func, "__name__", repr(func))
                logger.warning(f"Exception in {fn_name}: {e}. Returning exception instance.")
                return _TryResult(e)

        return wrapper

    return decorator


def run_if(
    predicate: t.Callable[..., bool], *predicate_args, **predicate_kwargs
) -> t.Callable[[t.Callable[P, R]], t.Callable[P, _MaybeResult[R]]]:
    """
    A decorator that only runs the wrapped function if the predicate returns True.

    Args:
        predicate: A callable that returns a boolean.
        *predicate_args: Arguments to pass to the predicate.
        **predicate_kwargs: Keyword arguments to pass to the predicate.

    Returns:
        The decorated function that runs only if predicate(*predicate_args, **predicate_kwargs) is True.
    """

    def decorator(func: t.Callable[P, R]) -> t.Callable[P, _MaybeResult[R]]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            fn_name = getattr(func, "__name__", repr(func))
            if predicate(*predicate_args, **predicate_kwargs):
                logger.debug(f"Predicate passed for {fn_name}, executing function")
                return _MaybeResult(func(*args, **kwargs))
            logger.debug(f"Predicate failed for {fn_name}, skipping execution")
            return _MaybeResult()

        return wrapper

    return decorator
