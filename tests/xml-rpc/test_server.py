import base64
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch
from xmlrpc.client import ServerProxy

import pytest
from pydantic import SecretStr

from clabe.xml_rpc._server import XmlRpcServer, XmlRpcServerSettings, get_local_ip


@pytest.fixture
def temp_transfer_dir():
    """Create a temporary directory for file transfers."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def rpc_settings(temp_transfer_dir):
    """Create XML-RPC server settings for testing."""
    from ipaddress import IPv4Address

    return XmlRpcServerSettings(
        token=SecretStr("test-token-123"),
        address=IPv4Address("127.0.0.1"),
        port=0,  # Let the OS choose a free port
        max_workers=2,
        max_file_size=1024 * 1024,  # 1MB for testing
        file_transfer_dir=temp_transfer_dir,
    )


@pytest.fixture
def rpc_server(rpc_settings):
    """Create an XML-RPC server instance for testing."""
    server = XmlRpcServer(rpc_settings)
    # Get the actual port assigned by the OS
    actual_port = server.server.server_address[1]
    rpc_settings.port = actual_port

    server_thread = threading.Thread(target=server.server.serve_forever, daemon=True)
    server_thread.start()

    # For some reason this is needed to prevent a race condition in tests
    time.sleep(0.1)

    yield server, rpc_settings

    # Shutdown the server
    server.server.shutdown()
    server.executor.shutdown(wait=True)


@pytest.fixture
def rpc_client(rpc_server):
    """Create an XML-RPC client connected to the test server."""
    server, settings = rpc_server
    client = ServerProxy(f"http://{settings.address}:{settings.port}")
    return client, settings.token.get_secret_value()


class TestXmlRpcServerSettings:
    """Test XML-RPC server settings configuration."""

    def test_custom_settings(self, temp_transfer_dir):
        """Test custom settings configuration."""
        from ipaddress import IPv4Address

        token = SecretStr("custom-token")
        settings = XmlRpcServerSettings(
            token=token,
            address=IPv4Address("192.168.1.1"),
            port=9000,
            max_workers=8,
            max_file_size=10 * 1024 * 1024,
            file_transfer_dir=temp_transfer_dir,
        )
        assert settings.token == token
        assert str(settings.address) == "192.168.1.1"
        assert settings.port == 9000
        assert settings.max_workers == 8
        assert settings.max_file_size == 10 * 1024 * 1024
        assert settings.file_transfer_dir == temp_transfer_dir


class TestRpcServer:
    """Test RPC server functionality."""

    def test_authentication_valid_token(self, rpc_server):
        """Test authentication with valid token."""
        server, settings = rpc_server
        valid_token = settings.token.get_secret_value()
        assert server.authenticate(valid_token) is True

    def test_authentication_invalid_token(self, rpc_server):
        """Test authentication with invalid token."""
        server, _ = rpc_server
        assert server.authenticate("invalid-token") is False
        assert server.authenticate("") is False
        assert server.authenticate(None) is False

    def test_submit_command_success(self, rpc_client):
        """Test successful command submission."""
        client, token = rpc_client

        import sys

        cmd = [sys.executable, "-c", "print('hello world')"]

        result = client.run(token, cmd)
        assert "job_id" in result
        assert isinstance(result["job_id"], str)

    def test_submit_command_invalid_auth(self, rpc_client):
        """Test command submission with invalid authentication."""
        client, _ = rpc_client

        import sys

        result = client.run("invalid-token", [sys.executable, "-c", "print('test')"])
        assert result == {"error": "Invalid or expired token"}

    def test_get_result_success(self, rpc_client):
        """Test getting command result."""
        client, token = rpc_client

        import sys

        cmd = [sys.executable, "-c", "print('test output')"]

        submit_result = client.run(token, cmd)
        job_id = submit_result["job_id"]

        max_attempts = 10
        result = None
        for _ in range(max_attempts):
            result = client.result(token, job_id)
            if result.get("status") == "done":
                break
            time.sleep(0.1)

        assert result is not None
        assert result["status"] == "done"
        assert "result" in result
        assert result["result"]["returncode"] == 0
        assert "test output" in result["result"]["stdout"]

    def test_get_result_invalid_job_id(self, rpc_client):
        """Test getting result for invalid job ID."""
        client, token = rpc_client

        result = client.result(token, "invalid-job-id")
        assert result["success"] is False
        assert result["error"] == "Invalid job_id"
        assert result["job_id"] == "invalid-job-id"
        assert result["status"] == "error"

    def test_is_running(self, rpc_client):
        """Test checking if job is running."""
        client, token = rpc_client

        # This is long running
        import sys

        cmd = [sys.executable, "-c", "import time; time.sleep(0.5); print('done')"]

        submit_result = client.run(token, cmd)
        job_id = submit_result["job_id"]

        # Check if running (might be true initially)
        is_running = client.is_running(token, job_id)
        assert isinstance(is_running, bool)

        time.sleep(1.0)
        is_running_after = client.is_running(token, job_id)
        assert is_running_after is False

    def test_is_running_invalid_job_id(self, rpc_client):
        """Test checking running status for invalid job ID."""
        client, token = rpc_client

        result = client.is_running(token, "invalid-job-id")
        assert result is False

    def test_list_jobs(self, rpc_client):
        """Test listing jobs."""
        client, token = rpc_client

        jobs = client.jobs(token)
        initial_running = len(jobs["running"])
        initial_finished = len(jobs["finished"])

        import sys

        cmd = [sys.executable, "-c", "print('test')"]

        client.run(token, cmd)

        jobs = client.jobs(token)
        assert len(jobs["running"]) >= initial_running
        assert len(jobs["finished"]) >= initial_finished

        time.sleep(0.5)
        jobs_after = client.jobs(token)
        assert isinstance(jobs_after["running"], list)
        assert isinstance(jobs_after["finished"], list)


class TestFileTransfer:
    """Test file transfer functionality."""

    def test_upload_file_success(self, rpc_client):
        """Test successful file upload."""
        client, token = rpc_client

        test_data = b"Hello, World! This is test data."
        data_base64 = base64.b64encode(test_data).decode("utf-8")

        result = client.upload_file(token, "test.txt", data_base64)

        assert result["success"] is True
        assert result["filename"] == "test.txt"
        assert result["size"] == len(test_data)

    def test_upload_file_invalid_filename(self, rpc_client):
        """Test file upload with invalid filename."""
        client, token = rpc_client

        test_data = b"test data"
        data_base64 = base64.b64encode(test_data).decode("utf-8")

        # Test directory traversal attempt
        result = client.upload_file(token, "../test.txt", data_base64)
        assert "error" in result
        assert "Invalid filename" in result["error"]

        # Test path with subdirectories
        result = client.upload_file(token, "subdir/test.txt", data_base64)
        assert "error" in result
        assert "Invalid filename" in result["error"]

    def test_upload_file_no_overwrite(self, rpc_client):
        """Test file upload without overwrite permission."""
        client, token = rpc_client

        test_data = b"original data"
        data_base64 = base64.b64encode(test_data).decode("utf-8")

        # Upload file first time
        result1 = client.upload_file(token, "no_overwrite.txt", data_base64)
        assert result1["success"] is True

        # Try to upload same file without overwrite
        new_data = b"new data"
        new_data_base64 = base64.b64encode(new_data).decode("utf-8")
        result2 = client.upload_file(token, "no_overwrite.txt", new_data_base64, False)

        assert "error" in result2
        assert "already exists" in result2["error"]

    def test_download_file_success(self, rpc_client):
        """Test successful file download."""
        client, token = rpc_client

        # First upload a file
        test_data = b"Download test data"
        data_base64 = base64.b64encode(test_data).decode("utf-8")

        upload_result = client.upload_file(token, "download_test.txt", data_base64)
        assert upload_result["success"] is True

        # Then download it
        download_result = client.download_file(token, "download_test.txt")

        assert download_result["success"] is True
        assert download_result["filename"] == "download_test.txt"
        assert download_result["size"] == len(test_data)

        downloaded_data = base64.b64decode(download_result["data"])
        assert downloaded_data == test_data

    def test_download_file_not_found(self, rpc_client):
        """Test downloading non-existent file."""
        client, token = rpc_client

        result = client.download_file(token, "nonexistent.txt")

        assert "error" in result
        assert "File not found" in result["error"]

    def test_list_files(self, rpc_client):
        """Test listing files."""
        client, token = rpc_client

        # Initially should be empty or have previous test files
        initial_result = client.list_files(token)
        assert initial_result["success"] is True
        assert isinstance(initial_result["files"], list)
        initial_count = initial_result["count"]

        test_data = b"List test data"
        data_base64 = base64.b64encode(test_data).decode("utf-8")

        upload_result = client.upload_file(token, "list_test.txt", data_base64)
        assert upload_result["success"] is True

        result = client.list_files(token)

        assert result["success"] is True
        assert isinstance(result["files"], list)
        assert result["count"] >= initial_count + 1

        filenames = [f["name"] for f in result["files"]]
        assert "list_test.txt" in filenames

        our_file = next(f for f in result["files"] if f["name"] == "list_test.txt")
        assert "size" in our_file
        assert "modified" in our_file
        assert our_file["size"] == len(test_data)

    def test_delete_file_success(self, rpc_client):
        """Test successful file deletion."""
        client, token = rpc_client

        # Upload a file to delete
        test_data = b"Delete test data"
        data_base64 = base64.b64encode(test_data).decode("utf-8")

        upload_result = client.upload_file(token, "delete_test.txt", data_base64)
        assert upload_result["success"] is True

        # Delete the file
        delete_result = client.delete_file(token, "delete_test.txt")

        assert delete_result["success"] is True
        assert delete_result["filename"] == "delete_test.txt"

        # Verify file is gone
        download_result = client.download_file(token, "delete_test.txt")
        assert "error" in download_result
        assert "File not found" in download_result["error"]

    def test_delete_file_not_found(self, rpc_client):
        """Test deleting non-existent file."""
        client, token = rpc_client

        result = client.delete_file(token, "nonexistent.txt")

        assert "error" in result
        assert "File not found" in result["error"]

    def test_delete_all_files(self, rpc_client):
        """Test deleting all files."""
        client, token = rpc_client

        # Upload multiple test files
        test_files = ["delete_all_1.txt", "delete_all_2.txt", "delete_all_3.txt"]
        for filename in test_files:
            test_data = f"Data for {filename}".encode()
            data_base64 = base64.b64encode(test_data).decode("utf-8")
            upload_result = client.upload_file(token, filename, data_base64)
            assert upload_result["success"] is True

        # Delete all files
        delete_result = client.delete_all_files(token)

        assert delete_result["success"] is True
        assert delete_result["deleted_count"] >= len(test_files)
        assert isinstance(delete_result["deleted_files"], list)

        # Verify all our test files were deleted
        for filename in test_files:
            assert filename in delete_result["deleted_files"]

        # Verify directory is empty or only has files from other tests
        list_result = client.list_files(token)
        filenames = [f["name"] for f in list_result["files"]]
        for filename in test_files:
            assert filename not in filenames


class TestHelperFunctions:
    """Test helper functions."""

    def test_get_local_ip(self):
        """Test getting local IP address."""
        ip = get_local_ip()
        assert isinstance(ip, str)
        # Basic IP format check
        parts = ip.split(".")
        assert len(parts) == 4
        for part in parts:
            assert 0 <= int(part) <= 255


class TestErrorHandling:
    """Test error handling and edge cases."""

    def test_file_size_limit(self, rpc_client):
        """Test file size limit enforcement."""
        client, token = rpc_client

        # Create data larger than the limit (1MB in our test settings)
        large_data = b"x" * (2 * 1024 * 1024)  # 2MB
        data_base64 = base64.b64encode(large_data).decode("utf-8")

        result = client.upload_file(token, "large_file.txt", data_base64)

        assert "error" in result
        assert "too large" in result["error"]

    @patch("subprocess.run")
    def test_command_execution_error(self, mock_run, rpc_client):
        """Test command execution with subprocess error."""
        client, token = rpc_client

        # Mock subprocess to raise an exception
        mock_run.side_effect = Exception("Subprocess error")

        import sys

        result = client.run(token, [sys.executable, "-c", "print('test')"])
        job_id = result["job_id"]

        # Wait for the job to complete
        time.sleep(0.5)

        get_result = client.result(token, job_id)
        assert get_result["status"] == "done"
        assert "error" in get_result["result"]
        assert "Subprocess error" in get_result["result"]["error"]

    def test_invalid_base64_upload(self, rpc_client):
        """Test file upload with invalid base64 data."""
        client, token = rpc_client

        # Invalid base64 data
        invalid_base64 = "this is not base64!"

        result = client.upload_file(token, "invalid.txt", invalid_base64)

        assert "error" in result
