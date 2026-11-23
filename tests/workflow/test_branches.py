"""Branches of a step: run together, fail, retry and stop independently."""

import asyncio
import sys
import time
from pathlib import Path

import pytest

from clabe.apps import Command, identity_parser
from clabe.apps._executors import AsyncLocalExecutor
from clabe.workflow import BranchesStopped, LocalClient, Run, RunStatus, Workflow, concurrently
from clabe.workflow.events import BranchEnded, BranchStarted, PromptOpened
from tests.conftest import MockClient


def _run(wf: Workflow, client: MockClient) -> Run:
    run = Run(wf)
    LocalClient(run, client).drive()
    return run


def _branch_events(run: Run) -> list[tuple[str, str, int, str | None]]:
    return [
        (e.type, e.branch, e.attempt, getattr(e, "outcome", None))
        for e in run.events.since(0)
        if isinstance(e, BranchStarted | BranchEnded)
    ]


def test_only_the_failed_branch_is_retried_while_the_other_keeps_going():
    calls = {"behavior": 0, "ephys": 0}
    behavior_running = asyncio.Event()
    wf = Workflow("demo")

    @wf.step
    async def acquire() -> dict:
        async def behavior():
            calls["behavior"] += 1
            behavior_running.set()
            await asyncio.sleep(0.5)
            return "behavior data"

        async def ephys():
            calls["ephys"] += 1
            await behavior_running.wait()
            if calls["ephys"] == 1:
                raise RuntimeError("probe not found")
            return "ephys data"

        return await concurrently(behavior=behavior, ephys=ephys)

    client = MockClient({"pick": ["retry"]})
    run = _run(wf, client)
    assert run.status is RunStatus.COMPLETED
    assert run.output("acquire") == {"behavior": "behavior data", "ephys": "ephys data"}
    assert calls == {"behavior": 1, "ephys": 2}
    failure = client.asked[0]
    assert "ephys failed: probe not found" in failure.label and failure.field == "acquire.ephys.failed"
    assert [o.label for o in failure.options] == ["Retry ephys", "Continue without ephys", "Stop all"]
    assert set(_branch_events(run)) == {
        ("branch_started", "behavior", 1, None),
        ("branch_started", "ephys", 1, None),
        ("branch_ended", "ephys", 1, "failed"),
        ("branch_started", "ephys", 2, None),
        ("branch_ended", "ephys", 2, "done"),
        ("branch_ended", "behavior", 1, "done"),
    }


def test_continuing_without_a_branch_gives_none():
    wf = Workflow("demo")

    @wf.step
    async def acquire() -> dict:
        def broken():
            raise OSError("no camera")

        return await concurrently(behavior=lambda: asyncio.sleep(0.1, result="ok"), video=broken)

    run = _run(wf, MockClient({"pick": ["skip"]}))
    assert run.output("acquire") == {"behavior": "ok", "video": None}
    assert ("branch_ended", "video", 1, "skipped") in _branch_events(run)


def test_stopping_all_cancels_the_others_cleanly_and_fails_the_step():
    cleaned = []
    wf = Workflow("demo")

    @wf.step
    async def acquire() -> dict:
        async def behavior():
            try:
                await asyncio.sleep(30)
            finally:
                cleaned.append("behavior")  # e.g. close the app

        async def ephys():
            raise RuntimeError("probe not found")

        return await concurrently(behavior=behavior, ephys=ephys)

    client = MockClient({"pick": ["stop", "abort"]})  # the branch's choice, then the step's Retry/Go back/Abort
    started = time.monotonic()
    run = _run(wf, client)
    assert time.monotonic() - started < 10
    assert run.status is RunStatus.FAILED  # aborting after a failure ends the run as failed
    assert cleaned == ["behavior"]
    assert ("branch_ended", "behavior", 1, "cancelled") in _branch_events(run)
    step_failure = client.asked[1]
    assert step_failure.field == "acquire.failed" and "Stopped all branches" in step_failure.label


def test_aborting_the_run_stops_every_branch():
    cleaned = []
    wf = Workflow("demo")

    @wf.step
    async def acquire() -> dict:
        async def forever(name):
            try:
                await asyncio.sleep(60)
            finally:
                cleaned.append(name)

        return await concurrently(behavior=lambda: forever("behavior"), ephys=lambda: forever("ephys"))

    run = Run(wf)
    run.start()
    deadline = time.monotonic() + 5
    while sum(isinstance(e, BranchStarted) for e in run.events.since(0)) < 2:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    run.abort("operator")
    assert run.wait(10) and run.status is RunStatus.ABORTED
    assert sorted(cleaned) == ["behavior", "ephys"]
    assert not any(isinstance(e, PromptOpened) for e in run.events.since(0))


def test_outside_a_run_a_failure_stops_everything_and_raises():
    cleaned = []

    async def main():
        async def slow():
            try:
                await asyncio.sleep(30)
            finally:
                cleaned.append("slow")

        async def broken():
            raise ValueError("bad")

        return await concurrently(slow=slow, broken=broken)

    with pytest.raises(ValueError, match="bad"):
        asyncio.run(main())
    assert cleaned == ["slow"]
    assert asyncio.run(concurrently()) == {}
    assert issubclass(BranchesStopped, Exception)


def test_a_cancelled_command_stops_its_process(tmp_path: Path):
    beat = tmp_path / "beat"
    script = f"import time, pathlib\nwhile True:\n    pathlib.Path({str(beat)!r}).write_text(str(time.time()))\n    time.sleep(0.05)\n"

    async def main():
        task = asyncio.ensure_future(
            AsyncLocalExecutor().run_async(Command(cmd=[sys.executable, "-c", script], output_parser=identity_parser))
        )
        while not beat.exists():
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    last = beat.read_text()
    time.sleep(0.5)
    assert beat.read_text() == last  # the process is gone
