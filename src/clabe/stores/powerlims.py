"""
A read-only client and store over the PowerLIMS scheduling API.

Everything comes from ``GET /planned_events``: the events planned for each mouse, and for each
event the settings that apply to it, keyed by kind (``behavior``, ``fip``).

Settings kinds are :class:`SettingsRow` subclasses. Each declares its API ``kind`` and, when one
kind holds several configurations, its ``discriminator`` (behavior rows are told apart by
``task_name``). To support a new kind:

1. Subclass :class:`SettingsRow`, setting ``kind`` (and ``discriminator`` if it has one).
2. Add a field of that name to :class:`EventSettings`.

Queries (:class:`EventQuery`), event accessors (:meth:`EventSettings.row`, :meth:`PlannedEvent.has`)
and the store (``store.resolve(Kind(FipRow))``) then work with it without further changes.

Nothing is written back: the API has no write endpoint yet.
"""

import datetime
import json
import logging
from collections.abc import Sequence
from typing import Any, ClassVar, Generic, Literal, TypedDict, TypeVar, Unpack

import requests
from aind_behavior_curriculum import TrainerState
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .. import ui
from ..services import ServiceSettings
from ..utils import get_computer_name
from ._base import Candidate, Kind, KindLike, Scope, StoreBase, as_kind

logger = logging.getLogger(__name__)

T = TypeVar("T")
RowT = TypeVar("RowT", bound="SettingsRow")

_TRAINER_STATE = "trainer_state"


class PowerLimsSettings(ServiceSettings):
    """Settings for :class:`PowerLimsClient`, read from the ``powerlims`` section of clabe.yml."""

    __yml_section__: ClassVar[str | None] = "powerlims"

    base_url: str = Field(
        default="https://powerlims.example.invalid",
        description="The API's root URL. The default is a placeholder; set the real one in clabe.yml.",
    )
    timeout: float = Field(default=10.0, description="Seconds to wait for each request.")


class PowerLimsError(RuntimeError):
    """A request to the PowerLIMS API failed: the server was unreachable or answered with an error."""


class InvalidSettingsError(ValueError):
    """
    PowerLIMS holds a settings row that does not validate.

    Bad data is never skipped: it is raised, naming the row. When it comes from validation, the
    pydantic error is the cause.
    """

    def __init__(self, kind: str, row_id: str | None, reason: str, *, message: str | None = None) -> None:
        self.kind = kind
        self.row_id = row_id
        super().__init__(message or f"PowerLIMS {kind} settings row {row_id} is invalid: {reason}")


class InvalidTrainerStateError(InvalidSettingsError):
    """A behavior row holds a trainer state that is not a valid :class:`~aind_behavior_curriculum.TrainerState`."""

    def __init__(self, row_id: str | None, task_name: str | None, reason: str) -> None:
        self.task_name = task_name
        super().__init__(
            "behavior",
            row_id,
            reason,
            message=f"PowerLIMS settings row {row_id} for {task_name} has an invalid trainer state: {reason}",
        )


class _ApiModel(BaseModel):
    """Base for API models. The API is young and grows fields often, so unknown ones are ignored, not rejected."""

    model_config = ConfigDict(extra="ignore")


# --------------------------------------------------------------------------------------------------
# Settings kinds
# --------------------------------------------------------------------------------------------------


class SettingsRow(_ApiModel):
    """
    Base of every settings kind. Subclasses set ``kind`` to the API's key and register themselves.

    Attributes:
        kind: The key of this kind in an event's ``settings``, and in ``settings=`` filters.
        discriminator: The field that tells this kind's configurations apart, e.g. ``task_name``.
            None when the kind has one configuration per mouse.
    """

    kind: ClassVar[str]
    discriminator: ClassVar[str | None] = None
    #: Every registered kind, by its API key.
    registry: ClassVar[dict[str, type["SettingsRow"]]] = {}

    id: str | None = None
    created_on: datetime.datetime | None = None

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if "kind" in cls.__dict__:
            SettingsRow.registry[cls.kind] = cls

    @classmethod
    def where(cls, value: str | None = None) -> str:
        """
        Returns the ``settings`` filter for this kind, optionally narrowed to one discriminator value.

        ``BehaviorRow.where("AindVrForaging")`` is ``"behavior:AindVrForaging"``.

        Raises:
            ValueError: If a value is given for a kind without a discriminator.
        """
        if value is None:
            return cls.kind
        if cls.discriminator is None:
            raise ValueError(f"{cls.kind!r} settings have no discriminator to filter on.")
        return f"{cls.kind}:{value}"

    @property
    def discriminator_value(self) -> str | None:
        """This row's discriminator value, or None if the kind has no discriminator."""
        return getattr(self, self.discriminator) if self.discriminator else None

    def matches(self, value: str | None = None) -> bool:
        """Whether this row has the given discriminator value; any row matches when the value is None."""
        return value is None or self.discriminator_value == value

    @property
    def summary(self) -> str:
        """A short description to show an operator."""
        created = self.created_on.isoformat() if self.created_on else "unknown date"
        return f"{self.kind} row {self.id} ({created})"


class BehaviorRow(SettingsRow):
    """A behavior curriculum settings row. ``task_name`` tells the configurations of different tasks apart."""

    kind: ClassVar[str] = "behavior"
    discriminator: ClassVar[str | None] = "task_name"

    task_name: str | None = None
    stage_name: str | None = None
    curriculum_name: str | None = None
    on_curriculum: bool | None = None
    has_trainer_state: bool = False
    trainer_state: TrainerState | None = Field(
        default=None,
        description="Only filled in when asked for with ``expand_settings``. Check ``has_trainer_state`` otherwise.",
    )

    @property
    def summary(self) -> str:
        """The stage and when the row was made, to show an operator."""
        created = self.created_on.isoformat() if self.created_on else "unknown date"
        return f"{self.stage_name or 'unknown stage'} ({created})"


class FipFiber(_ApiModel):
    """One fiber of a FIP settings row."""

    fiber: int
    enabled: bool | None = None
    patch_cord: str | None = None


class FipRow(SettingsRow):
    """A FIP fiber photometry settings row. Every FIP row of a mouse applies to its FIP events."""

    kind: ClassVar[str] = "fip"

    led_415nm: float | None = None
    led_470nm: float | None = None
    led_565nm: float | None = None
    fibers: list[FipFiber] = Field(default_factory=list)


class ResolvedSettings(_ApiModel, Generic[RowT]):
    """The settings row that applies to an event for one kind, and where it came from."""

    event_type: str
    source: Literal["pinned", "latest", "none"]
    row: RowT | None = None


class EventSettings(_ApiModel):
    """An event's settings, one field per kind, named after it. A field is None when the event has none of that kind."""

    behavior: ResolvedSettings[BehaviorRow] | None = None
    fip: ResolvedSettings[FipRow] | None = None

    def entry(self, row_type: type[RowT]) -> ResolvedSettings[RowT] | None:
        """Returns the event's settings of a kind, with their source, or None."""
        return getattr(self, row_type.kind)

    def row(self, row_type: type[RowT]) -> RowT | None:
        """Returns the event's settings row of a kind, or None if it has none."""
        entry = self.entry(row_type)
        return entry.row if entry is not None else None

    def rows(self) -> list[SettingsRow]:
        """Every settings row of the event, one per kind present. Suited to ``match row: case FipRow(): ...``."""
        return [r for row_type in SettingsRow.registry.values() if (r := self.row(row_type)) is not None]


_unlisted = set(SettingsRow.registry) - set(EventSettings.model_fields)
if _unlisted:
    raise TypeError(f"Settings kinds {sorted(_unlisted)} have no field on EventSettings.")


# --------------------------------------------------------------------------------------------------
# Events and queries
# --------------------------------------------------------------------------------------------------


def _aware(value: datetime.datetime | None) -> datetime.datetime | None:
    """Times without an offset are UTC, as the API documents."""
    return value.replace(tzinfo=datetime.UTC) if value is not None and value.tzinfo is None else value


class PlannedEvent(_ApiModel):
    """A mouse's planned event, with the settings that apply to it."""

    event_id: str | None = None
    event_name: str | None = None
    mouse_id: str | None = None
    instrument: str | None = None
    start: datetime.datetime | None = None
    end: datetime.datetime | None = None
    status: str | None = None
    event_types: list[str] = Field(default_factory=list)
    notes: str | None = None
    trainer: str | None = None
    scientific_contact: str | None = None
    settings: EventSettings = Field(default_factory=EventSettings)

    _normalize_times = field_validator("start", "end")(_aware)

    @property
    def kinds(self) -> list[str]:
        """The settings kinds this event has a row for."""
        return [row.kind for row in self.settings.rows()]

    def has(self, row_type: type[SettingsRow], value: str | None = None) -> bool:
        """Whether the event has a row of this kind, with this discriminator value when one is given."""
        row = self.settings.row(row_type)
        return row is not None and row.matches(value)

    def is_ongoing(self, at: datetime.datetime) -> bool:
        """Whether the event has started by ``at`` and not yet ended. An event without an end never ends."""
        return (self.start is None or self.start <= at) and (self.end is None or at <= self.end)


class EventQuery(BaseModel):
    """
    The filters of ``GET /planned_events``, validated before anything is sent.

    Every filter is optional. ``settings`` filters must all match.
    """

    model_config = ConfigDict(frozen=True)

    instrument: str | None = Field(default=None, description="Only events on this instrument (its SIPE comp_id).")
    mouse_id: str | None = Field(default=None, description="Only events of this subject.")
    start_gte: datetime.datetime | None = Field(default=None, description="Only events starting at or after this.")
    start_lte: datetime.datetime | None = Field(default=None, description="Only events starting at or before this.")
    settings: Sequence[str] = Field(
        default=(),
        description="Only events with these settings, each ``<kind>`` or ``<kind>:<value>``. Build them with "
        '``SettingsRow.where``, e.g. ``(FipRow.where(), BehaviorRow.where("AindVrForaging"))``.',
    )
    expand_settings: bool = Field(
        default=False,
        description="Fill in behavior rows' ``trainer_state``. They are large, so leave it off unless needed.",
    )

    @field_validator("settings")
    @classmethod
    def _check_settings(cls, value: Sequence[str]) -> Sequence[str]:
        """Rejects unknown kinds and values for kinds without a discriminator, which the server answers with 422."""
        for item in value:
            kind, sep, discriminator = item.partition(":")
            row_type = SettingsRow.registry.get(kind)
            if row_type is None:
                raise ValueError(f"Unknown settings kind {kind!r}; known: {sorted(SettingsRow.registry)}.")
            if sep and (row_type.discriminator is None or not discriminator):
                raise ValueError(f"{item!r}: {kind!r} settings cannot be filtered by value.")
        return value

    @field_validator("start_gte", "start_lte")
    @classmethod
    def _local_if_naive(cls, value: datetime.datetime | None) -> datetime.datetime | None:
        """The API reads naive times as UTC; on a rig, a naive time means local time."""
        return value.astimezone() if value is not None else None

    def to_params(self) -> dict[str, Any]:
        """The query string parameters, leaving out filters that are not set."""
        params: dict[str, Any] = {
            "instrument": self.instrument,
            "mouse_id": self.mouse_id,
            "start_gte": self.start_gte.isoformat() if self.start_gte else None,
            "start_lte": self.start_lte.isoformat() if self.start_lte else None,
            "settings": list(self.settings) or None,
            "expand_settings": "true" if self.expand_settings else None,
        }
        return {k: v for k, v in params.items() if v is not None}

    def matches(self, event: PlannedEvent) -> bool:
        """Whether an event passes the ``settings`` filters, as the server applies them."""
        for item in self.settings:
            kind, _, value = item.partition(":")
            if not event.has(SettingsRow.registry[kind], value or None):
                return False
        return True


class EventFilters(TypedDict, total=False):
    """The :class:`EventQuery` filters that apply to any time window."""

    instrument: str | None
    mouse_id: str | None
    settings: Sequence[str]
    expand_settings: bool


class _PlannedEventsPage(_ApiModel):
    """One page of ``GET /planned_events``; ``next_offset`` is None on the last page. Items are parsed one by one."""

    next_offset: int | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)


def _parse_event(item: dict[str, Any]) -> PlannedEvent:
    """
    Validates one event, turning a settings failure into an error that names the row.

    Validated as JSON, not Python: JSON turned the curriculum graph's int keys into strings, and
    only JSON mode turns them back.
    """
    try:
        return PlannedEvent.model_validate_json(json.dumps(item))
    except ValidationError as e:
        for error in e.errors():
            loc = error["loc"]
            if len(loc) >= 3 and loc[0] == "settings" and loc[2] == "row":
                kind = str(loc[1])
                row = ((item.get("settings") or {}).get(kind) or {}).get("row") or {}
                reason = f"{e.error_count()} validation error(s)"
                if kind == BehaviorRow.kind and "trainer_state" in loc:
                    raise InvalidTrainerStateError(row.get("id"), row.get("task_name"), reason) from e
                raise InvalidSettingsError(kind, row.get("id"), reason) from e
        raise PowerLimsError(f"PowerLIMS returned event {item.get('event_id')} in an unexpected shape: {e}") from e


def _day_bounds(day: datetime.date | None) -> tuple[datetime.datetime, datetime.datetime]:
    """Returns the first and last instant of a day (today when None) in this machine's time zone."""
    start = datetime.datetime.combine(day or datetime.date.today(), datetime.time()).astimezone()
    return start, start + datetime.timedelta(days=1, microseconds=-1)


def _by_start(events: list[PlannedEvent]) -> list[PlannedEvent]:
    """Sorts events by start time; events without one go last."""
    return sorted(events, key=lambda e: e.start or datetime.datetime.max.replace(tzinfo=datetime.UTC))


# --------------------------------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------------------------------


class PowerLimsClient:
    """
    A thin, read-only client for the PowerLIMS API endpoints a rig uses.

    Example:
        ```python
        client = PowerLimsClient()
        # What is on this rig right now, with a VR foraging behavior row and its trainer state?
        for event in client.events_at(
            instrument="FRG.12-B", settings=[BehaviorRow.where("AindVrForaging")], expand_settings=True
        ):
            behavior = event.settings.row(BehaviorRow)
            fip = event.settings.row(FipRow)  # None when the event has no FIP
        ```
    """

    def __init__(self, settings: PowerLimsSettings | None = None, *, http: requests.Session | None = None) -> None:
        """
        Args:
            settings: Which server to use. Read from clabe.yml when omitted.
            http: A ready HTTP session, e.g. a fake in tests. A new one is made when omitted.
        """
        self.settings = settings if settings is not None else PowerLimsSettings()
        self._http = http if http is not None else requests.Session()

    def __str__(self) -> str:
        return f"{type(self).__name__}({self.settings.base_url})"

    def _get(self, path: str, **kwargs: Any) -> Any:
        """Sends a GET and returns the decoded JSON body, raising :class:`PowerLimsError` on any failure."""
        url = self.settings.base_url.rstrip("/") + path
        try:
            response = self._http.request("GET", url, timeout=self.settings.timeout, **kwargs)
        except requests.RequestException as e:
            raise PowerLimsError(f"Could not reach the PowerLIMS API at {url}: {e}") from e
        if not response.ok:
            raise PowerLimsError(f"GET {path} failed with HTTP {response.status_code}: {response.text[:500]}")
        return response.json()

    def healthcheck(self) -> dict[str, Any]:
        """Returns the server's health status and version."""
        return self._get("/healthcheck")

    def events(self, query: EventQuery | None = None) -> list[PlannedEvent]:
        """
        Returns every event matching a query, fetching every page, in order of start time.

        Raises:
            PowerLimsError: If the API could not be reached or answered with an error.
            InvalidSettingsError: If an event's settings row does not validate.
                :class:`InvalidTrainerStateError` when it is a behavior row's trainer state.
        """
        params = {**(query or EventQuery()).to_params(), "limit": 100, "offset": 0}
        events: list[PlannedEvent] = []
        while True:
            page = _PlannedEventsPage.model_validate(self._get("/planned_events", params=params))
            events.extend(_parse_event(item) for item in page.items)
            if page.next_offset is None:
                break
            params["offset"] = page.next_offset
        return _by_start(events)

    def events_on(self, day: datetime.date | None = None, **filters: Unpack[EventFilters]) -> list[PlannedEvent]:
        """Returns the events starting on a day (today when omitted) in this machine's time zone. See :meth:`events`."""
        start, end = _day_bounds(day)
        return self.events(EventQuery(start_gte=start, start_lte=end, **filters))

    def events_at(
        self,
        at: datetime.datetime | None = None,
        *,
        lookback: datetime.timedelta = datetime.timedelta(hours=12),
        **filters: Unpack[EventFilters],
    ) -> list[PlannedEvent]:
        """
        Returns the events going on at a moment (now when omitted). See :meth:`events`.

        Args:
            at: The moment. Naive times are local.
            lookback: How long before ``at`` an event may have started. The API filters on start
                only, so an event without an end counts as going on for this long.
        """
        at = (at or datetime.datetime.now()).astimezone()
        events = self.events(EventQuery(start_gte=at - lookback, start_lte=at, **filters))
        return [e for e in events if e.is_ongoing(at)]

    def scheduled_subjects(self, instrument: str, day: datetime.date | None = None) -> list[str]:
        """Returns the mice with an event on this instrument on a day (today when omitted), by start time."""
        events = self.events_on(day, instrument=instrument)
        return list(dict.fromkeys(e.mouse_id for e in events if e.mouse_id))


# --------------------------------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------------------------------


class PowerLimsStore(StoreBase):
    """
    A read-only store over the PowerLIMS schedule, serving trainer state and settings rows.

    Reads come from the subject's events on the day, fetched once and kept, so everything the
    launcher shows comes from one snapshot; :meth:`events` exposes them. Events on this rig are
    preferred; when the mouse is only scheduled elsewhere that day, those events are used and the
    operator is warned, so a moved mouse never blocks a session.

    Kinds served:
        ``Kind.from_trainer_state()``: The trainer state of the behavior row for ``task_name``.
        ``Kind(<SettingsRow subclass>)``: Settings rows of that kind, e.g. ``Kind(FipRow)``. When
            the kind has a discriminator and the scope has a key of the same name (``task_name``
            for behavior), only rows with that value are offered.

    Scope:
        ``subject``: Required.
        ``task_name``: Required for trainer state, e.g. ``AindVrForaging``.
        ``instrument``: The rig's instrument name. Defaults to ``computer_name``, which defaults
            to this machine's name.

    Example:
        ```python
        powerlims = PowerLimsStore()
        session = SessionBuilder(launcher, subject_suggestions=powerlims.scheduled_subjects).build()
        store = CompositeStore(
            default=LocalFileStore(root=VR_LIB),
            routes={"trainer_state": powerlims, "fip_row": powerlims},
        ).scoped(subject=session.subject, task_name="AindVrForaging")

        trainer_state = store.resolve(Kind.from_trainer_state())
        fip = store.resolve(Kind(FipRow))
        ```
    """

    def __init__(
        self,
        settings: PowerLimsSettings | None = None,
        *,
        client: PowerLimsClient | None = None,
        scope: Scope | None = None,
        day: datetime.date | None = None,
    ) -> None:
        """
        Args:
            settings: Which server to use. Read from clabe.yml when omitted. Ignored when ``client`` is given.
            client: A ready client. Built from ``settings`` when omitted.
            scope: Initial scope. ``computer_name`` defaults to this machine's.
            day: The day whose schedule to read. Today when omitted.
        """
        super().__init__(scope={"computer_name": get_computer_name(), **(scope or {})})
        self._client = client if client is not None else PowerLimsClient(settings)
        self._day = day
        # Shared by every scoped view (scoped() copies attributes by reference), so events are fetched once.
        self._events: dict[tuple[str, str | None], list[PlannedEvent]] = {}

    def __str__(self) -> str:
        return f"{type(self).__name__}({self._client.settings.base_url})"

    @property
    def client(self) -> PowerLimsClient:
        """The underlying API client."""
        return self._client

    def events(self, *, scope: Scope | None = None, refresh: bool = False) -> list[PlannedEvent]:
        """
        Returns the subject's events on the day in order of start time: those on this rig, or, when
        it has none here, those elsewhere.

        Fetched on first use, trainer states included, and kept. Whether the mouse is scheduled
        here is shown to the user when fetched.

        Args:
            scope: Layered over the store's own scope.
            refresh: Fetch again even if events are already kept.

        Raises:
            LookupError: If ``subject`` is missing from scope.
            PowerLimsError: If the API could not be reached or answered with an error.
            InvalidSettingsError: If one of the subject's settings rows does not validate.
        """
        merged = self._merge_scope(scope)
        subject = self._require(merged, "subject")
        instrument = self._instrument(merged)
        key = (subject, instrument)
        if refresh or key not in self._events:
            with ui.activity(f"Asking PowerLIMS what is scheduled for {subject}..."):
                events = self._client.events_on(self._day, mouse_id=subject, expand_settings=True)
            here = [e for e in events if instrument is not None and e.instrument == instrument]
            self._announce(subject, instrument, here, events)
            self._events[key] = here or events
        return self._events[key]

    def scheduled_subjects(self) -> list[str]:
        """
        Returns the mice with an event on this rig on the day, in order of start time.

        Meant as ``SessionBuilder(subject_suggestions=store.scheduled_subjects)``.

        Raises:
            LookupError: If the scope has neither ``instrument`` nor ``computer_name``.
            PowerLimsError: If the API could not be reached or answered with an error.
        """
        instrument = self._instrument(self.scope)
        if instrument is None:
            raise LookupError("PowerLimsStore needs an instrument or computer_name in scope to look up the schedule.")
        return self._client.scheduled_subjects(instrument, self._day)

    def _candidates(self, kind: Kind[T], scope: Scope) -> Sequence[Candidate[T]]:
        """Routes a kind to the trainer state or settings row candidates, refusing any other kind."""
        if kind.name == _TRAINER_STATE:
            return self._trainer_states(kind, scope)
        if isinstance(kind.model, type) and issubclass(kind.model, SettingsRow):
            return self._settings_rows(kind, scope)  # type: ignore[arg-type, type-var]
        raise LookupError(
            f"PowerLimsStore only serves {_TRAINER_STATE!r} and settings rows "
            f"({', '.join(t.__name__ for t in SettingsRow.registry.values())}), not {kind.name!r}."
        )

    def _distinct_rows(self, row_type: type[RowT], scope: Scope, value: str | None) -> list[tuple[PlannedEvent, RowT]]:
        """Each distinct row of a kind on the subject's events. A "latest" row applies to many events, so it comes once."""
        found: list[tuple[PlannedEvent, RowT]] = []
        seen: set[str | None] = set()
        for event in self.events(scope=scope):
            row = event.settings.row(row_type)
            if row is None or not row.matches(value) or row.id in seen:
                continue
            seen.add(row.id)
            found.append((event, row))
        return found

    def _settings_rows(self, kind: Kind[RowT], scope: Scope) -> Sequence[Candidate[RowT]]:
        """Each distinct row of the kind, narrowed by its discriminator when the scope has a key of that name."""
        row_type: type[RowT] = kind.model
        value = scope.get(row_type.discriminator) if row_type.discriminator else None
        candidates = [
            Candidate(self._label(event, row_type, row), row)
            for event, row in self._distinct_rows(row_type, scope, value)
        ]
        if not candidates:
            logger.warning("PowerLIMS has no %s settings for %s.", row_type.kind, scope.get("subject"))
        return candidates

    def _trainer_states(self, kind: Kind[T], scope: Scope) -> Sequence[Candidate[T]]:
        """
        Returns the trainer state of each distinct behavior row for the task on the subject's events.

        Raises:
            InvalidTrainerStateError: If any of those rows holds a trainer state that is not valid,
                is for another task, or could not be read by the server. Bad data is never skipped.
        """
        task_name = self._require(scope, "task_name")
        candidates: list[Candidate[T]] = []
        for event, row in self._distinct_rows(BehaviorRow, scope, task_name):
            trainer_state: Any = row.trainer_state
            if trainer_state is None:
                if row.has_trainer_state:
                    # Asked for and present, yet null: the server could not parse the stored file.
                    raise InvalidTrainerStateError(row.id, task_name, "the server could not read the stored file")
                logger.warning("PowerLIMS settings row %s for %s has no trainer state.", row.id, task_name)
                continue
            if not isinstance(trainer_state, kind.model):
                # The kind asks for a narrower model than TrainerState, e.g. one typed by task.
                try:
                    trainer_state = kind.adapter.validate_json(trainer_state.model_dump_json())
                except ValidationError as e:
                    raise InvalidTrainerStateError(row.id, task_name, f"{e.error_count()} validation error(s)") from e
            stage = getattr(trainer_state, "stage", None)
            if stage is not None and stage.task.name != task_name:
                raise InvalidTrainerStateError(row.id, task_name, f"its stage is for task {stage.task.name!r}")
            candidates.append(Candidate(self._label(event, BehaviorRow, row), trainer_state))
        if not candidates:
            logger.warning("PowerLIMS has no %s trainer state for %s.", task_name, scope.get("subject"))
        return candidates

    def write(self, kind: KindLike[T], value: T, *, scope: Scope | None = None) -> None:
        """
        Not supported: the store is read-only.

        Raises:
            NotImplementedError: Always. The PowerLIMS API has no write endpoint yet.
        """
        raise NotImplementedError(f"{type(self).__name__} is read-only; cannot write {as_kind(kind).name!r}.")

    @staticmethod
    def _instrument(scope: Scope) -> str | None:
        """The instrument in scope, else the computer name, else None."""
        return scope.get("instrument") or scope.get("computer_name") or None

    @staticmethod
    def _announce(subject: str, instrument: str | None, here: list[PlannedEvent], events: list[PlannedEvent]) -> None:
        """Surfaces what the schedule says, so the operator sees it before running."""
        if here:
            event = here[0]
            what = event.event_name or ", ".join(event.event_types) or "an event"
            ui.notify(
                f"{subject} is scheduled for {what} on {event.instrument} (trainer: {event.trainer or 'unknown'})."
            )
        elif events:
            elsewhere = ", ".join(sorted({e.instrument or "an unknown instrument" for e in events}))
            ui.notify(
                f"{subject} is not scheduled on {instrument or 'this rig'}, only on {elsewhere}."
                " Using those events' settings.",
                ui.MessageLevel.WARNING,
            )
        else:
            ui.notify(f"{subject} has no events scheduled.", ui.MessageLevel.WARNING)

    @staticmethod
    def _require(scope: Scope, key: str) -> str:
        """Returns the scope's value for the key, raising LookupError if it is missing or empty."""
        value = scope.get(key)
        if not value:
            raise LookupError(f"PowerLimsStore requires {key!r} in scope.")
        return value

    @staticmethod
    def _label(event: PlannedEvent, row_type: type[SettingsRow], row: SettingsRow) -> str:
        """Labels a row by its summary, where it came from (pinned or latest) and its event."""
        entry = event.settings.entry(row_type)
        source = entry.source if entry is not None else "unknown source"
        at = event.start.astimezone().strftime("%H:%M") if event.start else "unknown time"
        return f"{row.summary} ({source}; event at {at} on {event.instrument})"


__all__ = [
    "BehaviorRow",
    "EventFilters",
    "EventQuery",
    "EventSettings",
    "FipFiber",
    "FipRow",
    "InvalidSettingsError",
    "InvalidTrainerStateError",
    "PlannedEvent",
    "PowerLimsClient",
    "PowerLimsError",
    "PowerLimsSettings",
    "PowerLimsStore",
    "ResolvedSettings",
    "SettingsRow",
]
