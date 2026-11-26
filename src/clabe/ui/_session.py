"""Sessions: whoever can ask the user right now (a run, or a local client outside a run), and the ask API over it.

Code asks through the bound session with `ask` or the typed helpers (`confirm`, `text`, ...);
it never talks to a client directly. The binding is process-wide (not per thread or task), so steps, the library
code they call and the threads they start all reach the same session: one session per process.
"""

import contextlib
import logging
import os
import threading
from collections.abc import Generator, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from ._messages import MessageLevel
from ._requests import (
    AcknowledgeRequest,
    AutoCompleteRequest,
    Choice,
    ConfirmRequest,
    FieldRequest,
    FormRequest,
    GroupRequest,
    MarkdownRequest,
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

#: Where messages go when nothing can show them to the user.
logger = logging.getLogger("clabe.ui")


class PromptCancelledError(Exception):
    """Raised by a prompt that was cancelled before it was answered (e.g. the run went back or aborted)."""


class NavigationRequested(Exception):
    """Raised by a client's ``ask`` when the user asks to navigate instead of answering (e.g. Alt+← in the TUI).

    Whoever drives the client handles it: during a run, [`LocalClient`][clabe.workflow.LocalClient] goes back
    (``"back"``) or forward (``"forward"``) and asks again if the run refuses; outside a run it is ignored.

    Args:
        action: ``"back"`` or ``"forward"``.
    """

    def __init__(self, action: Literal["back", "forward"]) -> None:
        super().__init__(action)
        self.action = action


class NoSessionError(RuntimeError):
    """Raised by `ask` when nothing can ask the user."""


@runtime_checkable
class Session(Protocol):
    """Something that can ask the user and tell them things: a run, or a local client outside a run."""

    def ask(self, request: Request) -> Any:
        """Asks ``request`` and blocks until it is answered; returns the validated answer."""
        ...

    def notify(self, message: str, level: MessageLevel = MessageLevel.INFO) -> None:
        """Tells the user something, without expecting an answer."""
        ...

    def header(self, text: str) -> None:
        """Shows the user a prominent header."""
        ...


class _Binding:
    """One active `bind` block; its identity tells it apart from other bindings of the same session."""

    __slots__ = ("session",)

    def __init__(self, session: Session | None) -> None:
        self.session = session


#: Active bindings, oldest first; the latest one is the current session. Guarded by ``_bindings_lock``.
_bindings: list[_Binding] = []
_bindings_lock = threading.Lock()


@contextlib.contextmanager
def bind(session: Session | None) -> Generator[None, None, None]:
    """Binds ``session`` as the process' session for the block, then removes that binding.

    Process-wide: every thread sees the binding. Bindings form an ordered list and the latest one is current, so
    blocks may end in any order (e.g. on different threads): ending a block removes its own binding wherever it is,
    and never brings back a binding whose block has already ended.

    Args:
        session: The session to bind, or ``None`` for "no session" while this binding is the latest.
    """
    binding = _Binding(session)
    with _bindings_lock:
        _bindings.append(binding)
    try:
        yield
    finally:
        with _bindings_lock:
            if binding in _bindings:  # by identity: _Binding does not define equality
                _bindings.remove(binding)


def current_session() -> Session | None:
    """Returns the session of the latest binding, or ``None`` if nothing is bound."""
    with _bindings_lock:
        return _bindings[-1].session if _bindings else None


def ask(request: Request) -> Any:
    """Asks ``request`` through the bound session and returns its validated answer.

    Args:
        request: What to ask (e.g. a [`TextRequest`][clabe.ui.TextRequest]).

    Returns:
        The answer, validated and coerced by the request (a [`FieldRequest`][clabe.ui.FieldRequest] gives the typed field
        value).

    Raises:
        NoSessionError: Nothing is bound that can ask the user.
        PromptCancelledError: The prompt was cancelled before it was answered.
    """
    session = current_session()
    if session is None:
        raise NoSessionError("No session is bound. User interaction is not possible.")
    return session.ask(request)


def notify(message: str, level: MessageLevel = MessageLevel.INFO) -> None:
    """Tells the user something through the bound session; logs it (at ``level``) when nothing can show it.

    Args:
        message: The text to show.
        level: How prominently to show it.
    """
    session = current_session()
    if session is not None:
        session.notify(message, level)
        return
    logger.log(level.logging_level, "%s", message)


def header(text: str) -> None:
    """Shows the user a header through the bound session; logs it when nothing can show it.

    Args:
        text: The header text.
    """
    session = current_session()
    if session is not None:
        session.header(text)
        return
    logger.info("%s", text)


def activity(description: str) -> contextlib.AbstractContextManager[None]:
    """Shows live work (e.g. a running app) for the duration of a block; a run records it as activity events.

    Args:
        description: What the work is, as shown to the user.
    """
    from ..runnable import get_activity_indicator  # lazily: clabe.runnable imports clabe.ui

    return get_activity_indicator().activity(description)


# --- typed helpers --------------------------------------------------------
# Each builds the request (keyword options mirror its fields) and asks it through the bound session.


def confirm(label: str, *, default: bool = False, field: str | None = None) -> bool:
    """Asks a yes/no question (see [`ConfirmRequest`][clabe.ui.ConfirmRequest])."""
    return ask(ConfirmRequest(label=label, default=default, field=field))


def text(
    label: str,
    *,
    default: str | None = None,
    multiline: bool = False,
    validators: Sequence[Validator] = (),
    field: str | None = None,
) -> str:
    """Asks for free-form text (see [`TextRequest`][clabe.ui.TextRequest])."""
    return ask(TextRequest(label=label, default=default, multiline=multiline, validators=list(validators), field=field))


def number(
    label: str,
    *,
    default: float | None = None,
    field: str | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    step: float | None = None,
    widget: Literal["input", "slider"] = "input",
) -> float:
    """Asks for a number, optionally bounded (see [`NumberRequest`][clabe.ui.NumberRequest])."""
    request = NumberRequest(
        label=label, default=default, field=field, minimum=minimum, maximum=maximum, step=step, widget=widget
    )
    return ask(request)


def pick(
    label: str,
    options: Sequence[str | Choice],
    *,
    default: str | None = None,
    allow_none: bool = True,
    none_label: str = "None",
    field: str | None = None,
    help: str | None = None,
) -> str | None:
    """Asks to pick one option; ``None`` when the "none" option is picked (see [`PickRequest`][clabe.ui.PickRequest])."""
    return ask(
        PickRequest(
            label=label,
            options=options,
            default=default,
            allow_none=allow_none,
            none_label=none_label,
            field=field,
            help=help,
        )
    )


def autocomplete(
    label: str,
    options: Sequence[str] | OptionsFunction,
    *,
    default: str | None = None,
    strict: bool = False,
    validators: Sequence[Validator] = (),
    field: str | None = None,
    help: str | None = None,
) -> str:
    """Asks for text completed against ``options``, a list or a function of the typed text returning the options (see
    [`AutoCompleteRequest`][clabe.ui.AutoCompleteRequest])."""
    return ask(
        AutoCompleteRequest(
            label=label,
            options=options,
            default=default,
            strict=strict,
            validators=list(validators),
            field=field,
            help=help,
        )
    )


def path(
    label: str,
    *,
    start: str | os.PathLike | None = None,
    default: str | None = None,
    must_exist: bool = True,
    kind: Literal["file", "dir", "any"] = "file",
    extensions: Sequence[str] | None = None,
    field: str | None = None,
    help: str | None = None,
) -> Path | None:
    """Asks for a filesystem path; ``None`` when cancelled (see [`PathRequest`][clabe.ui.PathRequest])."""
    return ask(
        PathRequest(
            label=label,
            start=start,
            default=default,
            must_exist=must_exist,
            kind=kind,
            extensions=extensions,
            field=field,
            help=help,
        )
    )


def form(model: type, *, title: str | None = None, initial: object | None = None, field: str | None = None) -> Any:
    """Asks to fill in ``model`` as a form; the model instance, or ``None`` when cancelled (see
    [`FormRequest`][clabe.ui.FormRequest])."""
    return ask(FormRequest(model=model, title=title, initial=initial, field=field))


def field(model: type, field_name: str, *, initial: object | None = None) -> Any:
    """Asks for one field of ``model`` and returns the typed value (see [`FieldRequest`][clabe.ui.FieldRequest]).

    The session resolves the field to the prompt its type calls for (e.g. yes/no for a ``bool``).
    """
    return ask(FieldRequest(model=model, field_name=field_name, initial=initial))


def table(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    title: str | None = None,
    prompt: str | None = None,
    confirm_label: str = "OK",
    cancel_label: str = "Cancel",
    field: str | None = None,
) -> bool:
    """Shows a read-only table and asks yes/no (see [`ReadOnlyTable`][clabe.ui.ReadOnlyTable])."""
    return ask(
        ReadOnlyTable(
            columns=columns,
            rows=rows,
            title=title,
            prompt=prompt,
            confirm_label=confirm_label,
            cancel_label=cancel_label,
            field=field,
        )
    )


def acknowledge(message: str, *, title: str = "Notice", button_label: str = "OK", field: str | None = None) -> None:
    """Shows ``message`` and blocks until the user dismisses it (see [`AcknowledgeRequest`][clabe.ui.AcknowledgeRequest])."""
    ask(AcknowledgeRequest(message=message, title=title, button_label=button_label, field=field))


def markdown(text: str, *, title: str | None = None, button_label: str = "Continue", field: str | None = None) -> None:
    """Shows a Markdown document and blocks until the user continues (see [`MarkdownRequest`][clabe.ui.MarkdownRequest])."""
    ask(MarkdownRequest(markdown=text, title=title, button_label=button_label, field=field))


def image(
    source: str | Path,
    *,
    title: str | None = None,
    caption: str | None = None,
    button_label: str = "Continue",
    field: str | None = None,
) -> None:
    """Shows an image (a local file or a URL) and blocks until the user continues.

    Clients that cannot show images log a warning with the file instead (see [`MediaRequest`][clabe.ui.MediaRequest]).
    """
    ask(MediaRequest(source, "image", title=title, caption=caption, button_label=button_label, field=field))


def html(
    source: str | Path,
    *,
    title: str | None = None,
    caption: str | None = None,
    button_label: str = "Continue",
    field: str | None = None,
) -> None:
    """Shows an HTML page (a local file, a URL, or the HTML itself) and blocks until the user continues.

    The page is sandboxed. Clients that cannot show HTML log a warning instead (see [`MediaRequest`][clabe.ui.MediaRequest]).
    """
    ask(MediaRequest(source, "html", title=title, caption=caption, button_label=button_label, field=field))


def url(
    address: str,
    *,
    title: str | None = None,
    caption: str | None = None,
    button_label: str = "Continue",
    field: str | None = None,
) -> None:
    """Shows a web page and blocks until the user continues.

    Sites that refuse to be framed still get a link to open them. Clients that cannot show web pages log a warning
    with the URL instead (see [`MediaRequest`][clabe.ui.MediaRequest]).
    """
    ask(MediaRequest(address, "url", title=title, caption=caption, button_label=button_label, field=field))


def group(title: str | None = None, /, **items: Request) -> dict[str, Any]:
    """Asks several prompts together as one (e.g. a text input and a slider) and returns their answers by name.

    Args:
        title: Optional title shown above the group.
        **items: The prompts, by name: text, number, confirm, pick, autocomplete, path or field requests.

    Returns:
        Each item's validated value, by name.
    """
    return ask(GroupRequest(items=items, title=title))
