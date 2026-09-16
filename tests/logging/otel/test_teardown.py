"""Tests for run-span teardown: the root span must always be ended and exported.

The root span is the last span of a run to end, so it is the one most easily lost. These tests
pin the three guarantees that keep a finished run from looking unfinished downstream: the
span is drained as soon as it ends, abnormal interpreter teardown still closes it as an
error, and the fallback that does so is installed once.
"""

import atexit
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.trace import StatusCode

import clabe.logging.otel as otel_mod


@pytest.fixture
def disarmed_atexit(monkeypatch):
    """Keep the process-wide hook out of the test session and report registrations."""
    registered = []
    monkeypatch.setattr(otel_mod, "_atexit_installed", False)
    monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: registered.append(fn) or fn)
    return registered


def _run(settings=None, **kwargs):
    """Enter and exit run_span() with the SDK mocked out, returning the root span mock."""
    root = MagicMock()
    settings = settings or MagicMock(enabled=True, run_name=None)
    settings.initial_attributes.return_value = {}
    with (
        patch.object(otel_mod, "_tracer", MagicMock(start_span=MagicMock(return_value=root))),
        patch("clabe.logging.otel.AindOtelSettings", return_value=settings),
        patch("clabe.logging.otel._setup.configure"),
        otel_mod.run_span(MagicMock(), **kwargs),
    ):
        pass
    return root


# --- draining on a clean end ---


def test_span_is_flushed_after_it_ends(disarmed_atexit):
    """The root span is exported on exit rather than left waiting for the next batch."""
    calls = []
    root = MagicMock()
    root.end.side_effect = lambda: calls.append("end")

    with (
        patch.object(otel_mod, "_tracer", MagicMock(start_span=MagicMock(return_value=root))),
        patch("clabe.logging.otel.AindOtelSettings", return_value=MagicMock(enabled=False, run_name=None)),
        patch.object(otel_mod, "flush", lambda *a, **k: calls.append("flush")),
        otel_mod.run_span(MagicMock()),
    ):
        calls.append("body")

    assert calls == ["body", "end", "flush"], "the span must be ended before it is drained"


# --- abnormal teardown ---


def test_abandon_marks_the_span_failed_and_flushes(disarmed_atexit):
    """A run torn down mid-flight is closed as an error, so it is not merely absent."""
    root = MagicMock()
    with (
        patch.object(otel_mod, "_tracer", MagicMock(start_span=MagicMock(return_value=root))),
        patch("clabe.logging.otel.AindOtelSettings", return_value=MagicMock(enabled=False, run_name=None)),
        patch.object(otel_mod, "flush") as flush,
        otel_mod.run_span(MagicMock()),
    ):
        otel_mod._abandon_run_span("process exited during a run")

        assert root.end.call_count == 1
        assert flush.call_count == 1
        status = root.set_status.call_args.args[0]
        assert status.status_code is StatusCode.ERROR
        assert status.description == "process exited during a run"


def test_abandon_is_idempotent(disarmed_atexit):
    """Repeated fallback calls only end the active span once."""
    root = MagicMock()
    with (
        patch.object(otel_mod, "_tracer", MagicMock(start_span=MagicMock(return_value=root))),
        patch("clabe.logging.otel.AindOtelSettings", return_value=MagicMock(enabled=False, run_name=None)),
        patch.object(otel_mod, "flush"),
        otel_mod.run_span(MagicMock()),
    ):
        otel_mod._abandon_run_span("first")
        otel_mod._abandon_run_span("second")

    assert root.end.call_count == 1


def test_abandon_outside_a_run_is_a_noop(disarmed_atexit):
    """Called with no run open — as atexit will be on most exits — it does nothing."""
    with patch.object(otel_mod, "flush") as flush:
        otel_mod._abandon_run_span("no run")
    flush.assert_not_called()


# --- fallback installation ---


def test_atexit_fallback_is_registered_for_an_instrumented_run(disarmed_atexit):
    _run()

    assert len(disarmed_atexit) == 1


def test_atexit_fallback_is_installed_once_per_process(disarmed_atexit):
    """Repeated runs must not stack handlers."""
    _run()
    _run()

    assert len(disarmed_atexit) == 1


def test_atexit_fallback_is_not_installed_when_telemetry_is_disabled(disarmed_atexit):
    settings = MagicMock(enabled=False, run_name=None)

    root = _run(settings)

    assert root.end.call_count == 1
    assert disarmed_atexit == []
