import asyncio
import io
import threading
import time
from unittest.mock import patch

import pytest
from rich.console import Console

from clabe.apps import (
    ActivityIndicator,
    AsyncExecutor,
    Command,
    CommandError,
    CommandResult,
    Executor,
    ProgressExecutor,
    get_activity_indicator,
    identity_parser,
)
from clabe.apps._base import ExecutableApp
from clabe.apps._executors import AsyncLocalExecutor, LocalExecutor, _DefaultExecutorMixin
from clabe.apps._progress import _default_description

# ============================================================================
# Test doubles
# ============================================================================


class MockExecutor(Executor):
    """A synchronous executor that records commands and returns a fixed result."""

    def __init__(self, return_value: CommandResult, delay: float = 0.0):
        self.return_value = return_value
        self.delay = delay
        self.executed_commands: list[list[str]] = []

    def run(self, command: Command) -> CommandResult:
        if self.delay:
            time.sleep(self.delay)
        self.executed_commands.append(command.cmd)
        return self.return_value


class MockAsyncExecutor(AsyncExecutor):
    """An asynchronous executor that records commands and returns a fixed result."""

    def __init__(self, return_value: CommandResult, delay: float = 0.0):
        self.return_value = return_value
        self.delay = delay
        self.executed_commands: list[list[str]] = []

    async def run_async(self, command: Command) -> CommandResult:
        if self.delay:
            await asyncio.sleep(self.delay)
        self.executed_commands.append(command.cmd)
        return self.return_value


class RaisingExecutor(Executor):
    """A synchronous executor that always raises."""

    def run(self, command: Command) -> CommandResult:
        raise RuntimeError("boom")


def _terminal_indicator() -> ActivityIndicator:
    """An indicator that renders to an in-memory, forced-terminal console.

    This exercises the real Progress start/stop path without a TTY.
    """
    console = Console(file=io.StringIO(), force_terminal=True)
    return ActivityIndicator(console=console)


@pytest.fixture
def simple_command() -> Command[CommandResult]:
    return Command[CommandResult](cmd=["python", "-c", "print('hi')"], output_parser=identity_parser)


# ============================================================================
# _default_description
# ============================================================================


class TestDefaultDescription:
    def test_surfaces_script_target(self):
        cmd = Command[CommandResult](cmd=["/usr/bin/python", "script.py"], output_parser=identity_parser)
        assert _default_description(cmd) == "Running python (script.py)"

    def test_surfaces_last_script_through_uv(self):
        cmd = Command[CommandResult](
            cmd=["uv", "run", "--directory", "/abs/proj", "analyze.py", "--flag"],
            output_parser=identity_parser,
        )
        assert _default_description(cmd) == "Running uv (analyze.py)"

    def test_surfaces_bonsai_workflow_and_strips_exe(self):
        cmd = Command[CommandResult](
            cmd=["C:/tools/bonsai.exe", "C:/wf/workflow.bonsai", "--start"],
            output_parser=identity_parser,
        )
        assert _default_description(cmd) == "Running bonsai (workflow.bonsai)"

    def test_surfaces_module_target(self):
        cmd = Command[CommandResult](cmd=["python", "-m", "pytest", "tests/"], output_parser=identity_parser)
        assert _default_description(cmd) == "Running python (pytest)"

    def test_no_target_falls_back_to_program(self):
        cmd = Command[CommandResult](cmd=["python", "-c", "print('ok')"], output_parser=identity_parser)
        assert _default_description(cmd) == "Running python"

    def test_console_script_through_uv_has_no_target(self):
        cmd = Command[CommandResult](
            cmd=["uv", "run", "-q", "--directory", "/proj", "curriculum", "run", "--curriculum", "template"],
            output_parser=identity_parser,
        )
        assert _default_description(cmd) == "Running uv"

    def test_empty_command(self):
        cmd = Command[CommandResult](cmd=[], output_parser=identity_parser)
        assert _default_description(cmd) == "Running command"


# ============================================================================
# ActivityIndicator
# ============================================================================


class TestActivityIndicator:
    def test_disabled_on_non_terminal_console(self):
        console = Console(file=io.StringIO(), force_terminal=False)
        indicator = ActivityIndicator(console=console)
        assert indicator.enabled is False

    def test_enabled_on_terminal_console(self):
        indicator = _terminal_indicator()
        assert indicator.enabled is True

    def test_explicit_enabled_overrides_detection(self):
        console = Console(file=io.StringIO(), force_terminal=False)
        indicator = ActivityIndicator(console=console, enabled=True)
        assert indicator.enabled is True

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
        # Display torn down after the block
        assert indicator._progress is None
        assert indicator._active == 0

    def test_nested_activities_share_one_display(self):
        indicator = _terminal_indicator()
        with indicator.activity("outer"):
            progress_outer = indicator._progress
            with indicator.activity("inner"):
                assert indicator._progress is progress_outer
                assert indicator._active == 2
                assert len(indicator._progress.tasks) == 2
            assert indicator._active == 1
            assert indicator._progress is progress_outer
        assert indicator._progress is None

    def test_activity_cleans_up_on_exception(self):
        indicator = _terminal_indicator()
        with pytest.raises(ValueError):
            with indicator.activity("work"):
                raise ValueError("boom")
        assert indicator._progress is None
        assert indicator._active == 0

    def test_concurrent_threads_share_one_display(self):
        indicator = _terminal_indicator()
        seen_progress: list[object] = []
        barrier = threading.Barrier(3)

        def worker():
            with indicator.activity("threaded"):
                barrier.wait(timeout=5)
                seen_progress.append(indicator._progress)
                # hold briefly so all three overlap
                time.sleep(0.05)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All three observed the same single Progress instance while overlapping
        assert len(seen_progress) == 3
        assert all(p is seen_progress[0] for p in seen_progress)
        # Fully torn down afterwards
        assert indicator._progress is None
        assert indicator._active == 0


class TestGetActivityIndicator:
    def test_returns_singleton(self):
        assert get_activity_indicator() is get_activity_indicator()


# ============================================================================
# ProgressExecutor
# ============================================================================


class TestProgressExecutor:
    def test_satisfies_both_protocols(self):
        executor = ProgressExecutor(MockExecutor(CommandResult(stdout="", stderr="", exit_code=0)))
        assert isinstance(executor, Executor)
        assert isinstance(executor, AsyncExecutor)

    def test_run_delegates_and_returns_result(self, simple_command):
        expected = CommandResult(stdout="output", stderr="", exit_code=0)
        inner = MockExecutor(expected)
        executor = ProgressExecutor(inner, indicator=_terminal_indicator())

        result = executor.run(simple_command)

        assert result is expected
        assert inner.executed_commands == [simple_command.cmd]

    def test_run_shows_activity_while_executing(self, simple_command):
        indicator = _terminal_indicator()
        observed: dict[str, object] = {}

        class ObservingExecutor(Executor):
            def run(self, command: Command) -> CommandResult:
                observed["active"] = indicator._active
                observed["progress"] = indicator._progress
                return CommandResult(stdout="", stderr="", exit_code=0)

        ProgressExecutor(ObservingExecutor(), indicator=indicator).run(simple_command)

        assert observed["active"] == 1
        assert observed["progress"] is not None
        # cleaned up after
        assert indicator._progress is None

    @pytest.mark.asyncio
    async def test_run_async_delegates_and_returns_result(self, simple_command):
        expected = CommandResult(stdout="async output", stderr="", exit_code=0)
        inner = MockAsyncExecutor(expected)
        executor = ProgressExecutor(inner, indicator=_terminal_indicator())

        result = await executor.run_async(simple_command)

        assert result is expected
        assert inner.executed_commands == [simple_command.cmd]

    @pytest.mark.asyncio
    async def test_concurrent_async_share_one_display(self, simple_command):
        indicator = _terminal_indicator()
        inner = MockAsyncExecutor(CommandResult(stdout="", stderr="", exit_code=0), delay=0.05)
        executor = ProgressExecutor(inner, indicator=indicator)

        cmd1 = Command[CommandResult](cmd=["python", "-c", "print(1)"], output_parser=identity_parser)
        cmd2 = Command[CommandResult](cmd=["python", "-c", "print(2)"], output_parser=identity_parser)

        results = await asyncio.gather(executor.run_async(cmd1), executor.run_async(cmd2))

        assert all(r.ok for r in results)
        # torn down once both finished
        assert indicator._progress is None
        assert indicator._active == 0

    def test_run_propagates_inner_exception_and_cleans_up(self, simple_command):
        indicator = _terminal_indicator()
        executor = ProgressExecutor(RaisingExecutor(), indicator=indicator)

        with pytest.raises(RuntimeError, match="boom"):
            executor.run(simple_command)

        assert indicator._progress is None
        assert indicator._active == 0

    def test_run_propagates_command_error(self, simple_command):
        inner = MockExecutor(CommandResult(stdout="", stderr="fail", exit_code=1))

        # The decorator does not alter result handling; CommandError is raised by
        # Command.execute via check_returncode, but the executor itself returns
        # the failing result unchanged.
        executor = ProgressExecutor(inner, indicator=_terminal_indicator())
        result = executor.run(simple_command)
        assert result.exit_code == 1
        with pytest.raises(CommandError):
            result.check_returncode()

    def test_custom_string_description(self, simple_command):
        indicator = _terminal_indicator()
        captured: dict[str, str] = {}

        class CapturingExecutor(Executor):
            def run(self, command: Command) -> CommandResult:
                captured["desc"] = indicator._progress.tasks[0].description
                return CommandResult(stdout="", stderr="", exit_code=0)

        ProgressExecutor(CapturingExecutor(), description="My label", indicator=indicator).run(simple_command)
        assert captured["desc"] == "My label"

    def test_callable_description(self, simple_command):
        indicator = _terminal_indicator()
        captured: dict[str, str] = {}

        class CapturingExecutor(Executor):
            def run(self, command: Command) -> CommandResult:
                captured["desc"] = indicator._progress.tasks[0].description
                return CommandResult(stdout="", stderr="", exit_code=0)

        ProgressExecutor(
            CapturingExecutor(),
            description=lambda cmd: f"cmd has {len(cmd.cmd)} args",
            indicator=indicator,
        ).run(simple_command)
        assert captured["desc"] == "cmd has 3 args"

    def test_default_description_used(self, simple_command):
        indicator = _terminal_indicator()
        captured: dict[str, str] = {}

        class CapturingExecutor(Executor):
            def run(self, command: Command) -> CommandResult:
                captured["desc"] = indicator._progress.tasks[0].description
                return CommandResult(stdout="", stderr="", exit_code=0)

        ProgressExecutor(CapturingExecutor(), indicator=indicator).run(simple_command)
        assert captured["desc"] == "Running python"

    def test_run_on_async_only_executor_raises(self, simple_command):
        executor = ProgressExecutor(MockAsyncExecutor(CommandResult(stdout="", stderr="", exit_code=0)))
        with pytest.raises(TypeError, match="does not support synchronous run"):
            executor.run(simple_command)

    @pytest.mark.asyncio
    async def test_run_async_on_sync_only_executor_raises(self, simple_command):
        executor = ProgressExecutor(MockExecutor(CommandResult(stdout="", stderr="", exit_code=0)))
        with pytest.raises(TypeError, match="does not support run_async"):
            await executor.run_async(simple_command)


# ============================================================================
# _DefaultExecutorMixin show_progress integration
# ============================================================================


class _DummyApp(ExecutableApp, _DefaultExecutorMixin):
    """A minimal app exposing a trivial command for mixin tests."""

    @property
    def command(self) -> Command[CommandResult]:
        return Command[CommandResult](cmd=["python", "-c", "print('ok')"], output_parser=identity_parser)


class TestDefaultExecutorMixinShowProgress:
    def test_run_without_progress_uses_plain_executor(self):
        app = _DummyApp()
        with patch("clabe.apps._executors.ProgressExecutor") as mock_progress:
            result = app.run()
        assert result.ok is True
        mock_progress.assert_not_called()

    def test_run_with_progress_wraps_local_executor(self):
        app = _DummyApp()
        with patch("clabe.apps._executors.ProgressExecutor", wraps=ProgressExecutor) as mock_progress:
            result = app.run(show_progress=True)
        assert result.ok is True
        mock_progress.assert_called_once()
        (inner,), _ = mock_progress.call_args
        assert isinstance(inner, LocalExecutor)

    @pytest.mark.asyncio
    async def test_run_async_without_progress_uses_plain_executor(self):
        app = _DummyApp()
        with patch("clabe.apps._executors.ProgressExecutor") as mock_progress:
            result = await app.run_async()
        assert result.ok is True
        mock_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_async_with_progress_wraps_async_executor(self):
        app = _DummyApp()
        with patch("clabe.apps._executors.ProgressExecutor", wraps=ProgressExecutor) as mock_progress:
            result = await app.run_async(show_progress=True)
        assert result.ok is True
        mock_progress.assert_called_once()
        (inner,), _ = mock_progress.call_args
        assert isinstance(inner, AsyncLocalExecutor)

    def test_default_label_uses_class_name(self):
        app = _DummyApp()
        with patch("clabe.apps._executors.ProgressExecutor", wraps=ProgressExecutor) as mock_progress:
            app.run(show_progress=True)
        _, kwargs = mock_progress.call_args
        assert kwargs["description"] == "Running _DummyApp"

    def test_explicit_progress_description_overrides_default(self):
        app = _DummyApp()
        with patch("clabe.apps._executors.ProgressExecutor", wraps=ProgressExecutor) as mock_progress:
            app.run(show_progress=True, progress_description="My custom label")
        _, kwargs = mock_progress.call_args
        assert kwargs["description"] == "My custom label"

    def test_default_progress_description_format(self):
        assert _DummyApp()._progress_description(None) == "Running _DummyApp"
        assert _DummyApp()._progress_description("explicit") == "explicit"
