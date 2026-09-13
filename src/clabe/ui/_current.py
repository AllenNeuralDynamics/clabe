import contextlib
import logging
from collections.abc import Generator
from pathlib import Path
from typing import Any

from ._frontend import Frontend
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

logger = logging.getLogger(__name__)

_current_frontend: Frontend | None = None


class NoFrontendError(RuntimeError):
    """Raised when a prompt is requested but no frontend is registered."""


def set_current_frontend(frontend: Frontend | None) -> None:
    """
    Registers the process-wide frontend used by this module's passthroughs.

    The launcher sets this so that library modules (which are otherwise
    UI-agnostic) can inform and prompt the user without taking a frontend
    dependency. Set to ``None`` to unregister.

    Args:
        frontend: The frontend to register, or ``None`` to clear it.
    """
    global _current_frontend
    _current_frontend = frontend


def current_frontend() -> Frontend | None:
    """Returns the currently registered frontend, or ``None`` if unset."""
    return _current_frontend


@contextlib.contextmanager
def use_frontend(frontend: Frontend | None) -> Generator[Frontend | None]:
    """
    Registers a frontend for the duration of a block, restoring the previous one on exit.

    Args:
        frontend: The frontend to register, or ``None`` to run without one.

    Yields:
        The frontend that was registered.
    """
    previous = _current_frontend
    set_current_frontend(frontend)
    try:
        yield frontend
    finally:
        set_current_frontend(previous)


def require_frontend() -> Frontend:
    """
    Returns the registered frontend.

    Raises:
        NoFrontendError: If no frontend is registered.
    """
    if _current_frontend is None:
        raise NoFrontendError("No frontend is registered. User interaction is not possible.")
    return _current_frontend


# Output-only passthroughs. These degrade to no-ops without a frontend so that
# library code stays safe to call headless.


def notify(message: str, level: MessageLevel = MessageLevel.INFO) -> None:
    """Surfaces a message to the user, or does nothing if no frontend is registered."""
    if _current_frontend is not None:
        _current_frontend.notify(message, level)


def header(text: str) -> None:
    """Surfaces a header to the user, or does nothing if no frontend is registered."""
    if _current_frontend is not None:
        _current_frontend.header(text)


def activity(description: str) -> contextlib.AbstractContextManager[None]:
    """Displays live activity for the duration of a block, or nothing if no frontend is registered."""
    if _current_frontend is None:
        return contextlib.nullcontext()
    return _current_frontend.activity(description)


# Prompting passthroughs. These need an answer, so they fail loudly rather than
# guess or hang when nothing can ask the user.


def prompt_pick(request: PickRequest) -> str | None:
    """Prompts the user to pick one option."""
    return require_frontend().prompt_pick(request)


def prompt_path(request: PathRequest) -> Path | None:
    """Prompts the user to browse for a filesystem path."""
    return require_frontend().prompt_path(request)


def prompt_confirm(request: ConfirmRequest) -> bool:
    """Asks the user a yes/no question."""
    return require_frontend().prompt_confirm(request)


def prompt_text(request: TextRequest) -> str:
    """Prompts the user for free-form text."""
    return require_frontend().prompt_text(request)


def prompt_autocomplete(request: AutoCompleteRequest) -> str:
    """Prompts for text with type-to-filter autocompletion."""
    return require_frontend().prompt_autocomplete(request)


def prompt_number(request: NumberRequest) -> float:
    """Prompts the user for a number."""
    return require_frontend().prompt_number(request)


def prompt_form(request: FormRequest) -> Any | None:
    """Prompts the user to fill in a Pydantic model form."""
    return require_frontend().prompt_form(request)


def prompt_read_only_table(request: ReadOnlyTable) -> bool:
    """Displays read-only tabular data and collects a yes/no answer."""
    return require_frontend().prompt_read_only_table(request)


def prompt_field(request: FieldRequest) -> Any:
    """Prompts the user for a single Pydantic model field value."""
    return require_frontend().prompt_field(request)


def prompt_acknowledge(request: AcknowledgeRequest) -> None:
    """Displays a message in a modal and blocks until the user dismisses it."""
    require_frontend().prompt_acknowledge(request)
