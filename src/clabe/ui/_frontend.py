import abc
import logging
from typing import ContextManager, List, Optional, Protocol, runtime_checkable

from ..logging_helper import _TRANSCRIPT_LOGGER_NAME
from ._messages import MessageLevel
from ._requests import (
    AutoCompleteRequest,
    ConfirmRequest,
    NumberRequest,
    PickRequest,
    TextRequest,
    Validator,
)


@runtime_checkable
class Frontend(Protocol):
    """
    The single boundary between the application and the user.

    A ``Frontend`` is responsible for *all* user-facing presentation (status
    messages, headers, live activity) and *all* interactive input (picking from
    a list, yes/no questions, free text, numbers). It is deliberately decoupled
    from logging: logging is a persistent record/diagnostic sink, while the
    frontend is how a human is informed and prompted.

    Concrete implementations exist for a rich-styled console and a Textual TUI;
    future implementations (e.g. a web app) only need to fulfil this protocol
    for the rest of the codebase to work unchanged.
    """

    def notify(self, message: str, level: MessageLevel = MessageLevel.INFO) -> None:
        """Surface a message to the user without expecting a response."""
        ...

    def header(self, text: str) -> None:
        """Surface a prominent header/banner to the user."""
        ...

    def activity(self, description: str) -> ContextManager[None]:
        """Display live activity (e.g. a spinner) for the duration of a block."""
        ...

    def prompt_pick(self, request: PickRequest) -> Optional[str]:
        """Prompt the user to pick one option; returns the value or ``None``."""
        ...

    def prompt_confirm(self, request: ConfirmRequest) -> bool:
        """Ask the user a yes/no question."""
        ...

    def prompt_text(self, request: TextRequest) -> str:
        """Prompt the user for free-form text."""
        ...

    def prompt_autocomplete(self, request: AutoCompleteRequest) -> str:
        """Prompt for text with type-to-filter autocompletion against a list."""
        ...

    def prompt_number(self, request: NumberRequest) -> float:
        """Prompt the user for a floating-point number."""
        ...


class FrontendBase(abc.ABC):
    """
    Base class implementing the transcript bridge and prompt orchestration.

    Subclasses implement only the rendering/asking primitives (``_render``,
    ``_ask_text``, ``_ask_pick``, ``_ask_confirm``). This base class:

    * records every surfaced message and every collected answer onto the
      ``clabe.transcript`` logger, so the persistent log file contains a full
      transcript of what the user saw and entered — without coupling any call
      site to logging; and
    * owns the validation/retry loop, so individual frontends do not each
      reimplement "ask, validate, re-ask on failure".
    """

    def __init__(self) -> None:
        """Initializes the frontend and its transcript logger."""
        self._transcript = logging.getLogger(_TRANSCRIPT_LOGGER_NAME)
        #: Minimum level a message must reach to be *rendered* to the user.
        #: Messages below this are still recorded to the transcript/log file.
        self._min_render_level = logging.WARNING

    def set_min_level(self, level: int) -> None:
        """
        Sets the minimum level a notification must reach to be shown.

        This only affects what is rendered to the user; every message is still
        recorded to the persistent transcript regardless of this threshold.

        Args:
            level: A standard ``logging`` level (e.g. ``logging.INFO``).
        """
        self._min_render_level = level

    # --- output -----------------------------------------------------------
    def notify(self, message: str, level: MessageLevel = MessageLevel.INFO) -> None:
        """
        Surfaces a message to the user and records it to the transcript.

        The message is always recorded to the transcript (and thus the log
        file); it is only rendered to the user when its level is at or above the
        configured threshold (see :meth:`set_min_level`).

        Args:
            message: The message to surface.
            level: The presentation level/intent of the message.
        """
        self._transcript.log(level.logging_level, "UI» %s", message)
        if level.logging_level >= self._min_render_level:
            self._render(message, level)

    def header(self, text: str) -> None:
        """
        Surfaces a prominent header to the user and records it.

        Args:
            text: The header text.
        """
        self._transcript.info("UI» %s", text)
        self._render_header(text)

    def activity(self, description: str) -> ContextManager[None]:
        """
        Returns a context manager that displays live activity while active.

        Args:
            description: Text shown next to the activity indicator.
        """
        from ..runnable import get_activity_indicator

        return get_activity_indicator().activity(description)

    # --- prompts ----------------------------------------------------------
    def prompt_text(self, request: TextRequest) -> str:
        """
        Prompts for text, applying validators and recording the answer.

        Args:
            request: The declarative text request.

        Returns:
            str: The validated answer.
        """
        while True:
            answer = self._ask_text(request)
            if answer == "" and request.default is not None:
                answer = request.default
            error = self._first_error(request.validators, answer)
            if error is None:
                self._record(request.field or request.label, answer)
                return answer
            self.notify(error, MessageLevel.ERROR)

    def prompt_autocomplete(self, request: AutoCompleteRequest) -> str:
        """
        Prompts for text with autocompletion, validating and recording it.

        Args:
            request: The declarative autocomplete request.

        Returns:
            str: The validated answer.
        """
        suggestions = request.suggestions()
        while True:
            answer = self._ask_autocomplete(request)
            if answer == "" and request.default is not None:
                answer = request.default
            if request.strict and answer not in suggestions:
                self.notify("Please choose one of the offered options.", MessageLevel.ERROR)
                continue
            error = self._first_error(request.validators, answer)
            if error is None:
                self._record(request.field or request.label, answer)
                return answer
            self.notify(error, MessageLevel.ERROR)

    def prompt_pick(self, request: PickRequest) -> Optional[str]:
        """
        Prompts the user to pick an option and records the answer.

        Args:
            request: The declarative pick request.

        Returns:
            Optional[str]: The chosen value, or ``None``.
        """
        answer = self._ask_pick(request)
        self._record(request.field or request.label, answer)
        return answer

    def prompt_confirm(self, request: ConfirmRequest) -> bool:
        """
        Asks a yes/no question and records the answer.

        Args:
            request: The declarative confirm request.

        Returns:
            bool: The user's choice.
        """
        answer = self._ask_confirm(request)
        self._record(request.field or request.label, answer)
        return answer

    def prompt_number(self, request: NumberRequest) -> float:
        """
        Prompts the user for a number, re-prompting until one is valid.

        Args:
            request: The declarative number request.

        Returns:
            float: The parsed number.
        """
        text_request = TextRequest(label=request.label, field=request.field)
        while True:
            raw = self._ask_text(text_request)
            if raw == "" and request.default is not None:
                self._record(request.field or request.label, request.default)
                return request.default
            try:
                value = float(raw)
            except ValueError:
                self.notify("Please enter a valid number.", MessageLevel.ERROR)
                continue
            self._record(request.field or request.label, value)
            return value

    # --- helpers ----------------------------------------------------------
    def _record(self, key: str, value: object) -> None:
        """Records a collected answer onto the transcript logger."""
        self._transcript.info("UI« %s = %r", key, value)
        self._on_answer(key, value)

    def _on_answer(self, key: str, value: object) -> None:
        """Hook called once a prompt is answered. Defaults to doing nothing.

        Frontends may override this to leave a concise record of the answer in
        the visible flow (e.g. a one-line summary in a scrolling transcript).
        """

    @staticmethod
    def _first_error(validators: List[Validator], value: str) -> Optional[str]:
        """Returns the first validation error for ``value``, or ``None``."""
        for validator in validators:
            error = validator(value)
            if error is not None:
                return error
        return None

    def _render_header(self, text: str) -> None:
        """Renders a header. Defaults to rendering it as an info message."""
        self._render(text, MessageLevel.INFO)

    # --- primitives to implement -----------------------------------------
    @abc.abstractmethod
    def _render(self, message: str, level: MessageLevel) -> None:
        """Renders a message to the user (no transcript side effects)."""

    @abc.abstractmethod
    def _ask_text(self, request: TextRequest) -> str:
        """Collects raw text from the user (no validation/transcript)."""

    @abc.abstractmethod
    def _ask_pick(self, request: PickRequest) -> Optional[str]:
        """Collects a single choice from the user (no transcript)."""

    @abc.abstractmethod
    def _ask_confirm(self, request: ConfirmRequest) -> bool:
        """Collects a yes/no answer from the user (no transcript)."""

    @abc.abstractmethod
    def _ask_autocomplete(self, request: AutoCompleteRequest) -> str:
        """Collects autocompleted text from the user (no validation/transcript)."""
