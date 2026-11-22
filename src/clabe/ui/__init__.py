"""How clabe talks to the user: requests that describe and validate themselves, a session API to ask them
(``ui.ask``, ``ui.confirm``, ``ui.group``, ...), and clients (console, TUI) that render and collect answers."""

from typing import TYPE_CHECKING

from ._client import Client, ClientSession, HeadlessClient, make_client
from ._console import ConsoleClient
from ._messages import MessageLevel
from ._requests import (
    AcknowledgeRequest,
    AnswerRejected,
    AutoCompleteRequest,
    Choice,
    ConfirmRequest,
    FieldRequest,
    FormRequest,
    GroupRequest,
    MarkdownRequest,
    MediaKind,
    MediaRequest,
    NumberRequest,
    OptionsFunction,
    PathRequest,
    PickRequest,
    ReadOnlyTable,
    Request,
    TextRequest,
    Validator,
)
from ._session import (
    NavigationRequested,
    NoSessionError,
    PromptCancelledError,
    Session,
    acknowledge,
    activity,
    ask,
    autocomplete,
    bind,
    confirm,
    current_session,
    field,
    form,
    group,
    header,
    html,
    image,
    markdown,
    notify,
    number,
    path,
    pick,
    table,
    text,
    url,
)

if TYPE_CHECKING:
    from ._textual import TextualClient


def __getattr__(name: str):
    """Loads the terminal UI (``TextualClient``) only when it is used: it needs the ``frontend`` extra."""
    if name == "TextualClient":
        from .._extras import require_tui

        require_tui()
        from ._textual import TextualClient

        return TextualClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AcknowledgeRequest",
    "AnswerRejected",
    "AutoCompleteRequest",
    "Choice",
    "Client",
    "ClientSession",
    "ConfirmRequest",
    "ConsoleClient",
    "FieldRequest",
    "FormRequest",
    "GroupRequest",
    "HeadlessClient",
    "MarkdownRequest",
    "MediaKind",
    "MediaRequest",
    "MessageLevel",
    "NavigationRequested",
    "NoSessionError",
    "NumberRequest",
    "OptionsFunction",
    "PathRequest",
    "PickRequest",
    "PromptCancelledError",
    "ReadOnlyTable",
    "Request",
    "Session",
    "TextRequest",
    "TextualClient",
    "Validator",
    "acknowledge",
    "activity",
    "ask",
    "autocomplete",
    "bind",
    "confirm",
    "current_session",
    "field",
    "form",
    "group",
    "header",
    "html",
    "image",
    "make_client",
    "markdown",
    "notify",
    "number",
    "path",
    "pick",
    "table",
    "text",
    "url",
]
