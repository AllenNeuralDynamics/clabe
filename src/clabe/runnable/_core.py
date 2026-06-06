import contextlib
import contextvars
import functools
import inspect
import logging
import time
from typing import Any, Callable, Optional, TypeVar, overload

from ..ui._messages import MessageLevel
from ._activity import get_activity_indicator
from ._settings import ReportTier, RunnableSettings, RunnableSpec, resolve

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

#: True while any runnable is executing on the current thread/task. Inner
#: runnables read it to fold into the outermost one's user-facing surface.
_active: contextvars.ContextVar[bool] = contextvars.ContextVar("clabe_runnable_active", default=False)

_settings: Optional[RunnableSettings] = None


def _get_settings() -> RunnableSettings:
    """Return the process-wide settings, loading them once on first use."""
    global _settings
    if _settings is None:
        _settings = RunnableSettings()
    return _settings


def set_tier(tier: ReportTier) -> None:
    """Override the process-wide reporting tier, keeping other settings intact.

    Intended for entry points (e.g. the launcher mapping its verbosity flags)
    that set the house tier programmatically.
    """
    global _settings
    _settings = _get_settings().model_copy(update={"tier": tier})


def _notify(message: str, level: MessageLevel) -> None:
    # Imported lazily: clabe.ui depends on this package for the activity indicator.
    from ..ui import notify

    notify(message, level)


def _elapsed(started: float) -> str:
    return f"{time.perf_counter() - started:.2f}s"


@contextlib.contextmanager
def _lifecycle(spec: RunnableSpec, name: str):
    """Wrap a unit of work with logging, an activity spinner, and notifications.

    Nested runnables fold into the outermost one: only the outermost shows the
    spinner and emits start/success/failure notifications (so a handled inner
    failure stays quiet, while one that propagates is announced exactly once).
    Logging happens at every level.
    """
    eff = resolve(spec, _get_settings())
    reentrant = _active.get()
    token = _active.set(True)
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
        timing = f" in {_elapsed(started)}" if eff.include_timing else ""
        if not reentrant and eff.notify_success:
            _notify(f"{name} finished{timing}", MessageLevel.SUCCESS)
        logger.log(eff.log_level, "%s finished in %s", name, _elapsed(started))
    finally:
        _active.reset(token)


def _make_wrapper(fn: Callable, spec: RunnableSpec) -> Callable:
    """Build the sync/async wrapper for ``fn`` carrying its resolved ``spec``."""
    # A dotted qualname (without a closure marker) means ``fn`` is a method, so
    # an unnamed runnable can report the actual instance class at call time.
    is_method = "." in fn.__qualname__ and "<locals>" not in fn.__qualname__

    def name_for(args: tuple) -> str:
        if spec.name:
            return spec.name
        if is_method and args:
            return type(args[0]).__name__
        return fn.__name__

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            with _lifecycle(spec, name_for(args)):
                return await fn(*args, **kwargs)
    else:

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
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
    tier: Optional[ReportTier] = ...,
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
    tier: Optional[ReportTier] = ...,
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
    tier=None,
    notify=None,
    show_activity=None,
    notify_start=None,
    notify_success=None,
    notify_fail=None,
):
    """Wrap a callable with the shared runnable lifecycle (logging, activity
    spinner, notifications, and a future OTEL span).

    Use it as a decorator at definition time (``@runnable`` or
    ``@runnable(name=..., tier=...)``) or to rewrap an existing callable at the
    call site (``runnable(obj.method, tier=...)()``). Rewrapping merges over the
    callable's existing spec rather than nesting, so it never double-reports.

    Args:
        fn: The callable to wrap. Omit it to use ``runnable`` as a decorator
            factory.
        name: Display/span name. When omitted it is derived at call time from
            the instance class for methods, else the function name.
        tier: The :class:`ReportTier` for this runnable. Defaults to the global
            setting.
        notify: A start message to surface to the user (implies notifying on
            start).
        show_activity, notify_start, notify_success, notify_fail: Per-flag
            overrides of the tier; ``None`` inherits.
    """
    overrides = RunnableSpec(
        name=name,
        tier=tier,
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
