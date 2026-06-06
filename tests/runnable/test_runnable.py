import contextlib

import pytest

from clabe.runnable import ReportTier, RunnableSettings, _core, runnable
from clabe.ui import MessageLevel, set_current_frontend


class RecordingFrontend:
    """Captures notifications instead of rendering them."""

    def __init__(self):
        self.messages: list[tuple[MessageLevel, str]] = []

    def notify(self, message: str, level: MessageLevel = MessageLevel.INFO) -> None:
        self.messages.append((level, message))

    def levels(self) -> list[MessageLevel]:
        return [level for level, _ in self.messages]


class RecordingIndicator:
    """Records activity descriptions and tracks nesting depth."""

    def __init__(self):
        self.descriptions: list[str] = []
        self._depth = 0
        self.max_depth = 0

    @contextlib.contextmanager
    def activity(self, description: str):
        self.descriptions.append(description)
        self._depth += 1
        self.max_depth = max(self.max_depth, self._depth)
        try:
            yield
        finally:
            self._depth -= 1


@pytest.fixture
def frontend() -> RecordingFrontend:
    fe = RecordingFrontend()
    set_current_frontend(fe)
    yield fe
    set_current_frontend(None)


@pytest.fixture
def indicator(monkeypatch) -> RecordingIndicator:
    ind = RecordingIndicator()
    monkeypatch.setattr(_core, "get_activity_indicator", lambda: ind)
    return ind


@pytest.fixture(autouse=True)
def default_tier(monkeypatch):
    """Pin the global default to FAILURES so config files can't sway tests."""
    monkeypatch.setattr(_core, "_settings", RunnableSettings(tier=ReportTier.FAILURES))


class TestSettings:
    def test_tier_accepts_name(self):
        assert RunnableSettings(tier="lifecycle").tier is ReportTier.LIFECYCLE

    def test_tier_accepts_int(self):
        assert RunnableSettings(tier=30).tier is ReportTier.VERBOSE

    def test_tier_rejects_unknown_name(self):
        with pytest.raises(ValueError, match="Unknown report tier"):
            RunnableSettings(tier="bogus")

    def test_set_tier_overrides_keeping_other_settings(self, monkeypatch):
        monkeypatch.setattr(_core, "_settings", RunnableSettings(tier=ReportTier.FAILURES, notify_success=False))
        _core.set_tier(ReportTier.VERBOSE)
        assert _core._settings.tier is ReportTier.VERBOSE
        assert _core._settings.notify_success is False  # preserved


class Service:
    @runnable(name="Explicit name")
    def named(self) -> str:
        return "ok"

    @runnable
    def bare(self) -> str:
        return "ok"

    @runnable(name="boom", tier=ReportTier.FAILURES)
    def fails(self):
        raise RuntimeError("kaboom")


class TestWrapping:
    def test_returns_value_unchanged(self):
        assert Service().named() == "ok"

    def test_preserves_metadata(self):
        assert Service.named.__name__ == "named"

    def test_bare_name_uses_instance_class(self, indicator):
        runnable(Service().bare, tier=ReportTier.LIFECYCLE)()
        assert indicator.descriptions == ["Service"]

    def test_explicit_name_used(self, indicator):
        runnable(Service().named, tier=ReportTier.LIFECYCLE)()
        assert indicator.descriptions == ["Explicit name"]

    @pytest.mark.asyncio
    async def test_async_wrapping(self, indicator):
        class AsyncService:
            @runnable(name="async work", tier=ReportTier.LIFECYCLE)
            async def go(self) -> str:
                return "done"

        assert await AsyncService().go() == "done"
        assert indicator.descriptions == ["async work"]

    def test_exception_propagates_unchanged(self):
        with pytest.raises(RuntimeError, match="kaboom"):
            Service().fails()


class TestTiers:
    def test_silent_does_not_notify(self, frontend, indicator):
        runnable(Service().named, tier=ReportTier.SILENT)()
        assert frontend.messages == []

    def test_failures_notifies_only_on_error(self, frontend):
        runnable(Service().named, tier=ReportTier.FAILURES)()
        assert frontend.messages == []
        with pytest.raises(RuntimeError):
            runnable(Service().fails, tier=ReportTier.FAILURES)()
        assert frontend.levels() == [MessageLevel.ERROR]

    def test_lifecycle_notifies_start_and_success(self, frontend):
        runnable(Service().named, tier=ReportTier.LIFECYCLE)()
        assert frontend.levels() == [MessageLevel.INFO, MessageLevel.SUCCESS]

    def test_notify_message_overrides_start_text(self, frontend):
        runnable(Service().named, tier=ReportTier.LIFECYCLE, notify="Working…")()
        assert frontend.messages[0] == (MessageLevel.INFO, "Working…")


class TestIdempotency:
    def test_rewrap_does_not_nest(self, frontend, indicator):
        # ``named`` is already decorated; rewrapping must not double-report.
        runnable(Service().named, tier=ReportTier.LIFECYCLE)()
        assert indicator.descriptions == ["Explicit name"]
        assert indicator.max_depth == 1
        assert frontend.levels() == [MessageLevel.INFO, MessageLevel.SUCCESS]

    def test_rewrap_merges_keeping_prior_name(self, indicator):
        # Call-site override bumps the tier but keeps the baked-in name.
        runnable(Service().named, tier=ReportTier.LIFECYCLE)()
        assert indicator.descriptions == ["Explicit name"]


class TestReentrancy:
    def test_nested_runnable_folds_into_outer(self, frontend, indicator):
        class Outer:
            @runnable(name="inner", tier=ReportTier.LIFECYCLE)
            def inner(self) -> str:
                return "inner-result"

            @runnable(name="outer", tier=ReportTier.LIFECYCLE)
            def outer(self) -> str:
                return self.inner()

        Outer().outer()

        # Only the outer activity is shown to the user.
        assert indicator.descriptions == ["outer"]
        assert indicator.max_depth == 1
        # Only the outer lifecycle notifies start/success.
        assert frontend.messages == [
            (MessageLevel.INFO, "outer…"),
            (MessageLevel.SUCCESS, "outer finished"),
        ]

    def test_handled_inner_failure_stays_quiet(self, frontend):
        class Service:
            @runnable(name="inner", tier=ReportTier.FAILURES)
            def inner(self):
                raise RuntimeError("informational")

            @runnable(name="outer", tier=ReportTier.FAILURES)
            def outer(self) -> str:
                try:
                    self.inner()
                except RuntimeError:
                    return "recovered"
                return "unreached"

        assert Service().outer() == "recovered"
        # The inner failure was handled, so no error reached the user.
        assert frontend.messages == []

    def test_propagating_failure_notifies_once(self, frontend):
        class Service:
            @runnable(name="inner", tier=ReportTier.FAILURES)
            def inner(self):
                raise RuntimeError("fatal")

            @runnable(name="outer", tier=ReportTier.FAILURES)
            def outer(self):
                self.inner()

        with pytest.raises(RuntimeError, match="fatal"):
            Service().outer()
        assert frontend.levels() == [MessageLevel.ERROR]


class TestCallSiteRewrap:
    def test_bound_method_rewrap_keeps_binding(self):
        service = Service()
        assert runnable(service.named, tier=ReportTier.SILENT)() == "ok"

    def test_rename_at_call_site(self, indicator):
        runnable(Service().named, name="Renamed", tier=ReportTier.LIFECYCLE)()
        assert indicator.descriptions == ["Renamed"]

    def test_wraps_undecorated_callable(self, frontend):
        calls = []

        def free(value):
            calls.append(value)
            return value * 2

        assert runnable(free, tier=ReportTier.LIFECYCLE)(21) == 42
        assert calls == [21]
        assert frontend.levels() == [MessageLevel.INFO, MessageLevel.SUCCESS]
