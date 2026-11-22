"""The worker role: run jobs and keep files for a run hosted on another machine, under a lease.

What the XML-RPC server did, over the daemon's HTTP API (``/api/worker``). A run on another machine takes the
worker's **lease**; while it holds it, requests without it are refused (423) and the daemon starts no run of its
own. A lease not renewed within its time-to-live expires (e.g. the launcher crashed); its jobs keep running.
"""

import ctypes
import datetime
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from opentelemetry import propagate, trace
from opentelemetry.trace import Status, StatusCode

from .. import __version__
from ..remote._models import (
    LEASE_HEADER,
    FileInfo,
    JobList,
    JobResult,
    JobStatus,
    JobSubmission,
    Lease,
    LeaseRequest,
    WorkerInfo,
)
from ._settings import DaemonSettings, RepositoryConfig

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("clabe.worker")


def _signed(returncode: int | None) -> int | None:
    """A Windows exit code (unsigned 32-bit, e.g. 0xC0000005) as the signed value other tools report."""
    return None if returncode is None else ctypes.c_int32(returncode).value


def environment_python(repository: Path) -> Path:
    """The Python interpreter of a repository's ``uv`` environment (``.venv``)."""
    venv = repository / ".venv"
    return venv / "Scripts" / "python.exe" if sys.platform == "win32" else venv / "bin" / "python"


class _Job:
    """A command running on the worker."""

    def __init__(self, job_id: str, cmd: list[str], cwd: str) -> None:
        self.job_id = job_id
        self.cmd = cmd
        self.cwd = cwd
        self.process: subprocess.Popen | None = None
        self.stopped = False
        self.future: Future | None = None


class Worker:
    """Jobs, files and the lease of the worker role."""

    def __init__(self, settings: DaemonSettings, *, busy: Callable[[], bool] = lambda: False) -> None:
        self.settings = settings
        self._busy = busy
        self._pool = ThreadPoolExecutor(max_workers=settings.max_jobs, thread_name_prefix="clabe-job")
        self._jobs: dict[str, _Job] = {}
        self._lease: Lease | None = None
        self._lock = threading.Lock()
        self.files_dir = settings.file_transfer_dir or Path(os.environ.get("TEMP", "/tmp")) / "clabe-worker"
        self.files_dir.mkdir(parents=True, exist_ok=True)

    # --- lease ------------------------------------------------------------------------------------------

    def lease(self) -> Lease | None:
        """The current lease, if it has not expired."""
        with self._lock:
            if self._lease is not None and self._lease.expires_at <= _now():
                logger.warning("The lease of %s expired", self._lease.owner)
                self._lease = None
            return self._lease

    def take(self, request: LeaseRequest) -> Lease:
        """Takes the worker for a run, or renews the lease it holds."""
        current = self.lease()
        with self._lock:
            if current is not None and current.lease_id != request.lease_id:
                raise HTTPException(status_code=423, detail=f"In use by {current.owner}.")
            if current is None and self._busy():
                raise HTTPException(status_code=409, detail="This machine is running an experiment of its own.")
            lease_id = current.lease_id if current is not None else secrets.token_urlsafe(16)
            expires = _now() + datetime.timedelta(seconds=request.ttl)
            self._lease = Lease(lease_id=lease_id, owner=request.owner, run_id=request.run_id, expires_at=expires)
            return self._lease

    def release(self, lease_id: str, *, stop_jobs: bool) -> None:
        """Gives the worker back; ``stop_jobs`` stops the jobs still running (e.g. the run was aborted)."""
        with self._lock:
            if self._lease is None or self._lease.lease_id != lease_id:
                raise HTTPException(status_code=404, detail="No such lease.")
            self._lease = None
        if stop_jobs:
            for job in list(self._jobs.values()):
                self.stop(job.job_id)

    def check_lease(self, given: str | None) -> None:
        """Refuses a request that does not carry the current lease (when there is one)."""
        current = self.lease()
        if current is not None and given != current.lease_id:
            raise HTTPException(status_code=423, detail=f"In use by {current.owner}.")

    # --- jobs -------------------------------------------------------------------------------------------

    def repository(self, name: str | None) -> RepositoryConfig | None:
        """The repository called ``name`` (by default the only, or first, configured one)."""
        repositories = self.settings.repositories
        if name is None:
            return repositories[0] if repositories else None
        for repository in repositories:
            if repository.display_name == name:
                return repository
        raise HTTPException(status_code=404, detail=f"No repository named {name!r}.")

    def info(self, repository: str | None) -> WorkerInfo:
        """The worker, as seen for ``repository``."""
        config = self.repository(repository)
        root = config.path if config is not None else Path.cwd()
        python = environment_python(root) if config is not None else Path(sys.executable)
        lease = self.lease()
        return WorkerInfo(
            clabe_version=__version__,
            hostname=socket.gethostname(),
            repository=config.display_name if config is not None else None,
            working_dir=str(root.resolve()),
            python_executable=str(python if python.exists() else sys.executable),
            leased_by=lease.owner if lease is not None else None,
        )

    def submit(self, submission: JobSubmission) -> JobResult:
        """Starts a job in the background."""
        if not submission.cmd:
            raise HTTPException(status_code=422, detail="Empty command.")
        config = self.repository(submission.repository)
        cwd = submission.cwd or str(config.path if config is not None else Path.cwd())
        job = _Job(str(uuid.uuid4()), submission.cmd, cwd)
        span = _tracer.start_span(
            "clabe.worker.job",
            context=propagate.extract(submission.trace_context),
            attributes={"clabe.job_id": job.job_id, "process.command_args": job.cmd, "process.working_directory": cwd},
        )
        job.future = self._pool.submit(self._run, job, span)
        self._jobs[job.job_id] = job
        logger.info("Job %s: %s (in %s)", job.job_id, job.cmd, cwd)
        return JobResult(job_id=job.job_id, status=JobStatus.RUNNING)

    def _run(self, job: _Job, span: trace.Span) -> JobResult:
        """Runs a job to its end, inside its span."""
        with trace.use_span(span, end_on_exit=True):
            try:
                job.process = subprocess.Popen(
                    job.cmd, cwd=job.cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                stdout, stderr = job.process.communicate()
            except OSError as exc:
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                return JobResult(job_id=job.job_id, status=JobStatus.ERROR, error=str(exc))
            code = _signed(job.process.returncode)
            span.set_attribute("process.exit.code", code if code is not None else -1)
            if code:
                span.set_status(Status(StatusCode.ERROR))
            return JobResult(
                job_id=job.job_id,
                status=JobStatus.STOPPED if job.stopped else JobStatus.DONE,
                stdout=stdout.decode(errors="replace"),
                stderr=stderr.decode(errors="replace"),
                returncode=code,
            )

    def result(self, job_id: str) -> JobResult:
        """A job's state; a finished job is forgotten once its result is read."""
        job = self._jobs.get(job_id)
        if job is None or job.future is None:
            raise HTTPException(status_code=404, detail=f"No job {job_id}.")
        if not job.future.done():
            return JobResult(job_id=job_id, status=JobStatus.RUNNING)
        del self._jobs[job_id]
        return job.future.result()

    def stop(self, job_id: str, timeout: float = 10.0) -> JobResult:
        """Stops a job's process: asks it to exit, then kills it."""
        job = self._jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"No job {job_id}.")
        job.stopped = True
        process = job.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout)
            except subprocess.TimeoutExpired:
                process.kill()
        return JobResult(job_id=job_id, status=JobStatus.STOPPED)

    def jobs(self) -> JobList:
        """The jobs, running and finished (but not yet read)."""
        running = [j.job_id for j in self._jobs.values() if j.future is not None and not j.future.done()]
        finished = [j.job_id for j in self._jobs.values() if j.future is not None and j.future.done()]
        return JobList(running=running, finished=finished)

    # --- files ------------------------------------------------------------------------------------------

    def _file(self, name: str) -> Path:
        """A file of the transfer directory; only plain names are accepted."""
        if Path(name).name != name or "/" in name or "\\" in name or name in ("", ".", ".."):
            raise HTTPException(status_code=422, detail="Only plain file names are accepted, not paths.")
        return self.files_dir / name

    def upload(self, name: str, data: bytes, overwrite: bool) -> FileInfo:
        """Writes a file to the transfer directory."""
        path = self._file(name)
        if len(data) > self.settings.max_file_size:
            raise HTTPException(status_code=413, detail=f"Larger than {self.settings.max_file_size} bytes.")
        if path.exists() and not overwrite:
            raise HTTPException(status_code=409, detail=f"{name} exists; upload with overwrite to replace it.")
        path.write_bytes(data)
        return _file_info(path)

    def download(self, name: str) -> Path:
        """A file of the transfer directory, to send back."""
        path = self._file(name)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"No file {name}.")
        return path

    def files(self) -> list[FileInfo]:
        """The files of the transfer directory."""
        return sorted((_file_info(p) for p in self.files_dir.iterdir() if p.is_file()), key=lambda f: f.name)

    def delete(self, name: str) -> None:
        """Deletes a file of the transfer directory."""
        self.download(name).unlink()

    def delete_all(self) -> list[str]:
        """Deletes every file of the transfer directory; returns their names."""
        deleted = []
        for path in self.files_dir.iterdir():
            if path.is_file():
                try:
                    path.unlink()
                    deleted.append(path.name)
                except OSError as exc:
                    logger.error("Could not delete %s: %s", path, exc)
        return deleted


def _now() -> datetime.datetime:
    """The current time (UTC)."""
    return datetime.datetime.now(datetime.UTC)


def _file_info(path: Path) -> FileInfo:
    """A file's description."""
    stat = path.stat()
    return FileInfo(name=path.name, size=stat.st_size, modified=stat.st_mtime, path=str(path.resolve()))


def worker_router(worker: Worker, token_check) -> APIRouter:
    """The worker API (``/api/worker``), behind the daemon's token; jobs and files also need the lease, if any."""
    api = APIRouter(prefix="/api/worker", dependencies=[Depends(token_check)], tags=["worker"])

    def leased(lease: str | None = Header(default=None, alias=LEASE_HEADER)) -> None:
        """Refuses requests of anyone but the lease holder."""
        worker.check_lease(lease)

    guarded = APIRouter(dependencies=[Depends(leased)])

    @api.get("/info", response_model=WorkerInfo)
    def info(repository: str | None = Query(default=None)) -> WorkerInfo:
        """The worker, as seen for a repository (by default the first configured one)."""
        return worker.info(repository)

    @api.post("/lease", response_model=Lease)
    def take(body: LeaseRequest) -> Lease:
        """Takes the worker for a run (or renews the lease); 423 while another run holds it."""
        return worker.take(body)

    @api.delete("/lease/{lease_id}", status_code=204)
    def release(lease_id: str, stop_jobs: bool = Query(default=False)) -> None:
        """Gives the worker back; ``stop_jobs`` stops its running jobs too."""
        worker.release(lease_id, stop_jobs=stop_jobs)

    @guarded.post("/jobs", response_model=JobResult)
    def submit(body: JobSubmission) -> JobResult:
        """Starts a command in the background."""
        return worker.submit(body)

    @guarded.get("/jobs", response_model=JobList)
    def jobs() -> JobList:
        """The jobs, running and finished."""
        return worker.jobs()

    @guarded.get("/jobs/{job_id}", response_model=JobResult)
    def result(job_id: str) -> JobResult:
        """A job's state, with its output once it ended (read once)."""
        return worker.result(job_id)

    @guarded.post("/jobs/{job_id}/stop", response_model=JobResult)
    def stop(job_id: str) -> JobResult:
        """Stops a job."""
        return worker.stop(job_id)

    @guarded.get("/files", response_model=list[FileInfo])
    def files() -> list[FileInfo]:
        """The files of the transfer directory."""
        return worker.files()

    @guarded.put("/files/{name}", response_model=FileInfo)
    async def upload(name: str, request: Request, overwrite: bool = Query(default=True)) -> FileInfo:
        """Uploads a file (the request body) to the transfer directory."""
        return worker.upload(name, await request.body(), overwrite)

    @guarded.get("/files/{name}", response_class=FileResponse)
    def download(name: str) -> FileResponse:
        """Downloads a file of the transfer directory."""
        return FileResponse(worker.download(name))

    @guarded.delete("/files/{name}", status_code=204)
    def delete(name: str) -> None:
        """Deletes a file of the transfer directory."""
        worker.delete(name)

    @guarded.delete("/files", response_model=list[str])
    def delete_all() -> list[str]:
        """Deletes every file of the transfer directory."""
        return worker.delete_all()

    api.include_router(guarded)
    return api
