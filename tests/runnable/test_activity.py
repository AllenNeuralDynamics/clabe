import io
import threading
import time

from rich.console import Console

from clabe.runnable import ActivityIndicator, get_activity_indicator


def _terminal_indicator() -> ActivityIndicator:
    """An indicator rendering to an in-memory, forced-terminal console.

    Exercises the real Progress start/stop path without a TTY.
    """
    console = Console(file=io.StringIO(), force_terminal=True)
    return ActivityIndicator(console=console)


class TestActivityIndicator:
    def test_disabled_on_non_terminal_console(self):
        console = Console(file=io.StringIO(), force_terminal=False)
        assert ActivityIndicator(console=console).enabled is False

    def test_enabled_on_terminal_console(self):
        assert _terminal_indicator().enabled is True

    def test_explicit_enabled_overrides_detection(self):
        console = Console(file=io.StringIO(), force_terminal=False)
        assert ActivityIndicator(console=console, enabled=True).enabled is True

    def test_disabled_activity_is_noop(self):
        console = Console(file=io.StringIO(), force_terminal=False)
        indicator = ActivityIndicator(console=console)
        with indicator.activity("work"):
            assert indicator._progress is None
        assert indicator._progress is None

    def test_activity_starts_and_stops_display(self):
        indicator = _terminal_indicator()
        with indicator.activity("work"):
            assert indicator._progress is not None
            assert indicator._active == 1
        assert indicator._progress is None
        assert indicator._active == 0

    def test_nested_activities_share_one_display(self):
        indicator = _terminal_indicator()
        with indicator.activity("outer"):
            outer = indicator._progress
            with indicator.activity("inner"):
                assert indicator._progress is outer
                assert indicator._active == 2
                assert len(indicator._progress.tasks) == 2
            assert indicator._active == 1
        assert indicator._progress is None

    def test_activity_cleans_up_on_exception(self):
        indicator = _terminal_indicator()
        try:
            with indicator.activity("work"):
                raise ValueError("boom")
        except ValueError:
            pass
        assert indicator._progress is None
        assert indicator._active == 0

    def test_concurrent_threads_share_one_display(self):
        indicator = _terminal_indicator()
        seen: list[object] = []
        barrier = threading.Barrier(3)

        def worker():
            with indicator.activity("threaded"):
                barrier.wait(timeout=5)
                seen.append(indicator._progress)
                time.sleep(0.05)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(seen) == 3
        assert all(p is seen[0] for p in seen)
        assert indicator._progress is None
        assert indicator._active == 0


class TestGetActivityIndicator:
    def test_returns_singleton(self):
        assert get_activity_indicator() is get_activity_indicator()
