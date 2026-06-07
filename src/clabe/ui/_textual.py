import contextlib
import datetime
import logging
import os
import queue
import re
import tempfile
import threading
from pathlib import Path
import typing
from enum import Enum
from typing import Annotated, Any, Iterator, List, Literal, Optional, get_args, get_origin

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, DirectoryTree, Footer, Header, Input, Label, OptionList, RichLog, Select, Static, Switch
from textual.screen import ModalScreen

from pydantic import ValidationError
from pydantic_core import PydanticUndefined

from .. import __version__
from ..logging_helper import _TRANSCRIPT_LOGGER_NAME
from ._frontend import FrontendBase
from ._messages import MessageLevel
from ._requests import AutoCompleteRequest, ConfirmRequest, FormRequest, PickRequest, TextRequest

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


#: Path-like tokens: a drive root (``C:\\a``), a rooted path (``/a/b``), or a
#: multi-segment relative path (``a/b``). The leading guard skips URL fragments
#: (e.g. ``http://host/x``); only tokens that resolve to an existing path are
#: linked, which filters out incidental ``a/b`` text.
_PATH_RE = re.compile(
    r"(?<![:\w/\\])"
    r"(?:[A-Za-z]:[\\/][\w.\-]+(?:[\\/][\w.\-]+)*"
    r"|[\\/][\w.\-]+(?:[\\/][\w.\-]+)*"
    r"|[\w.\-]+(?:[\\/][\w.\-]+)+)"
)


def _linkify(message: str, style: str) -> Text:
    """Render ``message`` with existing file paths as OSC 8 ``file://`` hyperlinks.

    Clickable in terminals that support OSC 8 (e.g. Windows Terminal); elsewhere
    the path just renders as underlined text. Relative paths are resolved against
    the working directory, and only paths that actually exist are linked.
    """
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
    BINDINGS = [
        Binding("ctrl+c", "cancel", "Exit", priority=True),
        Binding("ctrl+s", "screenshot", "Screenshot", priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.ready = threading.Event()
        self._pending: Optional["queue.Queue"] = None
        self._kind: Optional[str] = None
        self._pick_values: List[object] = []
        self._auto_all: List[str] = []

    def compose(self) -> ComposeResult:
        """Builds the layout: a persistent header, the four panes, and a footer."""
        yield Header()
        with Vertical(id="clabe-user"):
            yield Static("", id="clabe-logo")
            yield RichLog(id="clabe-user-log", highlight=False, wrap=True, auto_scroll=True)
        yield Vertical(id="clabe-processes")
        yield Vertical(id="clabe-prompt")
        yield RichLog(id="clabe-logs", highlight=False, wrap=True, auto_scroll=True)
        yield Footer()

    def on_mount(self) -> None:
        """Titles the panes/header and signals that the app is ready for input."""
        self.title = "clabe"
        self.sub_title = f"v{__version__}"
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

    def set_experiment(self, name: str) -> None:
        """Shows the running experiment's name alongside the version in the header."""
        self.sub_title = f"v{__version__} · {name}"

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

    async def ask_form(self, request: FormRequest, reply: "queue.Queue") -> None:
        """Pushes a form modal; delivers the instance, None (graceful cancel), or _CANCELLED via reply."""
        self._pending = reply
        self._kind = "form"

        async def _on_dismiss(result: object) -> None:
            if self._pending is reply:
                self._pending = None
                self._kind = None
                reply.put(result)

        await self.push_screen(_FormScreen(request), _on_dismiss)

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

    def action_screenshot(self) -> None:
        """Saves an SVG screenshot of the whole window and notes where it went.

        Saved to the OS temp directory so it works regardless of the launcher's
        working directory.
        """
        path = self.save_screenshot(path=tempfile.gettempdir())
        line = Text(f"[{_local_time()}] ", style="dim")
        line.append_text(_linkify(f"Saved screenshot to {path}", _RICH_STYLES[MessageLevel.SUCCESS]))
        self.write_user(line)


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
        self._frontend._write_log(_linkify(message, _log_style(record.levelno)))


def _unwrap(value: object) -> object:
    """Translates the cancellation sentinel into ``KeyboardInterrupt``."""
    if value is _CANCELLED:
        raise KeyboardInterrupt("Cancelled by user.")
    return value


# ---------------------------------------------------------------------------
# Form helpers
# ---------------------------------------------------------------------------

def _humanize(name: str) -> str:
    """Converts CamelCase or snake_case to Title Case."""
    name = re.sub(r"(?<=[a-z0-9])([A-Z])", r" \1", name)
    return name.replace("_", " ").replace("-", " ").title()


def _resolve_form_type(annotation: Any) -> tuple[Any, bool]:
    """Returns (inner_type, is_optional) after stripping Annotated and Optional wrappers."""
    if get_origin(annotation) is typing.Annotated:
        annotation = get_args(annotation)[0]
    if get_origin(annotation) is typing.Union:
        args = get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0], True
    return annotation, False


def _field_label(field_name: str, field_info: Any, is_optional: bool) -> str:
    """Returns a human-readable label, appending '*' for required fields."""
    base = getattr(field_info, "title", None) or _humanize(field_name)
    required = not is_optional and getattr(field_info, "is_required", lambda: False)()
    return f"{base} *" if required else base


def _field_default(field_name: str, field_info: Any, initial: Any) -> Any:
    """Returns the initial value from the instance (preferred) or the field default."""
    if initial is not None:
        try:
            return getattr(initial, field_name)
        except AttributeError:
            pass
    default = getattr(field_info, "default", PydanticUndefined)
    if default is not PydanticUndefined:
        return default
    factory = getattr(field_info, "default_factory", None)
    if factory is not None:
        return factory()
    return None


def _build_field_widget(field_name: str, inner_type: Any, initial: Any) -> "Widget":
    """Creates the appropriate Textual widget for a Pydantic field type."""
    wid = f"field-{field_name}"

    if inner_type is bool:
        return Switch(value=bool(initial) if initial is not None else False, id=wid)

    if get_origin(inner_type) is Literal:
        opts = [(str(v), v) for v in get_args(inner_type)]
        return Select(opts, value=initial if initial is not None else Select.NULL, id=wid, allow_blank=True)

    if isinstance(inner_type, type) and issubclass(inner_type, Enum):
        opts = [(m.name, m) for m in inner_type]
        return Select(opts, value=initial if initial is not None else Select.NULL, id=wid, allow_blank=True)

    if isinstance(inner_type, type) and issubclass(inner_type, Path):
        return Input(value=str(initial) if initial is not None else "", id=wid, placeholder="Enter path…")

    if inner_type is int:
        return Input(value=str(initial) if initial is not None else "", id=wid, restrict=r"-?[0-9]*", placeholder="Integer")

    if inner_type is float:
        return Input(value=str(initial) if initial is not None else "", id=wid, restrict=r"-?[0-9]*\.?[0-9]*", placeholder="Number")

    return Input(value=str(initial) if initial is not None else "", id=wid)


def _read_field_widget(widget: Any, is_optional: bool) -> Any:
    """Reads the current value from a field widget as a Python object."""
    if isinstance(widget, Switch):
        return widget.value
    if isinstance(widget, Select):
        v = widget.value
        return None if v is Select.NULL else v
    if isinstance(widget, Input):
        raw = widget.value.strip()
        return None if raw == "" else raw
    return None


def _path_completions(partial: str, max_results: int = 12) -> list[str]:
    """Return filesystem paths that complete *partial*, up to *max_results* entries."""
    if not partial:
        return []
    p = Path(partial)
    try:
        if partial[-1] in ("/", "\\"):
            parent, prefix = p, ""
        else:
            parent, prefix = p.parent, p.name.lower()
        if not parent.exists():
            return []
        return sorted(
            str(child) for child in parent.iterdir()
            if child.name.lower().startswith(prefix)
        )[:max_results]
    except (PermissionError, OSError):
        return []


# ---------------------------------------------------------------------------
# CSS constants for modal screens
# ---------------------------------------------------------------------------

_FILE_PICKER_CSS = """
_FilePickerScreen { align: center middle; }
#picker-container {
    width: 80%; height: 80%;
    background: $surface; border: thick $primary; padding: 1 2;
}
#picker-path { margin-bottom: 1; }
#picker-tree { height: 1fr; border: round $primary; }
#picker-buttons { height: 3; margin-top: 1; align-horizontal: right; }
#picker-buttons Button { margin-left: 1; }
"""

_FORM_CSS = """
_FormScreen { align: center middle; }
#form-outer {
    width: 80%; height: 80%;
    background: $surface; border: round $primary;
}
#form-header {
    height: 3; padding: 0 1;
    border-bottom: solid $primary;
    align-vertical: middle;
}
#form-title {
    width: 1fr; color: $accent; text-style: bold;
    content-align: left middle;
}
#form-close { width: auto; min-width: 5; }
#form-scroll { height: 1fr; padding: 1 2; }
.form-field { height: auto; margin-bottom: 1; }
.field-label { height: 1; }
.field-error { display: none; color: $error; height: 1; padding: 0; margin: 0; }
.field-error.--active { display: block; }
.path-row { height: 3; }
.path-row Input { width: 1fr; height: 3; }
.browse-btn { width: 12; min-width: 12; margin-left: 1; height: 3; }
.path-complete { display: none; max-height: 8; border: none; padding: 0; margin: 0; }
.path-complete.--active { display: block; }
"""

_HELP_POPUP_CSS = """
_HelpPopup { align: center middle; }
#help-box {
    width: 60; height: auto;
    background: $surface; border: round $accent; padding: 1 2;
}
#help-title { color: $accent; text-style: bold; margin-bottom: 1; }
#help-body { margin-bottom: 1; }
#help-ok { width: 100%; }
"""


# ---------------------------------------------------------------------------
# _HelpPopup
# ---------------------------------------------------------------------------

class _HelpPopup(ModalScreen):
    """Field-help dialog shown on F1; dismissed with Enter, Space, Escape, or F1."""

    DEFAULT_CSS = _HELP_POPUP_CSS
    BINDINGS = [
        Binding("enter", "ok", "", show=False),
        Binding("space", "ok", "", show=False),
        Binding("escape", "ok", "", show=False),
        Binding("f1", "ok", "", show=False),
    ]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Label(self._title, id="help-title")
            yield Label(self._body, id="help-body")
            yield Button("OK  [Enter]", id="help-ok", variant="primary")

    async def on_button_pressed(self, _: Button.Pressed) -> None:
        self.dismiss()

    def action_ok(self) -> None:
        self.dismiss()


# ---------------------------------------------------------------------------
# _FilePickerScreen
# ---------------------------------------------------------------------------

class _FilePickerScreen(ModalScreen):
    """Modal for browsing and selecting a filesystem path using DirectoryTree."""

    DEFAULT_CSS = _FILE_PICKER_CSS
    BINDINGS = [Binding("escape", "cancel_picker", "Cancel")]

    def __init__(self, start: str = "") -> None:
        super().__init__()
        self._start = start or str(Path.home())

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-container"):
            yield Label("Browse", id="picker-title")
            yield Input(value=self._start, id="picker-path", placeholder="Type a path or browse below…")
            yield DirectoryTree(self._start, id="picker-tree")
            with Horizontal(id="picker-buttons"):
                yield Button("Cancel", id="picker-cancel", variant="default")
                yield Button("Select", id="picker-select", variant="primary")

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.query_one("#picker-path", Input).value = str(event.path)

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self.query_one("#picker-path", Input).value = str(event.path)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "picker-select":
            raw = self.query_one("#picker-path", Input).value.strip()
            self.dismiss(Path(raw) if raw else None)
        elif event.button.id == "picker-cancel":
            self.dismiss(None)

    def action_cancel_picker(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# _FormScreen
# ---------------------------------------------------------------------------

class _FormScreen(ModalScreen):
    """Full-screen form that renders and validates a Pydantic model."""

    DEFAULT_CSS = _FORM_CSS
    BINDINGS = [
        Binding("f1", "show_help", "Field help"),
        Binding("f5", "submit_form", "Submit"),
        Binding("escape", "close_form", "Close"),
    ]

    def __init__(self, request: FormRequest) -> None:
        super().__init__()
        self._model = request.model
        self._title_text = request.title or _humanize(request.model.__name__)
        self._initial = request.initial
        self._field_widgets: dict[str, Any] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="form-outer"):
            with Horizontal(id="form-header"):
                yield Label(self._title_text, id="form-title")
                yield Button("✕", id="form-close", variant="default")
            with ScrollableContainer(id="form-scroll"):
                for field_name, field_info in self._model.model_fields.items():
                    annotation = field_info.annotation
                    inner_type, is_optional = _resolve_form_type(annotation)
                    label_text = _field_label(field_name, field_info, is_optional)
                    default = _field_default(field_name, field_info, self._initial)
                    widget = _build_field_widget(field_name, inner_type, default)
                    is_path = isinstance(inner_type, type) and issubclass(inner_type, Path)

                    with Vertical(classes="form-field"):
                        yield Label(label_text, classes="field-label")
                        if is_path:
                            with Horizontal(classes="path-row"):
                                yield widget
                                yield Button("Browse…", id=f"browse-{field_name}", classes="browse-btn")
                            yield OptionList(id=f"complete-{field_name}", classes="path-complete")
                        else:
                            yield widget
                        yield Label("", id=f"error-{field_name}", classes="field-error")
            yield Footer()

    def on_mount(self) -> None:
        self._field_order = list(self._model.model_fields.keys())
        self._path_fields: set[str] = set()
        for field_name, field_info in self._model.model_fields.items():
            inner_type, _ = _resolve_form_type(field_info.annotation)
            if isinstance(inner_type, type) and issubclass(inner_type, Path):
                self._path_fields.add(field_name)
            try:
                self._field_widgets[field_name] = self.query_one(f"#field-{field_name}")
            except Exception:
                pass
        # Focus the first field, not the ✕ button
        if self._field_order:
            first = self._field_widgets.get(self._field_order[0])
            if first is not None:
                first.focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Validate the field on Enter and advance focus; only F5 submits the form."""
        event.stop()  # prevent bubbling to _LauncherApp
        wid = event.input.id or ""
        if not wid.startswith("field-"):
            return
        field_name = wid[len("field-"):]
        if not self._validate_field(field_name, event.value):
            return
        self._focus_next_field(field_name)

    def _focus_next_field(self, current_field: str) -> None:
        """Move focus to the widget for the field after ``current_field``."""
        try:
            idx = self._field_order.index(current_field)
        except ValueError:
            return
        next_idx = idx + 1
        if next_idx < len(self._field_order):
            widget = self._field_widgets.get(self._field_order[next_idx])
            if widget is not None:
                widget.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Clear the inline error and refresh path completions as the user edits."""
        wid = event.input.id or ""
        if not wid.startswith("field-"):
            return
        field_name = wid[len("field-"):]
        self._clear_field_error(field_name)
        if field_name in self._path_fields:
            self._update_path_completions(field_name, event.value)

    def _update_path_completions(self, field_name: str, partial: str) -> None:
        try:
            opts = self.query_one(f"#complete-{field_name}", OptionList)
        except Exception:
            return
        completions = _path_completions(partial)
        opts.clear_options()
        if completions:
            opts.add_options(completions)
            opts.add_class("--active")
        else:
            opts.remove_class("--active")

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Selecting a path completion fills the input and triggers another completion pass."""
        opt_id = event.option_list.id or ""
        if not opt_id.startswith("complete-"):
            return
        field_name = opt_id[len("complete-"):]
        path_input = self._field_widgets.get(field_name)
        if not isinstance(path_input, Input):
            return
        selected = str(event.option.prompt)
        # Append a separator when the selection is a directory so the next keystroke
        # immediately lists its children rather than completing its own name again.
        if Path(selected).is_dir():
            selected = selected + os.sep
        path_input.value = selected
        path_input.focus()

    def _show_field_error(self, field_name: str, message: str) -> None:
        try:
            lbl = self.query_one(f"#error-{field_name}", Label)
            lbl.update(f"⚠ {message}")
            lbl.add_class("--active")
        except Exception:
            pass

    def _clear_field_error(self, field_name: str) -> None:
        try:
            lbl = self.query_one(f"#error-{field_name}", Label)
            lbl.update("")
            lbl.remove_class("--active")
        except Exception:
            pass

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id.startswith("browse-"):
            field_name = btn_id[len("browse-"):]
            path_input = self.query_one(f"#field-{field_name}", Input)
            # Walk up from the typed path until we find a directory that exists,
            # so DirectoryTree always starts with a valid root.
            raw = path_input.value.strip()
            candidate = Path(raw).expanduser().resolve() if raw else Path.home()
            while not candidate.is_dir() and candidate != candidate.parent:
                candidate = candidate.parent
            if not candidate.is_dir():
                candidate = Path.home()

            def _on_pick(result: Optional[Path]) -> None:
                if result is not None:
                    path_input.value = str(result)

            self.app.push_screen(_FilePickerScreen(str(candidate)), _on_pick)
        elif btn_id == "form-close":
            self.dismiss(None)

    async def action_submit_form(self) -> None:
        await self._submit()

    def action_close_form(self) -> None:
        self.dismiss(None)

    def _current_field_name(self) -> Optional[str]:
        """Returns the field name whose widget currently has focus, or None."""
        focused = self.focused
        if focused is None:
            return None
        fid = getattr(focused, "id", None) or ""
        return fid[len("field-"):] if fid.startswith("field-") else None

    def action_show_help(self) -> None:
        name = self._current_field_name()
        if name is None:
            return
        fi = self._model.model_fields.get(name)
        if fi is None:
            return
        label = getattr(fi, "title", None) or _humanize(name)
        desc = getattr(fi, "description", None) or "No description available for this field."
        self.app.push_screen(_HelpPopup(label, desc))

    def _validate_field(self, field_name: str, raw: str) -> bool:
        """Validates a single field against its Pydantic type; shows an inline error if invalid."""
        from pydantic import TypeAdapter
        from pydantic import ValidationError as _PydanticError

        field_info = self._model.model_fields.get(field_name)
        if field_info is None:
            return True
        base_annotation = field_info.annotation
        _, is_optional = _resolve_form_type(base_annotation)
        # Include field-level metadata (e.g. Gt, Lt constraints) in the validator.
        if field_info.metadata:
            annotation = Annotated[tuple([base_annotation] + list(field_info.metadata))]
        else:
            annotation = base_annotation
        val: Any = None if (is_optional and raw.strip() == "") else raw.strip()
        label = getattr(field_info, "title", None) or _humanize(field_name)
        try:
            TypeAdapter(annotation).validate_python(val)
            self._clear_field_error(field_name)
            return True
        except _PydanticError as exc:
            errs = exc.errors()
            msg = errs[0]["msg"] if errs else "Invalid value"
            self._show_field_error(field_name, f"{label}: {msg}")
            widget = self._field_widgets.get(field_name)
            if widget is not None:
                widget.focus()
            return False

    async def _submit(self) -> None:
        """Collects all field values, validates the whole model, dismisses or shows inline error."""
        for field_name in self._field_order:
            self._clear_field_error(field_name)

        data: dict[str, Any] = {}
        for field_name, field_info in self._model.model_fields.items():
            annotation = field_info.annotation
            _, is_optional = _resolve_form_type(annotation)
            widget = self._field_widgets.get(field_name)
            data[field_name] = _read_field_widget(widget, is_optional) if widget is not None else None

        try:
            instance = self._model.model_validate(data)
            self.dismiss(instance)
        except ValidationError as exc:
            errors = exc.errors()
            if errors:
                first = errors[0]
                loc = first.get("loc", ())
                msg = first.get("msg", "Invalid value")
                field_name = str(loc[0]) if loc else ""
                if field_name:
                    fi = self._model.model_fields.get(field_name)
                    label = (getattr(fi, "title", None) or _humanize(field_name)) if fi else field_name
                    self._show_field_error(field_name, f"{label}: {msg}")
                    widget = self._field_widgets.get(field_name)
                    if widget is not None:
                        widget.focus()


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

        from ..runnable import get_activity_indicator

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

    def set_experiment(self, name: str) -> None:
        """Shows the running experiment's name in the header."""
        self._ensure()
        if self._app is not None:
            self._app.call_from_thread(self._app.set_experiment, name)

    def _render(self, message: str, level: MessageLevel) -> None:
        """Writes a timestamped, level-styled message to the Session pane."""
        self._ensure()
        line = Text(f"[{_local_time()}] ", style="dim")
        line.append_text(_linkify(message, _RICH_STYLES[level]))
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

    def _ask_form(self, request: FormRequest) -> Optional[object]:
        """Pushes the form modal and blocks until the user submits, cancels, or Ctrl+C."""
        app = self._ensure()
        reply: "queue.Queue" = queue.Queue()
        app.call_from_thread(app.ask_form, request, reply)
        return _unwrap(reply.get())

    def prompt_form(self, request: FormRequest) -> Optional[object]:
        """Presents a Pydantic model form and returns the filled instance, or None if cancelled."""
        result = self._ask_form(request)
        if result is not None:
            self._record(request.field or request.model.__name__, str(result))
        return result
