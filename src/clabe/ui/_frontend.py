import abc
import contextlib
import dataclasses
import logging
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Protocol, get_args, get_origin, runtime_checkable

from ..logging import _TRANSCRIPT_LOGGER_NAME
from ._messages import MessageLevel
from ._requests import (
    AcknowledgeRequest,
    AutoCompleteRequest,
    ConfirmRequest,
    FieldRequest,
    FormRequest,
    NumberRequest,
    PathRequest,
    PickRequest,
    ReadOnlyTable,
    TextRequest,
)
from ._validation import first_error, resolve_field_default, resolve_field_type, validate_path


class PromptCancelledError(Exception):
    """Raised by a prompt that was aborted through :meth:`Frontend.cancel_prompt`."""


def _humanize_field(name: str) -> str:
    """Converts snake_case / kebab-case field names to Title Case."""
    return name.replace("_", " ").replace("-", " ").title()


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

    def activity(self, description: str) -> contextlib.AbstractContextManager[None]:
        """Display live activity (e.g. a spinner) for the duration of a block."""
        ...

    def prompt_pick(self, request: PickRequest) -> str | None:
        """Prompt the user to pick one option; returns the value or ``None``."""
        ...

    def prompt_path(self, request: PathRequest) -> Path | None:
        """Prompt the user to browse for a filesystem path; returns the path or ``None``."""
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

    def prompt_form(self, request: FormRequest) -> Any | None:
        """Prompt the user to fill in a Pydantic model form; returns the validated instance or None."""
        ...

    def prompt_read_only_table(self, request: ReadOnlyTable) -> bool:
        """Display read-only tabular data and collect a yes/no answer; returns the answer."""
        ...

    def prompt_field(self, request: FieldRequest) -> Any:
        """Prompt the user for a single Pydantic model field value; returns the validated Python value."""
        ...

    def prompt_acknowledge(self, request: AcknowledgeRequest) -> None:
        """Display a message in a modal and block until the user dismisses it."""
        ...

    def cancel_prompt(self) -> None:
        """Abort the prompt that is currently waiting for the user, if any.

        Safe to call from any thread. The waiting ``prompt_*`` call raises
        :class:`PromptCancelledError`. Has no effect when no prompt is waiting.
        """
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

    # --- output -----------------------------------------------------------
    def notify(self, message: str, level: MessageLevel = MessageLevel.INFO) -> None:
        """
        Surfaces a message to the user and records it to the transcript.

        Args:
            message: The message to surface.
            level: The presentation level/intent of the message.
        """
        self._transcript.log(level.logging_level, "UI» %s", message)
        self._render(message, level)

    def header(self, text: str) -> None:
        """
        Surfaces a prominent header to the user and records it.

        Args:
            text: The header text.
        """
        self._transcript.info("UI» %s", text)
        self._render_header(text)

    def activity(self, description: str) -> contextlib.AbstractContextManager[None]:
        """
        Returns a context manager that displays live activity while active.

        Args:
            description: Text shown next to the activity indicator.
        """
        from ..runnable import get_activity_indicator

        return get_activity_indicator().activity(description)

    # --- prompts ----------------------------------------------------------
    def cancel_prompt(self) -> None:
        """
        Aborts the prompt that is currently waiting for the user, if any.

        Safe to call from any thread; the waiting ``prompt_*`` call raises
        :class:`PromptCancelledError`. Frontends that cannot interrupt their
        prompts leave this as a no-op.
        """

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

    def prompt_pick(self, request: PickRequest) -> str | None:
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

    def prompt_path(self, request: PathRequest) -> Path | None:
        """
        Prompts the user to browse for a filesystem path, validating and recording it.

        The frontend's ``_ask_path`` only collects a *candidate* path (no
        constraint checking); this method enforces ``must_exist``/``kind``/
        ``extensions`` and re-prompts (preserving where the user left off) until
        the candidate satisfies them or the user cancels.

        Args:
            request: The declarative path request.

        Returns:
            Optional[Path]: The validated path, or ``None`` if cancelled.
        """
        current = request
        while True:
            candidate = self._ask_path(current)
            if candidate is None:
                self._record(request.field or request.label, None)
                return None
            error = self._validate_path(current, candidate)
            if error is None:
                self._record(request.field or request.label, str(candidate))
                return candidate
            self.notify(error, MessageLevel.ERROR)
            current = dataclasses.replace(current, start=str(candidate))

    _validate_path = staticmethod(validate_path)

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

    def prompt_form(self, request: FormRequest) -> Any | None:
        """
        Presents a Pydantic model form for the user to fill in.

        Args:
            request: The declarative form request.

        Returns:
            Optional[Any]: The validated model instance, or ``None`` if cancelled.

        Raises:
            NotImplementedError: This frontend does not support form prompts.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support form prompts.")

    def prompt_read_only_table(self, request: ReadOnlyTable) -> bool:
        """
        Displays read-only tabular data and collects a yes/no answer.

        Args:
            request: The declarative read-only-table request.

        Returns:
            bool: ``True`` if the user confirmed, ``False`` otherwise.

        Raises:
            NotImplementedError: This frontend does not support table prompts.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support read-only-table prompts.")

    def prompt_acknowledge(self, request: AcknowledgeRequest) -> None:
        """
        Displays a message in a modal overlay and blocks until the user dismisses it.

        Args:
            request: The declarative acknowledge request.
        """
        self._ask_acknowledge(request)
        self._record(request.field or request.title, "acknowledged")

    def prompt_field(self, request: FieldRequest) -> Any:
        """
        Prompts for a single Pydantic model field value and returns the validated Python object.

        Routing rules:

        * ``Literal[...]`` / ``Enum`` → autocomplete with strict matching against the
          allowed options; returns the original typed value (not a string).
        * ``bool`` → yes/no confirm; returns ``bool``.
        * All other types → text prompt with a Pydantic-backed validator that
          re-prompts until the value is accepted; returns the coerced Python type.

        Args:
            request: The declarative field request.

        Returns:
            Any: The validated, Python-typed field value.
        """
        from pydantic import TypeAdapter
        from pydantic import ValidationError as _PydanticError

        field_info = request.model.model_fields[request.field_name]
        annotation = field_info.annotation
        inner, is_optional = resolve_field_type(annotation)
        label = getattr(field_info, "title", None) or _humanize_field(request.field_name)
        default = resolve_field_default(field_info, request.initial)

        # bool → confirm
        if inner is bool:
            return self.prompt_confirm(
                ConfirmRequest(
                    label=label,
                    default=bool(default) if default is not None else False,
                    field=request.field_name,
                )
            )

        # Literal[...] → strict autocomplete on the literal values (preserves original type)
        if get_origin(inner) is Literal:
            literal_vals = get_args(inner)
            options = [str(v) for v in literal_vals]
            default_str = str(default) if default is not None else None
            answer = self.prompt_autocomplete(
                AutoCompleteRequest(
                    label=label,
                    options=options,
                    default=default_str,
                    strict=True,
                    field=request.field_name,
                )
            )
            for v in literal_vals:
                if str(v) == answer:
                    return v
            return answer

        # Enum → strict autocomplete on member names; returns the Enum member
        if isinstance(inner, type) and issubclass(inner, Enum):
            options = [m.name for m in inner]
            if isinstance(default, Enum):
                default_str: str | None = default.name
            else:
                default_str = str(default) if default is not None else None
            answer = self.prompt_autocomplete(
                AutoCompleteRequest(
                    label=label,
                    options=options,
                    default=default_str,
                    strict=True,
                    field=request.field_name,
                )
            )
            return inner[answer]

        # Path → browse for a filesystem path (existence is not required: a plain
        # ``Path`` field, unlike pydantic's ``FilePath``/``DirectoryPath``, doesn't
        # imply the target exists yet).
        if isinstance(inner, type) and issubclass(inner, Path):
            default_str = str(default) if default is not None else None
            return self.prompt_path(
                PathRequest(
                    label=label,
                    start=default_str,
                    default=default_str,
                    must_exist=False,
                    kind="any",
                    field=request.field_name,
                )
            )

        # Everything else → text prompt, re-prompting via pydantic validation
        adapter = TypeAdapter(annotation)

        def _pydantic_validator(raw: str) -> str | None:
            try:
                val: Any = None if (is_optional and raw == "") else raw
                adapter.validate_python(val)
                return None
            except _PydanticError as exc:
                errs = exc.errors()
                return errs[0]["msg"] if errs else "Invalid value."
            except Exception as exc:  # noqa: BLE001 -- any validator failure must surface as a re-promptable message, not crash
                return str(exc)

        default_str = str(default) if default is not None else None
        raw = self.prompt_text(
            TextRequest(
                label=label,
                default=default_str,
                validators=[_pydantic_validator],
                field=request.field_name,
            )
        )
        val = None if (is_optional and raw == "") else raw
        return adapter.validate_python(val)

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

    _first_error = staticmethod(first_error)

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
    def _ask_pick(self, request: PickRequest) -> str | None:
        """Collects a single choice from the user (no transcript)."""

    @abc.abstractmethod
    def _ask_path(self, request: PathRequest) -> Path | None:
        """Collects a candidate filesystem path from the user (no validation/transcript)."""

    @abc.abstractmethod
    def _ask_confirm(self, request: ConfirmRequest) -> bool:
        """Collects a yes/no answer from the user (no transcript)."""

    @abc.abstractmethod
    def _ask_autocomplete(self, request: AutoCompleteRequest) -> str:
        """Collects autocompleted text from the user (no validation/transcript)."""

    @abc.abstractmethod
    def _ask_acknowledge(self, request: AcknowledgeRequest) -> None:
        """Displays the acknowledge message and blocks until the user dismisses it."""
