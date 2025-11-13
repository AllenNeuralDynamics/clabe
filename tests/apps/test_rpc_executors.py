from unittest.mock import Mock

import pytest

from clabe.apps import Command, CommandResult, identity_parser
from clabe.xml_rpc import XmlRpcExecutor
from clabe.xml_rpc.models import JobResult, JobStatus, JobSubmissionResponse


class TestXmlRpcExecutor:
    """Test suite for XmlRpcExecutor class."""

    @pytest.fixture
    def mock_client(self):
        """Create a mocked RPC client."""
        mock_client = Mock()
        mock_client.settings.timeout = 30.0
        mock_client.settings.poll_interval = 0.5
        mock_client.settings.server_url = "http://localhost:8000"
        return mock_client

    @pytest.fixture
    def executor(self, mock_client):
        """Create XmlRpcExecutor with mocked client."""
        return XmlRpcExecutor(mock_client)

    def test_init(self):
        """Test XmlRpcExecutor initialization."""
        mock_client = Mock()
        mock_client.settings.server_url = "http://localhost:8000"

        executor = XmlRpcExecutor(mock_client, timeout=60.0, poll_interval=1.0)
        assert executor.client == mock_client
        assert executor.timeout == 60.0
        assert executor.poll_interval == 1.0

    def test_run_success(self, executor):
        """Test successful synchronous command execution."""
        job_result = JobResult(
            job_id="test-job", status=JobStatus.DONE, stdout="Hello World", stderr="", returncode=0, error=None
        )
        executor.client.run_command.return_value = job_result

        cmd = Command(cmd="echo 'Hello World'", output_parser=identity_parser)
        result = executor.run(cmd)

        assert isinstance(result, CommandResult)
        assert result.stdout == "Hello World"
        assert result.stderr == ""
        assert result.exit_code == 0
        assert result.ok
        executor.client.run_command.assert_called_once_with("echo 'Hello World'", timeout=None)

    def test_run_with_custom_timeout(self, executor):
        """Test synchronous command execution with custom timeout."""
        job_result = JobResult(
            job_id="test-job", status=JobStatus.DONE, stdout="output", stderr="", returncode=0, error=None
        )
        executor.client.run_command.return_value = job_result
        executor.timeout = 120.0

        cmd = Command(cmd="sleep 5", output_parser=identity_parser)
        executor.run(cmd)

        executor.client.run_command.assert_called_once_with("sleep 5", timeout=120.0)

    @pytest.mark.asyncio
    async def test_run_async_success(self, executor):
        """Test successful asynchronous command execution."""
        submission_response = JobSubmissionResponse(success=True, job_id="async-job")
        job_result = JobResult(
            job_id="async-job", status=JobStatus.DONE, stdout="Async output", stderr="", returncode=0, error=None
        )

        executor.client.submit_command.return_value = submission_response
        executor.client.get_result.return_value = job_result

        cmd = Command(cmd="echo 'Async output'", output_parser=identity_parser)
        result = await executor.run_async(cmd)

        assert isinstance(result, CommandResult)
        assert result.stdout == "Async output"
        assert result.stderr == ""
        assert result.exit_code == 0

        executor.client.submit_command.assert_called_once_with("echo 'Async output'")
        executor.client.get_result.assert_called_with("async-job")

    @pytest.mark.asyncio
    async def test_run_async_no_job_id(self, executor):
        """Test async execution failure when no job ID is returned."""
        submission_response = JobSubmissionResponse(success=False, job_id=None)
        executor.client.submit_command.return_value = submission_response

        cmd = Command(cmd="echo test", output_parser=identity_parser)

        with pytest.raises(Exception, match="Job submission failed: no job ID returned"):
            await executor.run_async(cmd)
