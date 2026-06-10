import abc
import logging
import typing
from enum import Enum
from typing import Any, ContextManager, List, Literal, Optional, Protocol, get_args, get_origin, runtime_checkable

from ..logging_helper import _TRANSCRIPT_LOGGER_NAME
from ._messages import MessageLevel
from ._requests import (
    AcknowledgeRequest,
    AutoCompleteRequest,
    ConfirmRequest,
    FieldRequest,
    FormRequest,
    NumberRequest,
    PickRequest,
    TextRequest,
    Validator,
)


def _humanize_field(name: str) -> str:
    """Converts snake_case / kebab-case field names to Title Case."""
    return name.replace("_", " ").replace("-", " ").title()


def _resolve_field_type(annotation: Any) -> tuple[Any, bool]:
    """Strip Annotated and Optional wrappers; return (inner_type, is_optional)."""
    if get_origin(annotation) is typing.Annotated:
        annotation = get_args(annotation)[0]
    if get_origin(annotation) is typing.Union:
        args = get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0], True
    return annotation, False


def _resolve_field_default(field_info: Any, initial: Any) -> Any:
    """Return initial if provided, else the field's declared default or None."""
    from pydantic_core import PydanticUndefined

    if initial is not None:
        return initial
    raw = getattr(field_info, "default", PydanticUndefined)
    if raw is not PydanticUndefined:
        return raw
    factory = getattr(field_info, "default_factory", None)
    return factory() if factory is not None else None


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

    def prompt_form(self, request: FormRequest) -> Optional[object]:
        """Prompt the user to fill in a Pydantic model form; returns the validated instance or None."""
        ...

    def prompt_field(self, request: FieldRequest) -> Any:
        """Prompt the user for a single Pydantic model field value; returns the validated Python value."""
        ...

    def prompt_acknowledge(self, request: AcknowledgeRequest) -> None:
        """Display a message in a modal and block until the user dismisses it."""
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

    def prompt_form(self, request: FormRequest) -> Optional[object]:
        """
        Presents a Pydantic model form for the user to fill in.

        Args:
            request: The declarative form request.

        Returns:
            Optional[object]: The validated model instance, or ``None`` if cancelled.

        Raises:
            NotImplementedError: This frontend does not support form prompts.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support form prompts.")

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
        inner, is_optional = _resolve_field_type(annotation)
        label = getattr(field_info, "title", None) or _humanize_field(request.field_name)
        default = _resolve_field_default(field_info, request.initial)

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
                default_str: Optional[str] = default.name
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

        # Everything else → text prompt, re-prompting via pydantic validation
        adapter = TypeAdapter(annotation)

        def _pydantic_validator(raw: str) -> Optional[str]:
            try:
                val: Any = None if (is_optional and raw == "") else raw
                adapter.validate_python(val)
                return None
            except _PydanticError as exc:
                errs = exc.errors()
                return errs[0]["msg"] if errs else "Invalid value."
            except Exception as exc:
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

    @abc.abstractmethod
    def _ask_acknowledge(self, request: AcknowledgeRequest) -> None:
        """Displays the acknowledge message and blocks until the user dismisses it."""
