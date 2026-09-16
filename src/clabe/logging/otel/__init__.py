import atexit
import contextlib
import logging
from collections.abc import Generator
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode
from opentelemetry.util.types import AttributeValue

from ._api import event, record_exception, set_attribute, span
from ._settings import AindOtelSettings, OtelSettings
from ._setup import flush, merge_attributes, set_attributes

if TYPE_CHECKING:
    from aind_behavior_services.session import Session

    from ...launcher import Launcher

__all__ = [
    "AindOtelSettings",
    "OtelSettings",
    "bind_session",
    "enrich_attribute",
    "event",
    "record_exception",
    "run_span",
    "set_attribute",
    "span",
]

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("clabe.launcher")
_active_span: Span | None = None
_active_settings: OtelSettings | None = None
_atexit_installed = False


def _abandon_run_span(reason: str, *_args: object) -> None:
    """End the root span as failed and drain telemetry, for a run that is being torn down.

    Used by the interpreter-exit fallback (see :func:`_install_atexit_fallback`). A no-op when no
    run span is open, so it is safe to call more than once.

    Args:
        reason: Recorded as the span's error status, to distinguish these from clean ends.
    """
    global _active_span, _active_settings
    root, _active_span, _active_settings = _active_span, None, None
    if root is None:
        return
    try:
        root.set_status(Status(StatusCode.ERROR, reason))
        root.end()
    except Exception:  # observability must never break a run
        logger.warning("Failed to close the run span during teardown", exc_info=True)
    finally:
        flush()


def _install_atexit_fallback() -> None:
    """Best-effort export when interpreter shutdown skips the run context's ``finally``.

    Signal handling belongs to the host application: replacing process-wide handlers here
    can interfere with a frontend or service's own graceful shutdown. Hard termination and
    power loss cannot be intercepted reliably.
    """
    global _atexit_installed
    if _atexit_installed:
        return
    _atexit_installed = True
    atexit.register(_abandon_run_span, "process exited without ending the run span")


@contextlib.contextmanager
def run_span(launcher: "Launcher", experiment_name: str | None = None) -> Generator[Span, None, None]:
    """Instrument a launcher run: install telemetry if enabled, then open the root span.

    Reads :class:`AindOtelSettings` from clabe.yml. When enabled, the SDK is configured
    before the span opens (so ``@runnable`` app spans nest under it) and the OTLP log bridge
    is attached. The run's attribute bag is seeded from the profile's config-and-defaults
    (:meth:`~clabe.logging.otel._settings.OtelSettings.initial_attributes`) before the root span
    starts, so the root span and every child carry it. Telemetry is best effort — a missing
    SDK or bad config is logged and the run proceeds untraced. The span is ended and exported
    on exit, before the caller's cleanup, so a caller that then blocks (or is killed) cannot
    strand it; an ``atexit`` fallback covers orderly interpreter teardown that skips this
    ``finally`` without replacing the host application's signal handlers.

    Args:
        launcher: The launcher being instrumented.
        experiment_name: Name derived from the experiment callable, used when
            :attr:`~clabe.logging.otel.OtelSettings.run_name` is not set in config.
            Pass the result of :func:`~clabe.launcher.get_experiment_name`.

    Yields:
        The root span for the run.
    """
    global _active_span, _active_settings
    telemetry_configured = False
    try:
        settings: OtelSettings | None = AindOtelSettings()
    except Exception:  # observability must never break a run
        logger.warning("Failed to load otel settings; telemetry disabled", exc_info=True)
        settings = None

    if settings is not None and settings.enabled:
        try:
            from ._setup import configure

            configure(settings)
            set_attributes(settings.initial_attributes())
            telemetry_configured = True
        except Exception:  # observability must never break a run
            logger.warning("Failed to set up telemetry; continuing without it", exc_info=True)

    span_name = (settings.run_name if settings is not None else None) or experiment_name or "experiment"
    root = _tracer.start_span(span_name)
    _active_span, _active_settings = root, settings
    if telemetry_configured:
        _install_atexit_fallback()
    try:
        with trace.use_span(root, end_on_exit=False):
            yield root
    finally:
        # The atexit fallback clears the active span when it closes one, so this tells us whether
        # it got there first and the span is already ended and exported.
        closed_by_atexit = _active_span is not root
        _active_span, _active_settings = None, None
        set_attributes({})
        if not closed_by_atexit:
            root.end()
            # Drain before returning: the root span has just ended and is still queued, and
            # the caller may block (or be killed) before the next batch would have gone out.
            flush()


def bind_session(session: "Session") -> None:
    """Enrich the active run with the session identity, as soon as it is registered.

    Called by the launcher from :meth:`~clabe.launcher.Launcher.register_session`, this is
    the first point the session (subject, experimenter, ...) is known — stage 3 of attribute
    population. A no-op outside a run or when telemetry is off.

    Args:
        session: The session model just registered with the launcher.
    """
    if _active_span is None or _active_settings is None:
        return
    try:
        _enrich(_active_settings.session_attributes(session))
    except Exception:  # observability must never break a run
        logger.warning("Failed to bind session identity to telemetry", exc_info=True)


def enrich_attribute(name: str, value: AttributeValue) -> None:
    """Set or override a single telemetry attribute at any point during a run.

    Stage 4 of attribute population: the escape hatch for values that have no dedicated
    config field (e.g. ``instrument_id``) or that become known mid-run. The attribute is
    added to the run's bag — so it appears on every subsequent span and log — and stamped on
    the currently open root span immediately. A no-op outside a run or when telemetry is off.

    Args:
        name: The attribute key, emitted verbatim (use log-schema snake_case, e.g. ``rig_id``).
        value: The attribute value.
    """
    if _active_span is None:
        return
    _enrich({name: value})


def _enrich(attributes: dict[str, AttributeValue]) -> None:
    """Merge attributes into the run's bag and stamp them on the active root span."""
    merge_attributes(attributes)
    if _active_span is not None:
        for key, value in attributes.items():
            _active_span.set_attribute(key, value)
