import base64
import logging
import time
import xmlrpc.client
from pathlib import Path
from typing import Optional, Union

from pydantic import BaseModel, Field, HttpUrl, SecretStr

from ..services import ServiceSettings
from .models import (
    FileBulkDeleteResponse,
    FileDeleteResponse,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    JobListResponse,
    JobResult,
    JobStatus,
    JobSubmissionResponse,
)

logger = logging.getLogger(__name__)


class RpcClientSettings(ServiceSettings):
    """Settings for RPC client configuration."""

    __yml_section__ = "rpc_client"

    server_url: HttpUrl = Field(description="URL of the RPC server (e.g., http://127.0.0.1:8000)")
    token: SecretStr = Field(description="Authentication token for RPC access")
    timeout: float = Field(default=30.0, description="Default timeout for RPC calls in seconds")
    poll_interval: float = Field(default=0.5, description="Polling interval for job status checks in seconds")
    max_file_size: int = Field(default=5 * 1024 * 1024, description="Maximum file size in bytes (default 5MB)")


class RpcClient:
    """Client for interacting with the RPC server."""

    def __init__(self, settings: RpcClientSettings):
        """
        Initialize the RPC client.

        Args:
            settings: Client configuration settings
        """
        self.settings = settings
        self._client = xmlrpc.client.ServerProxy(str(settings.server_url), allow_none=True)
        self._token = settings.token.get_secret_value()

        logger.info(f"RPC client initialized for server: {settings.server_url}")

    def _call_with_auth(self, method_name: str, *args, **kwargs):
        """
        Call a server method with authentication.

        Args:
            method_name: Name of the server method to call
            *args: Positional arguments for the method
            **kwargs: Keyword arguments for the method

        Returns:
            The result from the server method

        Raises:
            RuntimeError: If the server returns an authentication error or other error
        """
        method = getattr(self._client, method_name)
        result = method(self._token, *args, **kwargs)

        if isinstance(result, dict) and "error" in result and result["error"] is not None:
            raise RuntimeError(f"Server error: {result['error']}")

        return result

    def submit_command(self, cmd_args: list[str] | str) -> JobSubmissionResponse:
        """
        Submit a command for background execution.

        Args:
            cmd_args: List of command arguments (e.g., ["python", "-c", "print('hello')"])

        Returns:
            JobSubmissionResponse with job ID and success status

        Example:
            ```python
            client = RpcClient(settings)
            response = client.submit_command(["echo", "hello world"])
            job_id = response.job_id
            ```
        """
        if isinstance(cmd_args, str):
            cmd_args = [cmd_args]
        result = self._call_with_auth("run", cmd_args)
        response = JobSubmissionResponse(**result)
        logger.info(f"Submitted command {cmd_args} with job ID: {response.job_id}")
        return response

    def get_result(self, job_id: str) -> JobResult:
        """
        Get the result of a command execution.

        Args:
            job_id: Job ID returned from submit_command

        Returns:
            JobResult object with execution details

        Example:
            ```python
            result = client.get_result(job_id)
            if result.status == JobStatus.DONE:
                print(f"Exit code: {result.returncode}")
                print(f"Output: {result.stdout}")
            ```
        """
        result = self._call_with_auth("result", job_id)

        if result["status"] == JobStatus.RUNNING.value:
            return JobResult(
                job_id=job_id, status=JobStatus.RUNNING, stdout=None, stderr=None, returncode=None, error=None
            )
        elif result["status"] == JobStatus.DONE.value:
            job_result = result["result"]
            return JobResult(
                job_id=job_id,
                status=JobStatus.DONE,
                stdout=job_result.get("stdout"),
                stderr=job_result.get("stderr"),
                returncode=job_result.get("returncode"),
                error=job_result.get("error"),
            )
        else:
            raise RuntimeError(f"Unknown job status: {result['status']}")

    def wait_for_result(self, job_id: str, timeout: Optional[float] = None) -> JobResult:
        """
        Wait for a command to complete and return the result.

        Args:
            job_id: Job ID returned from submit_command
            timeout: Maximum time to wait in seconds (uses default if None)

        Returns:
            JobResult object with execution details

        Raises:
            TimeoutError: If the command doesn't complete within the timeout

        Example:
            ```python
            job_id = client.submit_command(["sleep", "5"])
            result = client.wait_for_result(job_id, timeout=10)
            print(f"Command completed with exit code: {result.returncode}")
            ```
        """
        if timeout is None:
            timeout = self.settings.timeout

        start_time = time.time()

        while time.time() - start_time < timeout:
            result = self.get_result(job_id)
            if result.status == JobStatus.DONE:
                return result
            time.sleep(self.settings.poll_interval)

        raise TimeoutError(f"Job {job_id} did not complete within {timeout} seconds")

    def run_command(self, cmd_args: list[str] | str, timeout: Optional[float] = None) -> JobResult:
        """
        Submit a command and wait for it to complete.

        Args:
            cmd_args: List of command arguments
            timeout: Maximum time to wait in seconds (uses default if None)

        Returns:
            JobResult object with execution details

        Example:
            ```python
            result = client.run_command(["python", "--version"])
            print(f"Python version: {result.stdout.strip()}")
            ```
        """
        submission = self.submit_command(cmd_args)
        if submission.job_id is None:
            raise RuntimeError("Job submission failed: no job ID returned")
        return self.wait_for_result(submission.job_id, timeout)

    def is_running(self, job_id: str) -> bool:
        """
        Check if a job is still running.

        Args:
            job_id: Job ID to check

        Returns:
            True if the job is still running, False otherwise

        Example:
            ```python
            if client.is_running(job_id):
                print("Job is still running...")
            else:
                print("Job has completed")
            ```
        """
        result = self._call_with_auth("is_running", job_id)
        return bool(result)

    def list_jobs(self) -> JobListResponse:
        """
        List all running and finished jobs.

        Returns:
            JobListResponse with lists of running and finished job IDs

        Example:
            ```python
            jobs = client.list_jobs()
            print(f"Running jobs: {jobs.running}")
            print(f"Finished jobs: {jobs.finished}")
            ```
        """
        result = self._call_with_auth("jobs")
        return JobListResponse(**result)

    def upload_file(
        self, local_path: Union[str, Path], remote_filename: Optional[str] = None, overwrite: bool = True
    ) -> FileUploadResponse:
        """
        Upload a file to the server.

        Args:
            local_path: Path to the local file to upload
            remote_filename: Name to use on the server (defaults to local filename)
            overwrite: Whether to overwrite existing files

        Returns:
            FileUploadResponse with upload result information

        Raises:
            FileNotFoundError: If the local file doesn't exist
            ValueError: If the file is too large or upload fails

        Example:
            ```python
            result = client.upload_file("./local_file.txt", "remote_file.txt")
            print(f"Uploaded {result.size} bytes")
            ```
        """
        local_path = Path(local_path)

        if not local_path.exists():
            raise FileNotFoundError(f"Local file not found: {local_path}")

        if not local_path.is_file():
            raise ValueError(f"Path is not a file: {local_path}")

        file_size = local_path.stat().st_size
        if file_size > self.settings.max_file_size:
            raise ValueError(
                f"File too large: {file_size} bytes. Maximum: {self.settings.max_file_size} bytes "
                f"({self.settings.max_file_size / (1024 * 1024):.1f} MB)"
            )

        if remote_filename is None:
            remote_filename = local_path.name

        # Read and encode file data
        file_data = local_path.read_bytes()
        data_base64 = base64.b64encode(file_data).decode("utf-8")

        logger.info(f"Uploading file {local_path} as {remote_filename} ({file_size} bytes)")

        result = self._call_with_auth("upload_file", remote_filename, data_base64, overwrite)
        response = FileUploadResponse(**result)

        logger.info(f"Successfully uploaded {remote_filename}")
        return response

    def upload_model(self, model: BaseModel, remote_filename: str, overwrite: bool = True) -> FileUploadResponse:
        """
        Upload a Pydantic model to the server as a JSON file.

        Args:
            model: Any Pydantic BaseModel or its subclass to upload
            remote_filename: Name to use on the server for the JSON file
            overwrite: Whether to overwrite existing files

        Returns:
            FileUploadResponse with upload result information

        Raises:
            ValueError: If the serialized data is too large or upload fails

        Example:
            ```python
            from pydantic import BaseModel

            class MyModel(BaseModel):
                name: str
                value: int

            my_data = MyModel(name="test", value=42)
            result = client.upload_model(my_data, "config.json")
            print(f"Uploaded {result.size} bytes")
            ```
        """
        json_data = model.model_dump_json()
        json_bytes = json_data.encode("utf-8")

        data_size = len(json_bytes)
        if data_size > self.settings.max_file_size:
            raise ValueError(
                f"Serialized model too large: {data_size} bytes. Maximum: {self.settings.max_file_size} bytes "
                f"({self.settings.max_file_size / (1024 * 1024):.1f} MB)"
            )

        # Encode for transport
        data_base64 = base64.b64encode(json_bytes).decode("utf-8")

        logger.info(f"Uploading model as {remote_filename} ({data_size} bytes)")

        result = self._call_with_auth("upload_file", remote_filename, data_base64, overwrite)
        response = FileUploadResponse(**result)

        logger.info(f"Successfully uploaded model as {remote_filename}")
        return response

    def download_file(self, remote_filename: str, local_path: Optional[Union[str, Path]] = None) -> Path:
        """
        Download a file from the server.

        Args:
            remote_filename: Name of the file on the server
            local_path: Where to save the file locally (defaults to current directory with same name)

        Returns:
            Path to the downloaded file

        Example:
            ```python
            downloaded_path = client.download_file("remote_file.txt", "./downloads/local_file.txt")
            print(f"Downloaded to: {downloaded_path}")
            ```
        """
        if local_path is None:
            local_path = Path(remote_filename)
        else:
            local_path = Path(local_path)

        logger.info(f"Downloading file {remote_filename} to {local_path}")

        result = self._call_with_auth("download_file", remote_filename)

        if "data" in result and result["data"] is not None and isinstance(result["data"], xmlrpc.client.Binary):
            result["data"] = result["data"].data

        response = FileDownloadResponse(**result)

        if not response.success:
            raise RuntimeError(f"Download failed: {response.error}")

        local_path.parent.mkdir(parents=True, exist_ok=True)

        if response.data is None:
            raise ValueError("No file data received from server")

        file_data = response.data
        local_path.write_bytes(file_data)

        logger.info(f"Successfully downloaded {remote_filename} ({response.size} bytes)")
        return local_path

    def list_files(self) -> list[FileInfo]:
        """
        List all files on the server.

        Returns:
            List of FileInfo objects with file details

        Example:
            ```python
            files = client.list_files()
            for file_info in files:
                print(f"{file_info.name}: {file_info.size} bytes")
            ```
        """
        result = self._call_with_auth("list_files")
        return [FileInfo(**file_data) for file_data in result["files"]]

    def delete_file(self, remote_filename: str) -> FileDeleteResponse:
        """
        Delete a file from the server.

        Args:
            remote_filename: Name of the file to delete

        Returns:
            FileDeleteResponse with deletion result

        Example:
            ```python
            result = client.delete_file("unwanted_file.txt")
            print(f"Deleted: {result.filename}")
            ```
        """
        logger.info(f"Deleting file {remote_filename}")
        result = self._call_with_auth("delete_file", remote_filename)
        response = FileDeleteResponse(**result)
        logger.info(f"Successfully deleted {remote_filename}")
        return response

    def delete_all_files(self) -> FileBulkDeleteResponse:
        """
        Delete all files from the server.

        Returns:
            FileBulkDeleteResponse with deletion results including count and list of deleted files

        Example:
            ```python
            result = client.delete_all_files()
            print(f"Deleted {result.deleted_count} files")
            ```
        """
        logger.info("Deleting all files from server")
        result = self._call_with_auth("delete_all_files")
        response = FileBulkDeleteResponse(**result)
        logger.info(f"Successfully deleted {response.deleted_count} files")
        return response

    def ping(self) -> bool:
        """
        Test connectivity to the server.

        Returns:
            True if the server is reachable and authentication works

        Example:
            ```python
            if client.ping():
                print("Server is reachable")
            else:
                print("Cannot connect to server")
            ```
        """
        try:
            # Try to list jobs as a simple connectivity test
            self.list_jobs()
            return True
        except (RuntimeError, ConnectionError, ValueError) as e:
            logger.warning(f"Server ping failed: {e}")
            return False

    def __enter__(self):
        """Enter the runtime context."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the runtime context."""
        logger.info("RPC client context exited")
        return False
