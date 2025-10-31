import base64
import logging
import os
import secrets
import socket
import subprocess
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from functools import wraps
from pathlib import Path
from typing import ClassVar
from xmlrpc.server import SimpleXMLRPCServer

from pydantic import Field, IPvAnyAddress, SecretStr
from pydantic_settings import CliApp

from clabe.services import ServiceSettings

logger = logging.getLogger(__name__)


def _default_token() -> SecretStr:
    return SecretStr(secrets.token_urlsafe(32))


class RpcServerSettings(ServiceSettings):
    __yaml_section__: ClassVar[str] = "rpc_server"

    token: SecretStr = Field(default_factory=_default_token, description="Authentication token for RPC access")
    address: IPvAnyAddress = Field(default="0.0.0.0", validate_default=True)
    port: int = Field(default=8000, description="Port to listen on")
    max_workers: int = Field(default=4, description="Maximum number of concurrent RPC commands")
    max_file_size: int = Field(default=5 * 1024 * 1024, description="Maximum file size in bytes (default 5MB)")
    file_transfer_dir: Path = Field(default_factory=lambda: Path(os.environ.get("TEMP", "temp")), description="Directory for file transfers")


def get_local_ip():
    """Get the local IP address"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]


class RpcServer:
    def __init__(self, settings: RpcServerSettings):
        self.settings = settings
        self.executor = ThreadPoolExecutor(max_workers=settings.max_workers)
        self.jobs: dict[str, Future] = {}

        # Ensure file transfer directory exists
        os.makedirs(settings.file_transfer_dir, exist_ok=True)

        server = SimpleXMLRPCServer((str(settings.address), settings.port), allow_none=True)
        server.register_function(self.require_auth(self.submit_command), "run")
        server.register_function(self.require_auth(self.get_result), "result")
        server.register_function(self.require_auth(self.list_jobs), "jobs")
        server.register_function(self.require_auth(self.is_running), "is_running")
        # File transfer api. The methods names are interpreted from the point of view of the client
        server.register_function(self.require_auth(self.upload_file), "upload_file")
        server.register_function(self.require_auth(self.download_file), "download_file")
        server.register_function(self.require_auth(self.list_files), "list_files")
        server.register_function(self.require_auth(self.delete_file), "delete_file")
        server.register_function(self.require_auth(self.delete_all_files), "delete_all_files")

        logger.info(f"Authentication token: {settings.token.get_secret_value()}")
        logger.info(f"XML-RPC server running on {settings.address}:{settings.port}...")
        logger.info(f"File transfer directory: {settings.file_transfer_dir.resolve()}")
        logger.info("Use the token above to authenticate requests")

        self.server = server

    def authenticate(self, token: str) -> bool:
        """Validate token and check expiry"""
        return bool(token and token == self.settings.token.get_secret_value())

    def require_auth(self, func):
        """Decorator to require authentication"""

        @wraps(func)
        def wrapper(token, *args, **kwargs):
            if not self.authenticate(token):
                return {"error": "Invalid or expired token"}
            return func(*args, **kwargs)

        return wrapper

    def _run_command_sync(self, cmd_args):
        """Internal method: actually runs the subprocess"""
        try:
            proc = subprocess.run(cmd_args, capture_output=True, text=True, check=True)
            return {"stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode}
        except subprocess.CalledProcessError as e:
            return {"stdout": e.stdout, "stderr": e.stderr, "returncode": e.returncode}
        except Exception as e:
            return {"error": str(e)}

    def submit_command(self, cmd_args):
        """Submit a command for background execution"""
        job_id = str(uuid.uuid4())
        future = self.executor.submit(self._run_command_sync, cmd_args)
        self.jobs[job_id] = future
        logger.info(f"Submitted job {job_id}: {cmd_args}")
        return {"job_id": job_id}

    def get_result(self, job_id):
        """Fetch the result of a finished command"""
        if job_id not in self.jobs:
            return {"error": "Invalid job_id"}
        future = self.jobs[job_id]
        if not future.done():
            return {"status": "running"}
        result = future.result()
        del self.jobs[job_id]  # cleanup finished job
        return {"status": "done", "result": result}

    def is_running(self, job_id):
        """Check if a job is still running"""
        if job_id not in self.jobs:
            return False
        return not self.jobs[job_id].done()

    def list_jobs(self):
        """List all running jobs"""
        return {
            "running": [jid for jid, fut in self.jobs.items() if not fut.done()],
            "finished": [jid for jid, fut in self.jobs.items() if fut.done()],
        }

    def upload_file(self, filename: str, data_base64: str, overwrite: bool = True) -> dict:
        """
        Upload a file to the server.

        Args:
            filename: Name of the file (relative path within transfer directory)
            data_base64: Base64-encoded file content
            overwrite: If True, overwrite existing file. Defaults to True

        Returns:
            dict: Status with 'success' or 'error' message

        Example:
            ```python
            # Client side
            with open("myfile.txt", "rb") as f:
                data = base64.b64encode(f.read()).decode('utf-8')
            result = server.upload_file(token, "myfile.txt", data)
            
            # Or prevent overwriting
            result = server.upload_file(token, "myfile.txt", data, False)
            ```
        """
        try:
            # For now lets force simple filenames to avoid directory traversal
            safe_filename = Path(filename).name
            if safe_filename != filename or ".." in filename:
                return {"error": "Invalid filename - only simple filenames allowed, no paths"}

            file_path = self.settings.file_transfer_dir / safe_filename
            if file_path.exists() and not overwrite:
                return {"error": f"File already exists: {safe_filename}. Set overwrite=True to replace."}

            file_data = base64.b64decode(data_base64)

            if len(file_data) > self.settings.max_file_size:
                return {
                    "error": f"File too large. Maximum size: {self.settings.max_file_size} bytes "
                    f"({self.settings.max_file_size / (1024*1024):.1f} MB)"
                }

            file_path.write_bytes(file_data)

            logger.info(f"File uploaded: {safe_filename} ({len(file_data)} bytes)")
            return {
                "success": True,
                "filename": safe_filename,
                "size": len(file_data),
                "overwritten": file_path.exists(),
            }

        except Exception as e:
            logger.error(f"Error uploading file: {e}")
            return {"error": str(e)}

    def download_file(self, filename: str) -> dict:
        """
        Download a file from the server.

        Args:
            filename: Name of the file to download (relative path within transfer directory)

        Returns:
            dict: Contains 'data' (base64-encoded), 'size', or 'error' message

        Example:
            ```python
            # Client side
            result = server.download_file(token, "myfile.txt")
            if 'data' in result:
                data = base64.b64decode(result['data'])
                with open("downloaded.txt", "wb") as f:
                    f.write(data)
            ```
        """
        try:
            safe_filename = Path(filename).name
            if safe_filename != filename or ".." in filename:
                return {"error": "Invalid filename - only simple filenames allowed, no paths"}

            file_path = self.settings.file_transfer_dir / safe_filename

            if not file_path.exists():
                return {"error": f"File not found: {safe_filename}"}

            if not file_path.is_file():
                return {"error": f"Not a file: {safe_filename}"}

            file_size = file_path.stat().st_size
            if file_size > self.settings.max_file_size:
                return {
                    "error": f"File too large. Maximum size: {self.settings.max_file_size} bytes "
                    f"({self.settings.max_file_size / (1024*1024):.1f} MB)"
                }

            file_data = file_path.read_bytes()
            data_base64 = base64.b64encode(file_data).decode("utf-8")

            logger.info(f"File downloaded: {safe_filename} ({len(file_data)} bytes)")
            return {"success": True, "filename": safe_filename, "size": len(file_data), "data": data_base64}

        except Exception as e:
            logger.error(f"Error downloading file: {e}")
            return {"error": str(e)}

    def list_files(self) -> dict:
        """
        List all files in the transfer directory.

        Returns:
            dict: Contains 'files' list with file info or 'error' message

        Example:
            ```python
            # Client side
            result = server.list_files(token)
            for file_info in result['files']:
                print(f"{file_info['name']}: {file_info['size']} bytes")
            ```
        """
        try:
            files = []
            for file_path in self.settings.file_transfer_dir.iterdir():
                if file_path.is_file():
                    stat = file_path.stat()
                    files.append(
                        {
                            "name": file_path.name,
                            "size": stat.st_size,
                            "modified": stat.st_mtime,
                        }
                    )

            files.sort(key=lambda x: x["name"])
            return {"success": True, "files": files, "count": len(files)}

        except Exception as e:
            logger.error(f"Error listing files: {e}")
            return {"error": str(e)}

    def delete_file(self, filename: str) -> dict:
        """
        Delete a file from the server.

        Args:
            filename: Name of the file to delete (relative path within transfer directory)

        Returns:
            dict: Status with 'success' or 'error' message

        Example:
            ```python
            # Client side
            result = server.delete_file(token, "myfile.txt")
            ```
        """
        try:
            safe_filename = Path(filename).name
            if safe_filename != filename or ".." in filename:
                return {"error": "Invalid filename - only simple filenames allowed, no paths"}

            file_path = self.settings.file_transfer_dir / safe_filename

            if not file_path.exists():
                return {"error": f"File not found: {safe_filename}"}

            if not file_path.is_file():
                return {"error": f"Not a file: {safe_filename}"}

            file_path.unlink()
            logger.info(f"File deleted: {safe_filename}")
            return {"success": True, "filename": safe_filename}

        except Exception as e:
            logger.error(f"Error deleting file: {e}")
            return {"error": str(e)}

    def delete_all_files(self) -> dict:
        """
        Delete all files from the server transfer directory.

        Returns:
            dict: Status with count of deleted files or 'error' message

        Example:
            ```python
            # Client side
            result = server.delete_all_files(token)
            # Returns: {'success': True, 'deleted_count': 5, 'deleted_files': ['file1.txt', 'file2.json', ...]}
            ```
        """
        try:
            deleted_files = []
            deleted_count = 0

            for file_path in self.settings.file_transfer_dir.iterdir():
                if file_path.is_file():
                    try:
                        file_path.unlink()
                        deleted_files.append(file_path.name)
                        deleted_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete {file_path.name}: {e}")

            logger.info(f"Deleted all files: {deleted_count} file(s) removed")
            return {
                "success": True,
                "deleted_count": deleted_count,
                "deleted_files": deleted_files,
            }

        except Exception as e:
            logger.error(f"Error deleting all files: {e}")
            return {"error": str(e)}


class _RpcServerCli(RpcServerSettings):
    def cli_cmd(self):
        server = RpcServer(settings=self)
        try:
            server.server.serve_forever()
        except KeyboardInterrupt:
            logger.info("Server shutting down...")


if __name__ == "__main__":
    CliApp().run(_RpcServerCli)
