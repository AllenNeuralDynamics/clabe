import contextlib
import datetime
import logging
import os
import platform as _platform
import queue
import re
import tempfile
import threading
from pathlib import Path
from typing import Iterator, List, Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, Label, OptionList, RichLog, Static

from .. import __version__
from ..logging_helper import _TRANSCRIPT_LOGGER_NAME
from ._frontend import FrontendBase
from ._messages import MessageLevel
from ._requests import AutoCompleteRequest, ConfirmRequest, FormRequest, PickRequest, TextRequest
from ._textual_form import _FormScreen

#: Sentinel pushed back to the caller when a prompt is cancelled (e.g. Ctrl+C).
_CANCELLED = object()
#: Sentinel representing the "none" option in a pick list.
_NONE = object()

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_RICH_STYLES = {
    MessageLevel.INFO: "",
    MessageLevel.SUCCESS: "green",
    MessageLevel.WARNING: "yellow",
    MessageLevel.ERROR: "bold red",
}

_LOG_STYLES = (
    (logging.CRITICAL, "bold white on red"),
    (logging.ERROR, "bold red"),
    (logging.WARNING, "yellow"),
    (logging.INFO, "cyan"),
    (logging.DEBUG, "grey50"),
)


def _log_style(levelno: int) -> str:
    """Return the Logs-pane color style for a logging level."""
    for threshold, style in _LOG_STYLES:
        if levelno >= threshold:
            return style
    return "grey50"


def _local_time() -> str:
    """Return the current local time formatted as HH:MM:SS."""
    return datetime.datetime.now().strftime("%H:%M:%S")


#: Matches drive-rooted, absolute, or multi-segment relative paths.
#: Only tokens that resolve to an existing path are linked — filters out incidental "a/b" text.
_PATH_RE = re.compile(
    r"(?<![:\w/\\])"
    r"(?:[A-Za-z]:[\\/][\w.\-]+(?:[\\/][\w.\-]+)*"
    r"|[\\/][\w.\-]+(?:[\\/][\w.\-]+)*"
    r"|[\w.\-]+(?:[\\/][\w.\-]+)+)"
)


def _linkify(message: str, style: str) -> Text:
    """Render paths in ``message`` as OSC 8 ``file://`` hyperlinks where they exist on disk."""
    text = Text(style=style)
    cursor = 0
    for match in _PATH_RE.finditer(message):
        token = match.group(0).rstrip(".,;:!?)]}'\"")
        absolute = os.path.abspath(token)
        if not os.path.exists(absolute):
            continue
        text.append(message[cursor : match.start()])
        text.append(token, style=f"underline link {Path(absolute).as_uri()}")
        cursor = match.start() + len(token)
    text.append(message[cursor:])
    return text


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
    """Animated spinner row shown while an activity is in progress."""

    def __init__(self, description: str) -> None:
        """Initialize with the activity description."""
        super().__init__()
        self._description = description
        self._frame = 0

    def on_mount(self) -> None:
        """Start the spinner animation on a 100 ms interval."""
        self._tick()
        self.set_interval(0.1, self._tick)

    def _tick(self) -> None:
        """Advance the spinner frame and redraw the row."""
        self._frame = (self._frame + 1) % len(_SPINNER_FRAMES)
        self.update(f"{_SPINNER_FRAMES[self._frame]} {self._description}")


if _platform.system() == "Windows":
    from textual.drivers.windows_driver import WindowsDriver as _WindowsDriver

    class _WindowsDriverNoAnyEvent(_WindowsDriver):
        """Windows driver that skips ?1003h (any-event motion tracking).

        The default driver sends \\x1b[?1003h which causes Windows terminals that
        only partially support VT mouse modes to echo raw escape sequences as text
        when the mouse moves or scrolls. Dropping it keeps clicks and scroll events
        (\\x1b[?1000h + \\x1b[?1006h) without the noise.
        """

        def _enable_mouse_support(self) -> None:
            """Enable button and scroll tracking without any-event motion."""
            if not self._mouse:
                return
            write = self.write
            write("\x1b[?1000h")  # button + scroll events
            write("\x1b[?1006h")  # SGR extended coordinates
            self.flush()

        def _disable_mouse_support(self) -> None:
            """Disable button and scroll tracking."""
            if not self._mouse:
                return
            write = self.write
            write("\x1b[?1000l")
            write("\x1b[?1006l")
            self.flush()

    _DRIVER_CLASS = _WindowsDriverNoAnyEvent
else:
    _DRIVER_CLASS = None  # type: ignore[assignment]


class _LauncherApp(App):
    """
    A single, long-lived Textual app that hosts the whole launcher session.

    The screen is split into four areas: Session (user-facing messages and
    answered prompts), Processes (animated activity rows), Input (current
    prompt widgets), and Logs (diagnostic log stream).

    Prompts are driven from the launcher thread via ``call_from_thread``; each
    ``ask_*`` method mounts widgets and blocks on a queue until the user answers.
    """

    CSS = _APP_CSS
    BINDINGS = [
        Binding("ctrl+c", "cancel", "Exit", priority=True),
        Binding("ctrl+s", "screenshot", "Screenshot", priority=True),
    ]

    def __init__(self) -> None:
        """Initialize the app with the platform-appropriate mouse driver."""
        super().__init__(driver_class=_DRIVER_CLASS)
        self.ready = threading.Event()
        self._pending: Optional["queue.Queue"] = None
        self._kind: Optional[str] = None
        self._pick_values: List[object] = []
        self._auto_all: List[str] = []

    def compose(self) -> ComposeResult:
        """Build the four-pane layout."""
        yield Header()
        with Vertical(id="clabe-user"):
            yield Static("", id="clabe-logo")
            yield RichLog(id="clabe-user-log", highlight=False, wrap=True, auto_scroll=True)
        yield Vertical(id="clabe-processes")
        yield Vertical(id="clabe-prompt")
        yield RichLog(id="clabe-logs", highlight=False, wrap=True, auto_scroll=True)
        yield Footer()

    def on_mount(self) -> None:
        """Set pane titles and signal that the app is ready for input."""
        self.title = "clabe"
        self.sub_title = f"v{__version__}"
        self.query_one("#clabe-user", Vertical).border_title = "Session"
        self.query_one("#clabe-processes", Vertical).border_title = "Processes"
        self.query_one("#clabe-prompt", Vertical).border_title = "Input"
        self.query_one("#clabe-logs", RichLog).border_title = "Logs"
        self.ready.set()

    def set_logo(self, text: str) -> None:
        """Set the placeholder logo shown in an empty Session pane."""
        logos = self.query("#clabe-logo")
        if len(logos):
            logos.first(Static).update(text)

    def set_experiment(self, name: str) -> None:
        """Show the experiment name in the header subtitle."""
        self.sub_title = f"v{__version__} · {name}"

    def write_user(self, renderable: object) -> None:
        """Append a renderable to the Session pane, retiring the logo on first use."""
        logos = self.query("#clabe-logo")
        if len(logos):
            logos.first(Static).remove()
            self.query_one("#clabe-user-log", RichLog).styles.display = "block"
        pane = self.query_one("#clabe-user-log", RichLog)
        pane.write(renderable)
        pane.scroll_end(animate=False)

    def write_log(self, renderable: object) -> None:
        """Append a renderable to the Logs pane."""
        pane = self.query_one("#clabe-logs", RichLog)
        pane.write(renderable)
        pane.scroll_end(animate=False)

    def add_activity(self, description: str) -> _ActivityRow:
        """Mount an activity row and reveal the Processes pane."""
        row = _ActivityRow(description)
        pane = self.query_one("#clabe-processes", Vertical)
        pane.styles.display = "block"
        pane.mount(row)
        return row

    def remove_activity(self, row: _ActivityRow) -> None:
        """Remove an activity row; hide the Processes pane when none remain."""
        pane = self.query_one("#clabe-processes", Vertical)
        row.remove()
        if not [w for w in pane.query(_ActivityRow) if w is not row]:
            pane.styles.display = "none"

    async def ask_text(self, request: TextRequest, reply: "queue.Queue") -> None:
        """Mount a text input; deliver the answer via reply."""
        self._pending, self._kind = reply, "text"
        await self._mount(Label(request.label), Input(value=request.default or "", id="clabe-input"))
        self.query_one("#clabe-input", Input).focus()

    async def ask_autocomplete(self, request: AutoCompleteRequest, reply: "queue.Queue") -> None:
        """Mount a filtering input and suggestion list; deliver the answer via reply."""
        self._pending, self._kind = reply, "auto"
        self._auto_all = request.suggestions()
        await self._mount(
            Label(request.label),
            Input(value=request.default or "", id="clabe-input"),
            OptionList(*self._auto_all, id="clabe-options"),
        )
        self.query_one("#clabe-input", Input).focus()

    async def ask_pick(self, request: PickRequest, reply: "queue.Queue") -> None:
        """Mount a pick list; deliver the answer via reply."""
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
        """Mount a Yes/No list; deliver the answer via reply."""
        self._pending, self._kind = reply, "confirm"
        await self._mount(Label(request.label), OptionList("Yes", "No", id="clabe-options"))
        option_list = self.query_one("#clabe-options", OptionList)
        option_list.highlighted = 0 if request.default else 1
        option_list.focus()

    async def ask_form(self, request: FormRequest, reply: "queue.Queue") -> None:
        """Push a form modal; deliver the result via reply."""
        self._pending = reply
        self._kind = "form"

        async def _on_dismiss(result: object) -> None:
            """Put the form result onto the reply queue."""
            if self._pending is reply:
                self._pending = None
                self._kind = None
                reply.put(result)

        await self.push_screen(_FormScreen(request), _on_dismiss)

    async def _mount(self, *widgets) -> None:
        """Replace the Input pane contents with the given widgets."""
        box = self.query_one("#clabe-prompt", Vertical)
        await box.remove_children()
        await box.mount(*widgets)

    async def _finish(self, value: object) -> None:
        """Clear the prompt area then hand the answer back to the caller.

        Clearing happens before the queue is released so the caller cannot
        mount its next prompt into a region that is about to be wiped.
        """
        reply, self._pending, self._kind = self._pending, None, None
        await self.query_one("#clabe-prompt", Vertical).remove_children()
        if reply is not None:
            reply.put(value)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Resolve text and autocomplete prompts on Enter."""
        if self._kind in ("text", "auto"):
            await self._finish(event.value)

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter the autocomplete suggestion list as the user types."""
        if self._kind != "auto":
            return
        option_list = self.query_one("#clabe-options", OptionList)
        option_list.clear_options()
        text = event.value.lower()
        option_list.add_options([opt for opt in self._auto_all if text in opt.lower()] if text else self._auto_all)

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Resolve pick, confirm, and autocomplete prompts on selection."""
        if self._kind == "pick":
            value = self._pick_values[event.option_index]
            await self._finish(None if value is _NONE else value)
        elif self._kind == "confirm":
            await self._finish(event.option_index == 0)
        elif self._kind == "auto":
            await self._finish(str(event.option.prompt))

    def on_key(self, event) -> None:
        """Move focus from the autocomplete input into the suggestion list on Down."""
        if self._kind == "auto" and event.key == "down" and isinstance(self.focused, Input):
            option_list = self.query_one("#clabe-options", OptionList)
            if option_list.option_count:
                option_list.focus()
                option_list.highlighted = 0
                event.stop()

    def action_cancel(self) -> None:
        """Abort on Ctrl+C.

        Textual consumes Ctrl+C as a key event, not a signal, so we propagate
        the interrupt ourselves. If a prompt is waiting we release it with the
        cancel sentinel (which raises KeyboardInterrupt at the call site);
        otherwise we interrupt the main thread directly.
        """
        if self._pending is not None:
            reply, self._pending, self._kind = self._pending, None, None
            reply.put(_CANCELLED)
        else:
            import _thread

            _thread.interrupt_main()

    def action_screenshot(self) -> None:
        """Save an SVG screenshot to the OS temp directory and note the path."""
        path = self.save_screenshot(path=tempfile.gettempdir())
        line = Text(f"[{_local_time()}] ", style="dim")
        line.append_text(_linkify(f"Saved screenshot to {path}", _RICH_STYLES[MessageLevel.SUCCESS]))
        self.write_user(line)


class _TuiActivitySink:
    """Renders activities as animated spinner rows inside the running TUI."""

    def __init__(self, app: _LauncherApp) -> None:
        """Initialize with the running launcher app."""
        self._app = app

    @contextlib.contextmanager
    def activity(self, description: str) -> Iterator[None]:
        """Show an activity row in the Processes pane for the duration of the block."""
        row = self._app.call_from_thread(self._app.add_activity, description)
        try:
            yield
        finally:
            self._app.call_from_thread(self._app.remove_activity, row)


class _TuiLogHandler(logging.Handler):
    """Routes log records (info and above) into the TUI Logs pane."""

    def __init__(self, frontend: "TextualFrontend") -> None:
        """Initialize with the owning frontend."""
        super().__init__(logging.INFO)
        self._frontend = frontend

    def emit(self, record: logging.LogRecord) -> None:
        """Format and write a color-coded log record to the Logs pane."""
        if record.name.startswith(_TRANSCRIPT_LOGGER_NAME):
            return
        try:
            message = self.format(record)
        except Exception:
            return
        self._frontend._write_log(_linkify(message, _log_style(record.levelno)))


def _unwrap(value: object) -> object:
    """Translate the cancellation sentinel into KeyboardInterrupt."""
    if value is _CANCELLED:
        raise KeyboardInterrupt("Cancelled by user.")
    return value


class TextualFrontend(FrontendBase):
    """
    Interactive TUI frontend backed by a single persistent Textual app.

    The app is started lazily on first use and runs on a background thread for
    the remainder of the session. Messages, answered prompts, and live activity
    all render inside the one app; console logging is muted for the app's
    lifetime so nothing corrupts the display.
    """

    def __init__(self) -> None:
        """Initialize the frontend."""
        super().__init__()
        self._app: Optional[_LauncherApp] = None
        self._thread: Optional[threading.Thread] = None
        self._log_handler: Optional[_TuiLogHandler] = None
        self._prev_console_level: Optional[int] = None

    def _ensure(self) -> _LauncherApp:
        """Start the TUI app on a background thread (once) and return it."""
        if self._app is not None:
            return self._app
        app = _LauncherApp()
        self._app = app
        self._thread = threading.Thread(target=app.run, name="clabe-tui", daemon=True)
        self._thread.start()
        if not app.ready.wait(timeout=15):
            raise RuntimeError("Timed out starting the TUI.")
        from ..runnable import get_activity_indicator

        get_activity_indicator().set_sink(_TuiActivitySink(app))
        self._capture_logging()
        return app

    def _capture_logging(self) -> None:
        """Mute the console handler and mirror log records into the Logs pane."""
        from ..logging_helper import rich_handler, set_console_level

        self._prev_console_level = rich_handler.level
        set_console_level(logging.CRITICAL + 1)
        handler = _TuiLogHandler(self)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"))
        logging.getLogger().addHandler(handler)
        self._log_handler = handler

    def close(self) -> None:
        """Tear down the TUI and restore console logging."""
        if self._app is None:
            return
        from ..logging_helper import set_console_level
        from ..runnable import get_activity_indicator

        get_activity_indicator().set_sink(None)
        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None
        if self._prev_console_level is not None:
            set_console_level(self._prev_console_level)
            self._prev_console_level = None
        try:
            self._app.call_from_thread(self._app.exit)
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._app = None
        self._thread = None

    def _write_user(self, renderable: object) -> None:
        """Forward a renderable to the Session pane on the app thread."""
        if self._app is not None:
            self._app.call_from_thread(self._app.write_user, renderable)

    def _write_log(self, renderable: object) -> None:
        """Forward a renderable to the Logs pane on the app thread."""
        if self._app is not None:
            self._app.call_from_thread(self._app.write_log, renderable)

    def _render_header(self, text: str) -> None:
        """Show the banner as the empty-state logo in the Session pane."""
        self._ensure()
        if self._app is not None:
            self._app.call_from_thread(self._app.set_logo, text)

    def set_experiment(self, name: str) -> None:
        """Show the running experiment name in the TUI header."""
        self._ensure()
        if self._app is not None:
            self._app.call_from_thread(self._app.set_experiment, name)

    def _render(self, message: str, level: MessageLevel) -> None:
        """Write a timestamped, level-styled message to the Session pane."""
        self._ensure()
        line = Text(f"[{_local_time()}] ", style="dim")
        line.append_text(_linkify(message, _RICH_STYLES[level]))
        self._write_user(line)

    def _on_answer(self, key: str, value: object) -> None:
        """Append an answered prompt as a timestamped line in the Session pane."""
        if value is None or value == "":
            return
        self._ensure()
        line = Text(f"[{_local_time()}] ", style="dim")
        line.append("› ", style="dim")
        line.append(f"{key}: ")
        line.append(str(value), style="bold")
        self._write_user(line)

    def _ask_text(self, request: TextRequest) -> str:
        """Request a text prompt from the app and block until answered."""
        app = self._ensure()
        reply: "queue.Queue" = queue.Queue()
        app.call_from_thread(app.ask_text, request, reply)
        return _unwrap(reply.get()) or ""

    def _ask_autocomplete(self, request: AutoCompleteRequest) -> str:
        """Request an autocomplete prompt and block until answered."""
        app = self._ensure()
        reply: "queue.Queue" = queue.Queue()
        app.call_from_thread(app.ask_autocomplete, request, reply)
        return _unwrap(reply.get()) or ""

    def _ask_pick(self, request: PickRequest) -> Optional[str]:
        """Request a pick prompt and block until answered."""
        app = self._ensure()
        reply: "queue.Queue" = queue.Queue()
        app.call_from_thread(app.ask_pick, request, reply)
        return _unwrap(reply.get())

    def _ask_confirm(self, request: ConfirmRequest) -> bool:
        """Request a confirm prompt and block until answered."""
        app = self._ensure()
        reply: "queue.Queue" = queue.Queue()
        app.call_from_thread(app.ask_confirm, request, reply)
        return bool(_unwrap(reply.get()))

    def _ask_form(self, request: FormRequest) -> Optional[object]:
        """Push the form modal and block until the user submits or cancels."""
        app = self._ensure()
        reply: "queue.Queue" = queue.Queue()
        app.call_from_thread(app.ask_form, request, reply)
        return _unwrap(reply.get())

    def prompt_form(self, request: FormRequest) -> Optional[object]:
        """Present a Pydantic model form; return the filled instance or None if cancelled."""
        result = self._ask_form(request)
        if result is not None:
            self._record(request.field or request.model.__name__, str(result))
        return result
