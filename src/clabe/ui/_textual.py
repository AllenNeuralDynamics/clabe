import contextlib
import datetime
import logging
import queue
import threading
from typing import Iterator, List, Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, Label, OptionList, RichLog, Static

from ..logging_helper import _TRANSCRIPT_LOGGER_NAME
from ._frontend import FrontendBase
from ._messages import MessageLevel
from ._requests import AutoCompleteRequest, ConfirmRequest, PickRequest, TextRequest

#: Sentinel pushed back to the caller when a prompt is cancelled (e.g. Ctrl+C).
_CANCELLED = object()
#: Sentinel representing the "none" option in a pick list.
_NONE = object()

#: Spinner animation frames for in-TUI activity rows.
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_RICH_STYLES = {
    MessageLevel.INFO: "",
    MessageLevel.SUCCESS: "green",
    MessageLevel.WARNING: "yellow",
    MessageLevel.ERROR: "bold red",
}

#: Per-level styles for the Logs pane, highest severity first.
_LOG_STYLES = (
    (logging.CRITICAL, "bold white on red"),
    (logging.ERROR, "bold red"),
    (logging.WARNING, "yellow"),
    (logging.INFO, "cyan"),
    (logging.DEBUG, "grey50"),
)


def _log_style(levelno: int) -> str:
    """Returns the Logs-pane style for a logging level."""
    for threshold, style in _LOG_STYLES:
        if levelno >= threshold:
            return style
    return "grey50"


def _local_time() -> str:
    """Returns the current local time as ``HH:MM:SS`` (no date)."""
    return datetime.datetime.now().strftime("%H:%M:%S")


_APP_CSS = """
Screen { layout: vertical; }
#clabe-user { height: 2fr; border: round $accent; padding: 0 1; }
#clabe-logo { height: auto; color: $accent; }
#clabe-user-log { height: 1fr; display: none; }
#clabe-processes { height: auto; min-height: 3; max-height: 8; border: round $warning; padding: 0 1; display: none; }
#clabe-prompt { height: auto; padding: 0 1; border: round $success; }
#clabe-prompt Label { margin: 0; height: 1; }
#clabe-prompt Input { border: none; height: 1; padding: 0; }
#clabe-prompt OptionList { max-height: 10; border: none; padding: 0; }
#clabe-logs { height: 1fr; border: round $primary; padding: 0 1; }
"""


class _ActivityRow(Static):
    """An animated spinner row shown while an activity is in progress."""

    def __init__(self, description: str) -> None:
        super().__init__()
        self._description = description
        self._frame = 0

    def on_mount(self) -> None:
        """Starts the spinner animation when the row is mounted."""
        self._tick()
        self.set_interval(0.1, self._tick)

    def _tick(self) -> None:
        """Advances the spinner frame and redraws the row."""
        self._frame = (self._frame + 1) % len(_SPINNER_FRAMES)
        self.update(f"{_SPINNER_FRAMES[self._frame]} {self._description}")


class _LauncherApp(App):
    """
    A single, long-lived Textual app that hosts the whole launcher session.

    The app is entered once and never left until the launcher exits, so there
    is no flashing between prompts. The screen is split into four areas:

    * **Session** — user-facing content (the banner, status messages and
      answered prompts), newest at the bottom;
    * **Processes** — animated rows for work running via the executors;
    * **Input** — where the current prompt's widgets are mounted;
    * **Logs** — the diagnostic log stream (info and above).

    Both content panes auto-scroll as new lines arrive, all inside the one TUI.

    Prompts are driven from the (synchronous) launcher thread: that thread calls
    one of the ``ask_*`` methods via :meth:`textual.app.App.call_from_thread`
    and blocks on a queue until the user answers, at which point the matching
    event handler pushes the result back.
    """

    CSS = _APP_CSS
    BINDINGS = [Binding("ctrl+c", "cancel", "Cancel", priority=True)]

    def __init__(self) -> None:
        super().__init__()
        self.ready = threading.Event()
        self._pending: Optional["queue.Queue"] = None
        self._kind: Optional[str] = None
        self._pick_values: List[object] = []
        self._auto_all: List[str] = []

    def compose(self) -> ComposeResult:
        """Builds the four-pane layout (Session, Processes, Input, Logs)."""
        with Vertical(id="clabe-user"):
            yield Static("", id="clabe-logo")
            yield RichLog(id="clabe-user-log", highlight=False, wrap=True, auto_scroll=True)
        yield Vertical(id="clabe-processes")
        yield Vertical(id="clabe-prompt")
        yield RichLog(id="clabe-logs", highlight=False, wrap=True, auto_scroll=True)

    def on_mount(self) -> None:
        """Titles the panes and signals that the app is ready for input."""
        self.query_one("#clabe-user", Vertical).border_title = "Session"
        self.query_one("#clabe-processes", Vertical).border_title = "Processes"
        self.query_one("#clabe-prompt", Vertical).border_title = "Input"
        self.query_one("#clabe-logs", RichLog).border_title = "Logs"
        self.ready.set()

    # --- content panes / activity (called via call_from_thread) -----------
    def set_logo(self, text: str) -> None:
        """Sets the placeholder logo shown while the Session pane is empty."""
        logos = self.query("#clabe-logo")
        if len(logos):
            logos.first(Static).update(text)

    def write_user(self, renderable: object) -> None:
        """Appends a renderable to the Session pane, retiring the logo on first use."""
        logos = self.query("#clabe-logo")
        if len(logos):
            logos.first(Static).remove()
            self.query_one("#clabe-user-log", RichLog).styles.display = "block"
        pane = self.query_one("#clabe-user-log", RichLog)
        pane.write(renderable)
        pane.scroll_end(animate=False)

    def write_log(self, renderable: object) -> None:
        """Appends a renderable to the Logs pane and scrolls to it."""
        pane = self.query_one("#clabe-logs", RichLog)
        pane.write(renderable)
        pane.scroll_end(animate=False)

    def add_activity(self, description: str) -> _ActivityRow:
        """Mounts an activity row, revealing the Processes pane while work runs."""
        row = _ActivityRow(description)
        pane = self.query_one("#clabe-processes", Vertical)
        pane.styles.display = "block"
        pane.mount(row)
        return row

    def remove_activity(self, row: _ActivityRow) -> None:
        """Removes an activity row, hiding the Processes pane once none remain."""
        pane = self.query_one("#clabe-processes", Vertical)
        row.remove()
        if not [w for w in pane.query(_ActivityRow) if w is not row]:
            pane.styles.display = "none"

    # --- prompt entry points (called via call_from_thread) ----------------
    async def ask_text(self, request: TextRequest, reply: "queue.Queue") -> None:
        """Mounts a text input; the answer is delivered via ``reply``."""
        self._pending, self._kind = reply, "text"
        await self._mount(Label(request.label), Input(value=request.default or "", id="clabe-input"))
        self.query_one("#clabe-input", Input).focus()

    async def ask_autocomplete(self, request: AutoCompleteRequest, reply: "queue.Queue") -> None:
        """Mounts an input with a filtering suggestion list; answer via ``reply``."""
        self._pending, self._kind = reply, "auto"
        self._auto_all = request.suggestions()
        await self._mount(
            Label(request.label),
            Input(value=request.default or "", id="clabe-input"),
            OptionList(*self._auto_all, id="clabe-options"),
        )
        self.query_one("#clabe-input", Input).focus()

    async def ask_pick(self, request: PickRequest, reply: "queue.Queue") -> None:
        """Mounts an option list to pick from; the answer is delivered via ``reply``."""
        self._pending, self._kind = reply, "pick"
        self._pick_values = []
        labels: List[str] = []
        if request.allow_none:
            labels.append(request.none_label)
            self._pick_values.append(_NONE)
        for choice in request.choices():
            labels.append(choice.display)
            self._pick_values.append(choice.value)
        await self._mount(Label(request.label), OptionList(*labels, id="clabe-options"))
        option_list = self.query_one("#clabe-options", OptionList)
        if request.default is not None and request.default in self._pick_values:
            option_list.highlighted = self._pick_values.index(request.default)
        option_list.focus()

    async def ask_confirm(self, request: ConfirmRequest, reply: "queue.Queue") -> None:
        """Mounts a Yes/No list; the answer is delivered via ``reply``."""
        self._pending, self._kind = reply, "confirm"
        await self._mount(Label(request.label), OptionList("Yes", "No", id="clabe-options"))
        option_list = self.query_one("#clabe-options", OptionList)
        option_list.highlighted = 0 if request.default else 1
        option_list.focus()

    # --- internals --------------------------------------------------------
    async def _mount(self, *widgets) -> None:
        """Replaces the Input pane's contents with ``widgets``."""
        box = self.query_one("#clabe-prompt", Vertical)
        await box.remove_children()
        await box.mount(*widgets)

    async def _finish(self, value: object) -> None:
        """Clears the prompt area, then hands the answer back to the caller.

        Clearing happens *before* the queue is released so the caller cannot
        mount its next prompt into a region that is about to be wiped.
        """
        reply, self._pending, self._kind = self._pending, None, None
        await self.query_one("#clabe-prompt", Vertical).remove_children()
        if reply is not None:
            reply.put(value)

    # --- events -----------------------------------------------------------
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Resolves text/autocomplete prompts when the user presses Enter."""
        if self._kind in ("text", "auto"):
            await self._finish(event.value)

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filters the autocomplete suggestion list as the user types."""
        if self._kind != "auto":
            return
        option_list = self.query_one("#clabe-options", OptionList)
        option_list.clear_options()
        text = event.value.lower()
        option_list.add_options([opt for opt in self._auto_all if text in opt.lower()] if text else self._auto_all)

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Resolves pick/confirm/autocomplete prompts from a list selection."""
        if self._kind == "pick":
            value = self._pick_values[event.option_index]
            await self._finish(None if value is _NONE else value)
        elif self._kind == "confirm":
            await self._finish(event.option_index == 0)
        elif self._kind == "auto":
            await self._finish(str(event.option.prompt))

    def on_key(self, event) -> None:
        """Lets Down move from the autocomplete input into the suggestion list."""
        if self._kind == "auto" and event.key == "down" and isinstance(self.focused, Input):
            option_list = self.query_one("#clabe-options", OptionList)
            if option_list.option_count:
                option_list.focus()
                option_list.highlighted = 0
                event.stop()

    def action_cancel(self) -> None:
        """Aborts the whole launcher on Ctrl+C.

        Textual consumes Ctrl+C as a key event rather than a signal, so we have
        to propagate the interrupt to the launcher thread ourselves. If a prompt
        is waiting, releasing it with the cancel sentinel raises
        ``KeyboardInterrupt`` where the prompt was requested; otherwise we raise
        it directly in the main thread to interrupt whatever is running.
        """
        if self._pending is not None:
            reply, self._pending, self._kind = self._pending, None, None
            reply.put(_CANCELLED)
        else:
            import _thread

            _thread.interrupt_main()


class _TuiActivitySink:
    """Renders activities as rows inside the running TUI instead of the console."""

    def __init__(self, app: _LauncherApp) -> None:
        self._app = app

    @contextlib.contextmanager
    def activity(self, description: str) -> Iterator[None]:
        """Shows an activity row in the Processes pane for the duration of the block."""
        row = self._app.call_from_thread(self._app.add_activity, description)
        try:
            yield
        finally:
            self._app.call_from_thread(self._app.remove_activity, row)


class _TuiLogHandler(logging.Handler):
    """Routes log records (info and above) into the TUI Logs pane while active."""

    def __init__(self, frontend: "TextualFrontend") -> None:
        super().__init__(logging.INFO)
        self._frontend = frontend

    def emit(self, record: logging.LogRecord) -> None:
        """Writes the formatted, color-coded record to the TUI Logs pane."""
        if record.name.startswith(_TRANSCRIPT_LOGGER_NAME):
            return
        try:
            message = self.format(record)
        except Exception:  # pragma: no cover - defensive
            return
        self._frontend._write_log(Text(message, style=_log_style(record.levelno)))


def _unwrap(value: object) -> object:
    """Translates the cancellation sentinel into ``KeyboardInterrupt``."""
    if value is _CANCELLED:
        raise KeyboardInterrupt("Cancelled by user.")
    return value


class TextualFrontend(FrontendBase):
    """
    Interactive TUI frontend backed by a single persistent Textual app.

    The app is started lazily on first use and runs on a background thread for
    the remainder of the session, so the launcher enters the TUI once and never
    leaves it. Messages, answered prompts and live activity all render inside
    the one app; console logging is muted (and warnings/errors are mirrored into
    the TUI) for the app's lifetime so nothing corrupts the display.
    """

    def __init__(self) -> None:
        super().__init__()
        self._app: Optional[_LauncherApp] = None
        self._thread: Optional[threading.Thread] = None
        self._log_handler: Optional[_TuiLogHandler] = None
        self._prev_console_level: Optional[int] = None

    # --- lifecycle --------------------------------------------------------
    def _ensure(self) -> _LauncherApp:
        """Starts the TUI app (once) on a background thread and returns it."""
        if self._app is not None:
            return self._app

        app = _LauncherApp()
        self._app = app
        self._thread = threading.Thread(target=app.run, name="clabe-tui", daemon=True)
        self._thread.start()
        if not app.ready.wait(timeout=15):
            raise RuntimeError("Timed out starting the TUI.")

        from ..apps._progress import get_activity_indicator

        get_activity_indicator().set_sink(_TuiActivitySink(app))
        self._capture_logging()
        return app

    def _capture_logging(self) -> None:
        """Mutes the console handler and mirrors log records into the Logs pane."""
        from ..logging_helper import rich_handler, set_console_level

        self._prev_console_level = rich_handler.level
        # Mute the console handler: the TUI owns the terminal. Warnings/errors
        # are still surfaced, but through the in-TUI log handler instead.
        set_console_level(logging.CRITICAL + 1)
        handler = _TuiLogHandler(self)
        # Local time, time only; the on-disk log keeps full UTC timestamps.
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"))
        logging.getLogger().addHandler(handler)
        self._log_handler = handler

    def close(self) -> None:
        """Tears down the TUI and restores console logging/activity rendering."""
        if self._app is None:
            return

        from ..apps._progress import get_activity_indicator
        from ..logging_helper import set_console_level

        get_activity_indicator().set_sink(None)
        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None
        if self._prev_console_level is not None:
            set_console_level(self._prev_console_level)
            self._prev_console_level = None

        try:
            self._app.call_from_thread(self._app.exit)
        except Exception:  # pragma: no cover - defensive on shutdown
            pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._app = None
        self._thread = None

    # --- output -----------------------------------------------------------
    def _write_user(self, renderable: object) -> None:
        """Sends a renderable to the Session pane on the app thread."""
        if self._app is not None:
            self._app.call_from_thread(self._app.write_user, renderable)

    def _write_log(self, renderable: object) -> None:
        """Sends a renderable to the Logs pane on the app thread."""
        if self._app is not None:
            self._app.call_from_thread(self._app.write_log, renderable)

    def _render_header(self, text: str) -> None:
        """Shows the banner as the empty-state logo (retired by the first message)."""
        self._ensure()
        if self._app is not None:
            self._app.call_from_thread(self._app.set_logo, text)

    def _render(self, message: str, level: MessageLevel) -> None:
        """Writes a timestamped, level-styled message to the Session pane."""
        self._ensure()
        line = Text(f"[{_local_time()}] ", style="dim")
        line.append(message, style=_RICH_STYLES[level])
        self._write_user(line)

    def _on_answer(self, key: str, value: object) -> None:
        """Records an answered prompt as a timestamped line in the Session pane."""
        if value is None or value == "":
            return
        self._ensure()
        line = Text(f"[{_local_time()}] ", style="dim")
        line.append("› ", style="dim")
        line.append(f"{key}: ")
        line.append(str(value), style="bold")
        self._write_user(line)

    # --- prompts ----------------------------------------------------------
    def _ask_text(self, request: TextRequest) -> str:
        """Requests a text prompt from the app and blocks until answered."""
        app = self._ensure()
        reply: "queue.Queue" = queue.Queue()
        app.call_from_thread(app.ask_text, request, reply)
        return _unwrap(reply.get()) or ""

    def _ask_autocomplete(self, request: AutoCompleteRequest) -> str:
        """Requests an autocomplete prompt from the app and blocks until answered."""
        app = self._ensure()
        reply: "queue.Queue" = queue.Queue()
        app.call_from_thread(app.ask_autocomplete, request, reply)
        return _unwrap(reply.get()) or ""

    def _ask_pick(self, request: PickRequest) -> Optional[str]:
        """Requests a pick prompt from the app and blocks until answered."""
        app = self._ensure()
        reply: "queue.Queue" = queue.Queue()
        app.call_from_thread(app.ask_pick, request, reply)
        return _unwrap(reply.get())

    def _ask_confirm(self, request: ConfirmRequest) -> bool:
        """Requests a confirm prompt from the app and blocks until answered."""
        app = self._ensure()
        reply: "queue.Queue" = queue.Queue()
        app.call_from_thread(app.ask_confirm, request, reply)
        return bool(_unwrap(reply.get()))
