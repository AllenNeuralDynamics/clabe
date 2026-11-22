"""Running commands on another machine's worker as if they were local (an :class:`~clabe.apps.Executor`)."""

import asyncio
import contextlib
import logging
import os
import re
import time
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..apps._base import Command, CommandResult
from ..workflow._records import ACTIVE_STEP
from ._models import JobResult, JobStatus
from ._provenance import record_job
from ._staged_command import StagedCommand, StagedFile

if TYPE_CHECKING:
    from ._client import WorkerClient

logger = logging.getLogger(__name__)


class RemoteExecutor:
    """Runs commands on a worker; any app runs on it unchanged (``app.run(executor=...)``).

    Inputs a command declares (``Command.inputs``) are sent along with it, and ``path_mapping`` rewrites local path
    prefixes to their location on the worker. A cancelled ``run_async`` (e.g. the run was aborted) stops the job on
    the worker. To run apps in a checkout of this repository on another machine, use
    :class:`~clabe.remote.RemoteRepository`, which builds on this executor.

    Args:
        client: The worker's client.
        timeout: Seconds to wait for a job (see the client's ``monitor``); the client's by default.
        poll_interval: Seconds between checks of a job; the client's by default.
        cwd: Working directory on the worker; the repository's by default.
        path_mapping: Local path prefixes to rewrite in commands, mapped to their location on the worker.
    """

    def __init__(
        self,
        client: "WorkerClient",
        timeout: float | None = None,
        poll_interval: float | None = None,
        cwd: str | None = None,
        path_mapping: Mapping[str, str] | None = None,
    ) -> None:
        self.client = client
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.cwd = cwd
        self.path_mapping = dict(path_mapping or {})
        self._python_executable: str | None = None

    @property
    def host(self) -> str | None:
        """The worker's host name (apps use it to reach services the command starts there)."""
        from urllib.parse import urlparse

        return urlparse(self.client.base_url).hostname

    def _map_paths(self, arg: str) -> str:
        """Rewrites local path prefixes in a command argument to their location on the worker."""
        flags = re.IGNORECASE if os.name == "nt" else 0
        for local, remote in self.path_mapping.items():
            pattern = re.escape(local.rstrip("\\/")) + r"(?=[\\/]|$)"  # whole components: C:\repo is not C:\repo2
            replacement = remote.rstrip("\\/")
            arg = re.sub(pattern, lambda _, r=replacement: r, arg, flags=flags)  # a function keeps backslashes
        return arg

    def _prepare(self, command: Command[Any]) -> list[str]:
        """The command line to submit: paths mapped, and staged through a spec file when it has inputs."""
        if not command.inputs:
            return [self._map_paths(arg) for arg in command.cmd] if self.path_mapping else list(command.cmd)
        placeholders = {str(path): name for name, path in command.inputs.items()}

        def substitute(arg: str) -> str:
            for local, name in placeholders.items():  # inputs first: they may live under a mapped prefix
                arg = arg.replace(local, f"{{{name}}}")
            return self._map_paths(arg)

        spec = StagedCommand(
            cmd=[substitute(arg) for arg in command.cmd],
            files={
                name: StagedFile(filename=Path(path).name, content=Path(path).read_text(encoding="utf-8"))
                for name, path in command.inputs.items()
            },
        )
        upload = self.client.upload_model(spec, f"clabe_staged_{uuid.uuid4().hex}.json")
        if self._python_executable is None:
            self._python_executable = self.client.info().python_executable
        return [self._python_executable, "-m", "clabe.remote._staged_command", upload.path]

    @staticmethod
    def _result(job: JobResult) -> CommandResult:
        """The command's result; raises when the job failed or was stopped."""
        if job.status is JobStatus.ERROR:
            raise RuntimeError(f"The worker could not run the job: {job.error}")
        result = CommandResult(stdout=job.stdout, stderr=job.stderr, exit_code=job.returncode or 0)
        if job.status is JobStatus.STOPPED and result.exit_code == 0:
            result = CommandResult(stdout=job.stdout, stderr=job.stderr, exit_code=-1)
        result.check_returncode()
        return result

    def run(self, command: Command[Any]) -> CommandResult:
        """Runs the command on the worker and waits for it; inside a run its output shows as a live activity."""
        logger.info("Running on %s: %s", self.client.base_url, command.cmd)
        cmd = self._prepare(command)
        job_id = self.client.submit_command(cmd, cwd=self.cwd).job_id
        record_job(self.client, job_id, cmd)
        return self.wait(job_id, _describe(cmd))

    def wait(self, job_id: str, description: str = "job") -> CommandResult:
        """Waits for a job already on the worker (e.g. one a crashed launcher started) and returns its result."""
        with self._activity(f"{description} on {self.host}") as follow:
            started = time.monotonic()
            while True:
                time.sleep(self._interval)
                follow.poll(job_id)
                result = self.client.get_result(job_id)
                if result.status is not JobStatus.RUNNING:
                    follow.poll(job_id, final=True)
                    return self._result(result)
                started = self._check_timeout(job_id, started)

    async def run_async(self, command: Command[Any]) -> CommandResult:
        """Runs the command on the worker without blocking the event loop; cancelling it stops the job. Inside a
        run its output shows as a live activity."""
        cmd = await asyncio.to_thread(self._prepare, command)
        job_id = (await asyncio.to_thread(self.client.submit_command, cmd, self.cwd)).job_id
        await asyncio.to_thread(record_job, self.client, job_id, cmd)
        return await self.wait_async(job_id, _describe(cmd))

    async def wait_async(self, job_id: str, description: str = "job") -> CommandResult:
        """Asynchronous :meth:`wait`; cancelling it stops the job."""
        try:
            with self._activity(f"{description} on {self.host}") as follow:
                started = time.monotonic()
                while True:
                    await asyncio.sleep(self._interval)
                    await asyncio.to_thread(follow.poll, job_id)
                    result = await asyncio.to_thread(self.client.get_result, job_id)
                    if result.status is not JobStatus.RUNNING:
                        await asyncio.to_thread(follow.poll, job_id, final=True)
                        return self._result(result)
                    started = self._check_timeout(job_id, started)
        except asyncio.CancelledError:
            logger.info("Stopping job %s on %s", job_id, self.client.base_url)
            await asyncio.to_thread(self.client.stop_job, job_id)
            raise

    @property
    def _interval(self) -> float:
        """Seconds between checks of a job."""
        return self.poll_interval or self.client.settings.poll_interval

    def _check_timeout(self, job_id: str, started: float) -> float:
        """The start of the timeout window: reset while monitoring (the job was seen running), else checked."""
        settings = self.client.settings
        if settings.monitor:
            return time.monotonic()
        timeout = self.timeout or settings.timeout
        if time.monotonic() - started >= timeout:
            raise TimeoutError(f"Job {job_id} did not complete within {timeout} seconds")
        return started

    @contextlib.contextmanager
    def _activity(self, description: str) -> Iterator["_Follow"]:
        """Inside a run, the job as an activity tagged with the worker's host, its output recorded as it comes."""
        context = ACTIVE_STEP.get()
        if context is None:
            yield _Follow(self.client, None, None)
            return
        with context.run.activity(description, machine=self.host) as activity_id:
            yield _Follow(self.client, context.run, activity_id)


def _describe(cmd: list[str]) -> str:
    """A short name for a command: its program."""
    return Path(cmd[0]).name if cmd else "job"


class _Follow:
    """Forwards a remote job's new output lines to its activity, at most about once a second."""

    def __init__(self, client: "WorkerClient", run: Any, activity_id: str | None) -> None:
        self.client = client
        self.run = run
        self.activity_id = activity_id
        self._after = 0
        self._last = 0.0

    def poll(self, job_id: str, *, final: bool = False) -> None:
        """Records the lines produced since the last poll (none outside a run); ``final`` reads the job's last lines
        whatever the time since the last poll."""
        if self.run is None or (not final and time.monotonic() - self._last < 1.0):
            return
        self._last = time.monotonic()
        try:
            output = self.client.job_output(job_id, self._after)
        except Exception:
            logger.debug("Could not follow the output of job %s", job_id, exc_info=True)
            return
        self._after = output.next
        self.run.activity_output(self.activity_id, output.lines)
