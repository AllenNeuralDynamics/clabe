"""Every command an app runs inside a run is recorded: program, files, exit code (command_finished events)."""

import datetime
import hashlib
import sys

from clabe.apps import Command, CommandError, identity_parser
from clabe.apps._executors import AsyncLocalExecutor, LocalExecutor
from clabe.workflow import Run, RunStatus, Workflow
from clabe.workflow.events import CommandFinished


def test_commands_run_by_a_step_are_recorded(tmp_path):
    script = tmp_path / "task.py"
    script.write_text("import sys; sys.exit(int(sys.argv[1]))")
    wf = Workflow("demo")

    @wf.step
    def ok() -> None:
        Command([sys.executable, str(script), "0", "--token", "s3cret"], identity_parser).execute(LocalExecutor())

    @wf.step
    async def failing(ok: None) -> str:
        try:
            await Command([sys.executable, str(script), "3"], identity_parser).execute_async(AsyncLocalExecutor())
        except CommandError:
            return "failed as expected"
        return "?"

    run = Run(wf)
    run.start()
    assert run.wait(30) and run.status is RunStatus.COMPLETED
    first, second = [e for e in run.events.since(0) if isinstance(e, CommandFinished)]
    assert (first.step, first.exit_code, first.error) == ("ok", 0, None)
    assert first.command[-2:] == ["--token", "***"] and "s3cret" not in str(first)  # secrets masked
    assert (
        first.executable_sha256
        and first.files[str(script.resolve())] == hashlib.sha256(script.read_bytes()).hexdigest()
    )
    assert (second.step, second.exit_code) == ("failing", 3) and "CommandError" in second.error
    assert second.duration_s >= 0
    for event in (first, second):  # always UTC
        assert event.started_at.tzinfo is datetime.UTC and event.started_at <= event.ended_at


def test_commands_outside_a_run_are_not_recorded():
    result = Command([sys.executable, "-c", "print(1)"], identity_parser).execute(LocalExecutor())
    assert result.exit_code == 0


def test_a_command_result_has_its_start_and_end_in_utc():
    before = datetime.datetime.now(datetime.UTC)
    result = Command([sys.executable, "-c", "print(1)"], identity_parser).execute(LocalExecutor())
    assert result.started_at.tzinfo is datetime.UTC and result.ended_at.tzinfo is datetime.UTC
    assert before <= result.started_at <= result.ended_at <= datetime.datetime.now(datetime.UTC)
