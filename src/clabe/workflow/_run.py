"""Executes a [`Workflow`][clabe.workflow.Workflow]: owns its state, its event log and its command inbox."""

import asyncio
import concurrent.futures
import contextlib
import contextvars
import ctypes
import dataclasses
import datetime
import enum
import inspect
import json
import logging
import threading
import traceback
import uuid
from collections.abc import Awaitable, Callable, Generator
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Secret, SecretBytes, SecretStr
from pydantic_core import to_jsonable_python

from .. import ui
from ..logging.otel import span
from ..runnable import get_activity_indicator
from ..ui import (
    AnswerRejected,
    Choice,
    ConfirmRequest,
    FieldRequest,
    MessageLevel,
    PickRequest,
    PromptCancelledError,
    Request,
)
from ._control import Abort, GoBack, GoBackRejected
from ._events import (
    ActivityEnded,
    ActivityOutput,
    ActivityStarted,
    ArtifactExported,
    EventLog,
    Header,
    Notify,
    PromptClosed,
    PromptOpened,
    RunClosed,
    RunClosing,
    RunFinished,
    RunStarted,
    SkipReason,
    StepFailed,
    StepFinished,
    StepsInvalidated,
    StepSkipped,
    StepStarted,
)
from ._records import ACTIVE_STEP, _StepContext, encode_output, output_line, write_artifact
from ._workflow import Step, Workflow

if TYPE_CHECKING:
    from ..launcher import Launcher

logger = logging.getLogger(__name__)


class StepStatus(enum.StrEnum):
    """Where a step is in its lifecycle."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class RunStatus(enum.StrEnum):
    """Where a run is in its lifecycle."""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


@dataclasses.dataclass(frozen=True)
class Ack:
    """A command or answer was accepted."""


@dataclasses.dataclass(frozen=True)
class Rejected:
    """A command or answer was refused; ``reason`` is shown to the client."""

    reason: str


class StepState(BaseModel):
    """Snapshot of one step, for clients."""

    name: str
    title: str
    status: StepStatus
    barrier: bool
    optional: str | None
    locked: bool
    summary: str | None = None
    conditional: bool = False
    skip_reason: SkipReason | None = None


class ClosingState(BaseModel):
    """The closing screen of a finished run (see [`Run.begin_closing`][clabe.workflow.Run.begin_closing])."""

    since: datetime.datetime
    deadline: datetime.datetime | None = None
    closed: bool = False
    closed_by: str | None = None


class RunState(BaseModel):
    """Snapshot of a run, for clients attaching mid-run. Follow events after ``last_seq``.

    ``back_target`` is the step [`Run.back`][clabe.workflow.Run.back] would go back to (``None``: Back is not
    available). ``closing`` is set once the finished run shows its closing screen.
    """

    run_id: str
    workflow: str
    status: RunStatus
    current_step: str | None
    steps: list[StepState]
    prompt: PromptOpened | None
    last_seq: int
    back_target: str | None = None
    closing: ClosingState | None = None


class _Stop(Exception):
    """Ends the run from inside the run thread."""

    def __init__(self, status: RunStatus, reason: str | None = None, error: BaseException | None = None) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.error = error


@dataclasses.dataclass
class _OpenPrompt:
    """A prompt waiting for an answer, and how the waiting step is woken."""

    request: Request
    opened: PromptOpened
    convert: Callable[[Any], Any] | None = None  # maps the validated answer to what the asker gets (resolved fields)
    identity: "_PromptKey | None" = None  # remembered answers are keyed by it; None: never remembered
    previous: Any = None  # the earlier accepted answer of the same prompt (validated value), when opened.can_forward
    done: threading.Event = dataclasses.field(default_factory=threading.Event)
    answer: Any = None
    cancelled: bool = False

    @property
    def id(self) -> str:
        """The prompt's identifier."""
        return self.opened.prompt_id


#: Identity of a prompt across attempts of its step: (owning step, request field, else label/title, else kind).
_PromptKey = tuple[str | None, str]


def _prompt_key(step: str | None, request: Request) -> _PromptKey | None:
    """The identity under which ``request``'s answer is remembered; ``None`` for prompts never forwarded.

    A failed step's Retry / Go back / Abort question is never remembered: repeating it is not a choice to replay.
    """
    name = getattr(request, "field", None) or getattr(request, "label", None) or getattr(request, "title", None)
    if step is not None and name == f"{step}.failed":
        return None
    return (step, str(name) if name else request.prompt_kind)


@dataclasses.dataclass(frozen=True)
class _Command:
    """A pending go back or abort, applied by the run thread."""

    kind: Literal["back", "abort"]
    target: str | None = None
    reason: str | None = None


def _summarize(value: Any, limit: int = 160) -> str | None:
    """A short, display-only rendering of a step output (never the raw object); never raises.

    Text and numbers as they are; a model as its name and first few fields (``Session: subject=123456, ...``); a
    mapping as its first few entries; a collection as its size; another object as its type unless it has a
    meaningful ``repr``.
    """
    if value is None:
        return None
    try:
        text = _short(value)
    except Exception:  # noqa: BLE001 -- a summary is cosmetic; an unprintable output must not fail its step
        return f"<{type(value).__name__}>"
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _short(value: Any) -> str:
    """The text of `_summarize`, before truncation."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float | Path | enum.Enum):
        return str(value.value if isinstance(value, enum.Enum) else value)
    if isinstance(value, BaseModel):
        fields = _scalar_fields(value.model_dump(mode="json"))
        return f"{type(value).__name__}: {fields}" if fields else type(value).__name__
    if isinstance(value, dict):
        return _scalar_fields(value) or f"{len(value)} entries"
    if isinstance(value, list | tuple | set | frozenset):
        return f"{len(value)} item{'s' if len(value) != 1 else ''}"
    text = repr(value)
    return type(value).__name__ if " object at 0x" in text else text


def _scalar_fields(data: dict[str, Any], count: int = 4) -> str:
    """The first ``count`` non-empty scalar entries of ``data`` (versions left out), as ``key=value``."""
    shown = [
        f"{key}={value}"
        for key, value in data.items()
        if isinstance(value, str | int | float | bool) and value != "" and not str(key).endswith("version")
    ]
    return ", ".join(shown[:count])


def _jsonable(value: Any) -> Any:
    """The JSON form of an accepted answer, for its ``PromptClosed`` event (secrets stay masked); never raises."""
    try:
        return to_jsonable_python(value, fallback=repr)
    except Exception:  # noqa: BLE001 -- a record must never refuse an answer the step accepted
        try:
            return repr(value)
        except Exception:  # noqa: BLE001 -- not even a repr: record the type alone
            return f"<{type(value).__name__}>"


#: Pydantic's secret types: an answer converted to one is recorded masked, never as the text that was typed.
_SECRET_TYPES = (SecretStr, SecretBytes, Secret)


def _contains_secret(value: Any, depth: int = 0) -> bool:
    """Whether ``value`` is, or holds (in containers or model fields), a pydantic secret."""
    if isinstance(value, _SECRET_TYPES):
        return True
    if depth > 8:
        return False
    if isinstance(value, dict):
        return any(_contains_secret(v, depth + 1) for v in value.values())
    if isinstance(value, list | tuple | set | frozenset):
        return any(_contains_secret(v, depth + 1) for v in value)
    if isinstance(value, BaseModel):
        return any(_contains_secret(v, depth + 1) for v in value.__dict__.values())
    return False


def _raise_in_thread(thread_id: int, exc_type: type[BaseException]) -> None:
    """Raises ``exc_type`` asynchronously in thread ``thread_id`` (CPython only)."""
    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id), ctypes.py_object(exc_type))


#: Seconds between checks that the run's event loop is alive, while the run thread waits for an async step.
_LOOP_POLL_INTERVAL = 0.1

#: Seconds the run waits for its event loop to cancel leftover tasks, and then to stop, when the run ends.
_LOOP_SHUTDOWN_TIMEOUT = 5.0


@dataclasses.dataclass(frozen=True)
class _Escaped:
    """A ``KeyboardInterrupt``/``SystemExit`` raised by an async step, carried to the run thread to re-raise."""

    exception: BaseException


async def _cancel_remaining_tasks() -> None:
    """Cancels every other task on the running loop and waits (bounded) for them to finish."""
    current = asyncio.current_task()
    tasks = [task for task in asyncio.all_tasks() if task is not current]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=_LOOP_SHUTDOWN_TIMEOUT / 2)
    await asyncio.get_running_loop().shutdown_asyncgens()


class Run:
    """One execution of a workflow.

    Steps run on a dedicated run thread, which has no running event loop: a sync step may run its own (e.g.
    ``asyncio.run``). Async steps run on one event loop per run, on a thread of its own, so tasks they create
    (e.g. an app's background task) and loop-bound objects carry across steps; tasks still running when the run
    ends are cancelled. Every state change is recorded in `events`. Commands (answers, go back,
    abort) are thread-safe and may come from any client.

    While it executes, the run is the process' [`Session`][clabe.ui.Session]: ``ui.ask``, the typed helpers (e.g.
    ``ui.confirm``), ``ui.notify`` and ``ui.header`` called from steps, from library code and from any thread reach
    it. One run per process is supported.

    Args:
        workflow: The workflow to execute. The run keeps a copy (`workflow`) of its steps as they are when the
            run is created; later changes to ``workflow`` do not affect the run.
        launcher: Passed to steps that take a ``launcher`` parameter.
        record_dir: When given, the run's records are written there: ``events.jsonl``, ``outputs.jsonl`` (one
            line per completed step attempt) and ``artifacts/`` (see [`export_artifact`][clabe.workflow.export_artifact]).
        run_id: Identifier of the run; generated when omitted.
    """

    def __init__(
        self,
        workflow: Workflow,
        *,
        launcher: "Launcher | None" = None,
        record_dir: Path | None = None,
        run_id: str | None = None,
    ) -> None:
        self.id = run_id or uuid.uuid4().hex[:12]
        # A copy: steps added to or replaced in ``workflow`` after the run is created do not change what it runs.
        self.workflow = workflow.variant(workflow.name)
        self.launcher = launcher
        self.record_dir = record_dir
        self.events = EventLog(self.id, record_dir / "events.jsonl" if record_dir is not None else None)
        self.status = RunStatus.CREATED
        self.reason: str | None = None
        self.error: BaseException | None = None
        self._lock = threading.RLock()
        self._prompt_gate = threading.Lock()
        self._finished = threading.Event()
        self._finishing = False
        self._finalizers: list[Callable[[RunStatus], Any]] = []
        self._steps: dict[str, StepStatus] = {step.name: StepStatus.PENDING for step in self.workflow.steps}
        self._outputs: dict[str, Any] = {}
        self._summaries: dict[str, str | None] = {}  # display renderings of _outputs, computed once per output
        self._attempts: dict[str, int] = {}
        self._skip_reasons: dict[str, SkipReason] = {}
        self._produced_by: dict[str, int] = {}  # step -> attempt whose output is current (0: skipped)
        self._artifacts: dict[tuple[str, int], list[str]] = {}
        self._barrier = -1
        self._current: Step | None = None
        self._prompt: _OpenPrompt | None = None
        self._pending: _Command | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._loop_stopping = False  # set once the run asks its loop to stop: the loop thread must not restart it
        self._task: asyncio.Future[Any] | None = None
        self._in_sync_call = False
        self._session_scope = contextlib.ExitStack()  # the run's binding as the process' session (see start())
        self._answers: dict[_PromptKey, tuple[Any, Any]] = {}  # identity -> (validated answer, its JSON form)
        self._kept_open = False
        self._closing: ClosingState | None = None
        self._closed = threading.Event()

    # --- lifecycle --------------------------------------------------------
    def start(self) -> None:
        """Starts executing the workflow on a background thread."""
        with self._lock:
            if self.status is not RunStatus.CREATED:
                raise RuntimeError("A run can only be started once.")
            self.status = RunStatus.RUNNING
            context = contextvars.copy_context()
            self._thread = threading.Thread(
                target=context.run, args=(self._main,), name=f"clabe-run-{self.id}", daemon=True
            )
            # The run is the process' session (see ``clabe.ui.bind``) from here until just after RunFinished, so
            # ``ui.ask``/``ui.notify`` from steps, the code they call and their threads reach it. Bound before the
            # thread starts, so it already is when start() returns; _main unbinds it.
            self._session_scope.enter_context(ui.bind(self))
        try:
            self._thread.start()
        except BaseException:
            self._session_scope.close()
            raise

    def add_finalizer(self, callback: Callable[[RunStatus], Any]) -> None:
        """Calls ``callback(status)`` once when the run ends, before ``RunFinished`` is recorded.

        For resources a run holds beyond its steps, e.g. a remote machine's lease. Callbacks run on the run thread,
        in the order they were added; one that raises is logged and does not stop the others.
        """
        with self._lock:
            self._finalizers.append(callback)

    def wait(self, timeout: float | None = None) -> bool:
        """Blocks until the run finishes; returns ``False`` if ``timeout`` elapsed first."""
        return self._finished.wait(timeout)

    def output(self, name: str) -> Any:
        """Returns the output of step ``name`` (``KeyError`` if it has not produced one)."""
        with self._lock:
            return self._outputs[name]

    def _main(self) -> None:
        """Body of the run thread: drives the steps and records how the run ended."""
        stop = _Stop(RunStatus.COMPLETED)
        remove_observer: Callable[[], None] | None = None
        try:
            self.events.append(RunStarted(workflow=self.workflow.name, steps=[s.name for s in self.workflow.steps]))
            # After RunStarted, so no activity precedes it. Activities anywhere in the process (e.g. an app a step
            # launches) are recorded while the run lives.
            remove_observer = get_activity_indicator().observe(self.activity)
            self._start_loop()
            self._drive()
        except _Stop as exc:
            stop = exc
        except KeyboardInterrupt as exc:  # injected by abort() into a busy synchronous step
            logger.info("Run %s was interrupted", self.id)
            stop = _Stop(RunStatus.ABORTED, str(exc) or type(exc).__name__, exc)
        except BaseException as exc:
            logger.exception("Run %s crashed", self.id)
            stop = _Stop(RunStatus.FAILED, str(exc) or type(exc).__name__, exc)
        finally:
            try:
                self._stop_loop()
                self._finalize(stop.status)
                try:
                    if remove_observer is not None:  # before waiters are released: no activity is recorded after
                        remove_observer()
                finally:
                    with self._lock:
                        self._finishing = True  # commands arriving from here on would be lost: refuse them
                        self.status, self.reason, self.error = stop.status, stop.reason, stop.error
                        if self._prompt is not None:  # a stray thread must never hang on a finished run
                            self._cancel_prompt_locked()
                        self.events.append(RunFinished(status=stop.status.value, reason=stop.reason))
            except Exception:  # waiters must be released even if the end cannot be recorded
                logger.exception("Run %s could not record its end", self.id)
            finally:
                try:
                    self._session_scope.close()  # bound by start(); unbound before waiters are released
                except Exception:
                    logger.exception("Run %s could not unbind itself as the session", self.id)
                finally:
                    self._finished.set()

    def _finalize(self, status: RunStatus) -> None:
        """Calls the finalizers with the run's final status."""
        with self._lock:
            finalizers, self._finalizers = self._finalizers, []
        for callback in finalizers:
            try:
                callback(status)
            except Exception:
                logger.exception("A finalizer of run %s failed", self.id)

    def _drive(self) -> None:
        """Runs pending steps in declaration order until none is left, applying commands between steps."""
        while True:
            self._apply_pending()
            step = self._next_step()
            if step is None:
                with self._lock:
                    if self._pending is not None:
                        continue  # a command arrived after the last step: apply it
                    self._finishing = True  # from here on, commands are refused instead of lost
                return
            self._run_step(step)

    def _next_step(self) -> Step | None:
        """The first pending step in declaration order, or ``None`` when all have run."""
        with self._lock:
            for step in self.workflow.steps:
                if self._steps[step.name] is StepStatus.PENDING:
                    return step
        return None

    # --- executing a step -------------------------------------------------
    def _run_step(self, step: Step) -> None:
        """Executes ``step``: checks its condition, asks first if optional, handles go back and abort, and pauses on
        failures."""
        if step.when is not None:
            try:
                holds = step.when(**{name: self._resolve(name) for name in step.when_params})
            except Exception as exc:  # noqa: BLE001 -- a failing condition pauses the run like a failing step
                self._fail(step, RuntimeError(f"The condition of {step.title} failed: {exc}"))
                return
            if not holds:
                self._skip(step, "condition")
                return
        if step.optional is not None and not self._confirm_optional(step):
            return
        self._begin(step)
        try:
            result = self._call(step)
        except GoBack as exc:
            self._end(step, StepStatus.PENDING)
            to, rejection = self.workflow.step_named_by(exc.to)
            if rejection is None and to == step.name:
                return  # going back to itself: run it again
            if rejection is None:
                rejection = self._go_back_from(step, to)
            else:
                self.notify(rejection, MessageLevel.WARNING)
            if rejection is not None:
                error = GoBackRejected(rejection)
                error.__cause__ = exc  # the failure's traceback shows where the step raised GoBack
                self._fail(step, error)
            return
        except Abort as exc:
            with self._lock:
                pending = self._pending  # a client's abort came first: its reason wins
                reason = pending.reason if pending is not None and pending.kind == "abort" else exc.reason
                stop = self._stop(RunStatus.ABORTED, reason)  # before anything else: refuse commands from here on
            self._end(step, StepStatus.PENDING)
            raise stop from exc
        except (PromptCancelledError, asyncio.CancelledError, KeyboardInterrupt) as exc:
            with self._lock:
                # An abort's KeyboardInterrupt may land in _call's finally before it clears the flag.
                self._in_sync_call = False
            self._end(step, StepStatus.PENDING)
            if not self._has_pending():  # otherwise the loop applies the command (go back / abort)
                self._fail(step, exc)
            return
        except Exception as exc:  # noqa: BLE001 -- any step error pauses the run instead of killing the launcher
            self._end(step, StepStatus.FAILED)
            self._fail(step, exc)
            return
        self._complete(step, result)

    def _begin(self, step: Step) -> None:
        """Marks ``step`` as running, as its next attempt."""
        with self._lock:
            attempt = self._attempts.get(step.name, 0) + 1
            self._attempts[step.name] = attempt
            self._steps[step.name] = StepStatus.RUNNING
            self._current = step
            self.events.append(StepStarted(step=step.name, attempt=attempt))

    def _end(self, step: Step, status: StepStatus) -> None:
        """Marks ``step`` as no longer running, with ``status``."""
        with self._lock:
            self._current = None
            self._steps[step.name] = status

    def _complete(self, step: Step, result: Any) -> None:
        """Records ``result`` as the output of ``step``, and its line in ``outputs.jsonl``."""
        with self._lock:
            attempt = self._attempts[step.name]
            inputs = {dep: self._produced_by.get(dep, 0) for dep in step.deps}
        # Outside the lock: repr of a large output may be slow, and the codec is user code.
        summary = _summarize(result)
        sha256 = self._record_output(step, attempt, inputs, result)  # before StepFinished: its line is on disk
        with self._lock:
            self._current = None
            self._steps[step.name] = StepStatus.DONE
            self._outputs[step.name] = result
            self._summaries[step.name] = summary
            self._produced_by[step.name] = attempt
            if step.barrier:
                self._barrier = max(self._barrier, self.workflow.index(step.name))
            self.events.append(
                StepFinished(step=step.name, summary=summary, attempt=attempt, inputs=inputs, output_sha256=sha256)
            )

    def _record_output(self, step: Step, attempt: int, inputs: dict[str, int], result: Any) -> str | None:
        """Encodes ``result`` with the step's codec and appends its line to ``outputs.jsonl``; returns its hash.

        Does nothing (returns ``None``) when the run keeps no records. Called without the run lock: the codec is user
        code and may export artifacts, which takes the lock itself. Never raises.
        """
        if self.record_dir is None:
            return None
        token = ACTIVE_STEP.set(_StepContext(run=self, step=step, attempt=attempt))
        try:
            encoded = encode_output(result, step.codec)
        finally:
            ACTIVE_STEP.reset(token)
        if encoded.error is not None:
            if step.codec is not None:
                logger.warning("Codec %s failed on the output of step %s: %s", encoded.codec, step.name, encoded.error)
                self.notify(f"Could not encode the output of {step.title}: {encoded.error}", MessageLevel.WARNING)
            else:
                logger.debug("Recorded the output of step %s as its repr: %s", step.name, encoded.error)
        with self._lock:
            artifacts = list(self._artifacts.get((step.name, attempt), []))
        line = output_line(
            run_id=self.id,
            step=step.name,
            index=self.workflow.index(step.name),
            attempt=attempt,
            inputs=inputs,
            encoded=encoded,
            artifacts=artifacts,
        )
        self._write_output_line(step, line)
        return encoded.sha256

    def _write_output_line(self, step: Step, line: dict[str, Any]) -> None:
        """Appends ``line`` to ``outputs.jsonl``; a failure is logged and reported, never raised."""
        if self.record_dir is None:
            return
        try:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            with (self.record_dir / "outputs.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(line, ensure_ascii=False) + "\n")
        except Exception as exc:  # a record that cannot be written must not fail the step
            logger.exception("Could not record the output of step %s", step.name)
            self.notify(f"Could not record the output of {step.title}: {exc}", MessageLevel.WARNING)

    def _export_artifact(self, step: Step, attempt: int, source: Any, name: str | None) -> Path:
        """Writes an artifact of ``step``'s attempt ``attempt`` and records it (see `export_artifact`)."""
        if self.record_dir is None:
            raise RuntimeError("This run has no record directory, so it cannot keep artifacts.")
        directory = self.record_dir / "artifacts" / step.name / str(attempt)
        written = write_artifact(directory, source, name)  # outside the lock: copying may be slow
        relative = (PurePosixPath(step.name) / str(attempt) / written).as_posix()
        with self._lock:
            self._artifacts.setdefault((step.name, attempt), []).append(relative)
            self.events.append(ArtifactExported(step=step.name, attempt=attempt, path=relative))
        return self.record_dir / "artifacts" / relative

    def _call(self, step: Step) -> Any:
        """Calls the step function with its resolved parameters, awaiting its result if needed.

        No event loop runs on the run thread while the function is called, so a sync step may run its own (e.g.
        ``asyncio.run``). An awaitable result is awaited as a task on the run's event loop.
        """
        kwargs = {name: self._resolve(name) for name in step.params}
        with self._lock:
            attempt = self._attempts[step.name]
        attributes = {"clabe.step": step.name, "clabe.run_id": self.id, "clabe.step.attempt": attempt}
        # Set before the function is called, so the task of a coroutine (which copies the context) sees it too.
        # Before the flag below is set, so an abort's KeyboardInterrupt cannot land between setting and resetting.
        token = ACTIVE_STEP.set(_StepContext(run=self, step=step, attempt=attempt))
        try:
            # The span covers the sync call and the awaited task (which inherits it as its parent). An abort's
            # KeyboardInterrupt (see below) always lands inside the span's body, never in its own bookkeeping.
            with span(f"clabe.step.{step.name}", attributes=attributes):
                # abort() injects KeyboardInterrupt only while this flag is set, so it lands between setting the
                # flag here and clearing it in `finally`: always inside this try, and so always reaching _run_step.
                # If it lands in `finally` before the flag is cleared, _run_step's interrupt branch clears it.
                # abort() injects at most once.
                try:
                    with self._lock:
                        if self._aborting_locked():  # aborted as the step started, before it could be interrupted
                            raise KeyboardInterrupt
                        # Calling a coroutine function runs none of its code: nothing to interrupt, and an injected
                        # KeyboardInterrupt would discard the coroutine unawaited. The task below is cancelled
                        # instead.
                        self._in_sync_call = not inspect.iscoroutinefunction(step.func)
                    result = step.func(**kwargs)
                finally:
                    with self._lock:
                        self._in_sync_call = False
                if inspect.isawaitable(result):
                    # Copied here, so the task sees ACTIVE_STEP and the span (its parent).
                    result = self._await_on_loop(step, result, contextvars.copy_context())
                return result
        finally:
            ACTIVE_STEP.reset(token)

    def _await_on_loop(self, step: Step, awaitable: Awaitable[Any], context: contextvars.Context) -> Any:
        """Awaits ``awaitable`` as a task on the run's loop, in ``context``, blocking the run thread until it ends.

        The task is registered as cancellable by commands (see `_interrupt_locked`). A cancelled task raises
        `asyncio.CancelledError` here; ``KeyboardInterrupt`` and ``SystemExit`` raised by the step are re-raised
        here rather than escaping the loop (which would stop it).

        Raises:
            RuntimeError: If the run's event loop stops before the task ends.
        """
        loop, thread = self._loop, self._loop_thread
        assert loop is not None and thread is not None, "the run's event loop is not running"
        done: concurrent.futures.Future[Any] = concurrent.futures.Future()

        async def guarded() -> Any:
            """Awaits the step, catching what would otherwise escape (and stop) the loop."""
            try:
                return await awaitable
            except (KeyboardInterrupt, SystemExit) as exc:
                return _Escaped(exc)

        def finished(task: asyncio.Task[Any]) -> None:
            with self._lock:
                if self._task is task:
                    self._task = None  # before the run thread resumes: the next step's task may be registered next
            if task.cancelled():
                if inspect.iscoroutine(awaitable) and inspect.getcoroutinestate(awaitable) == inspect.CORO_CREATED:
                    awaitable.close()  # cancelled before it started: no "never awaited" warning
                done.set_exception(asyncio.CancelledError())
            elif (error := task.exception()) is not None:
                done.set_exception(error)
            elif isinstance(result := task.result(), _Escaped):
                done.set_exception(result.exception)
            else:
                done.set_result(result)

        def create() -> None:
            # Runs in ``context`` (see below), which the task copies.
            try:
                task = asyncio.ensure_future(guarded())
            except BaseException as exc:  # noqa: BLE001 -- the run thread must never wait on a task that never was
                done.set_exception(exc)
                return
            task.add_done_callback(finished)
            with self._lock:
                self._task = task
                pending = self._pending  # arrived while neither the sync call nor the task could be interrupted
                if pending is not None and (pending.kind == "abort" or step.cancellable):
                    task.cancel()

        loop.call_soon_threadsafe(create, context=context)
        while not concurrent.futures.wait([done], timeout=_LOOP_POLL_INTERVAL).done:
            if not thread.is_alive() and not done.done():
                with self._lock:
                    self._task = None  # dead with its loop: no longer something a command can interrupt
                raise RuntimeError("The run's event loop stopped unexpectedly.")
        return done.result()

    def _start_loop(self) -> None:
        """Starts the run's event loop on a thread of its own; async steps and their tasks run there."""
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=self._run_loop, args=(loop,), name=f"clabe-run-{self.id}-loop", daemon=True)
        with self._lock:
            self._loop, self._loop_thread = loop, thread
        thread.start()

    def _run_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Body of the loop thread: runs ``loop`` until it is stopped, surviving exceptions that escape it."""
        while True:
            try:
                loop.run_forever()
                return  # stopped
            except BaseException:  # e.g. SystemExit in a background task: keep the run's other tasks running
                # run_forever forgets a stop request when an exception escapes it: honour the run's own.
                with self._lock:
                    stopping = self._loop_stopping
                if stopping:
                    logger.exception("An exception escaped the event loop of run %s as it stopped", self.id)
                    return
                logger.exception("An exception escaped the event loop of run %s; it keeps running", self.id)

    def _stop_loop(self) -> None:
        """Cancels the tasks still running on the run's loop, then stops and closes it. Bounded; never raises."""
        with self._lock:
            loop, thread = self._loop, self._loop_thread
        if loop is None or thread is None:
            return
        try:
            if thread.is_alive():
                try:
                    asyncio.run_coroutine_threadsafe(_cancel_remaining_tasks(), loop).result(_LOOP_SHUTDOWN_TIMEOUT)
                except Exception:
                    logger.exception("Run %s could not cancel the tasks left on its event loop", self.id)
                with self._lock:
                    self._loop_stopping = True
                loop.call_soon_threadsafe(loop.stop)
                thread.join(_LOOP_SHUTDOWN_TIMEOUT)
            if thread.is_alive():
                logger.warning("The event loop of run %s did not stop; leaving it running", self.id)
            else:
                loop.close()
        except Exception:
            logger.exception("Run %s could not stop its event loop", self.id)

    def _resolve(self, name: str) -> Any:
        """The value of parameter ``name``: a builtin or an earlier step's output."""
        if name == "launcher":
            return self.launcher
        if name == "run":
            return self
        with self._lock:
            return self._outputs[name]

    # --- prompts ----------------------------------------------------------
    def ask(self, request: Request) -> Any:
        """Opens a prompt for ``request`` and blocks until a client answers it.

        Called from step code (directly, or through [`clabe.ui.ask`][clabe.ui.ask] and its helpers). A [`FieldRequest`][clabe.ui.FieldRequest] is
        resolved first: the run opens the primitive prompt it resolves to (e.g. a ``confirm`` for a ``bool`` field)
        and returns the answer converted to the field's type.

        Args:
            request: A prompt request from [`clabe.ui`][clabe.ui] (e.g. a [`TextRequest`][clabe.ui.TextRequest]).

        Returns:
            The validated, coerced answer.

        Raises:
            PromptCancelledError: The prompt was cancelled (e.g. by going back or aborting). After it, return or
                raise; do not ask again — the run is changing course.
        """
        if not isinstance(request, Request):
            raise TypeError(f"Unsupported prompt request: {type(request).__name__}")
        if isinstance(request, FieldRequest):
            primitive, convert = request.resolve()
            return self._ask(primitive, None, convert)
        return self._ask(request, None)

    def _ask(self, request: Request, step: Step | None, convert: Callable[[Any], Any] | None = None) -> Any:
        """Opens a prompt owned by ``step`` (default: the running step) and waits for it to close.

        ``convert`` (for a resolved field) maps the validated answer to the value returned; it runs when the answer
        arrives, so a failing conversion rejects the answer and a secret result is what gets recorded (masked).
        """
        prompt_id = uuid.uuid4().hex[:12]
        kind, spec, schema = request.prompt_kind, request.spec(), request.answer_schema()  # may be slow
        with self._prompt_gate:
            with self._lock:
                if self.status is not RunStatus.RUNNING:
                    raise PromptCancelledError("The run is not running.")
                if self._pending is not None:
                    raise PromptCancelledError("The run is changing course.")
                owner = step or self._current
                identity = _prompt_key(owner.name if owner else None, request)
                remembered = self._answers.get(identity) if identity is not None else None
                opened = PromptOpened(
                    prompt_id=prompt_id,
                    step=owner.name if owner else None,
                    kind=kind,
                    spec=spec,
                    answer_schema=schema,
                    previous_answer=remembered[1] if remembered is not None else None,
                    can_forward=remembered is not None,
                )
                prompt = _OpenPrompt(
                    request=request,
                    opened=opened,
                    convert=convert,
                    identity=identity,
                    previous=remembered[0] if remembered is not None else None,
                )
                # Visible before it is published, so a subscriber that answers on this thread (which the event log
                # forbids, but which is tolerated) finds it open.
                self._prompt = prompt
                try:
                    prompt.opened = self.events.append(opened)
                except BaseException:
                    if self._prompt is prompt:
                        self._prompt = None
                    raise
            prompt.done.wait()
        if prompt.cancelled:
            raise PromptCancelledError("The prompt was cancelled.")
        return prompt.answer

    def answer(self, prompt_id: str, value: Any, *, client: str = "local") -> Ack | Rejected:
        """Answers the open prompt ``prompt_id``. The first valid answer wins.

        Args:
            prompt_id: The prompt being answered.
            value: The answer (a Python value or its JSON form).
            client: Who answered; recorded in the ``PromptClosed`` event.

        Returns:
            `Ack` if the answer closed the prompt, otherwise `Rejected` with the reason.
        """
        with self._lock:
            prompt = self._prompt
        if prompt is None or prompt.id != prompt_id:
            return Rejected("This prompt is no longer open.")
        try:
            coerced = prompt.request.validate_answer(value)  # outside the lock: validators may be slow
            result = prompt.convert(coerced) if prompt.convert is not None else coerced
        except AnswerRejected as exc:
            return Rejected(str(exc))
        except Exception as exc:  # a malformed answer must never reach the client as an exception
            logger.debug("Answer %r to prompt %s broke its validator", value, prompt_id, exc_info=True)
            return Rejected(f"Invalid answer: {exc}")
        # The primitive answer is recorded (it replays the prompt), except a secret: then only its mask is.
        secret = _contains_secret(result)
        recorded = _jsonable(result if secret else coerced)  # outside the lock: may be slow
        with self._lock:
            if self._prompt is not prompt:
                return Rejected("This prompt is no longer open.")
            prompt.answer = result
            self._prompt = None
            if prompt.identity is not None:
                if secret:  # never prefilled nor forwarded
                    self._answers.pop(prompt.identity, None)
                else:
                    self._answers[prompt.identity] = (coerced, recorded)
            self.events.append(
                PromptClosed(prompt_id=prompt_id, outcome="answered", answered_by=client, answer=recorded)
            )
            prompt.done.set()
        return Ack()

    def forward(self, *, client: str = "local") -> Ack | Rejected:
        """Answers the open prompt with the answer it got last time (Forward), through the normal validation.

        A prompt's earlier answer is remembered per prompt identity: the step that asks it and the request's
        ``field`` (else its label or title, else its kind), so it is stable across attempts of the step. Secret
        answers and a failed step's Retry / Go back / Abort question are never remembered. The open prompt's
        ``can_forward`` and ``previous_answer`` (see [`PromptOpened`][clabe.workflow.events.PromptOpened]) say whether
        and what Forward answers.

        Args:
            client: Who forwards; recorded in the ``PromptClosed`` event.

        Returns:
            `Ack` if the earlier answer closed the prompt, otherwise `Rejected` with the reason (no prompt is open,
            it has no earlier answer, or the answer is no longer valid).
        """
        with self._lock:
            prompt = self._prompt
        if prompt is None:
            return Rejected("No prompt is open.")
        if not prompt.opened.can_forward:
            return Rejected("This prompt has no earlier answer to repeat.")
        return self.answer(prompt.id, prompt.previous, client=client)

    def previous_answer(self, prompt_id: str) -> tuple[bool, Any]:
        """Whether the open prompt ``prompt_id`` was answered before, and that answer (the validated value).

        For in-process clients that prefill the prompt (see `forward`); ``(False, None)`` once it closed.
        """
        with self._lock:
            prompt = self._prompt
            if prompt is None or prompt.id != prompt_id or not prompt.opened.can_forward:
                return False, None
            return True, prompt.previous

    def cancel_prompt(self) -> None:
        """Cancels the open prompt, if any; the waiting step gets [`PromptCancelledError`][clabe.ui.PromptCancelledError]."""
        with self._lock:
            if self._prompt is not None:
                self._cancel_prompt_locked()

    def prompt_request(self, prompt_id: str) -> Request | None:
        """The request object behind an open prompt (for in-process clients), or ``None`` if it closed."""
        with self._lock:
            prompt = self._prompt
            return prompt.request if prompt is not None and prompt.id == prompt_id else None

    def is_prompt_open(self, prompt_id: str) -> bool:
        """Whether ``prompt_id`` is still waiting for an answer."""
        return self.prompt_request(prompt_id) is not None

    def _cancel_prompt_locked(self) -> None:
        """Cancels the open prompt; the caller holds ``_lock`` and has checked a prompt is open."""
        prompt = self._prompt
        assert prompt is not None
        prompt.cancelled = True
        self._prompt = None
        self.events.append(PromptClosed(prompt_id=prompt.id, outcome="cancelled"))
        prompt.done.set()

    # --- navigation -------------------------------------------------------
    def go_back(self, target: str) -> Ack | Rejected:
        """Goes back to step ``target``: it and its dependents run again.

        Rejected when ``target`` has not run, is locked by a completed barrier, or the current step is busy and
        cannot be interrupted (only ``cancellable`` async steps can be).

        Args:
            target: Name of the step to go back to.

        Returns:
            `Ack` if the command was accepted, otherwise `Rejected` with the reason.
        """
        with self._lock:
            if self._finished.is_set():
                return Rejected("The run has finished.")
            if self._finishing:
                return Rejected("The run is finishing.")
            if self._aborting_locked():
                return Rejected("The run is aborting.")
            rejection = self._back_rejection(target)
            if rejection is not None:
                return Rejected(rejection)
            busy = self._current
            if self._prompt is None and busy is not None and not (busy.cancellable and self._task is not None):
                return Rejected(f"{busy.title} is busy and cannot be interrupted.")
            self._pending = _Command("back", target=target)
            self._interrupt_locked(allow_sync=False)
        return Ack()

    def back(self) -> Ack | Rejected:
        """Goes back to the previous step (Back): the nearest earlier step that has run and is not locked.

        The previous step of the step asking the open prompt, else of the running step; see `back_target`.

        Returns:
            `Ack` if the command was accepted, otherwise `Rejected` with the reason.
        """
        target = self.back_target()
        if target is None:
            return Rejected("There is no earlier step to go back to.")
        return self.go_back(target)

    def back_target(self) -> str | None:
        """The step `back` would go back to now, or ``None`` when Back is not available."""
        with self._lock:
            if self._finished.is_set() or self._finishing:
                return None
            name = self._prompt.opened.step if self._prompt is not None else None
            if name is None and self._current is not None:
                name = self._current.name
            if name is None or name not in self._steps:
                return None
            return self._default_back_target(self.workflow.get(name))

    def retry(self, step: str, *, client: str = "local") -> Ack | Rejected:
        """Retries ``step`` after it failed: answers its open Retry / Go back / Abort prompt with "retry"."""
        return self._answer_step_prompt(step, f"{step}.failed", "retry", "has no failure waiting", client)

    def skip(self, step: str, *, client: str = "local") -> Ack | Rejected:
        """Skips the optional ``step``: answers its open "run this step?" prompt with no."""
        return self._answer_step_prompt(step, f"{step}.optional", False, "is not asking whether to run", client)

    def _answer_step_prompt(self, step: str, field: str, value: Any, missing: str, client: str) -> Ack | Rejected:
        """Answers the open prompt of ``step`` whose request field is ``field``, if it is the one open."""
        with self._lock:
            prompt = self._prompt
        if prompt is None or prompt.opened.step != step or getattr(prompt.request, "field", None) != field:
            return Rejected(f"Step {step!r} {missing}.")
        return self.answer(prompt.id, value, client=client)

    def abort(self, reason: str = "Aborted by user.") -> Ack:
        """Stops the run: cancels the open prompt or interrupts the busy step, then ends the run as aborted.

        A busy async step is cancelled; a busy sync step gets a ``KeyboardInterrupt`` (raised when it next runs
        Python code, so not during a blocking native call). An async step that blocks its event loop (e.g.
        ``time.sleep`` inside a coroutine) cannot be cancelled until it yields. Has no effect once the run is
        finishing, or when an abort is already under way (the first reason is kept).

        Args:
            reason: Why the run was aborted; recorded as the run's reason.

        Returns:
            `Ack`.
        """
        with self._lock:
            if not (self._finished.is_set() or self._finishing or self._aborting_locked()):
                self._pending = _Command("abort", reason=reason)
                self._interrupt_locked(allow_sync=True)  # once per run: a second abort is a no-op
        return Ack()

    def _aborting_locked(self) -> bool:
        """Whether an abort is waiting to be applied; the caller holds ``_lock``."""
        return self._pending is not None and self._pending.kind == "abort"

    def _stop(self, status: RunStatus, reason: str | None = None, error: BaseException | None = None) -> _Stop:
        """Marks the run as finishing (commands are refused from here on) and returns the `_Stop` to raise."""
        with self._lock:
            self._finishing = True
        return _Stop(status, reason, error)

    def _back_rejection(self, target: str) -> str | None:
        """Why going back to ``target`` is not possible now, or ``None``; the caller holds ``_lock``."""
        if target not in self._steps:
            return f"There is no step named {target!r}."
        title = self.workflow.get(target).title
        if self.workflow.index(target) <= self._barrier:
            return f"Cannot go back to {title}: a barrier has been passed."
        is_current = self._current is not None and self._current.name == target
        if self._steps[target] is StepStatus.PENDING and not is_current:
            return f"Cannot go back to {title}: it has not run yet."
        return None

    def _default_back_target(self, step: Step) -> str | None:
        """The nearest step before ``step`` that has run and is not locked by a barrier."""
        with self._lock:
            earlier = self.workflow.steps[self._barrier + 1 : self.workflow.index(step.name)]
            for candidate in reversed(earlier):
                if self._steps[candidate.name] is not StepStatus.PENDING:
                    return candidate.name
        return None

    def _go_back_from(self, step: Step, to: str | None) -> str | None:
        """Handles ``GoBack(to)`` raised by ``step``: rewinds and returns ``None``, or warns and returns why not."""
        target = to if to is not None else self._default_back_target(step)
        if target is None:
            rejection = f"Cannot go back from {step.title}: no earlier step can run again."
        else:
            with self._lock:
                rejection = self._back_rejection(target)
        if rejection is not None:
            self.notify(rejection, MessageLevel.WARNING)
            return rejection
        assert target is not None
        self._rewind(target)
        return None

    def _has_pending(self) -> bool:
        """Whether a command is waiting to be applied by the run thread."""
        with self._lock:
            return self._pending is not None

    def _apply_pending(self) -> None:
        """Applies the pending command, if any (run thread, between steps)."""
        with self._lock:
            command, self._pending = self._pending, None
            if command is not None and command.kind == "abort":
                raise self._stop(RunStatus.ABORTED, command.reason)  # under the lock: no window to accept a command
        if command is None:
            return
        assert command.target is not None
        if self.workflow.index(command.target) <= self._barrier:
            title = self.workflow.get(command.target).title
            self.notify(f"Cannot go back to {title}: a barrier has been passed.", MessageLevel.WARNING)
            return
        self._rewind(command.target)

    def _rewind(self, target: str) -> None:
        """Resets ``target`` and its dependents to pending, undoing completed undoable steps."""
        to_undo: list[tuple[Step, Any]] = []
        with self._lock:
            invalidated = []
            for name in [target, *self.workflow.dependents(target)]:
                if self._steps[name] is StepStatus.PENDING:
                    continue
                step = self.workflow.get(name)
                if self._steps[name] is StepStatus.DONE and step.undo is not None:
                    to_undo.append((step, self._outputs.get(name)))
                self._steps[name] = StepStatus.PENDING
                self._outputs.pop(name, None)
                self._summaries.pop(name, None)
                self._produced_by.pop(name, None)
                self._skip_reasons.pop(name, None)
                invalidated.append(name)
            if invalidated:
                self.events.append(StepsInvalidated(steps=invalidated))
        for step, output in reversed(to_undo):
            try:
                assert step.undo is not None
                step.undo(output)
            except Exception as exc:  # a failed undo is reported, not fatal
                logger.exception("Undo of step %s failed", step.name)
                self.notify(f"Could not undo {step.title}: {exc}", MessageLevel.WARNING)

    def _interrupt_locked(self, *, allow_sync: bool) -> None:
        """Interrupts whatever the run thread is doing so it can apply the pending command."""
        if self._prompt is not None:
            self._cancel_prompt_locked()
        elif self._task is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._task.cancel)
        elif allow_sync and self._in_sync_call and self._thread is not None and self._thread.ident is not None:
            _raise_in_thread(self._thread.ident, KeyboardInterrupt)

    # --- failures and optional steps ---------------------------------------
    def _set_status(self, step: Step, status: StepStatus) -> None:
        """Sets the status of ``step``."""
        with self._lock:
            self._steps[step.name] = status

    def _fail(self, step: Step, exc: BaseException) -> None:
        """Records a failure, then continues (warn) or asks Retry / Go back / Abort."""
        warn_only = step.on_error == "warn"
        logger.error("Step %s failed: %s", step.name, exc, exc_info=exc)
        with self._lock:
            self._steps[step.name] = StepStatus.FAILED
            if warn_only:
                self._outputs[step.name] = None
                self._summaries[step.name] = None
                self._produced_by[step.name] = self._attempts.get(step.name, 0)
            self.events.append(
                StepFailed(
                    step=step.name,
                    error=str(exc) or type(exc).__name__,
                    traceback="".join(traceback.format_exception(exc)),
                    warn_only=warn_only,
                )
            )
        if warn_only:
            self.notify(f"{step.title} failed: {exc}", MessageLevel.WARNING)
            return
        back = self._default_back_target(step)
        options = [Choice("retry", "Retry")]
        if back is not None:
            options.append(Choice("back", f"Go back to {self.workflow.get(back).title}"))
        options.append(Choice("abort", "Abort"))
        request = PickRequest(
            label=f"{step.title} failed: {exc}", options=options, allow_none=False, field=f"{step.name}.failed"
        )
        try:
            choice = self._ask(request, step)
        except PromptCancelledError:
            if self._has_pending():
                self._set_status(step, StepStatus.PENDING)  # the loop applies the command (go back / abort)
                return
            choice = "abort"
        if choice == "abort":
            raise self._stop(RunStatus.FAILED, f"{step.title} failed: {exc}", exc)
        self._set_status(step, StepStatus.PENDING)
        if choice == "back" and back is not None:
            self._rewind(back)

    def _confirm_optional(self, step: Step) -> bool:
        """Asks whether to run an optional step; records it as skipped on "no"."""
        request = ConfirmRequest(
            label=step.optional or step.title, default=step.optional_default, field=f"{step.name}.optional"
        )
        try:
            run_it = self._ask(request, step)
        except PromptCancelledError:
            if self._has_pending():
                return False  # stays pending; the loop applies the command
            run_it = False
        if run_it:
            return True
        self._skip(step, "declined")
        return False

    def _skip(self, step: Step, reason: SkipReason) -> None:
        """Records ``step`` as skipped (its output ``None``): declined, or its condition did not hold."""
        with self._lock:
            self._steps[step.name] = StepStatus.SKIPPED
            self._outputs[step.name] = None
            self._summaries[step.name] = None
            self._produced_by[step.name] = 0
            self._skip_reasons[step.name] = reason
            self.events.append(StepSkipped(step=step.name, reason=reason))

    # --- closing screen ---------------------------------------------------
    def keep_open(self) -> None:
        """Keeps the run's session open after it finishes, until `close` (or the launcher) ends it.

        Called before the run starts by whoever shows a closing screen (the launcher, when the run is served). Event
        streams of a run kept open end with ``RunClosed`` instead of ``RunFinished``.
        """
        with self._lock:
            self._kept_open = True

    @property
    def kept_open(self) -> bool:
        """Whether the session stays open after the run finishes (see `keep_open`)."""
        with self._lock:
            return self._kept_open

    def begin_closing(self, deadline: datetime.datetime | None = None) -> None:
        """Shows the closing screen: records ``RunClosing`` and exposes it in `state` (``closing``).

        Does nothing if the screen is already shown, or if a client closed the session first.

        Args:
            deadline: When the session closes on its own; ``None`` waits for a client.
        """
        with self._lock:
            if self._closing is not None:
                return
            self._closing = ClosingState(since=datetime.datetime.now(datetime.UTC), deadline=deadline)
            self.events.append(RunClosing(deadline=deadline))

    def close(self, *, client: str = "local") -> Ack | Rejected:
        """Closes the finished run's session (the closing screen's Close button). The first close wins.

        Accepted once the run has finished, if its session is kept open (see `keep_open`) or shows a closing screen.

        Args:
            client: Who closed; recorded in the ``RunClosed`` event (``"timeout"`` when the deadline passed).

        Returns:
            `Ack` once the session is closed by this call, otherwise `Rejected` (the run has not finished, its
            session is not kept open, or it is already closed).
        """
        with self._lock:
            if not self._finished.is_set():
                return Rejected("The run has not finished.")
            if not self._kept_open and self._closing is None:
                return Rejected("This run has no closing screen.")
            if self._closed.is_set():
                return Rejected("The session is already closed.")
            if self._closing is None:
                self._closing = ClosingState(since=datetime.datetime.now(datetime.UTC))
            self._closing.closed, self._closing.closed_by = True, client
            self.events.append(RunClosed(closed_by=client))
            self._closed.set()
        return Ack()

    def wait_closed(self, timeout: float | None = None) -> bool:
        """Blocks until the session is closed (see `close`); returns ``False`` if ``timeout`` elapsed first."""
        return self._closed.wait(timeout)

    @property
    def closed(self) -> bool:
        """Whether the finished run's session was closed (see `close`)."""
        return self._closed.is_set()

    # --- messages ---------------------------------------------------------
    def notify(self, message: str, level: MessageLevel = MessageLevel.INFO) -> None:
        """Records a message for the user.

        Args:
            message: The text to show.
            level: How prominently to show it.
        """
        with self._lock:
            self.events.append(Notify(message=message, level=level.value))

    def header(self, text: str) -> None:
        """Records a header for the user.

        Args:
            text: The header text.
        """
        with self._lock:
            self.events.append(Header(text=text))

    # --- observability ----------------------------------------------------
    @contextlib.contextmanager
    def activity(self, description: str, *, machine: str | None = None) -> Generator[str, None, None]:
        """Records live work (e.g. a running app) as start/end events around the block.

        The run observes the process-wide activity indicator while it executes, so it records activities from any
        thread: each is attributed to the step running when it starts (``step=None`` outside a step), and two
        concurrent runs in one process would record each other's activities (one run per process is supported).

        Args:
            description: What the work is, as shown to the user.
            machine: The machine the work runs on, when not this one.

        Yields:
            The activity's id, to record its output with `activity_output`.
        """
        activity_id = uuid.uuid4().hex[:12]
        with self._lock:  # the step read and the event are one atomic change: no step can start or end between
            step = self._current.name if self._current is not None else None
            self.events.append(
                ActivityStarted(activity_id=activity_id, description=description, step=step, machine=machine)
            )
        try:
            yield activity_id
        finally:
            # Not under _lock (no state is read): an abort's KeyboardInterrupt could land on acquiring it and the
            # activity would never end. The event log is thread-safe on its own.
            self.events.append(ActivityEnded(activity_id=activity_id))

    def activity_output(self, activity_id: str, lines: list[str]) -> None:
        """Records new output lines of a running activity."""
        if lines:
            self.events.append(ActivityOutput(activity_id=activity_id, lines=lines))

    def state(self) -> RunState:
        """A consistent snapshot of the run; clients then follow events after ``last_seq``.

        Returns:
            The run's status, its steps, the open prompt (if any) and the sequence number of the latest event the
            snapshot reflects.
        """
        with self._lock:
            steps = [
                StepState(
                    name=step.name,
                    title=step.title,
                    status=self._steps[step.name],
                    barrier=step.barrier,
                    optional=step.optional,
                    locked=index <= self._barrier,
                    summary=self._summaries.get(step.name),
                    conditional=step.when is not None,
                    skip_reason=self._skip_reasons.get(step.name),
                )
                for index, step in enumerate(self.workflow.steps)
            ]
            return RunState(
                run_id=self.id,
                workflow=self.workflow.name,
                status=self.status,
                current_step=self._current.name if self._current is not None else None,
                steps=steps,
                prompt=self._prompt.opened if self._prompt is not None else None,
                last_seq=self.events.last_seq,
                back_target=self.back_target(),
                closing=self._closing.model_copy() if self._closing is not None else None,
            )
