from ._console import ConsoleFrontend
from ._current import current_frontend, notify, set_current_frontend
from ._frontend import Frontend, FrontendBase
from ._messages import MessageLevel
from ._requests import (
    AcknowledgeRequest,
    AutoCompleteRequest,
    Choice,
    ConfirmRequest,
    FieldRequest,
    FormRequest,
    NumberRequest,
    PickRequest,
    ReadOnlyTable,
    TextRequest,
    Validator,
)
from ._textual import TextualFrontend

#: The default frontend class used across the launcher.
DefaultFrontend = TextualFrontend


def default_frontend() -> Frontend:
    """
    Returns the frontend to use by default.

    Uses the interactive Textual TUI when attached to a real terminal, and
    falls back to the plain console frontend otherwise (e.g. output piped to a
    file or running in CI), where a full TUI cannot run.

    Returns:
        Frontend: A ready-to-use frontend instance.
    """
    from ..logging_helper import clabe_console

    if clabe_console.is_terminal:
        return TextualFrontend()
    return ConsoleFrontend()


def make_frontend(backend: str = "auto") -> Frontend:
    """
    Builds a frontend for the requested backend.

    Args:
        backend: One of ``"auto"`` (TUI when on a terminal, else console),
            ``"tui"`` or ``"console"``.

    Returns:
        Frontend: A ready-to-use frontend instance.

    Raises:
        ValueError: If ``backend`` is not a recognized option.
    """
    backend = (backend or "auto").lower()
    if backend == "auto":
        return default_frontend()
    if backend == "tui":
        return TextualFrontend()
    if backend == "console":
        return ConsoleFrontend()
    raise ValueError(f"Unknown UI backend: {backend!r}")


__all__ = [
    "Frontend",
    "FrontendBase",
    "FieldRequest",
    "FormRequest",
    "MessageLevel",
    "Choice",
    "PickRequest",
    "ReadOnlyTable",
    "ConfirmRequest",
    "TextRequest",
    "AutoCompleteRequest",
    "NumberRequest",
    "AcknowledgeRequest",
    "Validator",
    "ConsoleFrontend",
    "TextualFrontend",
    "DefaultFrontend",
    "default_frontend",
    "make_frontend",
    "notify",
    "set_current_frontend",
    "current_frontend",
]
