import asyncio
import os
import subprocess
from typing import Any, Optional

from ._base import AsyncExecutor, Command, CommandResult, ExecutableApp, Executor


class LocalExecutor(Executor):
    def __init__(self, cwd: os.PathLike | None = None, env: dict[str, str] | None = None) -> None:
        self.cwd = cwd or os.getcwd()
        self.env = env

    def run(self, command: Command[Any]) -> CommandResult:
        proc = subprocess.run(command.cmd, cwd=self.cwd, env=self.env, text=True, capture_output=True)
        return CommandResult(stdout=proc.stdout, stderr=proc.stderr, exit_code=proc.returncode)


class AsyncLocalExecutor(AsyncExecutor):
    def __init__(self, cwd: os.PathLike | None = None, env: dict[str, str] | None = None) -> None:
        self.cwd = cwd or os.getcwd()
        self.env = env

    async def run_async(self, command: Command) -> CommandResult:
        proc = await asyncio.create_subprocess_shell(
            command.cmd,
            cwd=self.cwd,
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode is None:
            raise RuntimeError("Process did not complete successfully and returned no return code.")

        return CommandResult(
            stdout=stdout.decode(),
            stderr=stderr.decode(),
            exit_code=proc.returncode,
        )


class _DefaultExecutorMixin:
    def run(self: ExecutableApp, executor_kwargs: Optional[dict[str, Any]] = None) -> CommandResult:
        """Execute the command using a local executor and return the result."""
        executor = LocalExecutor(**(executor_kwargs or {}))
        return self.command.execute(executor)

    async def run_async(self: ExecutableApp, executor_kwargs: Optional[dict[str, Any]] = None) -> CommandResult:
        """Execute the command asynchronously using a local executor and return the result."""
        executor = AsyncLocalExecutor(**(executor_kwargs or {}))
        return await self.command.execute_async(executor)
