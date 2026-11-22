"""Run events: the append-only, sequenced record every client follows."""

import datetime
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

logger = logging.getLogger(__name__)


def _now() -> datetime.datetime:
    """Returns the current time in UTC."""
    return datetime.datetime.now(datetime.UTC)


class _Event(BaseModel):
    """Fields shared by every event; ``seq`` and ``run_id`` are stamped by :class:`EventLog`."""

    seq: int = 0
    ts: datetime.datetime = Field(default_factory=_now)
    run_id: str = ""


class RunStarted(_Event):
    """The run began executing."""

    type: Literal["run_started"] = "run_started"
    workflow: str
    steps: list[str]


class RunFinished(_Event):
    """The run ended."""

    type: Literal["run_finished"] = "run_finished"
    status: Literal["completed", "aborted", "failed"]
    reason: str | None = None


class StepStarted(_Event):
    """A step began running (``attempt`` counts runs of this step, from 1)."""

    type: Literal["step_started"] = "step_started"
    step: str
    attempt: int = 1


class StepFinished(_Event):
    """A step completed.

    ``summary`` is a truncated rendering of its output, ``output_sha256`` links to its line in ``outputs.jsonl``
    and ``inputs`` gives the attempt of each upstream step whose output it consumed (``0``: skipped). The attempt of
    a ``warn``-only failure (whose output is ``None``) has a ``StepFailed`` event and no line in ``outputs.jsonl``.
    ``output_sha256`` is ``None`` when the output was omitted or the run keeps no records.
    """

    type: Literal["step_finished"] = "step_finished"
    step: str
    summary: str | None = None
    attempt: int = 1
    inputs: dict[str, int] = Field(default_factory=dict)
    output_sha256: str | None = None


class ArtifactExported(_Event):
    """A step saved a file; ``path`` is relative to the run's ``artifacts/`` folder."""

    type: Literal["artifact_exported"] = "artifact_exported"
    step: str
    attempt: int
    path: str


class StepFailed(_Event):
    """A step raised. ``warn_only`` steps continue; others pause the run."""

    type: Literal["step_failed"] = "step_failed"
    step: str
    error: str
    traceback: str
    warn_only: bool = False


class StepSkipped(_Event):
    """An optional step was declined."""

    type: Literal["step_skipped"] = "step_skipped"
    step: str


class StepsInvalidated(_Event):
    """Completed steps were reset to pending by going back."""

    type: Literal["steps_invalidated"] = "steps_invalidated"
    steps: list[str]


class PromptOpened(_Event):
    """A step is waiting for an answer. ``answer_schema`` is the JSON Schema of a valid answer."""

    type: Literal["prompt_opened"] = "prompt_opened"
    prompt_id: str
    step: str | None
    kind: str
    spec: dict[str, Any]
    answer_schema: dict[str, Any]


class PromptClosed(_Event):
    """A prompt was answered (by ``answered_by``) or cancelled.

    ``answer`` is the JSON form of the accepted answer (secrets stay masked); ``None`` when cancelled.
    """

    type: Literal["prompt_closed"] = "prompt_closed"
    prompt_id: str
    outcome: Literal["answered", "cancelled"]
    answered_by: str | None = None
    answer: Any = None


class Notify(_Event):
    """A message for the user. ``level`` is a :class:`~clabe.ui.MessageLevel` value."""

    type: Literal["notify"] = "notify"
    message: str
    level: str


class Header(_Event):
    """A prominent header for the user."""

    type: Literal["header"] = "header"
    text: str


class ActivityStarted(_Event):
    """Long-running work began (e.g. an app is running)."""

    type: Literal["activity_started"] = "activity_started"
    activity_id: str
    description: str
    step: str | None = None
    machine: str | None = None
    """The machine it runs on, when not this one (e.g. a job on a leased worker)."""


class ActivityEnded(_Event):
    """Long-running work ended."""

    type: Literal["activity_ended"] = "activity_ended"
    activity_id: str


class ActivityOutput(_Event):
    """New output lines of a running activity (e.g. a remote job's log), at most about one event per second."""

    type: Literal["activity_output"] = "activity_output"
    activity_id: str
    lines: list[str]


class CommandFinished(_Event):
    """A command an app ran (Bonsai, Open Ephys, a script) ended: what exactly ran, for provenance.

    ``files`` maps each existing file the command named (its arguments, its inputs, ``Bonsai.config`` next to the
    program) to its SHA-256 (``None`` when too large to hash). Files of a remote command are hashed here, where the
    run is (the remote checkout is the same commit when ``RemoteRepository.verify()`` passed).
    """

    type: Literal["command_finished"] = "command_finished"
    step: str
    attempt: int = 1
    command: list[str]
    cwd: str
    machine: str | None = None
    executable: str
    executable_sha256: str | None = None
    files: dict[str, str | None] = Field(default_factory=dict)
    exit_code: int | None = None
    duration_s: float
    error: str | None = None


class RecordResolved(_Event):
    """A step took a record (a rig, a trainer state, ...) from a store: which one, from where, and its content hash.

    The record itself is saved as an artifact of the step (``stores/<kind>.json``), so the session keeps exactly what
    it used even if the store changes later.
    """

    type: Literal["record_resolved"] = "record_resolved"
    step: str
    attempt: int = 1
    kind: str
    store: str
    label: str
    scope: dict[str, str] = Field(default_factory=dict)
    sha256: str
    revision: str | None = None
    artifact: str | None = None


class BranchStarted(_Event):
    """A branch of a step started (see :func:`~clabe.workflow.concurrently`); ``attempt`` counts its retries."""

    type: Literal["branch_started"] = "branch_started"
    step: str
    branch: str
    attempt: int = 1


class BranchEnded(_Event):
    """A branch of a step ended: done, failed (``error``), cancelled (stopped with the others), or skipped (the user
    continued without it after it failed)."""

    type: Literal["branch_ended"] = "branch_ended"
    step: str
    branch: str
    attempt: int = 1
    outcome: Literal["done", "failed", "cancelled", "skipped"]
    error: str | None = None


Event = Annotated[
    RunStarted
    | RunFinished
    | StepStarted
    | StepFinished
    | StepFailed
    | StepSkipped
    | StepsInvalidated
    | ArtifactExported
    | PromptOpened
    | PromptClosed
    | Notify
    | Header
    | ActivityStarted
    | ActivityEnded
    | ActivityOutput
    | CommandFinished
    | RecordResolved
    | BranchStarted
    | BranchEnded,
    Field(discriminator="type"),
]
_EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


class EventLog:
    """Append-only, sequenced event log, optionally mirrored to a JSON-lines file.

    Subscribers are called synchronously, in order, on the appending thread while the log's lock is held, and
    possibly re-entrantly: an event appended from inside a subscriber reaches the later subscribers before the
    event being published does. A subscriber must return quickly, must never block on another thread that appends
    events, and must not call :class:`~clabe.workflow.Run` methods that take the run's lock (``answer``,
    ``state``, ``abort``, ``go_back``, ...): hand such work to a queue or another thread.

    Writing the file never fails a run: if a write raises, it is logged once and the file stops being written for
    the rest of the run, while events are still recorded in memory and published.

    Args:
        run_id: Stamped on every event.
        path: When given, each event is appended to this file as one JSON line.
    """

    def __init__(self, run_id: str, path: Path | None = None) -> None:
        self.run_id = run_id
        self.path = path
        self._lock = threading.RLock()
        self._events: list[Event] = []
        self._subscribers: list[Callable[[Event], None]] = []
        self._write_failed = False
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def last_seq(self) -> int:
        """Sequence number of the latest event (``0`` when empty)."""
        with self._lock:
            return len(self._events)

    def append(self, event: Event) -> Event:
        """Stamps ``event`` with the next sequence number and the run id, records and publishes it.

        A failure to write the file is logged (once) and stops the mirroring; it is never raised.
        """
        with self._lock:
            event = event.model_copy(update={"seq": len(self._events) + 1, "run_id": self.run_id})
            self._events.append(event)
            if self.path is not None and not self._write_failed:
                try:
                    with self.path.open("a", encoding="utf-8") as stream:
                        stream.write(event.model_dump_json() + "\n")
                except Exception:  # a record that cannot be written must not stop the run
                    self._write_failed = True
                    logger.exception(
                        "Could not write event %s to %s; no longer writing events there", event.seq, self.path
                    )
            for subscriber in list(self._subscribers):
                try:
                    subscriber(event)
                except Exception:  # one broken client must not break the run
                    logger.exception("Event subscriber %r failed", subscriber)
            return event

    def since(self, seq: int = 0) -> list[Event]:
        """Returns the events after sequence number ``seq``."""
        with self._lock:
            return self._events[seq:]

    def subscribe(self, subscriber: Callable[[Event], None]) -> Callable[[], None]:
        """Calls ``subscriber`` for every future event; returns a function that unsubscribes.

        ``subscriber`` is called synchronously on the thread that appends the event, with the log's lock held, and
        possibly re-entrantly (an event appended from inside a subscriber reaches the later subscribers before the
        event being published does). It must return quickly, must never block on another thread that appends
        events, and must not call :class:`~clabe.workflow.Run` methods that take the run's lock (``answer``,
        ``state``, ``abort``, ``go_back``, ...): hand such work to a queue or another thread. An exception it
        raises is logged and otherwise ignored.
        """
        with self._lock:
            self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe

    @staticmethod
    def read(path: Path) -> list[Event]:
        """Reads the events persisted at ``path``."""
        lines = path.read_text(encoding="utf-8").splitlines()
        return [_EVENT_ADAPTER.validate_json(line) for line in lines if line]
