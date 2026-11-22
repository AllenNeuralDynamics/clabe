"""Several things at once inside one step: branches that fail, retry and stop independently."""

import asyncio
import contextlib
import inspect
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from ..ui import Choice, PickRequest
from ._events import BranchEnded, BranchStarted
from ._records import ACTIVE_STEP, _StepContext

logger = logging.getLogger(__name__)

#: Seconds to wait for the branches' clean-up (``finally`` blocks, processes stopping) once they are cancelled.
STOP_TIMEOUT = 30.0


class BranchesStopped(Exception):
    """Every branch was stopped because ``branch`` failed and the user chose to stop them all."""

    def __init__(self, branch: str, error: BaseException) -> None:
        super().__init__(f"Stopped all branches after {branch!r} failed: {error}")
        self.branch = branch
        self.error = error


async def concurrently(
    *, around: Callable[[], AbstractAsyncContextManager[Any]] | None = None, **branches: Callable[[], Any]
) -> dict[str, Any]:
    """Runs ``branches`` at the same time and returns their results by name. Await it from an async step.

    Each branch is a function of no arguments: an ``async def``, a lambda returning an awaitable
    (``lambda: app.run_async()``), or a plain function (run in a worker thread). When a branch fails, the others
    keep going and the user picks, for that branch alone:

    - **Retry** it (it is called again),
    - **Continue without** it (its result is ``None``), or
    - **Stop all**: every other branch is cancelled and `BranchesStopped` is raised, so the step fails and the
      run offers Retry / Go back / Abort for the whole step.

    Aborting the run (or going back, when the step is ``cancellable``) cancels every branch too. Cancelled branches
    get `STOP_TIMEOUT` seconds to clean up: put the shutting-down in ``finally`` (e.g.
    ``await open_ephys.close()``); commands run by clabe's executors are stopped for you.

    ``around`` is something that must run for as long as the branches do, such as a recording: a function returning
    an async context manager (e.g. an ``@asynccontextmanager``). It is entered before any branch starts and exited
    once they have all ended, also when they fail or are cancelled. If entering it fails, the user picks as for a
    failed branch: **Retry** it, **Continue without** it (the branches run alone), or **Stop all**.

    Outside a run (e.g. calling the step function in a test) nobody is asked: a failure stops all the branches and
    is raised.

    Example:
        ```python
        @asynccontextmanager
        async def recording():
            await open_ephys.launch(executor=remote)
            try:
                open_ephys.start()
                yield
            finally:
                open_ephys.stop()
                await open_ephys.close()


        @wf.step(title="Acquire", cancellable=True)
        async def acquire(launcher: Launcher, rig: Rig, session: Session) -> dict[str, Any]:
            return await concurrently(behavior=lambda: bonsai_app(rig, session).run_async(), around=recording)
        ```
    """
    if around is None:
        return await _run_branches(branches)
    context = ACTIVE_STEP.get()
    name = getattr(around, "__name__", "around")
    stack = contextlib.AsyncExitStack()
    attempt = await _enter(name, around, stack, context)  # on failure, nothing was entered
    if attempt is None:  # continued without it
        return await _run_branches(branches)
    try:
        async with stack:  # exits `around` once the branches have ended
            results = await _run_branches(branches)
    except asyncio.CancelledError:
        _emit(context, BranchEnded, name, attempt, outcome="cancelled")
        raise
    except Exception as exc:
        _emit(context, BranchEnded, name, attempt, outcome="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    _emit(context, BranchEnded, name, attempt, outcome="done")
    return results


async def _enter(
    name: str,
    around: Callable[[], AbstractAsyncContextManager[Any]],
    stack: contextlib.AsyncExitStack,
    context: _StepContext | None,
) -> int | None:
    """Enters ``around`` on ``stack``, asking what to do when that fails; returns the attempt that entered it, or
    ``None`` when the user continued without it."""
    attempt = 0
    while True:
        attempt += 1
        _emit(context, BranchStarted, name, attempt)
        try:
            await stack.enter_async_context(around())
            return attempt
        except asyncio.CancelledError:
            _emit(context, BranchEnded, name, attempt, outcome="cancelled")
            raise
        except Exception as exc:
            logger.error("%r failed to start: %s", name, exc, exc_info=exc)
            _emit(context, BranchEnded, name, attempt, outcome="failed", error=f"{type(exc).__name__}: {exc}")
            if context is None:
                raise
            choice = await asyncio.to_thread(context.run.ask, _failure_request(context, name, exc))
            if choice == "retry":
                continue
            if choice == "skip":
                _emit(context, BranchEnded, name, attempt, outcome="skipped")
                return None
            raise BranchesStopped(name, exc) from exc


async def _run_branches(branches: dict[str, Callable[[], Any]]) -> dict[str, Any]:
    """Runs ``branches`` at once under supervision (see `concurrently`)."""
    if not branches:
        return {}
    context = ACTIVE_STEP.get()
    asking = asyncio.Lock()  # one failure prompt at a time
    supervisors = {
        name: asyncio.ensure_future(_supervise(name, branch, context, asking)) for name, branch in branches.items()
    }
    try:
        await asyncio.gather(*supervisors.values())
    except BaseException:
        for supervisor in supervisors.values():
            supervisor.cancel()
        await asyncio.wait(supervisors.values(), timeout=STOP_TIMEOUT)
        if any(not s.done() for s in supervisors.values()):
            logger.warning("Some branches did not stop within %ss.", STOP_TIMEOUT)
        raise
    return {name: supervisor.result() for name, supervisor in supervisors.items()}


async def _supervise(name: str, branch: Callable[[], Any], context: _StepContext | None, asking: asyncio.Lock) -> Any:
    """Runs one branch until it succeeds, is skipped, or the user stops everything."""
    attempt = 0
    while True:
        attempt += 1
        _emit(context, BranchStarted, name, attempt)
        try:
            result = await _call(branch)
        except asyncio.CancelledError:
            _emit(context, BranchEnded, name, attempt, outcome="cancelled")
            raise
        except Exception as exc:
            logger.error("Branch %r failed: %s", name, exc, exc_info=exc)
            _emit(context, BranchEnded, name, attempt, outcome="failed", error=f"{type(exc).__name__}: {exc}")
            if context is None:
                raise
            async with asking:
                choice = await asyncio.to_thread(context.run.ask, _failure_request(context, name, exc))
            if choice == "retry":
                continue
            if choice == "skip":
                _emit(context, BranchEnded, name, attempt, outcome="skipped")
                return None
            raise BranchesStopped(name, exc) from exc
        _emit(context, BranchEnded, name, attempt, outcome="done")
        return result


async def _call(branch: Callable[[], Any]) -> Any:
    """Calls a branch: coroutine functions on the loop, anything else in a thread; awaits what it returns."""
    if inspect.iscoroutinefunction(branch):
        return await branch()
    result = await asyncio.to_thread(branch)
    return await result if inspect.isawaitable(result) else result


def _failure_request(context: _StepContext, name: str, error: BaseException) -> PickRequest:
    """What to do about a failed branch."""
    return PickRequest(
        label=f"{context.step.title}: {name} failed: {error}",
        options=[
            Choice("retry", f"Retry {name}"),
            Choice("skip", f"Continue without {name}"),
            Choice("stop", "Stop all"),
        ],
        allow_none=False,
        field=f"{context.step.name}.{name}.failed",
    )


def _emit(context: _StepContext | None, event: type, name: str, attempt: int, **fields: Any) -> None:
    """Records a branch event, when running in a run."""
    if context is not None:
        context.run.events.append(event(step=context.step.name, branch=name, attempt=attempt, **fields))
