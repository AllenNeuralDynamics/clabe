from ._console import ConsoleFrontend
from ._current import (
    NoFrontendError,
    activity,
    current_frontend,
    header,
    notify,
    prompt_acknowledge,
    prompt_autocomplete,
    prompt_confirm,
    prompt_field,
    prompt_form,
    prompt_number,
    prompt_path,
    prompt_pick,
    prompt_read_only_table,
    prompt_text,
    require_frontend,
    set_current_frontend,
    use_frontend,
)
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
    PathRequest,
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
    from ..logging import clabe_console

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
    "AcknowledgeRequest",
    "AutoCompleteRequest",
    "Choice",
    "ConfirmRequest",
    "ConsoleFrontend",
    "DefaultFrontend",
    "FieldRequest",
    "FormRequest",
    "Frontend",
    "FrontendBase",
    "MessageLevel",
    "NoFrontendError",
    "NumberRequest",
    "PathRequest",
    "PickRequest",
    "ReadOnlyTable",
    "TextRequest",
    "TextualFrontend",
    "Validator",
    "activity",
    "current_frontend",
    "default_frontend",
    "header",
    "make_frontend",
    "notify",
    "prompt_acknowledge",
    "prompt_autocomplete",
    "prompt_confirm",
    "prompt_field",
    "prompt_form",
    "prompt_number",
    "prompt_path",
    "prompt_pick",
    "prompt_read_only_table",
    "prompt_text",
    "require_frontend",
    "set_current_frontend",
    "use_frontend",
]
