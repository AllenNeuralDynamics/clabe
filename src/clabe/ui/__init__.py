from ._console import ConsoleFrontend
from ._frontend import Frontend, FrontendBase
from ._messages import MessageLevel
from ._questionary import QuestionaryFrontend
from ._requests import (
    AutoCompleteRequest,
    Choice,
    ConfirmRequest,
    NumberRequest,
    PickRequest,
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
    from ..logging_helper import console

    if console.is_terminal:
        return TextualFrontend()
    return ConsoleFrontend()


__all__ = [
    "Frontend",
    "FrontendBase",
    "MessageLevel",
    "Choice",
    "PickRequest",
    "ConfirmRequest",
    "TextRequest",
    "AutoCompleteRequest",
    "NumberRequest",
    "Validator",
    "ConsoleFrontend",
    "QuestionaryFrontend",
    "TextualFrontend",
    "DefaultFrontend",
    "default_frontend",
]
