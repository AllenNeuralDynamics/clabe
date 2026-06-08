import contextlib

import pytest

from clabe.runnable import _core, runnable
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


class Service:
    @runnable(name="Explicit name")
    def named(self) -> str:
        return "ok"

    @runnable
    def bare(self) -> str:
        return "ok"

    @runnable(name="boom")
    def fails(self):
        raise RuntimeError("kaboom")


class TestWrapping:
    def test_returns_value_unchanged(self):
        assert Service().named() == "ok"

    def test_preserves_metadata(self):
        assert Service.named.__name__ == "named"

    def test_bare_name_uses_instance_class(self, indicator):
        Service().bare()
        assert indicator.descriptions == ["Service"]

    def test_explicit_name_used(self, indicator):
        Service().named()
        assert indicator.descriptions == ["Explicit name"]

    @pytest.mark.asyncio
    async def test_async_wrapping(self, indicator):
        class AsyncService:
            @runnable(name="async work")
            async def go(self) -> str:
                return "done"

        assert await AsyncService().go() == "done"
        assert indicator.descriptions == ["async work"]

    def test_exception_propagates_unchanged(self):
        with pytest.raises(RuntimeError, match="kaboom"):
            Service().fails()


class TestNotifications:
    def test_notifies_start_and_success_by_default(self, frontend):
        Service().named()
        assert frontend.levels() == [MessageLevel.INFO, MessageLevel.SUCCESS]

    def test_notifies_error_on_failure(self, frontend):
        with pytest.raises(RuntimeError):
            Service().fails()
        assert frontend.levels() == [MessageLevel.INFO, MessageLevel.ERROR]

    def test_notify_message_overrides_start_text(self, frontend):
        runnable(Service().named, notify="Working…")()
        assert frontend.messages[0] == (MessageLevel.INFO, "Working…")

    def test_notify_start_false_suppresses_start(self, frontend):
        runnable(Service().named, notify_start=False)()
        assert frontend.levels() == [MessageLevel.SUCCESS]

    def test_notify_success_false_suppresses_success(self, frontend):
        runnable(Service().named, notify_success=False)()
        assert frontend.levels() == [MessageLevel.INFO]

    def test_notify_fail_false_suppresses_error(self, frontend):
        with pytest.raises(RuntimeError):
            runnable(Service().fails, notify_fail=False)()
        # start still fires, only the failure notification is suppressed
        assert frontend.levels() == [MessageLevel.INFO]


class TestIdempotency:
    def test_rewrap_does_not_nest(self, frontend, indicator):
        # ``named`` is already decorated; rewrapping must not double-report.
        Service().named()
        assert indicator.descriptions == ["Explicit name"]
        assert indicator.max_depth == 1
        assert frontend.levels() == [MessageLevel.INFO, MessageLevel.SUCCESS]

    def test_rewrap_merges_keeping_prior_name(self, indicator):
        # Call-site override keeps the baked-in name.
        runnable(Service().named, notify_success=False)()
        assert indicator.descriptions == ["Explicit name"]


class TestReentrancy:
    def test_nested_runnable_folds_into_outer(self, frontend, indicator):
        class Outer:
            @runnable(name="inner")
            def inner(self) -> str:
                return "inner-result"

            @runnable(name="outer")
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
            @runnable(name="inner")
            def inner(self):
                raise RuntimeError("informational")

            @runnable(name="outer")
            def outer(self) -> str:
                try:
                    self.inner()
                except RuntimeError:
                    return "recovered"
                return "unreached"

        assert Service().outer() == "recovered"
        # The inner failure was handled, so no error reached the user.
        assert frontend.messages == [
            (MessageLevel.INFO, "outer…"),
            (MessageLevel.SUCCESS, "outer finished"),
        ]

    def test_propagating_failure_notifies_once(self, frontend):
        class Service:
            @runnable(name="inner")
            def inner(self):
                raise RuntimeError("fatal")

            @runnable(name="outer")
            def outer(self):
                self.inner()

        with pytest.raises(RuntimeError, match="fatal"):
            Service().outer()
        assert frontend.levels() == [MessageLevel.INFO, MessageLevel.ERROR]


class TestCallSiteRewrap:
    def test_bound_method_rewrap_keeps_binding(self):
        service = Service()
        assert runnable(service.named, notify_start=False, notify_success=False)() == "ok"

    def test_rename_at_call_site(self, indicator):
        runnable(Service().named, name="Renamed")()
        assert indicator.descriptions == ["Renamed"]

    def test_wraps_undecorated_callable(self, frontend):
        calls = []

        def free(value):
            calls.append(value)
            return value * 2

        assert runnable(free)(21) == 42
        assert calls == [21]
        assert frontend.levels() == [MessageLevel.INFO, MessageLevel.SUCCESS]
