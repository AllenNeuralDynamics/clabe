import dataclasses
from typing import Any, Callable, List, Mapping, Optional, Sequence, Union

from pydantic import BaseModel

#: A validator takes a candidate answer and returns ``None`` when the value is
#: acceptable, or an error message (to surface to the user) when it is not.
Validator = Callable[[str], Optional[str]]


@dataclasses.dataclass
class Choice:
    """
    A single selectable option in a :class:`PickRequest`.

    Attributes:
        value: The value returned when this option is chosen.
        label: Optional human-readable label. Defaults to ``value``.
    """

    value: str
    label: Optional[str] = None

    @property
    def display(self) -> str:
        """The text shown to the user for this choice."""
        return self.label if self.label is not None else self.value


@dataclasses.dataclass
class PickRequest:
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
    options: Sequence[Union[str, Choice]]
    default: Optional[str] = None
    allow_none: bool = True
    none_label: str = "None"
    field: Optional[str] = None
    help: Optional[str] = None

    def choices(self) -> List[Choice]:
        """Normalize ``options`` into a list of :class:`Choice`."""
        return [opt if isinstance(opt, Choice) else Choice(value=opt) for opt in self.options]


@dataclasses.dataclass
class ConfirmRequest:
    """
    A declarative yes/no question.

    Attributes:
        label: The question shown to the user.
        default: The value used when the user accepts the default.
        field: Logical field name used in the persisted transcript.
    """

    label: str
    default: bool = False
    field: Optional[str] = None


@dataclasses.dataclass
class AcknowledgeRequest:
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
    field: Optional[str] = None


@dataclasses.dataclass
class TextRequest:
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
    default: Optional[str] = None
    multiline: bool = False
    validators: List[Validator] = dataclasses.field(default_factory=list)
    field: Optional[str] = None


@dataclasses.dataclass
class AutoCompleteRequest:
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
    default: Optional[str] = None
    strict: bool = False
    validators: List[Validator] = dataclasses.field(default_factory=list)
    field: Optional[str] = None
    help: Optional[str] = None

    def suggestions(self) -> List[str]:
        """Return ``options`` normalized to a list of strings."""
        return [str(option) for option in self.options]


@dataclasses.dataclass
class NumberRequest:
    """
    A declarative request for a floating-point number.

    Attributes:
        label: The prompt shown to the user.
        default: Value returned when the user submits an empty answer.
        field: Logical field name used in the persisted transcript.
    """

    label: str
    default: Optional[float] = None
    field: Optional[str] = None


@dataclasses.dataclass
class FormRequest:
    """
    A declarative request to fill in a Pydantic model as a form.

    Attributes:
        model: The Pydantic model class to render.
        title: Optional title override. Defaults to the humanized class name.
        initial: Optional pre-populated model instance to seed field defaults.
        field: Logical field name used in the persisted transcript.
    """

    model: type
    title: Optional[str] = None
    initial: Optional[object] = None
    field: Optional[str] = None


@dataclasses.dataclass
class FieldRequest:
    """
    A declarative request to enter a single field from a Pydantic model.

    The frontend inspects the field's type annotation and routes to the most
    appropriate input: autocomplete for ``Literal`` / ``Enum`` fields, yes/no
    for ``bool``, and a validated text prompt for everything else.

    Attributes:
        model: The Pydantic model class that owns the field.
        field_name: The name of the field to prompt for.
        initial: Optional value to use as the pre-filled default (overrides the
            field's declared default).
    """

    model: type
    field_name: str
    initial: Optional[object] = None


@dataclasses.dataclass
class ReadOnlyTable:
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
    title: Optional[str] = None
    prompt: Optional[str] = None
    confirm_label: str = "OK"
    cancel_label: str = "Cancel"
    field: Optional[str] = None

    @classmethod
    def from_records(
        cls,
        records: Sequence[Mapping[str, Any]],
        *,
        columns: Optional[Sequence[str]] = None,
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
        obj: Union[Mapping[str, Any], BaseModel],
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
