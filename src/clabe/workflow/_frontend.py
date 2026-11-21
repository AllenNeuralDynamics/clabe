"""The legacy ``Frontend`` protocol implemented on top of a :class:`Run`."""

import contextlib
from pathlib import Path
from typing import Any

from ..runnable import get_activity_indicator
from ..ui import (
    AcknowledgeRequest,
    AutoCompleteRequest,
    ConfirmRequest,
    FieldRequest,
    FormRequest,
    MessageLevel,
    NumberRequest,
    PathRequest,
    PickRequest,
    ReadOnlyTable,
    TextRequest,
)
from ._run import Run


class RunFrontend:
    """A :class:`~clabe.ui.Frontend` whose messages and prompts become run events.

    Installed as ``launcher.frontend`` (and as the current frontend) while a run executes, so step code and
    library code written against ``Frontend`` keep working unchanged.

    Args:
        run: The run that records the messages and asks the prompts.
    """

    def __init__(self, run: Run) -> None:
        self.run = run

    def notify(self, message: str, level: MessageLevel = MessageLevel.INFO) -> None:
        """Records a message for the user."""
        self.run.notify(message, level)

    def header(self, text: str) -> None:
        """Records a header for the user."""
        self.run.header(text)

    def activity(self, description: str) -> contextlib.AbstractContextManager[None]:
        """Shows live activity; the run observes the shared indicator and records it."""
        return get_activity_indicator().activity(description)

    def cancel_prompt(self) -> None:
        """Cancels the open prompt, if any."""
        self.run.cancel_prompt()

    def prompt_pick(self, request: PickRequest) -> str | None:
        """Asks the user to pick an option."""
        return self.run.ask(request)

    def prompt_path(self, request: PathRequest) -> Path | None:
        """Asks the user for a filesystem path."""
        return self.run.ask(request)

    def prompt_confirm(self, request: ConfirmRequest) -> bool:
        """Asks a yes/no question."""
        return self.run.ask(request)

    def prompt_text(self, request: TextRequest) -> str:
        """Asks for free-form text."""
        return self.run.ask(request)

    def prompt_autocomplete(self, request: AutoCompleteRequest) -> str:
        """Asks for text with autocompletion."""
        return self.run.ask(request)

    def prompt_number(self, request: NumberRequest) -> float:
        """Asks for a number."""
        return self.run.ask(request)

    def prompt_form(self, request: FormRequest) -> Any | None:
        """Asks the user to fill in a Pydantic model."""
        return self.run.ask(request)

    def prompt_read_only_table(self, request: ReadOnlyTable) -> bool:
        """Shows a read-only table and asks for a yes/no."""
        return self.run.ask(request)

    def prompt_field(self, request: FieldRequest) -> Any:
        """Asks for a single model field."""
        return self.run.ask(request)

    def prompt_acknowledge(self, request: AcknowledgeRequest) -> None:
        """Shows a message and waits until it is dismissed."""
        self.run.ask(request)
