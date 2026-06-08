import asyncio
import contextlib
import contextvars
import functools
import inspect
import logging
import time
from typing import Any, Callable, Optional, TypeVar, overload

from ..ui._messages import MessageLevel
from ._activity import get_activity_indicator
from ._settings import RunnableSpec, _include_timing

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

_active: contextvars.ContextVar[bool] = contextvars.ContextVar("clabe_runnable_active", default=False)
#: Tracks which asyncio Task set _active so gather()-spawned sibling tasks
#: (which copy context) are not mistaken for nested runnables.
_active_task: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar("clabe_runnable_task", default=None)

#: Defaults applied when a spec field is still None after merging.
_DEFAULTS: dict[str, bool] = {
    "show_activity": True,
    "notify_start": True,
    "notify_success": True,
    "notify_fail": True,
}


def _fill(spec: RunnableSpec) -> RunnableSpec:
    """Fill any remaining None flags with their defaults."""
    from dataclasses import replace

    updates = {k: v for k, v in _DEFAULTS.items() if getattr(spec, k) is None}
    if spec.notify is not None:
        updates.setdefault("notify_start", True)
    return replace(spec, **updates) if updates else spec


def _notify(message: str, level: MessageLevel) -> None:
    """Surface a message to the active frontend (imported lazily to avoid a cycle)."""
    from ..ui import notify

    notify(message, level)


def _elapsed(started: float) -> str:
    """Format the seconds elapsed since ``started`` (a ``perf_counter`` value)."""
    return f"{time.perf_counter() - started:.2f}s"


@contextlib.contextmanager
def _lifecycle(spec: RunnableSpec, name: str):
    """Wrap a unit of work with logging, an activity spinner, and notifications.

    Nested runnables fold into the outermost one: only the outermost shows the
    spinner and emits start/success/failure notifications (so a handled inner
    failure stays quiet, while one that propagates is announced exactly once).
    Logging happens at every level.
    """
    eff = _fill(spec)
    try:
        current_task = asyncio.current_task()
    except RuntimeError:
        current_task = None
    outer_task = _active_task.get()
    reentrant = _active.get() and (current_task is None or current_task is outer_task)
    token = _active.set(True)
    task_token = _active_task.set(current_task)
    started = time.perf_counter()

    if not reentrant and eff.notify_start:
        _notify(spec.notify or f"{name}…", MessageLevel.INFO)
    logger.log(eff.log_level, "%s started", name)

    display = (
        get_activity_indicator().activity(name) if eff.show_activity and not reentrant else contextlib.nullcontext()
    )
    try:
        with display:
            yield
    except Exception as exc:
        if not reentrant and eff.notify_fail:
            _notify(f"{name} failed: {exc}", MessageLevel.ERROR)
        logger.log(eff.log_level, "%s failed after %s", name, _elapsed(started))
        raise
    else:
        timing = f" in {_elapsed(started)}" if _include_timing else ""
        if not reentrant and eff.notify_success:
            _notify(f"{name} finished{timing}", MessageLevel.SUCCESS)
        logger.log(eff.log_level, "%s finished in %s", name, _elapsed(started))
    finally:
        _active.reset(token)
        _active_task.reset(task_token)


def _make_wrapper(fn: Callable, spec: RunnableSpec) -> Callable:
    """Build the sync/async wrapper for ``fn`` carrying its resolved ``spec``."""
    # A dotted qualname (without a closure marker) means ``fn`` is a method, so
    # an unnamed runnable can report the actual instance class at call time.
    is_method = "." in fn.__qualname__ and "<locals>" not in fn.__qualname__

    def name_for(args: tuple) -> str:
        """Resolve the display name for a call, deriving it from the instance when unnamed."""
        if spec.name:
            return spec.name
        if is_method and args:
            return type(args[0]).__name__
        return fn.__name__

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            """Run the wrapped coroutine inside the runnable lifecycle."""
            with _lifecycle(spec, name_for(args)):
                return await fn(*args, **kwargs)
    else:

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            """Run the wrapped function inside the runnable lifecycle."""
            with _lifecycle(spec, name_for(args)):
                return fn(*args, **kwargs)

    wrapper.__runnable_original__ = fn
    wrapper.__runnable_spec__ = spec
    return wrapper


@overload
def runnable(
    fn: F,
    *,
    name: Optional[str] = ...,
    notify: Optional[str] = ...,
    show_activity: Optional[bool] = ...,
    notify_start: Optional[bool] = ...,
    notify_success: Optional[bool] = ...,
    notify_fail: Optional[bool] = ...,
) -> F: ...


@overload
def runnable(
    fn: None = ...,
    *,
    name: Optional[str] = ...,
    notify: Optional[str] = ...,
    show_activity: Optional[bool] = ...,
    notify_start: Optional[bool] = ...,
    notify_success: Optional[bool] = ...,
    notify_fail: Optional[bool] = ...,
) -> Callable[[F], F]: ...


def runnable(
    fn=None,
    *,
    name=None,
    notify=None,
    show_activity=None,
    notify_start=None,
    notify_success=None,
    notify_fail=None,
):
    """Wrap a callable with the shared runnable lifecycle (logging, activity
    spinner, notifications, and a future OTEL span).

    Use it as a decorator at definition time (``@runnable`` or
    ``@runnable(name=..., notify=...)``) or to rewrap an existing callable at
    the call site (``runnable(obj.method, notify_success=False)()``). Rewrapping
    merges over the callable's existing spec rather than nesting, so it never
    double-reports.

    By default all notifications are on (start, success, failure). Pass
    ``notify_start=False`` etc. to suppress individual ones.

    Args:
        fn: The callable to wrap. Omit to use ``runnable`` as a decorator factory.
        name: Display name. Derived from the instance class or function name when omitted.
        notify: Custom start message (shown instead of ``"<name>…"``).
        show_activity: Show a spinner while running. Default ``True``.
        notify_start: Notify on start. Default ``True``.
        notify_success: Notify on success. Default ``True``.
        notify_fail: Notify on failure. Default ``True``.
    """
    overrides = RunnableSpec(
        name=name,
        notify=notify,
        show_activity=show_activity,
        notify_start=notify_start,
        notify_success=notify_success,
        notify_fail=notify_fail,
    )

    def wrap(target):
        # Unwrap to the original function (idempotent rewrap) and merge specs.
        # Bound methods are rebound so the wrapped callable keeps its instance.
        host = target.__func__ if inspect.ismethod(target) else target
        original = getattr(host, "__runnable_original__", host)
        prior = getattr(host, "__runnable_spec__", RunnableSpec())
        wrapper = _make_wrapper(original, prior.merge(overrides))
        return wrapper.__get__(target.__self__) if inspect.ismethod(target) else wrapper

    return wrap(fn) if fn is not None else wrap
