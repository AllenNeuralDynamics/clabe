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
from collections import deque
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
    JobOutput,
    JobResult,
    JobStatus,
    JobSubmission,
    JobSummary,
    Lease,
    LeaseRequest,
    WorkerInfo,
)
from ._settings import DaemonSettings, RepositoryConfig

logger = logging.getLogger(__name__)

#: Output lines kept per job, for following it while it runs.
OUTPUT_LINES = 500
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

    def __init__(self, job_id: str, cmd: list[str], cwd: str, run_id: str | None = None) -> None:
        self.job_id = job_id
        self.cmd = cmd
        self.cwd = cwd
        self.run_id = run_id
        self.submitted_at = _now()
        self.process: subprocess.Popen | None = None
        self.stopped = False
        self.future: Future | None = None
        self._output: deque[tuple[int, str]] = deque(maxlen=OUTPUT_LINES)
        self._lines = 0
        self._output_lock = threading.Lock()

    def add_line(self, text: str) -> None:
        """Keeps a line of the job's output (stdout and stderr, as they come)."""
        with self._output_lock:
            self._lines += 1
            self._output.append((self._lines, text))

    def output(self, after: int) -> JobOutput:
        """The kept lines after line number ``after``."""
        with self._output_lock:
            return JobOutput(lines=[text for n, text in self._output if n > after], next=self._lines)


class Worker:
    """Jobs, files and the lease of the worker role."""

    def __init__(self, settings: DaemonSettings, *, busy: Callable[[], bool] = lambda: False) -> None:
        self.settings = settings
        self._busy = busy
        self._pool = ThreadPoolExecutor(max_workers=settings.max_jobs, thread_name_prefix="clabe-job")
        self._jobs: dict[str, _Job] = {}
        self._read: deque[_Job] = deque(maxlen=50)  # finished jobs whose result was read: output still served
        self._lease: Lease | None = None
        self._expired: Lease | None = None  # the last lease that expired: its run may take the worker back first
        self._lock = threading.Lock()
        self.files_dir = settings.file_transfer_dir or Path(os.environ.get("TEMP", "/tmp")) / "clabe-worker"
        self.files_dir.mkdir(parents=True, exist_ok=True)

    # --- lease ------------------------------------------------------------------------------------------

    def lease(self) -> Lease | None:
        """The current lease, if it has not expired."""
        with self._lock:
            if self._lease is not None and self._lease.expires_at <= _now():
                logger.warning("The lease of %s expired", self._lease.owner)
                self._expired, self._lease = self._lease, None
            return self._lease

    def take(self, request: LeaseRequest) -> Lease:
        """Takes the worker for a run, or renews the lease it holds."""
        current = self.lease()
        with self._lock:
            if current is not None and current.lease_id != request.lease_id:
                raise HTTPException(status_code=423, detail=f"In use by {current.owner}.")
            if current is None and self._busy():
                raise HTTPException(status_code=409, detail="This machine is running an experiment of its own.")
            kept = self._expired
            if current is None and kept is not None and kept.run_id is not None and kept.run_id != request.run_id:
                until = kept.expires_at + datetime.timedelta(seconds=self.settings.lease_grace)
                if _now() < until:
                    raise HTTPException(
                        status_code=423,
                        detail=f"Kept for run {kept.run_id} of {kept.owner} until {until:%H:%M:%S} UTC (it may "
                        "reattach to its jobs).",
                    )
            lease_id = current.lease_id if current is not None else secrets.token_urlsafe(16)
            expires = _now() + datetime.timedelta(seconds=request.ttl)
            self._lease = Lease(lease_id=lease_id, owner=request.owner, run_id=request.run_id, expires_at=expires)
            return self._lease

    def release(self, lease_id: str, *, stop_jobs: bool) -> None:
        """Gives the worker back; ``stop_jobs`` stops the jobs still running (e.g. the run was aborted)."""
        with self._lock:
            if self._lease is None or self._lease.lease_id != lease_id:
                raise HTTPException(status_code=404, detail="No such lease.")
            run_id, self._lease, self._expired = self._lease.run_id, None, None
        if stop_jobs:
            for job in list(self._jobs.values()):
                if run_id is None or job.run_id == run_id:
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
            os_name=os.name,
        )

    def submit(self, submission: JobSubmission) -> JobResult:
        """Starts a job in the background."""
        if not submission.cmd:
            raise HTTPException(status_code=422, detail="Empty command.")
        config = self.repository(submission.repository)
        cwd = submission.cwd or str(config.path if config is not None else Path.cwd())
        job = _Job(str(uuid.uuid4()), submission.cmd, cwd, submission.run_id)
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
                stdout, stderr = _pump(job)
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
                stdout=stdout,
                stderr=stderr,
                returncode=code,
            )

    def output(self, job_id: str, after: int) -> JobOutput:
        """A job's output lines after line ``after`` (the last `OUTPUT_LINES` are kept)."""
        job = self._jobs.get(job_id) or next((j for j in self._read if j.job_id == job_id), None)
        if job is None:
            raise HTTPException(status_code=404, detail=f"No job {job_id}.")
        return job.output(after)

    def result(self, job_id: str) -> JobResult:
        """A job's state; a finished job is forgotten once its result is read (its last output lines are kept a
        little longer, for whoever follows them)."""
        job = self._jobs.get(job_id)
        if job is None or job.future is None:
            raise HTTPException(status_code=404, detail=f"No job {job_id}.")
        if not job.future.done():
            return JobResult(job_id=job_id, status=JobStatus.RUNNING)
        del self._jobs[job_id]
        self._read.append(job)
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

    def jobs_of(self, run_id: str) -> list[JobSummary]:
        """The jobs of run ``run_id`` not yet collected (running, or finished with an unread result)."""
        return [
            JobSummary(
                job_id=job.job_id,
                run_id=job.run_id,
                command=job.cmd,
                running=job.future is not None and not job.future.done(),
                submitted_at=job.submitted_at,
            )
            for job in list(self._jobs.values())
            if job.run_id == run_id
        ]

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


def _pump(job: _Job) -> tuple[str, str]:
    """Reads a job's stdout and stderr to their end, keeping each line as it comes; returns them whole."""
    assert job.process is not None and job.process.stdout is not None and job.process.stderr is not None
    chunks: dict[str, list[str]] = {"stdout": [], "stderr": []}

    def read(stream, name: str) -> None:
        """Reads one stream line by line."""
        for raw in iter(stream.readline, b""):
            text = raw.decode(errors="replace")
            chunks[name].append(text)
            job.add_line(text.rstrip("\r\n"))
        stream.close()

    readers = [
        threading.Thread(target=read, args=(job.process.stdout, "stdout"), daemon=True),
        threading.Thread(target=read, args=(job.process.stderr, "stderr"), daemon=True),
    ]
    for reader in readers:
        reader.start()
    job.process.wait()
    for reader in readers:
        reader.join()
    return "".join(chunks["stdout"]), "".join(chunks["stderr"])


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

    @guarded.get("/runs/{run_id}/jobs", response_model=list[JobSummary])
    def jobs_of(run_id: str) -> list[JobSummary]:
        """The jobs of a run not yet collected, e.g. for a restarted launcher to reattach to them."""
        return worker.jobs_of(run_id)

    @api.delete("/lease/{lease_id}", status_code=204)
    def release(lease_id: str, stop_jobs: bool = Query(default=False)) -> None:
        """Gives the worker back; ``stop_jobs`` stops its running jobs too."""
        worker.release(lease_id, stop_jobs=stop_jobs)

    _add_job_routes(guarded, worker)
    _add_file_routes(guarded, worker)
    api.include_router(guarded)
    return api


def _add_job_routes(router: APIRouter, worker: Worker) -> None:
    """The job routes of the worker API."""

    @router.post("/jobs", response_model=JobResult)
    def submit(body: JobSubmission) -> JobResult:
        """Starts a command in the background."""
        return worker.submit(body)

    @router.get("/jobs", response_model=JobList)
    def jobs() -> JobList:
        """The jobs, running and finished."""
        return worker.jobs()

    @router.get("/jobs/{job_id}", response_model=JobResult)
    def result(job_id: str) -> JobResult:
        """A job's state, with its output once it ended (read once)."""
        return worker.result(job_id)

    @router.get("/jobs/{job_id}/output", response_model=JobOutput)
    def output(job_id: str, after: int = Query(default=0, ge=0)) -> JobOutput:
        """A job's output lines after line ``after``, while it runs (the last 500 lines are kept)."""
        return worker.output(job_id, after)

    @router.post("/jobs/{job_id}/stop", response_model=JobResult)
    def stop(job_id: str) -> JobResult:
        """Stops a job."""
        return worker.stop(job_id)


def _add_file_routes(router: APIRouter, worker: Worker) -> None:
    """The file routes of the worker API."""

    @router.get("/files", response_model=list[FileInfo])
    def files() -> list[FileInfo]:
        """The files of the transfer directory."""
        return worker.files()

    @router.put("/files/{name}", response_model=FileInfo)
    async def upload(name: str, request: Request, overwrite: bool = Query(default=True)) -> FileInfo:
        """Uploads a file (the request body) to the transfer directory."""
        return worker.upload(name, await request.body(), overwrite)

    @router.get("/files/{name}", response_class=FileResponse)
    def download(name: str) -> FileResponse:
        """Downloads a file of the transfer directory."""
        return FileResponse(worker.download(name))

    @router.delete("/files/{name}", status_code=204)
    def delete(name: str) -> None:
        """Deletes a file of the transfer directory."""
        worker.delete(name)

    @router.delete("/files", response_model=list[str])
    def delete_all() -> list[str]:
        """Deletes every file of the transfer directory."""
        return worker.delete_all()
