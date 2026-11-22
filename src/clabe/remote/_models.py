"""The worker API's models, shared by the daemon (server) and [`WorkerClient`][clabe.remote.WorkerClient]."""

import datetime
from enum import Enum

from pydantic import BaseModel, Field

#: The daemon's default port, where the worker role is served too.
DEFAULT_PORT = 8070

#: Header carrying the lease of the run a request belongs to.
LEASE_HEADER = "X-Clabe-Lease"


class JobStatus(str, Enum):
    """Where a job is."""

    RUNNING = "running"
    DONE = "done"
    STOPPED = "stopped"
    ERROR = "error"


class WorkerInfo(BaseModel):
    """A worker, as seen for one of its repositories."""

    clabe_version: str = Field(description="clabe version of the daemon")
    hostname: str = Field(description="The machine's name")
    repository: str | None = Field(default=None, description="The repository jobs run in by default")
    working_dir: str = Field(description="Default working directory of jobs (the repository's root)")
    python_executable: str = Field(description="The repository environment's Python interpreter")
    leased_by: str | None = Field(default=None, description="Who holds the worker's lease, if anyone")
    os_name: str = Field(default="nt", description="The worker's ``os.name``: its paths are Windows ('nt') or POSIX")


class JobSubmission(BaseModel):
    """A command to run on the worker."""

    cmd: list[str]
    cwd: str | None = Field(default=None, description="Working directory; defaults to the repository's")
    run_id: str | None = Field(default=None, description="The run it belongs to (to find it again after a crash)")
    repository: str | None = Field(default=None, description="Repository whose root is the default working directory")
    trace_context: dict[str, str] = Field(default_factory=dict, description="W3C trace context of the caller")


class JobResult(BaseModel):
    """A job's state, and its output once it ended."""

    job_id: str
    status: JobStatus
    stdout: str | None = None
    stderr: str | None = None
    returncode: int | None = None
    error: str | None = None


class JobOutput(BaseModel):
    """Output lines of a running job, and the number of the last one (pass it as ``after`` next time)."""

    lines: list[str]
    next: int


class JobSummary(BaseModel):
    """A job, as listed for the run it belongs to."""

    job_id: str
    run_id: str | None
    command: list[str]
    running: bool
    submitted_at: datetime.datetime


class JobList(BaseModel):
    """The worker's jobs."""

    running: list[str] = Field(default_factory=list)
    finished: list[str] = Field(default_factory=list)


class FileInfo(BaseModel):
    """A file in the worker's transfer directory."""

    name: str
    size: int
    modified: float = Field(description="Last modified time as Unix timestamp")
    path: str = Field(description="Full path of the file on the worker")


class LeaseRequest(BaseModel):
    """Taking (or renewing) the worker for a run."""

    owner: str = Field(description="Who takes it, e.g. 'rig-3 / vr-foraging'")
    run_id: str | None = None
    ttl: float = Field(default=60.0, gt=0, description="Seconds until it expires unless renewed")
    lease_id: str | None = Field(default=None, description="The lease to renew")


class Lease(BaseModel):
    """The worker taken by a run; requests carrying it (``X-Clabe-Lease``) are served, others refused."""

    lease_id: str
    owner: str
    run_id: str | None
    expires_at: datetime.datetime
