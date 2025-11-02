import asyncio
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from clabe.apps import (
    AsyncExecutor,
    BonsaiApp,
    Command,
    CommandError,
    CommandResult,
    Executor,
    PythonScriptApp,
    identity_parser,
)
from clabe.apps._executors import AsyncLocalExecutor, LocalExecutor
from clabe.apps.open_ephys import OpenEphysApp, Status, _OpenEphysGuiClient

# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def simple_command() -> Command[CommandResult]:
    """A simple command that echoes text."""
    return Command[CommandResult](
        cmd="python -c \"print('hello')\"",
        output_parser=identity_parser,
    )


@pytest.fixture
def failing_command() -> Command[CommandResult]:
    """A command that fails."""
    return Command[CommandResult](
        cmd='python -c "import sys; sys.exit(1)"',
        output_parser=identity_parser,
    )


@pytest.fixture
def local_executor() -> LocalExecutor:
    """A local executor for running commands."""
    return LocalExecutor()


@pytest.fixture
def async_local_executor() -> AsyncLocalExecutor:
    """An async local executor for running commands."""
    return AsyncLocalExecutor()


class MockExecutor(Executor):
    """A mock executor that captures commands without running them."""

    def __init__(self, return_value: CommandResult):
        self.return_value = return_value
        self.executed_commands: list[str] = []

    def run(self, command: Command) -> CommandResult:
        self.executed_commands.append(command.cmd)
        return self.return_value


class MockAsyncExecutor(AsyncExecutor):
    """A mock async executor that captures commands without running them."""

    def __init__(self, return_value: CommandResult):
        self.return_value = return_value
        self.executed_commands: list[str] = []

    async def run_async(self, command: Command) -> CommandResult:
        self.executed_commands.append(command.cmd)
        return self.return_value


# ============================================================================
# CommandResult Tests
# ============================================================================


class TestCommandResult:
    """Tests for CommandResult model."""

    def test_command_result_ok_property_success(self):
        """Test that ok returns True when exit_code is 0."""
        result = CommandResult(stdout="output", stderr="", exit_code=0)
        assert result.ok is True

    def test_command_result_ok_property_failure(self):
        """Test that ok returns False when exit_code is non-zero."""
        result = CommandResult(stdout="", stderr="error", exit_code=1)
        assert result.ok is False

    def test_command_result_with_none_values(self):
        """Test CommandResult with None stdout/stderr."""
        result = CommandResult(stdout=None, stderr=None, exit_code=0)
        assert result.ok is True
        assert result.stdout is None
        assert result.stderr is None


# ============================================================================
# Command Tests
# ============================================================================


class TestCommand:
    """Tests for Command class."""

    def test_command_initialization(self):
        """Test basic command initialization."""
        cmd = Command[str](cmd="echo hello", output_parser=lambda r: r.stdout or "")
        assert cmd.cmd == "echo hello"

    def test_command_append_arg_single_string(self):
        """Test appending a single argument."""
        cmd = Command[CommandResult](cmd="echo", output_parser=identity_parser)
        cmd.append_arg("hello")
        assert cmd.cmd == "echo hello"

    def test_command_append_arg_list(self):
        """Test appending multiple arguments as a list."""
        cmd = Command[CommandResult](cmd="echo", output_parser=identity_parser)
        cmd.append_arg(["hello", "world"])
        assert cmd.cmd == "echo hello world"

    def test_command_append_arg_filters_empty_strings(self):
        """Test that empty strings are filtered out when appending args."""
        cmd = Command[CommandResult](cmd="echo", output_parser=identity_parser)
        cmd.append_arg(["hello", "", "world"])
        assert cmd.cmd == "echo hello world"

    def test_command_append_arg_chaining(self):
        """Test that append_arg returns self for chaining."""
        cmd = Command[CommandResult](cmd="echo", output_parser=identity_parser)
        result = cmd.append_arg("hello").append_arg("world")
        assert result is cmd
        assert cmd.cmd == "echo hello world"

    def test_command_result_property_before_execution_raises(self):
        """Test that accessing result before execution raises RuntimeError."""
        cmd = Command[CommandResult](cmd="echo hello", output_parser=identity_parser)
        with pytest.raises(RuntimeError, match="Command has not been executed yet"):
            _ = cmd.result

    def test_command_execute_with_mock_executor(self):
        """Test command execution with a mock executor."""
        expected_result = CommandResult(stdout="output", stderr="", exit_code=0)
        executor = MockExecutor(return_value=expected_result)

        cmd = Command[CommandResult](cmd="echo hello", output_parser=identity_parser)
        result = cmd.execute(executor)

        assert result == expected_result
        assert "echo hello" in executor.executed_commands
        assert cmd.result == expected_result

    @pytest.mark.asyncio
    async def test_command_execute_async_with_mock_executor(self):
        """Test async command execution with a mock async executor."""
        expected_result = CommandResult(stdout="output", stderr="", exit_code=0)
        executor = MockAsyncExecutor(return_value=expected_result)

        cmd = Command[CommandResult](cmd="echo hello", output_parser=identity_parser)
        result = await cmd.execute_async(executor)

        assert result == expected_result
        assert "echo hello" in executor.executed_commands
        assert cmd.result == expected_result

    def test_command_custom_output_parser(self):
        """Test command with a custom output parser."""

        def parse_int(result: CommandResult) -> int:
            return int(result.stdout.strip()) if result.stdout else 0

        cmd = Command[int](cmd='python -c "print(42)"', output_parser=parse_int)
        executor = MockExecutor(CommandResult(stdout="42\n", stderr="", exit_code=0))

        result = cmd.execute(executor)
        assert result == 42


# ============================================================================
# Executor Tests
# ============================================================================


class TestLocalExecutor:
    """Tests for LocalExecutor."""

    def test_local_executor_runs_simple_command(self, local_executor: LocalExecutor):
        """Test that LocalExecutor can run a simple command."""
        cmd = Command[CommandResult](cmd="python -c \"print('test')\"", output_parser=identity_parser)
        result = cmd.execute(local_executor)

        assert result.ok is True
        assert "test" in (result.stdout or "")

    def test_local_executor_captures_stderr(self, local_executor: LocalExecutor):
        """Test that LocalExecutor captures stderr."""
        cmd = Command[CommandResult](
            cmd="python -c \"import sys; sys.stderr.write('error')\"", output_parser=identity_parser
        )
        result = cmd.execute(local_executor)

        assert result.ok is True
        assert "error" in (result.stderr or "")

    def test_local_executor_handles_failing_command(self, local_executor: LocalExecutor, failing_command: Command):
        """Test that LocalExecutor properly handles failing commands."""
        with pytest.raises(CommandError):
            failing_command.execute(local_executor)

    def test_local_executor_with_custom_cwd(self, tmp_path: Path):
        """Test LocalExecutor with a custom working directory."""
        executor = LocalExecutor(cwd=tmp_path)
        cmd = Command[CommandResult](
            cmd='python -c "import os; print(os.getcwd())"',
            output_parser=identity_parser,
        )
        result = cmd.execute(executor)

        assert result.ok is True
        # Normalize paths for comparison (handle Windows/Unix differences)
        assert str(tmp_path).lower() in (result.stdout or "").lower()


class TestAsyncLocalExecutor:
    """Tests for AsyncLocalExecutor."""

    @pytest.mark.asyncio
    async def test_async_executor_runs_simple_command(self, async_local_executor: AsyncLocalExecutor):
        """Test that AsyncLocalExecutor can run a simple command."""
        cmd = Command[CommandResult](cmd="python -c \"print('async test')\"", output_parser=identity_parser)
        result = await cmd.execute_async(async_local_executor)

        assert result.ok is True
        assert "async test" in (result.stdout or "")

    @pytest.mark.asyncio
    async def test_async_executor_handles_failing_command(
        self, async_local_executor: AsyncLocalExecutor, failing_command: Command
    ):
        """Test that AsyncLocalExecutor properly handles failing commands."""
        with pytest.raises(CommandError):
            await failing_command.execute_async(async_local_executor)

    @pytest.mark.asyncio
    async def test_async_executor_concurrent_execution(self, async_local_executor: AsyncLocalExecutor):
        """Test running multiple commands concurrently."""
        cmd1 = Command[CommandResult](cmd="python -c \"print('cmd1')\"", output_parser=identity_parser)
        cmd2 = Command[CommandResult](cmd="python -c \"print('cmd2')\"", output_parser=identity_parser)

        results = await asyncio.gather(
            cmd1.execute_async(async_local_executor), cmd2.execute_async(async_local_executor)
        )

        assert all(r.ok for r in results)
        assert "cmd1" in (results[0].stdout or "")
        assert "cmd2" in (results[1].stdout or "")


# ============================================================================
# BonsaiApp Tests
# ============================================================================


class TestBonsaiApp:
    """Tests for BonsaiApp."""

    @pytest.fixture
    def temp_bonsai_files(self, tmp_path: Path):
        """Create temporary bonsai executable and workflow files."""
        exe = tmp_path / "bonsai.exe"
        workflow = tmp_path / "workflow.bonsai"
        exe.touch()
        workflow.touch()
        return {"exe": exe, "workflow": workflow}

    def test_bonsai_app_initialization(self, temp_bonsai_files):
        """Test BonsaiApp initialization with valid files."""
        app = BonsaiApp(
            executable=temp_bonsai_files["exe"],
            workflow=temp_bonsai_files["workflow"],
        )
        assert app.executable == temp_bonsai_files["exe"].resolve()
        assert app.workflow == temp_bonsai_files["workflow"].resolve()

    def test_bonsai_app_builds_command_correctly(self, temp_bonsai_files):
        """Test that BonsaiApp builds the correct command."""
        app = BonsaiApp(
            executable=temp_bonsai_files["exe"],
            workflow=temp_bonsai_files["workflow"],
            is_editor_mode=True,
            is_start_flag=True,
        )
        cmd = app.command.cmd
        assert str(temp_bonsai_files["exe"]) in cmd
        assert str(temp_bonsai_files["workflow"]) in cmd
        assert "--start" in cmd

    def test_bonsai_app_no_editor_mode(self, temp_bonsai_files):
        """Test BonsaiApp command in no-editor mode."""
        app = BonsaiApp(
            executable=temp_bonsai_files["exe"],
            workflow=temp_bonsai_files["workflow"],
            is_editor_mode=False,
        )
        cmd = app.command.cmd
        assert "--no-editor" in cmd
        assert "--start" not in cmd

    def test_bonsai_app_with_additional_properties(self, temp_bonsai_files):
        """Test BonsaiApp with additional properties."""
        app = BonsaiApp(
            executable=temp_bonsai_files["exe"],
            workflow=temp_bonsai_files["workflow"],
            additional_externalized_properties={"param1": "value1", "param2": "value2"},
        )
        cmd = app.command.cmd
        assert '-p:"param1"="value1"' in cmd
        assert '-p:"param2"="value2"' in cmd

    def test_bonsai_app_validates_executable_exists(self, tmp_path: Path):
        """Test that BonsaiApp validation fails if executable doesn't exist."""
        workflow = tmp_path / "workflow.bonsai"
        workflow.touch()

        with pytest.raises(FileNotFoundError, match="Executable not found"):
            BonsaiApp(
                executable=tmp_path / "nonexistent.exe",
                workflow=workflow,
            )

    def test_bonsai_app_validates_workflow_exists(self, tmp_path: Path):
        """Test that BonsaiApp validation fails if workflow doesn't exist."""
        exe = tmp_path / "bonsai.exe"
        exe.touch()

        with pytest.raises(FileNotFoundError, match="Workflow file not found"):
            BonsaiApp(
                executable=exe,
                workflow=tmp_path / "nonexistent.bonsai",
            )

    def test_bonsai_app_can_be_executed_with_mock_executor(self, temp_bonsai_files):
        """Test that BonsaiApp can be executed with a mock executor."""
        app = BonsaiApp(
            executable=temp_bonsai_files["exe"],
            workflow=temp_bonsai_files["workflow"],
        )

        mock_result = CommandResult(stdout="Bonsai output", stderr="", exit_code=0)
        executor = MockExecutor(return_value=mock_result)

        result = app.command.execute(executor)
        assert result.ok is True
        assert result.stdout == "Bonsai output"

    def test_bonsai_app_default_run_method(self, temp_bonsai_files):
        """Test BonsaiApp's default run method (requires mocking subprocess)."""
        from unittest.mock import MagicMock, patch

        app = BonsaiApp(
            executable=temp_bonsai_files["exe"],
            workflow=temp_bonsai_files["workflow"],
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="output", stderr="", returncode=0)
            result = app.run()

            assert result.ok is True
            mock_run.assert_called_once()


# ============================================================================
# PythonScriptApp Tests
# ============================================================================


class TestPythonScriptApp:
    """Tests for PythonScriptApp."""

    def test_python_script_app_initialization(self, tmp_path: Path):
        """Test PythonScriptApp initialization."""
        # Create a dummy .venv to skip validation
        venv_path = tmp_path / ".venv"
        venv_path.mkdir()

        app = PythonScriptApp(
            script="test_script.py",
            project_directory=tmp_path,
        )

        assert "uv run" in app.command.cmd
        assert "test_script.py" in app.command.cmd

    def test_python_script_app_with_additional_arguments(self, tmp_path: Path):
        """Test PythonScriptApp with additional arguments."""
        venv_path = tmp_path / ".venv"
        venv_path.mkdir()

        app = PythonScriptApp(
            script="test.py",
            additional_arguments="--verbose --debug",
            project_directory=tmp_path,
        )

        cmd = app.command.cmd
        assert "--verbose" in cmd
        assert "--debug" in cmd

    def test_python_script_app_with_optional_dependencies(self, tmp_path: Path):
        """Test PythonScriptApp with optional TOML dependencies."""
        venv_path = tmp_path / ".venv"
        venv_path.mkdir()

        app = PythonScriptApp(
            script="test.py",
            project_directory=tmp_path,
            optional_toml_dependencies=["dev", "test"],
        )

        cmd = app.command.cmd
        assert "--extra dev" in cmd or "--with dev" in cmd or "dev" in cmd

    def test_python_script_app_appends_python_exe(self, tmp_path: Path):
        """Test PythonScriptApp with append_python_exe=True."""
        venv_path = tmp_path / ".venv"
        venv_path.mkdir()

        app = PythonScriptApp(
            script="test.py",
            project_directory=tmp_path,
            append_python_exe=True,
        )

        cmd = app.command.cmd
        assert "python" in cmd
        assert "test.py" in cmd

    def test_python_script_app_has_venv_check(self, tmp_path: Path):
        """Test the _has_venv static method."""
        assert PythonScriptApp._has_venv(tmp_path) is False

        venv_path = tmp_path / ".venv"
        venv_path.mkdir()

        assert PythonScriptApp._has_venv(tmp_path) is True

    def test_python_script_app_skip_validation(self, tmp_path: Path):
        """Test PythonScriptApp with skip_validation=True."""
        # Should not raise even without .venv
        app = PythonScriptApp(
            script="test.py",
            project_directory=tmp_path,
            skip_validation=True,
        )

        assert "test.py" in app.command.cmd

    def test_python_script_app_can_be_executed_with_mock_executor(self, tmp_path: Path):
        """Test that PythonScriptApp can be executed with a mock executor."""
        venv_path = tmp_path / ".venv"
        venv_path.mkdir()

        app = PythonScriptApp(
            script="test.py",
            project_directory=tmp_path,
        )

        mock_result = CommandResult(stdout="Script output", stderr="", exit_code=0)
        executor = MockExecutor(return_value=mock_result)

        result = app.command.execute(executor)
        assert result.ok is True
        assert result.stdout == "Script output"


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """Integration tests demonstrating the command/executor separation."""

    def test_same_command_different_executors(self, tmp_path: Path):
        """Test that the same command can be run with different executors."""
        cmd = Command[CommandResult](cmd="python -c \"print('hello')\"", output_parser=identity_parser)

        # Run with first executor
        executor1 = MockExecutor(CommandResult(stdout="output1", stderr="", exit_code=0))
        cmd.execute(executor1)

        # The command now has a result, so we need to override it
        executor2 = MockExecutor(CommandResult(stdout="output2", stderr="", exit_code=0))
        cmd._set_result(executor2.run(cmd), override=True)

        assert "hello" in executor1.executed_commands[0]
        assert "hello" in executor2.executed_commands[0]

    def test_app_with_custom_executor(self, tmp_path: Path):
        """Test using an app with a custom executor instead of the default."""
        exe = tmp_path / "bonsai.exe"
        workflow = tmp_path / "workflow.bonsai"
        exe.touch()
        workflow.touch()

        app = BonsaiApp(executable=exe, workflow=workflow)

        # Use a custom executor instead of the default run() method
        custom_executor = MockExecutor(CommandResult(stdout="Custom executor output", stderr="", exit_code=0))
        result = app.command.execute(custom_executor)

        assert result.stdout == "Custom executor output"
        assert len(custom_executor.executed_commands) == 1

    @pytest.mark.asyncio
    async def test_async_and_sync_executors_with_same_command_type(self):
        """Test that both sync and async executors can work with commands."""
        # Note: We use different command instances since they store results
        sync_cmd = Command[CommandResult](cmd="python -c \"print('sync')\"", output_parser=identity_parser)
        async_cmd = Command[CommandResult](cmd="python -c \"print('async')\"", output_parser=identity_parser)

        sync_executor = MockExecutor(CommandResult(stdout="sync output", stderr="", exit_code=0))
        async_executor = MockAsyncExecutor(CommandResult(stdout="async output", stderr="", exit_code=0))

        sync_result = sync_cmd.execute(sync_executor)
        async_result = await async_cmd.execute_async(async_executor)

        assert sync_result.stdout == "sync output"
        assert async_result.stdout == "async output"


# ============================================================================
# Edge Cases and Error Handling
# ============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_command_with_empty_string(self):
        """Test command with empty cmd string."""
        cmd = Command[CommandResult](cmd="", output_parser=identity_parser)
        assert cmd.cmd == ""

    def test_command_result_multiple_override_warning(self):
        """Test that overriding result logs a warning."""

        cmd = Command[CommandResult](cmd="echo test", output_parser=identity_parser)
        result1 = CommandResult(stdout="first", stderr="", exit_code=0)
        result2 = CommandResult(stdout="second", stderr="", exit_code=0)

        cmd._set_result(result1, override=False)

        # Should log warning when overriding
        with pytest.raises(RuntimeError, match="Result has already been set"):
            cmd._set_result(result2, override=False)

    def test_executor_with_none_cwd(self):
        """Test executor with None as cwd falls back to current directory."""
        executor = LocalExecutor(cwd=None)
        # Should use os.getcwd() as default
        import os

        assert executor.cwd == os.getcwd()

    def test_python_script_app_with_empty_additional_arguments(self, tmp_path: Path):
        """Test PythonScriptApp filters out empty arguments."""
        venv_path = tmp_path / ".venv"
        venv_path.mkdir()

        app = PythonScriptApp(
            script="test.py",
            additional_arguments="",  # Empty string
            project_directory=tmp_path,
        )

        # Command should still be valid
        assert "test.py" in app.command.cmd


@pytest.fixture
def open_ephys_app() -> OpenEphysApp:
    """OpenEphysApp fixture."""
    signal_chain = Path("test_signal_chain.xml")
    executable = Path(".open_ephys/open_ephys.exe")
    mock_client = MagicMock(spec=_OpenEphysGuiClient)
    app = OpenEphysApp(signal_chain=signal_chain, executable=executable, client=mock_client)
    return app


class TestOpenEphysGuiClient:
    """Test _OpenEphysGuiClient."""

    @pytest.fixture
    def client(self) -> _OpenEphysGuiClient:
        """Create a client instance."""
        return _OpenEphysGuiClient(host="localhost", port=37497, timeout=5.0)

    def test_client_init(self, client: _OpenEphysGuiClient) -> None:
        """Test client initialization."""
        assert client.base_url == "http://localhost:37497/api"
        assert client._timeout == 5.0

    @patch("requests.get")
    def test_get(self, mock_get: MagicMock, client: _OpenEphysGuiClient) -> None:
        """Test generic GET request."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"mode": "IDLE"}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = client._get("/status")

        assert result == {"mode": "IDLE"}
        mock_get.assert_called_once_with("http://localhost:37497/api/status", timeout=5.0)
        mock_response.raise_for_status.assert_called_once()

    @patch("requests.put")
    def test_put(self, mock_put: MagicMock, client: _OpenEphysGuiClient) -> None:
        """Test generic PUT request with Pydantic model."""
        from clabe.apps.open_ephys import StatusRequest

        mock_response = MagicMock()
        mock_response.json.return_value = {"mode": "ACQUIRE"}
        mock_response.raise_for_status = MagicMock()
        mock_put.return_value = mock_response

        request = StatusRequest(mode=Status.ACQUIRE)
        result = client._put("/status", request)

        assert result == {"mode": "ACQUIRE"}
        mock_put.assert_called_once()
        call_args = mock_put.call_args
        assert call_args[0][0] == "http://localhost:37497/api/status"
        assert "json" in call_args[1]
        assert call_args[1]["json"] == {"mode": "ACQUIRE"}
        assert call_args[1]["timeout"] == 5.0
        mock_response.raise_for_status.assert_called_once()

    @patch("requests.get")
    def test_get_request_exception(self, mock_get: MagicMock, client: _OpenEphysGuiClient) -> None:
        """Test GET request with request exception."""
        mock_get.side_effect = requests.RequestException("Connection error")

        with pytest.raises(requests.RequestException):
            client._get("/status")

    @patch("requests.put")
    def test_put_request_exception(self, mock_put: MagicMock, client: _OpenEphysGuiClient) -> None:
        """Test PUT request with request exception."""
        from clabe.apps.open_ephys import StatusRequest

        mock_put.side_effect = requests.RequestException("Connection error")

        with pytest.raises(requests.RequestException):
            request = StatusRequest(mode=Status.IDLE)
            client._put("/status", request)
