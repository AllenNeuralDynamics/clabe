import logging
import secrets
import socket
import subprocess
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from functools import wraps
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

        server = SimpleXMLRPCServer((str(settings.address), settings.port), allow_none=True)
        server.register_function(self.require_auth(self.submit_command), "run")
        server.register_function(self.require_auth(self.get_result), "result")
        server.register_function(self.require_auth(self.list_jobs), "jobs")

        logger.info(f"Authentication token: {settings.token.get_secret_value()}")
        logger.info(f"XML-RPC server running on {settings.address}:{settings.port}...")
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

    def list_jobs(self):
        """List all running jobs"""
        return {
            "running": [jid for jid, fut in self.jobs.items() if not fut.done()],
            "finished": [jid for jid, fut in self.jobs.items() if fut.done()],
        }


class _RpcServerCli(RpcServerSettings):
    def cli_cmd(self):
        server = RpcServer(settings=self)
        try:
            server.server.serve_forever()
        except KeyboardInterrupt:
            logger.info("Server shutting down...")


if __name__ == "__main__":
    CliApp().run(_RpcServerCli)
