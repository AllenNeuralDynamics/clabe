"""A client of a machine's worker role (its clabe daemon): jobs, files, and the lease that reserves it."""

import logging
import socket
import threading
import time
from pathlib import Path

import requests
from opentelemetry import propagate
from pydantic import BaseModel, Field, HttpUrl, SecretStr

from ..apps import Command
from ..apps._base import CommandResult
from ..services import ServiceSettings
from ._models import (
    LEASE_HEADER,
    FileInfo,
    JobList,
    JobOutput,
    JobResult,
    JobStatus,
    JobSubmission,
    JobSummary,
    Lease,
    LeaseRequest,
    WorkerInfo,
)

logger = logging.getLogger(__name__)


class WorkerError(RuntimeError):
    """The worker refused or failed a request; ``status`` is the HTTP status."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


class WorkerClientSettings(ServiceSettings):
    """Settings of a `WorkerClient` (``worker_client:`` in clabe.yml)."""

    __yml_section__ = "worker_client"

    url: HttpUrl = Field(description="The worker's daemon, e.g. http://rig-b:8070")
    token: SecretStr | None = Field(default=None, description="The daemon's token")
    repository: str | None = Field(
        default=None, description="The repository jobs run in on the worker; defaults to its first configured one"
    )
    timeout: float = Field(default=30.0, description="Seconds to wait for a job (see monitor) or a request")
    poll_interval: float = Field(default=0.5, description="Seconds between checks of a job")
    max_file_size: int = Field(default=50 * 1024 * 1024, description="Largest file to upload (bytes)")
    monitor: bool = Field(
        default=True,
        description="If True, timeout is a liveness window: waiting goes on as long as the job is still running. "
        "If False, timeout is the total time a job may take.",
    )
    lease_ttl: float = Field(default=60.0, description="Seconds a lease lasts unless renewed (renewed every third)")


class WorkerClient:
    """Talks to the worker role of another machine's daemon.

    Jobs and files need the worker's **lease** when someone holds it: `lease` takes it (and keeps renewing
    it in the background) so that no other run uses the machine meanwhile; `release` gives it back.
    """

    def __init__(self, settings: WorkerClientSettings) -> None:
        self.settings = settings
        self._session = requests.Session()
        if settings.token is not None:
            self._session.headers["Authorization"] = f"Bearer {settings.token.get_secret_value()}"
        self._lease: Lease | None = None
        self._heartbeat: threading.Thread | None = None
        self._stop_heartbeat = threading.Event()

    @property
    def base_url(self) -> str:
        """The daemon's root URL."""
        return str(self.settings.url).rstrip("/")

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Sends a request to the worker API; raises `WorkerError` when it is refused."""
        headers = kwargs.pop("headers", {})
        if self._lease is not None:
            headers[LEASE_HEADER] = self._lease.lease_id
        response = self._session.request(
            method, f"{self.base_url}/api/worker{path}", headers=headers, timeout=self.settings.timeout, **kwargs
        )
        if not response.ok:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise WorkerError(f"{method} {path}: {response.status_code} {detail}", response.status_code)
        return response

    # --- identity and lease -----------------------------------------------------------------------------

    def info(self) -> WorkerInfo:
        """The worker, as seen for the configured repository."""
        params = {"repository": self.settings.repository} if self.settings.repository else None
        return WorkerInfo.model_validate_json(self._request("GET", "/info", params=params).content)

    def ping(self) -> bool:
        """Whether the worker answers."""
        try:
            self.info()
        except (requests.RequestException, WorkerError):
            return False
        return True

    def lease(self, owner: str | None = None, run_id: str | None = None) -> Lease:
        """Takes the worker for this run and keeps renewing it until `release`.

        Raises:
            WorkerError: 423 if another run holds it, 409 if the machine runs an experiment of its own.
        """
        owner = owner or socket.gethostname()
        body = LeaseRequest(owner=owner, run_id=run_id, ttl=self.settings.lease_ttl)
        if self._lease is not None:
            body.lease_id = self._lease.lease_id
        self._lease = Lease.model_validate_json(
            self._request("POST", "/lease", json=body.model_dump(mode="json")).content
        )
        if self._heartbeat is None:
            self._stop_heartbeat.clear()
            self._heartbeat = threading.Thread(
                target=self._renew, args=(owner, run_id), name="clabe-worker-lease", daemon=True
            )
            self._heartbeat.start()
        return self._lease

    def _renew(self, owner: str, run_id: str | None) -> None:
        """Renews the lease every third of its time-to-live until released."""
        while not self._stop_heartbeat.wait(self.settings.lease_ttl / 3):
            try:
                self.lease(owner, run_id)
            except (requests.RequestException, WorkerError) as exc:
                logger.warning("Could not renew the lease on %s: %s", self.base_url, exc)

    @property
    def leased(self) -> bool:
        """Whether this client holds the worker's lease."""
        return self._lease is not None

    def release(self, *, stop_jobs: bool = False) -> None:
        """Gives the worker back; ``stop_jobs`` also stops the jobs still running there."""
        self._stop_heartbeat.set()
        self._heartbeat = None
        lease, self._lease = self._lease, None
        if lease is None:
            return
        try:
            self._session.delete(
                f"{self.base_url}/api/worker/lease/{lease.lease_id}",
                params={"stop_jobs": stop_jobs},
                timeout=self.settings.timeout,
            )
        except requests.RequestException as exc:
            logger.warning("Could not release the lease on %s: %s", self.base_url, exc)

    # --- jobs -------------------------------------------------------------------------------------------

    def submit_command(self, cmd_args: list[str] | str, cwd: str | None = None) -> JobResult:
        """Starts a command in the background; the current trace context goes along."""
        trace_context: dict[str, str] = {}
        propagate.inject(trace_context)
        body = JobSubmission(
            cmd=[cmd_args] if isinstance(cmd_args, str) else [str(a) for a in cmd_args],
            cwd=cwd,
            run_id=self._lease.run_id if self._lease is not None else None,
            repository=self.settings.repository,
            trace_context=trace_context,
        )
        submitted = JobResult.model_validate_json(self._request("POST", "/jobs", json=body.model_dump()).content)
        logger.info("Submitted %s to %s as job %s", body.cmd, self.base_url, submitted.job_id)
        return submitted

    def jobs_of(self, run_id: str) -> list[JobSummary]:
        """The jobs of run ``run_id`` not yet collected (running, or finished with an unread result)."""
        return [JobSummary.model_validate(j) for j in self._request("GET", f"/runs/{run_id}/jobs").json()]

    def get_result(self, job_id: str) -> JobResult:
        """A job's state, with its output once it ended (the worker forgets it once read)."""
        return JobResult.model_validate_json(self._request("GET", f"/jobs/{job_id}").content)

    def is_running(self, job_id: str) -> bool:
        """Whether a job is still running."""
        return job_id in self.list_jobs().running

    def list_jobs(self) -> JobList:
        """The worker's jobs."""
        return JobList.model_validate_json(self._request("GET", "/jobs").content)

    def job_output(self, job_id: str, after: int = 0) -> JobOutput:
        """A running job's output lines after line ``after``."""
        response = self._request("GET", f"/jobs/{job_id}/output", params={"after": after})
        return JobOutput.model_validate_json(response.content)

    def stop_job(self, job_id: str) -> JobResult:
        """Stops a job's process."""
        return JobResult.model_validate_json(self._request("POST", f"/jobs/{job_id}/stop").content)

    def wait_for_result(self, job_id: str, timeout: float | None = None) -> JobResult:
        """Waits for a job to end (see ``monitor`` in the settings) and returns its result.

        Raises:
            TimeoutError: The job did not end in time (or, when monitoring, stopped being seen running).
        """
        timeout = self.settings.timeout if timeout is None else timeout
        started = time.monotonic()
        while True:
            result = self.get_result(job_id)
            if result.status is not JobStatus.RUNNING:
                return result
            if self.settings.monitor:
                started = time.monotonic()  # seen running: the liveness window starts again
            elif time.monotonic() - started >= timeout:
                raise TimeoutError(f"Job {job_id} did not complete within {timeout} seconds")
            time.sleep(self.settings.poll_interval)

    def run_command(self, cmd_args: list[str] | str, timeout: float | None = None, cwd: str | None = None) -> JobResult:
        """Runs a command and waits for its result."""
        return self.wait_for_result(self.submit_command(cmd_args, cwd=cwd).job_id, timeout)

    # --- files ------------------------------------------------------------------------------------------

    def upload_file(
        self, local_path: str | Path, remote_filename: str | None = None, overwrite: bool = True
    ) -> FileInfo:
        """Uploads a file to the worker's transfer directory."""
        path = Path(local_path)
        if not path.is_file():
            raise FileNotFoundError(f"Local file not found: {path}")
        return self.upload_bytes(path.read_bytes(), remote_filename or path.name, overwrite=overwrite)

    def upload_bytes(self, data: bytes, remote_filename: str, overwrite: bool = True) -> FileInfo:
        """Uploads content as a file of the worker's transfer directory."""
        if len(data) > self.settings.max_file_size:
            raise ValueError(f"{remote_filename} is larger than {self.settings.max_file_size} bytes.")
        response = self._request("PUT", f"/files/{remote_filename}", params={"overwrite": overwrite}, data=data)
        return FileInfo.model_validate_json(response.content)

    def upload_model(self, model: BaseModel, remote_filename: str, overwrite: bool = True) -> FileInfo:
        """Uploads a pydantic model as a JSON file."""
        return self.upload_bytes(model.model_dump_json(indent=2).encode("utf-8"), remote_filename, overwrite)

    def download_file(self, remote_filename: str, local_path: str | Path | None = None) -> Path:
        """Downloads a file of the worker's transfer directory (to ``local_path``, default its name here)."""
        target = Path(local_path) if local_path is not None else Path(remote_filename)
        target.write_bytes(self._request("GET", f"/files/{remote_filename}").content)
        return target

    def list_files(self) -> list[FileInfo]:
        """The files of the worker's transfer directory."""
        return [FileInfo.model_validate(f) for f in self._request("GET", "/files").json()]

    def delete_file(self, remote_filename: str) -> None:
        """Deletes a file of the worker's transfer directory."""
        self._request("DELETE", f"/files/{remote_filename}")

    def delete_all_files(self) -> list[str]:
        """Deletes every file of the worker's transfer directory; returns their names."""
        return self._request("DELETE", "/files").json()

    # --- as an executor ---------------------------------------------------------------------------------

    @property
    def executor(self):
        """An executor running commands on the worker (see [`RemoteExecutor`][clabe.remote.RemoteExecutor])."""
        from ._executor import RemoteExecutor

        return RemoteExecutor(self)

    def run(self, command: Command) -> CommandResult:
        """Runs ``command`` on the worker, e.g. ``app.run(executor=client)``."""
        return self.executor.run(command)

    async def run_async(self, command: Command) -> CommandResult:
        """Runs ``command`` on the worker, e.g. ``await app.run_async(executor=client)``."""
        return await self.executor.run_async(command)
