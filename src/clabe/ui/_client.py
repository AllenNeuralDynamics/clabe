"""Clients: user interfaces (console, TUI, later web/MCP) that render messages and collect raw answers.

A client never validates. Whoever drives it does, with the request's own rules: a run (through
:class:`~clabe.workflow.LocalClient`) or, outside a run, a :class:`ClientSession`.
"""

import logging
from typing import Any, Protocol, runtime_checkable

from ._messages import MessageLevel
from ._requests import AnswerRejected, FieldRequest, Request

logger = logging.getLogger(__name__)


@runtime_checkable
class Client(Protocol):
    """A user interface that renders messages and collects raw answers (it never validates)."""

    def ask(self, request: Request) -> Any:
        """Shows ``request`` and blocks until the user answers; returns the raw answer.

        Raises:
            PromptCancelledError: :meth:`cancel` was called while the prompt was shown.
        """
        ...

    def cancel(self) -> None:
        """Cancels the prompt being shown, if any. Thread-safe."""
        ...

    def notify(self, message: str, level: MessageLevel = MessageLevel.INFO) -> None:
        """Shows the user a message, without expecting an answer."""
        ...

    def header(self, text: str) -> None:
        """Shows the user a prominent header."""
        ...

    def close(self) -> None:
        """Releases the interface (e.g. tears down a TUI)."""
        ...


class ClientSession:
    """Uses a :class:`Client` as the :class:`~clabe.ui.Session` outside a run.

    Resolves :class:`~clabe.ui.FieldRequest` s, validates answers with the request's own rules and asks again
    (showing the reason) until an answer is accepted, as a run does.

    Args:
        client: The client that asks and renders.
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    @property
    def client(self) -> Client:
        """The client this session asks through."""
        return self._client

    @client.setter
    def client(self, client: Client) -> None:
        """Switches the client this session asks through."""
        self._client = client

    def ask(self, request: Request) -> Any:
        """Asks ``request`` until it gets a valid answer and returns that answer, validated and coerced.

        Raises:
            PromptCancelledError: The client's prompt was cancelled.
        """
        convert = None
        if isinstance(request, FieldRequest):
            request, convert = request.resolve()
        while True:
            raw = self._client.ask(request)
            try:
                value = request.validate_answer(raw)
            except AnswerRejected as exc:
                self._client.notify(str(exc), MessageLevel.ERROR)
                continue
            return convert(value) if convert is not None else value

    def notify(self, message: str, level: MessageLevel = MessageLevel.INFO) -> None:
        """Shows ``message`` on the client (and records it in the log file, at debug level)."""
        logger.debug("UI» %s", message)
        self._client.notify(message, level)

    def header(self, text: str) -> None:
        """Shows a header on the client."""
        self._client.header(text)


class HeadlessClient:
    """A client with no screen: prompts wait until answered elsewhere (e.g. the web UI), messages go to the log.

    For runs started without a terminal (by the daemon, a scheduler or a service) and served over HTTP.
    """

    #: There is nobody at this client to ask: the launcher skips its exit prompt.
    interactive = False

    def __init__(self) -> None:
        import threading

        self._cancelled = threading.Event()

    def ask(self, request: Request) -> Any:
        """Waits until the prompt is answered by another client (which cancels this one)."""
        from ._session import PromptCancelledError

        self._cancelled.wait()
        self._cancelled.clear()
        raise PromptCancelledError()

    def cancel(self) -> None:
        """Releases the waiting prompt."""
        self._cancelled.set()

    def notify(self, message: str, level: MessageLevel = MessageLevel.INFO) -> None:
        """Logs the message."""
        logger.log(MessageLevel(level).logging_level, "%s", message)

    def header(self, text: str) -> None:
        """Logs the header."""
        logger.info("%s", text)

    def close(self) -> None:
        """Nothing to tear down."""


def make_client(backend: str = "auto") -> Client:
    """Builds a client for the requested backend.

    Args:
        backend: ``"auto"`` (TUI on a terminal, else console), ``"tui"``, ``"console"`` or ``"headless"`` (no
            screen: prompts are answered from the web UI).

    Raises:
        ValueError: ``backend`` is not a recognized option.
    """
    from ..logging import clabe_console
    from ._console import ConsoleClient
    from ._textual import TextualClient

    choice = (backend or "auto").lower()
    if choice == "auto":
        choice = "tui" if clabe_console.is_terminal else "console"
    if choice == "tui":
        return TextualClient()
    if choice == "console":
        return ConsoleClient()
    if choice == "headless":
        return HeadlessClient()
    raise ValueError(f"Unknown UI backend: {backend!r}")
