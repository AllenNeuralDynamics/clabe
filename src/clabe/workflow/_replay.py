"""Replaying a session: a client that answers a run's prompts with the answers recorded in an ``events.jsonl``."""

import logging
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..ui import MessageLevel, Request
from ._events import Event, EventLog, PromptClosed, PromptOpened

logger = logging.getLogger(__name__)

#: How a secret answer is recorded (it cannot be replayed).
_MASK = "**********"


class ReplayMismatch(RuntimeError):
    """The run asked something the recorded session did not answer at that point."""


class ReplayClient:
    """Answers each prompt with the next answer of a recorded session, checking it is the same question.

    Prompts are matched in order: the n-th prompt the run asks gets the n-th recorded answer, provided its kind and
    label are the recorded ones. Answers are validated by the run as any client's are. Commands given outside prompts
    (clicking a step to go back, aborting) are not replayed; answers given through prompts (a review's "Go back", a
    failed step's "Retry") are. Secret answers are recorded masked and cannot be replayed.

    Use it to reproduce a session's decisions, or as a test of a workflow: record a session once, then replay it
    after changing the workflow (``clabe replay events.jsonl experiment.py``).

    Args:
        events: The recorded session's events, or the path of its ``events.jsonl``.
    """

    #: Nobody is at this client: the launcher skips its exit prompt.
    interactive = False

    def __init__(self, events: Iterable[Event] | str | Path) -> None:
        if isinstance(events, str | Path):
            events = EventLog.read(Path(events))
        opened: dict[str, PromptOpened] = {}
        self._answers: deque[tuple[PromptOpened, Any]] = deque()
        for event in events:
            if isinstance(event, PromptOpened):
                opened[event.prompt_id] = event
            elif isinstance(event, PromptClosed) and event.outcome == "answered" and event.prompt_id in opened:
                self._answers.append((opened[event.prompt_id], event.answer))
        self.replayed = 0

    @property
    def remaining(self) -> int:
        """Recorded answers not used yet."""
        return len(self._answers)

    def ask(self, request: Request) -> Any:
        """The next recorded answer, after checking it answered the same question."""
        asked = _describe(request.prompt_kind, request.spec())
        if not self._answers:
            raise ReplayMismatch(f"The recorded session has no answer left for {asked}: the run asks more than it did.")
        recorded, answer = self._answers.popleft()
        expected = _describe(recorded.kind, recorded.spec)
        if expected != asked:
            raise ReplayMismatch(f"The run asks {asked}, but the recorded session answered {expected} at this point.")
        if answer == _MASK:
            raise ReplayMismatch(
                f"The recorded answer to {asked} is a secret, which is not recorded: it cannot be replayed."
            )
        self.replayed += 1
        logger.info("Replaying %s: %r", asked, answer)
        return answer

    def cancel(self) -> None:
        """Nothing to cancel: answers are immediate."""

    def notify(self, message: str, level: MessageLevel = MessageLevel.INFO) -> None:
        """Logs the message."""
        logger.log(MessageLevel(level).logging_level, "%s", message)

    def header(self, text: str) -> None:
        """Logs the header."""
        logger.info("%s", text)

    def close(self) -> None:
        """Nothing to tear down."""


def _describe(kind: str, spec: dict[str, Any]) -> str:
    """A prompt, for matching and messages: its kind and what it says."""
    label = spec.get("label") or spec.get("title") or spec.get("message") or ""
    return f"{kind} {label!r}" if label else kind
