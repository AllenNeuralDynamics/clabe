"""Declarative prompt requests: what to ask, how to describe it to any client, and which answers are valid."""

import dataclasses
import logging
import math
import os
from collections.abc import Callable, Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, get_args, get_origin

import pydantic_core
from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    PydanticInvalidForJsonSchema,
    PydanticSchemaGenerationError,
    TypeAdapter,
    ValidationError,
    create_model,
)

from ._validation import first_error, resolve_field_default, resolve_field_type, validate_path

logger = logging.getLogger(__name__)

#: A validator takes a candidate answer and returns ``None`` when the value is
#: acceptable, or an error message (to surface to the user) when it is not.
Validator = Callable[[str], str | None]


class AnswerRejected(ValueError):
    """An answer does not satisfy its request; the message is shown to whoever answered."""


def _jsonable_fallback(value: Any) -> str:
    """Renders a value pydantic cannot serialize: paths as their filesystem string, anything else as its repr."""
    return os.fspath(value) if isinstance(value, os.PathLike) else repr(value)


def _message(exc: ValidationError) -> str:
    """Formats the first pydantic error as ``location: message``."""
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"])
    return f"{location}: {error['msg']}" if location else error["msg"]


def _boolean(value: Any) -> bool:
    """Accepts a strict boolean."""
    if not isinstance(value, bool):
        raise AnswerRejected("Expected yes or no.")
    return value


def _text(request: "TextRequest | AutoCompleteRequest", value: Any) -> str:
    """Accepts a string, falling back to the default when empty, then runs the validators."""
    if not isinstance(value, str):
        raise AnswerRejected("Expected text.")
    if value == "" and request.default is not None:
        value = request.default
    error = first_error(request.validators, value)
    if error is not None:
        raise AnswerRejected(error)
    return value


def _humanize_field(name: str) -> str:
    """Converts snake_case / kebab-case field names to Title Case."""
    return name.replace("_", " ").replace("-", " ").title()


@dataclasses.dataclass
class Request:
    """
    Base of every declarative prompt request.

    A request describes *what* to ask; it never asks. It knows its prompt kind, how to describe itself to any
    client as JSON (:meth:`spec`), the JSON Schema of a valid answer (:meth:`answer_schema`) and how to check and
    coerce an answer (:meth:`validate_answer`), so every client and the run apply the same rules.

    Attributes:
        prompt_kind: The prompt kind (``"text"``, ``"pick"``, ...), shared by every instance of a request class.
            (Not ``kind``: :class:`PathRequest` already has a ``kind`` field.)
    """

    prompt_kind: ClassVar[str]

    def spec(self) -> dict[str, Any]:
        """Returns a JSON-safe description of what to show (callables dropped, models named)."""
        return pydantic_core.to_jsonable_python(self._spec_fields(), fallback=_jsonable_fallback, bytes_mode="base64")

    def _spec_fields(self) -> dict[str, Any]:
        """The fields :meth:`spec` describes, before JSON conversion (validators and models left out)."""
        return {
            f.name: getattr(self, f.name) for f in dataclasses.fields(self) if f.name not in ("validators", "model")
        }

    def answer_schema(self) -> dict[str, Any]:
        """Returns the JSON Schema a valid answer must satisfy (no answer, by default)."""
        return {"type": "null"}

    def validate_answer(self, value: Any) -> Any:
        """Validates ``value`` and returns it coerced to the type the asker expects.

        Raises:
            AnswerRejected: The answer is not acceptable; the prompt should stay open.
        """
        raise NotImplementedError(f"{type(self).__name__} does not validate answers.")


def normalize_extensions(extensions: Sequence[str] | None) -> set[str] | None:
    """Returns ``extensions`` as lowercase, dot-prefixed suffixes, or ``None`` if unset/empty.

    The single source of truth for extension matching everywhere a
    :class:`PathRequest` is consumed — every frontend and
    :func:`~clabe.ui._validation.validate_path`
    must compare a candidate's ``Path.suffix.lower()`` against this rather than
    re-deriving its own normalized set, so a file offered by a picker is never
    subsequently rejected by validation over a case or leading-dot mismatch.
    """
    if not extensions:
        return None
    return {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions}


@dataclasses.dataclass
class Choice:
    """
    A single selectable option in a :class:`PickRequest`.

    Attributes:
        value: The value returned when this option is chosen.
        label: Optional human-readable label. Defaults to ``value``.
    """

    value: str
    label: str | None = None

    @property
    def display(self) -> str:
        """The text shown to the user for this choice."""
        return self.label if self.label is not None else self.value


@dataclasses.dataclass
class PickRequest(Request):
    """
    A declarative request to pick one option from a list.

    Frontends render this however is appropriate (a numbered list on a console,
    a dropdown/option-list in a TUI/GUI, a ``<select>`` on the web). Application
    code only describes *what* it needs, never *how* to ask.

    Attributes:
        label: The prompt shown to the user.
        options: The available options (plain strings or :class:`Choice`).
        default: Value selected by default when the frontend supports it.
        allow_none: Whether a "none" sentinel option is offered.
        none_label: Label for the "none" option when ``allow_none`` is set.
        field: Logical field name used in the persisted transcript.
        help: Optional supplementary help text.
    """

    label: str
    options: Sequence[str | Choice]
    default: str | None = None
    allow_none: bool = True
    none_label: str = "None"
    field: str | None = None
    help: str | None = None

    prompt_kind: ClassVar[str] = "pick"

    def choices(self) -> list[Choice]:
        """Normalize ``options`` into a list of :class:`Choice`."""
        return [opt if isinstance(opt, Choice) else Choice(value=opt) for opt in self.options]

    def _spec_fields(self) -> dict[str, Any]:
        """The fields to describe, with options as ``{value, label}`` pairs."""
        spec = super()._spec_fields()
        spec["options"] = [{"value": c.value, "label": c.display} for c in self.choices()]
        return spec

    def answer_schema(self) -> dict[str, Any]:
        """One of the option values, or ``null`` when the request allows it."""
        values: list[Any] = [c.value for c in self.choices()]
        return {"enum": values + [None] if self.allow_none else values}

    def validate_answer(self, value: Any) -> Any:
        """Accepts one of the option values, or ``None`` when the request allows it."""
        if value is None:
            if self.allow_none:
                return None
            raise AnswerRejected("Please choose one of the options.")
        if value not in [c.value for c in self.choices()]:
            raise AnswerRejected(f"{value!r} is not one of the options.")
        return value


@dataclasses.dataclass
class ConfirmRequest(Request):
    """
    A declarative yes/no question.

    Attributes:
        label: The question shown to the user.
        default: The value used when the user accepts the default.
        field: Logical field name used in the persisted transcript.
    """

    label: str
    default: bool = False
    field: str | None = None

    prompt_kind: ClassVar[str] = "confirm"

    def answer_schema(self) -> dict[str, Any]:
        """A boolean."""
        return {"type": "boolean"}

    def validate_answer(self, value: Any) -> bool:
        """Accepts a strict boolean."""
        return _boolean(value)


@dataclasses.dataclass
class AcknowledgeRequest(Request):
    """
    A declarative acknowledgement gate.

    Displays a message (optionally with a title) in a modal overlay and blocks
    until the user dismisses it. No answer is collected — the sole purpose is to
    ensure the user has read the message before execution continues.

    Attributes:
        message: The body text the user must read before continuing.
        title: Optional bold heading shown above the message.
        button_label: Label on the dismiss button. Defaults to ``"OK"``.
        field: Logical field name used in the persisted transcript.
    """

    message: str
    title: str = "Notice"
    button_label: str = "OK"
    field: str | None = None

    prompt_kind: ClassVar[str] = "acknowledge"

    def validate_answer(self, value: Any) -> None:
        """Accepts anything: dismissing the message is the answer (``None``)."""


@dataclasses.dataclass
class MarkdownRequest(Request):
    """
    Shows a Markdown document and blocks until the user continues (no answer is collected).

    The web UI and the TUI render the Markdown; the console prints it with Rich. Raw HTML in the document is not
    rendered (use :class:`MediaRequest` with ``media="html"`` for HTML).

    Attributes:
        markdown: The document (CommonMark, plus tables and strikethrough).
        title: Optional heading shown above it.
        button_label: Label of the button that continues.
        field: Logical field name used in the persisted transcript.
    """

    markdown: str
    title: str | None = None
    button_label: str = "Continue"
    field: str | None = None

    prompt_kind: ClassVar[str] = "markdown"

    def _spec_fields(self) -> dict[str, Any]:
        """The fields plus ``html``: the document rendered (without raw HTML) for clients that show HTML."""
        return {**super()._spec_fields(), "html": render_markdown(self.markdown)}

    def validate_answer(self, value: Any) -> None:
        """Accepts anything: continuing is the answer (``None``)."""


def render_markdown(markdown: str) -> str:
    """Renders Markdown to HTML (CommonMark with tables and strikethrough; raw HTML is escaped)."""
    from markdown_it import MarkdownIt

    return MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"]).render(markdown)


MediaKind = Literal["image", "html", "url"]


@dataclasses.dataclass
class MediaRequest(Request):
    """
    Shows an image, an HTML page or a web page, and blocks until the user continues (no answer is collected).

    The web UI shows all three: local files are served by the run server (only the file of this prompt), URLs are
    loaded by the browser and inline HTML is shown in a sandboxed frame. Clients that cannot show the media (the
    console, the TUI) log a warning naming the file or URL instead, and still wait for the user to continue.

    Attributes:
        source: A local file (image or HTML page), an ``http(s)`` URL, or — for ``media="html"`` — the HTML itself.
        media: ``"image"``, ``"html"`` (a page or a fragment) or ``"url"`` (a web page, shown in a frame when the
            site allows it).
        title: Optional heading shown above it.
        caption: Optional text shown below it.
        button_label: Label of the button that continues.
        field: Logical field name used in the persisted transcript.
    """

    source: str | Path
    media: MediaKind = "image"
    title: str | None = None
    caption: str | None = None
    button_label: str = "Continue"
    field: str | None = None

    prompt_kind: ClassVar[str] = "media"

    def __post_init__(self) -> None:
        if self.media not in get_args(MediaKind):
            raise ValueError(f"media must be one of {get_args(MediaKind)}, not {self.media!r}.")
        if self.media == "url" and self.location != "url":
            raise ValueError(f"A url media needs an http(s) URL, not {self.source!r}.")

    @property
    def location(self) -> Literal["url", "file", "inline"]:
        """Where the media is: a URL, a local file, or (HTML only) the source itself."""
        text = str(self.source)
        if isinstance(self.source, str) and text.lower().startswith(("http://", "https://")):
            return "url"
        if self.media == "html" and isinstance(self.source, str) and "<" in text:
            return "inline"
        return "file"

    def local_file(self) -> Path | None:
        """The local file to show, when the media is one and it exists."""
        if self.location != "file":
            return None
        path = Path(self.source).expanduser().resolve()
        return path if path.is_file() else None

    def describe(self) -> str:
        """A one-line description for clients that cannot show the media."""
        where = "inline HTML" if self.location == "inline" else str(self.source)
        return f"{self.media} {where}"

    def _spec_fields(self) -> dict[str, Any]:
        """The fields, with ``source`` as text and the ``location`` of the media."""
        fields = super()._spec_fields()
        fields["source"] = (
            str(self.source) if self.location != "file" else str(Path(self.source).expanduser().resolve())
        )
        fields["location"] = self.location
        return fields

    def validate_answer(self, value: Any) -> None:
        """Accepts anything: continuing is the answer (``None``)."""


@dataclasses.dataclass
class TextRequest(Request):
    """
    A declarative request for free-form text.

    Attributes:
        label: The prompt shown to the user.
        default: Value returned when the user submits an empty answer.
        multiline: Hint that a multi-line editor is appropriate.
        validators: Validators applied to the answer; the frontend re-prompts
            and surfaces the returned error until they all pass.
        field: Logical field name used in the persisted transcript.
    """

    label: str
    default: str | None = None
    multiline: bool = False
    validators: list[Validator] = dataclasses.field(default_factory=list)
    field: str | None = None

    prompt_kind: ClassVar[str] = "text"

    def answer_schema(self) -> dict[str, Any]:
        """A string."""
        return {"type": "string"}

    def validate_answer(self, value: Any) -> str:
        """Accepts a string, falling back to the default when empty, then runs the validators."""
        return _text(self, value)


@dataclasses.dataclass
class AutoCompleteRequest(Request):
    """
    A declarative request for text with autocompletion against a list.

    As the user types, frontends narrow the offered options; the user may keep
    typing a free-form value to the end, or pick a suggestion with the arrow
    keys. Unlike :class:`PickRequest`, the answer is not constrained to the
    provided options unless ``strict`` is set.

    Attributes:
        label: The prompt shown to the user.
        options: The suggestions to complete against.
        default: Value returned when the user submits an empty answer.
        strict: When set, only a value present in ``options`` is accepted.
        validators: Validators applied to the answer; the frontend re-prompts
            and surfaces the returned error until they all pass.
        field: Logical field name used in the persisted transcript.
        help: Optional supplementary help text.
    """

    label: str
    options: Sequence[str]
    default: str | None = None
    strict: bool = False
    validators: list[Validator] = dataclasses.field(default_factory=list)
    field: str | None = None
    help: str | None = None

    prompt_kind: ClassVar[str] = "autocomplete"

    def suggestions(self) -> list[str]:
        """Return ``options`` normalized to a list of strings."""
        return [str(option) for option in self.options]

    def answer_schema(self) -> dict[str, Any]:
        """A string; one of the suggestions when the request is strict."""
        return {"type": "string", "enum": self.suggestions()} if self.strict else {"type": "string"}

    def validate_answer(self, value: Any) -> str:
        """Like a text answer, but a strict request only accepts one of its suggestions (checked first)."""
        if not isinstance(value, str):
            raise AnswerRejected("Expected text.")
        if value == "" and self.default is not None:
            value = self.default
        if self.strict and value not in self.suggestions():
            raise AnswerRejected("Please choose one of the offered options.")
        return _text(self, value)


@dataclasses.dataclass
class NumberRequest(Request):
    """
    A declarative request for a floating-point number, optionally bounded.

    Attributes:
        label: The prompt shown to the user.
        default: Value returned when the user submits an empty answer.
        field: Logical field name used in the persisted transcript.
        minimum: Smallest accepted value (inclusive), if any.
        maximum: Largest accepted value (inclusive), if any.
        step: The value must be a multiple of ``step``, if set.
        widget: Rendering hint: ``"input"`` (type a value) or ``"slider"`` (pick within the bounds). Clients without
            a slider fall back to an input showing the range.
    """

    label: str
    default: float | None = None
    field: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    widget: Literal["input", "slider"] = "input"

    prompt_kind: ClassVar[str] = "number"

    def answer_schema(self) -> dict[str, Any]:
        """A number, with its bounds, step and widget hint."""
        schema: dict[str, Any] = {"type": "number"}
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        if self.step is not None:
            schema["multipleOf"] = self.step
        if self.widget != "input":
            schema["x-widget"] = self.widget
        return schema

    def validate_answer(self, value: Any) -> float:
        """Accepts a number or numeric text within the bounds; an empty answer falls back to the default."""
        if value in ("", None) and self.default is not None:
            return self.default
        if isinstance(value, bool):
            raise AnswerRejected("Please enter a valid number.")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise AnswerRejected("Please enter a valid number.") from None
        if self.minimum is not None and number < self.minimum:
            raise AnswerRejected(f"Must be ≥ {self.minimum:g}.")
        if self.maximum is not None and number > self.maximum:
            raise AnswerRejected(f"Must be ≤ {self.maximum:g}.")
        if self.step is not None and not _is_multiple(number, self.step):
            raise AnswerRejected(f"Must be a multiple of {self.step:g}.")
        return number


def _is_multiple(value: float, step: float) -> bool:
    """Whether ``value`` is a multiple of ``step``, tolerating floating-point error."""
    return math.isclose(value / step, round(value / step), abs_tol=1e-9)


@dataclasses.dataclass
class PathRequest(Request):
    """
    A declarative request to choose a filesystem path.

    Frontends render this as whatever browsing UI is appropriate (a live,
    filesystem-aware browser on a console; a modal file-browser dialog on a
    TUI/GUI). Application code only describes *what* it needs — a file or a
    directory, which extensions are acceptable, whether the target must
    already exist — never *how* to browse for it.

    Attributes:
        label: The prompt shown to the user.
        start: Directory (or partial path) to open the browser in. Defaults to
            the current working directory.
        default: Value returned when the user submits an empty answer.
        must_exist: Whether the chosen path must already exist on disk. For
            ``kind="dir"``, only honored on frontends whose picker supports
            navigating to a not-yet-created directory; the Textual frontend's
            directory dialog only ever lists existing directories, so a new
            one can't be named there regardless of this flag.
        kind: Restricts the selectable target: ``"file"``, ``"dir"``, or
            ``"any"``. ``"any"`` is more expensive to render (frontends that
            need a dedicated file-vs-folder dialog, e.g. the Textual one, ask
            an extra up-front question to disambiguate) — prefer ``"file"``/
            ``"dir"`` whenever the field's intent is known.
        extensions: When set, only files with one of these suffixes (e.g.
            ``[".json"]``) are selectable. Ignored when ``kind="dir"``.
            Matching is case-insensitive and tolerates a missing leading dot.
        field: Logical field name used in the persisted transcript.
        help: Optional supplementary help text.
    """

    label: str
    start: str | os.PathLike | None = None
    default: str | None = None
    must_exist: bool = True
    kind: Literal["file", "dir", "any"] = "file"
    extensions: Sequence[str] | None = None
    field: str | None = None
    help: str | None = None

    prompt_kind: ClassVar[str] = "path"

    def normalized_extensions(self) -> set[str] | None:
        """Returns ``extensions`` as lowercase, dot-prefixed suffixes, or ``None`` if unset.

        See :func:`normalize_extensions` — every frontend must compare against
        this (or the free function directly) rather than re-deriving its own
        normalized set, so a file offered by a picker is never subsequently
        rejected by validation over a case or leading-dot mismatch.
        """
        return normalize_extensions(self.extensions)

    def answer_schema(self) -> dict[str, Any]:
        """A path string, or ``null`` (cancelled)."""
        return {"type": ["string", "null"]}

    def validate_answer(self, value: Any) -> Path | None:
        """Accepts ``None`` (cancelled) or a path satisfying the request; an empty answer falls back to the default."""
        if value == "":
            value = self.default
        if value is None:
            return None
        path = Path(value)
        error = validate_path(self, path)
        if error is not None:
            raise AnswerRejected(error)
        return path


@dataclasses.dataclass
class FormRequest(Request):
    """
    A declarative request to fill in a Pydantic model as a form.

    Attributes:
        model: The Pydantic model class to render.
        title: Optional title override. Defaults to the humanized class name.
        initial: Optional pre-populated model instance to seed field defaults.
        field: Logical field name used in the persisted transcript.
    """

    model: type
    title: str | None = None
    initial: object | None = None
    field: str | None = None

    prompt_kind: ClassVar[str] = "form"

    def _spec_fields(self) -> dict[str, Any]:
        """The fields to describe, with the model named."""
        return {**super()._spec_fields(), "model": self.model.__name__}

    def answer_schema(self) -> dict[str, Any]:
        """The model's JSON Schema, or ``null`` (cancelled); ``{}`` (anything) when pydantic cannot describe it."""
        try:
            return {"anyOf": [self.model.model_json_schema(), {"type": "null"}]}
        except (PydanticInvalidForJsonSchema, PydanticSchemaGenerationError) as exc:
            logger.debug("No answer schema for form prompt on %s: %s", self.model.__name__, exc)
            return {}

    def validate_answer(self, value: Any) -> Any:
        """Accepts ``None`` (cancelled), a model instance, or data that validates as the model."""
        if value is None or isinstance(value, self.model):
            return value
        try:
            return self.model.model_validate(value)
        except ValidationError as exc:
            raise AnswerRejected(_message(exc)) from None


@dataclasses.dataclass
class FieldRequest(Request):
    """
    A declarative request to enter a single field from a Pydantic model.

    The asker resolves it (:meth:`resolve`) into the most appropriate primitive
    request from the field's type annotation: strict autocomplete for
    ``Literal`` / ``Enum`` fields, yes/no for ``bool``, a path for ``Path`` and
    a validated text prompt for everything else. Clients never see it.

    Attributes:
        model: The Pydantic model class that owns the field.
        field_name: The name of the field to prompt for.
        initial: Optional value to use as the pre-filled default (overrides the
            field's declared default).
    """

    model: type
    field_name: str
    initial: object | None = None

    prompt_kind: ClassVar[str] = "field"

    def _spec_fields(self) -> dict[str, Any]:
        """The fields to describe, with the model named."""
        return {**super()._spec_fields(), "model": self.model.__name__}

    def _adapter(self) -> TypeAdapter:
        """Builds a validator for the requested model field, including its constraints (e.g. ``gt=0``)."""
        info = self.model.model_fields[self.field_name]
        annotation = Annotated[(info.annotation, *info.metadata)] if info.metadata else info.annotation
        return TypeAdapter(annotation)

    def answer_schema(self) -> dict[str, Any]:
        """The field's JSON Schema; ``{}`` (anything) when pydantic cannot describe it."""
        try:
            return self._adapter().json_schema()
        except (PydanticInvalidForJsonSchema, PydanticSchemaGenerationError) as exc:
            logger.debug("No answer schema for field prompt on %s: %s", self.model.__name__, exc)
            return {}

    def validate_answer(self, value: Any) -> Any:
        """Validates a field value answered directly (as :meth:`answer_schema` describes it).

        An empty answer falls back to the default, else ``None`` if the field is optional. Askers do not open field
        prompts: they ask the request :meth:`resolve` returns, whose answers are validated by that request.
        """
        if value == "":
            info = self.model.model_fields[self.field_name]
            default = resolve_field_default(info, self.initial)
            if default is not None:
                value = default
            elif resolve_field_type(info.annotation)[1]:
                value = None
        try:
            return self._adapter().validate_python(value)
        except ValidationError as exc:
            raise AnswerRejected(_message(exc)) from None

    def resolve(self) -> tuple[Request, Callable[[Any], Any]]:
        """Returns the primitive request to ask for this field, and how to turn its answer into the field value.

        Routing rules:

        * ``bool`` → yes/no confirm.
        * ``Literal[...]`` → strict autocomplete on the literal values (as text); converts back to the literal.
        * ``Enum`` → strict autocomplete on the member names; converts to the member.
        * ``Path`` → any path, which need not exist (a plain ``Path`` field does not imply it does).
        * Anything else → text validated by the field's type and constraints; converts to the typed value. An
          empty answer (or the default's text) gives the default value itself, else ``None`` for an optional field.

        Returns:
            The primitive request, and a function mapping its validated answer to the typed field value.
        """
        info = self.model.model_fields[self.field_name]
        inner, is_optional = resolve_field_type(info.annotation)
        label = getattr(info, "title", None) or _humanize_field(self.field_name)
        default = resolve_field_default(info, self.initial)
        default_str = str(default) if default is not None else None
        field = self.field_name

        if inner is bool:
            confirm = ConfirmRequest(label=label, default=bool(default) if default is not None else False, field=field)
            return confirm, _identity

        if get_origin(inner) is Literal:
            values = get_args(inner)

            def to_literal(answer: Any) -> Any:
                return next((v for v in values if str(v) == answer), answer)

            options = [str(v) for v in values]
            return AutoCompleteRequest(
                label=label, options=options, default=default_str, strict=True, field=field
            ), to_literal

        if isinstance(inner, type) and issubclass(inner, Enum):
            enum_type = inner
            if isinstance(default, Enum):
                default_str = default.name
            options = [m.name for m in enum_type]
            return AutoCompleteRequest(
                label=label, options=options, default=default_str, strict=True, field=field
            ), lambda a: enum_type[a]

        if isinstance(inner, type) and issubclass(inner, Path):
            path = PathRequest(
                label=label, start=default_str, default=default_str, must_exist=False, kind="any", field=field
            )
            return path, _identity

        adapter = self._adapter()

        def typed(raw: str) -> Any:
            # An empty answer becomes ``default_str`` (the text request's default): give back the real default, not
            # its text round-tripped (``str([1, 2])`` is not a list; ``str(SecretStr(...))`` is its mask).
            if default is not None and raw in ("", default_str):
                return default
            return adapter.validate_python(None if (is_optional and raw == "") else raw)

        def check(raw: str) -> str | None:
            try:
                typed(raw)
                return None
            except ValidationError as exc:
                errors = exc.errors()
                return errors[0]["msg"] if errors else "Invalid value."
            except Exception as exc:  # noqa: BLE001 -- any validator failure must surface as a re-promptable message
                return str(exc)

        return TextRequest(label=label, default=default_str, validators=[check], field=field), typed


def _identity(value: Any) -> Any:
    """Returns ``value`` unchanged (a resolved field whose answer already is the field value)."""
    return value


@dataclasses.dataclass
class ReadOnlyTable(Request):
    """
    A declarative request to display tabular data read-only and collect a yes/no.

    The table is never editable; it exists to show the user a set of values and
    gather a single confirmation. The affirmative button returns ``True`` and the
    negative button returns ``False`` (as does dismissing the dialog).

    Prefer the constructors over populating ``columns`` and ``rows`` by hand:

    * :meth:`from_records` — a sequence of mappings, one row each; columns are
      inferred from the keys (first-seen order) unless given explicitly.
    * :meth:`from_object` — a Pydantic model instance or a plain mapping rendered
      as a two-column ``Parameter | Value`` table, one row per field/key.

    Attributes:
        columns: Column headers, left to right.
        rows: Row values; each inner sequence aligns positionally to ``columns``.
        title: Optional title shown above the table.
        prompt: Optional question shown near the buttons (e.g. "Is this correct?").
        confirm_label: Label on the affirmative button. Defaults to ``"OK"``.
        cancel_label: Label on the negative button. Defaults to ``"Cancel"``.
        field: Logical field name used in the persisted transcript.
    """

    columns: Sequence[str]
    rows: Sequence[Sequence[Any]]
    title: str | None = None
    prompt: str | None = None
    confirm_label: str = "OK"
    cancel_label: str = "Cancel"
    field: str | None = None

    prompt_kind: ClassVar[str] = "table"

    def answer_schema(self) -> dict[str, Any]:
        """A boolean."""
        return {"type": "boolean"}

    def validate_answer(self, value: Any) -> bool:
        """Accepts a strict boolean."""
        return _boolean(value)

    @classmethod
    def from_records(
        cls,
        records: Sequence[Mapping[str, Any]],
        *,
        columns: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> "ReadOnlyTable":
        """
        Build a table from a sequence of mappings (one row per mapping).

        When ``columns`` is omitted it is inferred from the union of the record
        keys, preserving first-seen order. Keys absent from a given record render
        as empty cells.
        """
        records = list(records)
        if columns is None:
            seen: dict = {}
            for record in records:
                for key in record:
                    seen.setdefault(key, None)
            columns = list(seen)
        else:
            columns = list(columns)
        rows = [[record.get(column, "") for column in columns] for record in records]
        return cls(columns=columns, rows=rows, **kwargs)

    @classmethod
    def from_object(
        cls,
        obj: Mapping[str, Any] | BaseModel,
        *,
        key_header: str = "Parameter",
        value_header: str = "Value",
        **kwargs: Any,
    ) -> "ReadOnlyTable":
        """
        Build a two-column ``Parameter | Value`` table from a model or mapping.

        Accepts a Pydantic model instance or a plain mapping; each field/key
        becomes one row.
        """
        if isinstance(obj, BaseModel):
            data = obj.model_dump()
        elif isinstance(obj, Mapping):
            data = dict(obj)
        else:
            raise TypeError(f"from_object expects a Pydantic model or mapping, got {type(obj).__name__}.")
        rows = [[str(key), value] for key, value in data.items()]
        return cls(columns=[key_header, value_header], rows=rows, **kwargs)


#: Prompt kinds that cannot be items of a group (they are whole interactions of their own).
_NOT_GROUPABLE = frozenset({"group", "form", "table", "acknowledge", "markdown", "media"})


def _validators_check(validators: Sequence[Validator]) -> AfterValidator:
    """A pydantic after-validator running a text request's validators."""

    def check(value: str) -> str:
        error = first_error(validators, value)
        if error is not None:
            raise ValueError(error)
        return value

    return AfterValidator(check)


def _path_check(request: "PathRequest") -> AfterValidator:
    """A pydantic after-validator applying a path request's rules."""

    def check(value: Path | None) -> Path | None:
        if value is None:
            return None
        error = validate_path(request, value)
        if error is not None:
            raise ValueError(error)
        return value

    return AfterValidator(check)


def _with_default(default: Any, **kwargs: Any) -> Any:
    """A ``Field`` that is required when ``default`` is ``None``, else defaults to it."""
    return Field(**kwargs) if default is None else Field(default=default, **kwargs)


def _group_field(item: Request) -> tuple[Any, Any]:
    """The pydantic ``(annotation, FieldInfo)`` for one item of a group."""
    if isinstance(item, FieldRequest):
        info = item.model.model_fields[item.field_name]
        annotation = Annotated[(info.annotation, *info.metadata)] if info.metadata else info.annotation
        default = resolve_field_default(info, item.initial)
        title = info.title or _humanize_field(item.field_name)
        if default is None and info.is_required():
            return annotation, Field(title=title, description=info.description)
        return annotation, Field(default=default, title=title, description=info.description)
    title = getattr(item, "label", None)
    if isinstance(item, ConfirmRequest):
        return bool, Field(default=item.default, title=title)
    if isinstance(item, PickRequest):
        values = tuple(choice.value for choice in item.choices())
        if item.allow_none:
            return Literal[values] | None, Field(default=item.default, title=title)
        return Literal[values], _with_default(item.default, title=title)
    if isinstance(item, AutoCompleteRequest) and item.strict:
        return Literal[tuple(item.suggestions())], _with_default(item.default, title=title)
    if isinstance(item, (TextRequest, AutoCompleteRequest)):
        annotation = Annotated[str, _validators_check(item.validators)] if item.validators else str
        return annotation, Field(default=item.default if item.default is not None else "", title=title)
    if isinstance(item, NumberRequest):
        description = None
        if item.minimum is not None and item.maximum is not None:
            description = f"Between {item.minimum:g} and {item.maximum:g}"
        return float, _with_default(
            item.default,
            ge=item.minimum,
            le=item.maximum,
            multiple_of=item.step,
            title=title,
            description=description,
            json_schema_extra={"x-widget": item.widget} if item.widget != "input" else None,
        )
    if isinstance(item, PathRequest):
        default = Path(item.default) if item.default else None
        return Annotated[Path | None, _path_check(item)], Field(default=default, title=title)
    raise ValueError(f"A {item.prompt_kind!r} prompt cannot be part of a group.")


@dataclasses.dataclass
class GroupRequest(Request):
    """
    Several prompts asked together as one: e.g. a text input and a slider shown side by side.

    The run opens a single prompt; the answer maps each item name to its validated value. Items may be text, number,
    confirm, pick, autocomplete, path and field requests (not groups, forms, tables or acknowledgements).

    Example:
        ```python
        from clabe import ui

        answer = ui.group(
            "Session",
            notes=ui.TextRequest(label="Notes"),
            gain=ui.NumberRequest(label="Gain", minimum=0, maximum=1, step=0.05, widget="slider"),
        )
        answer["gain"]  # a float in [0, 1]
        ```

    Attributes:
        items: The prompts, by name (each name must be a Python identifier).
        title: Optional title shown above the group.
        field: Logical field name used in the persisted transcript.
    """

    items: Mapping[str, Request]
    title: str | None = None
    field: str | None = None

    prompt_kind: ClassVar[str] = "group"

    def __post_init__(self) -> None:
        for name, item in self.items.items():
            if not name.isidentifier():
                raise ValueError(f"Group item name {name!r} must be a Python identifier.")
            if item.prompt_kind in _NOT_GROUPABLE:
                raise ValueError(f"A {item.prompt_kind!r} prompt cannot be part of a group ({name!r}).")
        self._model: type[BaseModel] | None = None

    def model(self) -> type[BaseModel]:
        """The pydantic model the group compiles to (one field per item); clients render it as a form."""
        if self._model is None:
            fields = {name: _group_field(item) for name, item in self.items.items()}
            self._model = create_model("Group", **fields)
        return self._model

    def _spec_fields(self) -> dict[str, Any]:
        """The title and the description of every item (its prompt kind as ``prompt_kind``: a path's spec has its own
        ``kind``)."""
        items = {name: {**item.spec(), "prompt_kind": item.prompt_kind} for name, item in self.items.items()}
        return {"title": self.title, "field": self.field, "items": items}

    def answer_schema(self) -> dict[str, Any]:
        """The compiled model's JSON Schema; ``{}`` (anything) when pydantic cannot describe it."""
        try:
            return self.model().model_json_schema()
        except (PydanticInvalidForJsonSchema, PydanticSchemaGenerationError) as exc:
            logger.debug("No answer schema for group prompt: %s", exc)
            return {}

    def validate_answer(self, value: Any) -> dict[str, Any]:
        """Accepts a mapping (or an instance of the compiled model) and returns each item's typed value."""
        model = self.model()
        if isinstance(value, BaseModel):
            value = {name: getattr(value, name) for name in self.items if hasattr(value, name)}
        try:
            validated = model.model_validate(value)
        except ValidationError as exc:
            raise AnswerRejected(_message(exc)) from None
        return {name: getattr(validated, name) for name in self.items}
