"""Running on other machines: a checkout of this repository on another rig (`RemoteRepository`), reached
through that machine's clabe daemon, which runs jobs and keeps files for the run that leased it."""

from ._client import WorkerClient, WorkerClientSettings, WorkerError
from ._executor import RemoteExecutor
from ._models import FileInfo, JobResult, JobStatus, JobSummary, Lease, Share, WorkerInfo
from ._repository import RemoteRepository, RepositoryMismatchError, to_unc

__all__ = [
    "FileInfo",
    "JobResult",
    "JobStatus",
    "JobSummary",
    "Lease",
    "RemoteExecutor",
    "RemoteRepository",
    "RepositoryMismatchError",
    "Share",
    "WorkerClient",
    "WorkerClientSettings",
    "WorkerError",
    "WorkerInfo",
    "to_unc",
]
