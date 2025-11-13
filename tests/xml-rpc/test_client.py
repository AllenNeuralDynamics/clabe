import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch
from xmlrpc.client import ServerProxy

import pytest
from pydantic import HttpUrl, SecretStr

from clabe.xml_rpc._client import (
    XmlRpcClient,
    XmlRpcClientSettings,
)
from clabe.xml_rpc._server import XmlRpcServer, XmlRpcServerSettings
from clabe.xml_rpc.models import FileInfo, JobResult, JobStatus


@pytest.fixture
def client(test_server):
    """Create an XML-RPC client for the test server."""
    _, port, token = test_server

    settings = XmlRpcClientSettings(
        server_url=HttpUrl(f"http://127.0.0.1:{port}"),
        token=SecretStr(token),
    )
    return XmlRpcClient(settings)


@pytest.fixture
def temp_transfer_dir():
    """Create a temporary directory for file transfers."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def test_server(temp_transfer_dir):
    """Create and start a test XML-RPC server."""
    from ipaddress import IPv4Address

    server_settings = XmlRpcServerSettings(
        token=SecretStr("test-client-token"),
        address=IPv4Address("127.0.0.1"),
        port=0,  # Let the OS choose a free port
        max_workers=2,
        max_file_size=1024 * 1024,  # 1MB for testing
        file_transfer_dir=temp_transfer_dir,
    )

    server = XmlRpcServer(server_settings)
    actual_port = server.server.server_address[1]

    server_thread = threading.Thread(target=server.server.serve_forever, daemon=True)
    server_thread.start()

    time.sleep(0.1)

    yield server, actual_port, server_settings.token.get_secret_value()

    server.server.shutdown()
    server.executor.shutdown(wait=True)


@pytest.fixture
def client_settings(test_server):
    """Create client settings for the test server."""
    server, port, token = test_server
    return XmlRpcClientSettings(
        server_url=HttpUrl(f"http://127.0.0.1:{port}"),
        token=SecretStr(token),
        timeout=5.0,
        poll_interval=0.1,
        max_file_size=1024 * 1024,
    )


@pytest.fixture
def rpc_client(client_settings) -> XmlRpcClient:
    """Create an XML-RPC client instance."""
    return XmlRpcClient(client_settings)


class TestXmlRpcClientSettings:
    """Test XML-RPC client settings configuration."""

    def test_client_settings_creation(self):
        """Test creating client settings with required parameters."""
        settings = XmlRpcClientSettings(server_url=HttpUrl("http://localhost:8000"), token=SecretStr("test-token"))

        assert str(settings.server_url) == "http://localhost:8000/"
        assert settings.token.get_secret_value() == "test-token"
        assert settings.timeout == 30.0
        assert settings.poll_interval == 0.5
        assert settings.max_file_size == 5 * 1024 * 1024

    def test_client_settings_custom_values(self):
        """Test creating client settings with custom values."""
        settings = XmlRpcClientSettings(
            server_url=HttpUrl("http://192.168.1.100:9000"),
            token=SecretStr("custom-token"),
            timeout=60.0,
            poll_interval=1.0,
            max_file_size=10 * 1024 * 1024,
        )

        assert str(settings.server_url) == "http://192.168.1.100:9000/"
        assert settings.token.get_secret_value() == "custom-token"
        assert settings.timeout == 60.0
        assert settings.poll_interval == 1.0
        assert settings.max_file_size == 10 * 1024 * 1024


class TestJobResult:
    """Test JobResult model."""

    def test_job_result_running(self):
        """Test creating a JobResult for a running job."""
        result = JobResult(job_id="test-123", status=JobStatus.RUNNING)

        assert result.job_id == "test-123"
        assert result.status == JobStatus.RUNNING
        assert result.stdout is None
        assert result.stderr is None
        assert result.returncode is None
        assert result.error is None

    def test_job_result_completed(self):
        """Test creating a JobResult for a completed job."""
        result = JobResult(job_id="test-456", status=JobStatus.DONE, stdout="Hello World", stderr="", returncode=0)

        assert result.job_id == "test-456"
        assert result.status == JobStatus.DONE
        assert result.stdout == "Hello World"
        assert result.stderr == ""
        assert result.returncode == 0
        assert result.error is None

    def test_job_result_error(self):
        """Test creating a JobResult for a failed job."""
        result = JobResult(job_id="test-789", status=JobStatus.DONE, error="Command not found")

        assert result.job_id == "test-789"
        assert result.status == JobStatus.DONE
        assert result.error == "Command not found"


class TestFileInfo:
    """Test FileInfo model."""

    def test_file_info_creation(self):
        """Test creating FileInfo objects."""
        info = FileInfo(name="test.txt", size=1024, modified=1640995200.0)

        assert info.name == "test.txt"
        assert info.size == 1024
        assert info.modified == 1640995200.0


class TestXmlRpcClient:
    """Test XML-RPC client functionality."""

    def test_client_initialization(self, client_settings):
        """Test client initialization."""
        client = XmlRpcClient(client_settings)

        assert client.settings == client_settings
        assert isinstance(client._client, ServerProxy)
        assert client._token == client_settings.token.get_secret_value()

    def test_ping_success(self, rpc_client: XmlRpcClient):
        """Test successful server ping."""
        assert rpc_client.ping() is True

    def test_ping_failure(self, client_settings):
        """Test server ping failure."""
        # Use invalid port to simulate connection failure
        bad_settings = XmlRpcClientSettings(
            server_url=HttpUrl("http://127.0.0.1:65535"),  # Valid but likely unused port
            token=client_settings.token,
        )
        client = XmlRpcClient(bad_settings)
        assert client.ping() is False

    def test_submit_command(self, rpc_client: XmlRpcClient):
        """Test command submission."""
        import sys

        response = rpc_client.submit_command([sys.executable, "-c", "print('test')"])

        assert response.success is True
        assert isinstance(response.job_id, str)
        assert len(response.job_id) > 0

    def test_get_result_running(self, rpc_client: XmlRpcClient):
        """Test getting result of a running job."""
        import sys

        submission = rpc_client.submit_command([sys.executable, "-c", "import time; time.sleep(1)"])

        # Check immediately - should be running
        result = rpc_client.get_result(submission.job_id)

        assert result.job_id == submission.job_id
        # Job might complete quickly, so status could be "running" or "done"
        assert result.status in [JobStatus.RUNNING, JobStatus.DONE]

    def test_get_result_completed(self, rpc_client: XmlRpcClient):
        """Test getting result of a completed job."""
        import sys

        submission = rpc_client.submit_command([sys.executable, "-c", "print('hello world')"])

        time.sleep(1.0)
        result = rpc_client.get_result(submission.job_id)

        assert result.job_id == submission.job_id
        assert result.status == JobStatus.DONE
        assert result.returncode == 0
        assert "hello world" in result.stdout

    def test_wait_for_result_success(self, rpc_client: XmlRpcClient):
        """Test waiting for command completion."""
        import sys

        submission = rpc_client.submit_command([sys.executable, "-c", "print('completed')"])

        result = rpc_client.wait_for_result(submission.job_id, timeout=5.0)

        assert result.job_id == submission.job_id
        assert result.status == JobStatus.DONE
        assert result.returncode == 0
        assert "completed" in result.stdout

    def test_wait_for_result_timeout(self, rpc_client: XmlRpcClient):
        """Test timeout when waiting for command completion."""
        import sys

        submission = rpc_client.submit_command([sys.executable, "-c", "import time; time.sleep(10)"])

        with pytest.raises(TimeoutError, match="did not complete within"):
            rpc_client.wait_for_result(submission.job_id, timeout=0.5)

    def test_run_command_success(self, rpc_client: XmlRpcClient):
        """Test running a command to completion."""
        import sys

        result = rpc_client.run_command([sys.executable, "-c", "print('direct run')"])

        assert result.status == JobStatus.DONE
        assert result.returncode == 0
        assert "direct run" in result.stdout

    def test_is_running(self, rpc_client: XmlRpcClient):
        """Test checking if job is running."""
        import sys

        submission = rpc_client.submit_command([sys.executable, "-c", "import time; time.sleep(0.5)"])

        # Job might be running initially
        is_running_initial = rpc_client.is_running(submission.job_id)
        assert isinstance(is_running_initial, bool)

        time.sleep(1.0)
        is_running_after = rpc_client.is_running(submission.job_id)
        assert is_running_after is False

    def test_list_jobs(self, rpc_client: XmlRpcClient):
        """Test listing jobs."""
        # Submit a job
        import sys

        rpc_client.submit_command([sys.executable, "-c", "print('test job')"])

        jobs = rpc_client.list_jobs()

        assert hasattr(jobs, "running")
        assert hasattr(jobs, "finished")
        assert isinstance(jobs.running, list)
        assert isinstance(jobs.finished, list)

    @patch("clabe.xml_rpc._client.XmlRpcClient._call_with_auth")
    def test_authentication_error(self, mock_call, client_settings):
        """Test handling of authentication errors."""
        mock_call.side_effect = Exception("Server error: Invalid or expired token")

        client = XmlRpcClient(client_settings)

        with pytest.raises(Exception, match="Invalid or expired token"):
            client.submit_command(["echo", "test"])


class TestFileOperations:
    """Test file upload/download operations."""

    def test_upload_file_success(self, rpc_client: XmlRpcClient, tmp_path):
        """Test successful file upload."""
        # Create a test file
        test_file = tmp_path / "upload_test.txt"
        test_content = "Hello from client test"
        test_file.write_text(test_content)

        result = rpc_client.upload_file(test_file, "client_upload.txt")

        assert result.success is True
        assert result.filename == "client_upload.txt"
        assert result.size == len(test_content.encode())

    def test_upload_file_not_found(self, rpc_client, tmp_path):
        """Test uploading non-existent file."""
        non_existent_file = tmp_path / "does_not_exist.txt"

        with pytest.raises(FileNotFoundError, match="Local file not found"):
            rpc_client.upload_file(non_existent_file)

    def test_upload_file_too_large(self, rpc_client, tmp_path):
        """Test uploading file that exceeds size limit."""
        # Create a file larger than the limit
        large_file = tmp_path / "large_file.txt"
        large_content = "x" * (2 * 1024 * 1024)  # 2MB, larger than 1MB limit
        large_file.write_bytes(large_content.encode())

        with pytest.raises(Exception, match="File too large"):
            rpc_client.upload_file(large_file)

    def test_upload_file_default_name(self, rpc_client, tmp_path):
        """Test uploading file with default remote name."""
        test_file = tmp_path / "default_name.txt"
        test_file.write_text("test content")

        result = rpc_client.upload_file(test_file)

        assert result.filename == "default_name.txt"

    def test_upload_model_success(self, rpc_client: XmlRpcClient):
        """Test successful model upload."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            name: str
            value: int
            active: bool

        test_model = TestModel(name="test_config", value=42, active=True)
        result = rpc_client.upload_model(test_model, "test_model.json")

        assert result.success is True
        assert result.filename == "test_model.json"
        # The size should match the JSON serialized size
        expected_json = test_model.model_dump_json()
        assert result.size == len(expected_json.encode("utf-8"))

    def test_upload_model_too_large(self, rpc_client: XmlRpcClient):
        """Test uploading model that serializes to data larger than limit."""
        from pydantic import BaseModel

        class LargeModel(BaseModel):
            large_data: str

        # Create a model with data that exceeds the 1MB limit when serialized
        large_data = "x" * (2 * 1024 * 1024)  # 2MB string
        large_model = LargeModel(large_data=large_data)

        with pytest.raises(Exception, match="Serialized model too large"):
            rpc_client.upload_model(large_model, "large_model.json")

    def test_download_file_success(self, rpc_client: XmlRpcClient, tmp_path):
        """Test successful file download."""
        # First upload a file
        upload_file = tmp_path / "for_download.txt"
        test_content = "Content for download test"
        upload_file.write_text(test_content)

        rpc_client.upload_file(upload_file, "download_test.txt")

        # Then download it
        download_path = tmp_path / "downloaded.txt"
        result_path = rpc_client.download_file("download_test.txt", download_path)

        assert result_path == download_path
        assert download_path.exists()
        assert download_path.read_text() == test_content

    def test_download_file_default_path(self, rpc_client: XmlRpcClient, tmp_path):
        """Test downloading file with default local path."""
        # Upload a file first
        upload_file = tmp_path / "default_download.txt"
        upload_file.write_text("default path test")

        rpc_client.upload_file(upload_file, "default_path.txt")

        # Download without specifying local path (creates a Path relative to current working directory)
        import os

        original_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            result_path = rpc_client.download_file("default_path.txt")

            # The result is a relative Path, resolve it to get the absolute path
            expected_path = Path("default_path.txt")
            assert result_path == expected_path
            # Verify the file was actually created in the current directory
            assert (tmp_path / "default_path.txt").exists()
        finally:
            os.chdir(original_cwd)

    def test_list_files(self, rpc_client: XmlRpcClient, tmp_path):
        """Test listing files on server."""
        # Upload a test file
        test_file = tmp_path / "list_test.txt"
        test_file.write_text("file for listing")

        rpc_client.upload_file(test_file, "file_list_test.txt")

        files = rpc_client.list_files()

        assert isinstance(files, list)
        filenames = [f.name for f in files]
        assert "file_list_test.txt" in filenames

        our_file = next(f for f in files if f.name == "file_list_test.txt")
        assert isinstance(our_file, FileInfo)
        assert our_file.size > 0
        assert our_file.modified > 0

    def test_delete_file(self, rpc_client: XmlRpcClient, tmp_path):
        """Test deleting a file from server."""
        test_file = tmp_path / "delete_me.txt"
        test_file.write_text("delete this file")

        rpc_client.upload_file(test_file, "to_delete.txt")

        files_before = rpc_client.list_files()
        filenames_before = [f.name for f in files_before]
        assert "to_delete.txt" in filenames_before

        result = rpc_client.delete_file("to_delete.txt")

        assert result.success is True
        assert result.filename == "to_delete.txt"

        files_after = rpc_client.list_files()
        filenames_after = [f.name for f in files_after]
        assert "to_delete.txt" not in filenames_after

    def test_delete_all_files(self, rpc_client: XmlRpcClient, tmp_path):
        """Test deleting all files from server."""
        # Upload multiple files
        for i in range(3):
            test_file = tmp_path / f"bulk_delete_{i}.txt"
            test_file.write_text(f"File {i} content")
            rpc_client.upload_file(test_file, f"bulk_{i}.txt")

        # Delete all files
        result = rpc_client.delete_all_files()

        assert result.success is True
        assert result.deleted_count >= 3
        assert isinstance(result.deleted_files, list)

        for i in range(3):
            assert f"bulk_{i}.txt" in result.deleted_files


class TestXmlRpcClientContext:
    """Test XML RPC client context manager."""

    def test_context_manager(self, client_settings):
        """Test using client as context manager."""
        with XmlRpcClient(client_settings) as client:
            assert isinstance(client, XmlRpcClient)
            assert client.ping() is True

    def test_context_manager_exception_handling(self, client_settings):
        """Test context manager handles exceptions properly."""
        try:
            with XmlRpcClient(client_settings):
                # This should not prevent proper cleanup
                raise ValueError("Test exception")
        except ValueError:
            pass  # Expected

        # Context should have cleaned up properly
        # (Currently no cleanup needed, but test structure is ready)


class TestClientFixture:
    """Test client fixture functionality."""

    def test_client_fixture(self, client: XmlRpcClient):
        """Test client fixture creates working client."""
        assert isinstance(client, XmlRpcClient)
        assert client.ping() is True

    def test_client_fixture_with_custom_settings(self, test_server):
        """Test creating client with custom settings."""
        _, port, token = test_server

        settings = XmlRpcClientSettings(
            server_url=HttpUrl(f"http://127.0.0.1:{port}"),
            token=SecretStr(token),
            timeout=10.0,
            poll_interval=0.2,
        )
        client = XmlRpcClient(settings)

        assert isinstance(client, XmlRpcClient)
        assert client.settings.timeout == 10.0
        assert client.settings.poll_interval == 0.2


class TestErrorHandling:
    """Test error handling scenarios."""

    @patch("clabe.xml_rpc._client.XmlRpcClient._call_with_auth")
    def test_server_error_handling(self, mock_call, rpc_client: XmlRpcClient):
        """Test handling of server errors."""
        mock_call.side_effect = Exception("Server error: Server internal error")

        with pytest.raises(Exception, match="Server internal error"):
            rpc_client.submit_command(["test"])

    def test_invalid_job_id(self, rpc_client: XmlRpcClient):
        """Test handling of invalid job IDs."""
        with pytest.raises(Exception, match="Invalid job_id"):
            rpc_client.get_result("invalid-job-id-12345")

    def test_file_not_found_download(self, rpc_client: XmlRpcClient):
        """Test downloading non-existent file."""
        with pytest.raises(Exception, match="File not found"):
            rpc_client.download_file("nonexistent_file.txt")

    def test_file_not_found_delete(self, rpc_client: XmlRpcClient):
        """Test deleting non-existent file."""
        with pytest.raises(Exception, match="File not found"):
            rpc_client.delete_file("nonexistent_file.txt")
