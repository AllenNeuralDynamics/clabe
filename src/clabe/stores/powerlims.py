"""
A read-only store over the PowerLIMS scheduling API.

Everything comes from ``GET /planned_events``: the mice scheduled on a rig today, and for each
event the settings row that applies to each of its event types (pinned to the event, else the
mouse's newest unpinned row, else none). A behavior row's trainer state, when present, is served
as the subject's trainer state.

Nothing is written back: the API has no write endpoint yet.
"""

import datetime
import json
import logging
from collections.abc import Iterator, Sequence
from typing import Any, ClassVar, TypeVar

import requests
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import ui
from ..services import ServiceSettings
from ..utils import get_computer_name
from ._base import Candidate, Kind, KindLike, Scope, StoreBase, as_kind

logger = logging.getLogger(__name__)

T = TypeVar("T")

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


class InvalidTrainerStateError(ValueError):
    """
    PowerLIMS holds a trainer state that is not a valid :class:`~aind_behavior_curriculum.TrainerState`.

    The server passes trainer states through as plain JSON, so this is where corrupt or
    incomplete ones are caught. When it comes from validation, the pydantic error is the cause.
    """

    def __init__(self, row_id: str | None, task_name: str, reason: str) -> None:
        self.row_id = row_id
        self.task_name = task_name
        super().__init__(f"PowerLIMS settings row {row_id} for {task_name} has an invalid trainer state: {reason}")


class _ApiModel(BaseModel):
    """Base for API models. The API is young and grows fields often, so unknown ones are ignored, not rejected."""

    model_config = ConfigDict(extra="ignore")


class PlannedEvent(_ApiModel):
    """
    A mouse's planned event, with the settings row that applies to each of its event types.

    ``settings`` is kept as the API sends it, keyed by event type name:
    ``{"source": "pinned" | "latest" | "none", "row": {...} | None}``. Only a behavior row's
    ``trainer_state`` is given a type, by :class:`PowerLimsStore`.
    """

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
    settings: dict[str, dict[str, Any]] = Field(default_factory=dict)


class _PlannedEventsPage(_ApiModel):
    """One page of ``GET /planned_events``; ``next_offset`` is None on the last page."""

    limit: int
    offset: int
    next_offset: int | None = None
    items: list[PlannedEvent] = Field(default_factory=list)


_FAR_FUTURE = datetime.datetime.max.replace(tzinfo=datetime.UTC)


def _by_start(events: list[PlannedEvent]) -> list[PlannedEvent]:
    """Orders events by start time, events without one last."""
    return sorted(events, key=lambda e: e.start or _FAR_FUTURE)


def _today_bounds(now: datetime.datetime | None = None) -> tuple[datetime.datetime, datetime.datetime]:
    """Returns the start and end of today in this machine's local time, offsets included."""
    now = (now or datetime.datetime.now()).astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + datetime.timedelta(days=1) - datetime.timedelta(microseconds=1)


class PowerLimsClient:
    """A thin, read-only client for the PowerLIMS API endpoints a rig uses."""

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

    def planned_events(
        self,
        *,
        instrument: str | None = None,
        mouse_id: str | None = None,
        start_gte: datetime.datetime | None = None,
        start_lte: datetime.datetime | None = None,
        include_trainer_state: bool = False,
        page_size: int = 100,
    ) -> list[PlannedEvent]:
        """
        Returns every planned event matching the filters, newest start first, following all pages.

        Args:
            instrument: The instrument name (its SIPE comp_id).
            mouse_id: The subject.
            start_gte: Events starting at or after this time.
            start_lte: Events starting at or before this time.
            include_trainer_state: Fill in each behavior row's ``trainer_state``. They are large,
                so the API leaves them out unless asked.
            page_size: Events per request, 1 to 100.
        """
        params: dict[str, Any] = {"limit": page_size, "offset": 0}
        if instrument:
            params["instrument"] = instrument
        if mouse_id:
            params["mouse_id"] = mouse_id
        if start_gte is not None:
            params["start_gte"] = start_gte.isoformat()
        if start_lte is not None:
            params["start_lte"] = start_lte.isoformat()
        if include_trainer_state:
            params["include_trainer_state"] = "true"
        events: list[PlannedEvent] = []
        while True:
            page = _PlannedEventsPage.model_validate(self._get("/planned_events", params=params))
            events.extend(page.items)
            if page.next_offset is None:
                return events
            params["offset"] = page.next_offset

    def events_today(
        self,
        *,
        instrument: str | None = None,
        mouse_id: str | None = None,
        include_trainer_state: bool = False,
        now: datetime.datetime | None = None,
    ) -> list[PlannedEvent]:
        """Returns today's events (in this machine's time zone) matching the filters, in order of start time."""
        start, end = _today_bounds(now)
        events = self.planned_events(
            instrument=instrument,
            mouse_id=mouse_id,
            start_gte=start,
            start_lte=end,
            include_trainer_state=include_trainer_state,
        )
        return _by_start(events)

    def subjects_scheduled_today(self, instrument: str, *, now: datetime.datetime | None = None) -> list[str]:
        """Returns the mice with an event on this instrument today, in order of start time."""
        events = self.events_today(instrument=instrument, now=now)
        return list(dict.fromkeys(e.mouse_id for e in events if e.mouse_id))


class PowerLimsStore(StoreBase):
    """
    A read-only store over the PowerLIMS schedule, serving trainer state.

    Reads come from the subject's events today, fetched once and kept, so the trainer state and
    the event the launcher shows are the same snapshot; :meth:`events` exposes them. Events on
    this rig are preferred; when the mouse is only scheduled elsewhere today, those events are
    used and the operator is warned, so a moved mouse never blocks a session.

    Scope:
        ``subject``: Required.
        ``task_name``: Required for trainer state. It is the event type name the settings are
            keyed by, e.g. ``AindVrForaging``.
        ``instrument``: The rig's instrument name. Defaults to ``computer_name``, which defaults
            to this machine's name.

    Example:
        ```python
        powerlims = PowerLimsStore()
        session = SessionBuilder(launcher, subject_suggestions=powerlims.scheduled_subjects).build()
        store = CompositeStore(
            default=LocalFileStore(root=VR_LIB),
            routes={"trainer_state": powerlims},
        ).scoped(subject=session.subject, task_name="AindVrForaging")

        trainer_state = store.resolve(Kind.from_trainer_state())
        fip_row = powerlims.scoped(subject=session.subject).events()[0].settings["FIP Fiber Photometry"]["row"]
        ```
    """

    def __init__(
        self,
        settings: PowerLimsSettings | None = None,
        *,
        client: PowerLimsClient | None = None,
        scope: Scope | None = None,
    ) -> None:
        """
        Args:
            settings: Which server to use. Read from clabe.yml when omitted. Ignored when ``client`` is given.
            client: A ready client. Built from ``settings`` when omitted.
            scope: Initial scope. ``computer_name`` defaults to this machine's.
        """
        super().__init__(scope={"computer_name": get_computer_name(), **(scope or {})})
        self._client = client if client is not None else PowerLimsClient(settings)
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
        Returns the subject's events today in order of start time: those on this rig, or, when it
        has none here, those elsewhere.

        Fetched on first use, trainer states included, and kept. Whether the mouse is scheduled
        here is shown to the user when fetched.

        Args:
            scope: Layered over the store's own scope.
            refresh: Fetch again even if events are already kept.

        Raises:
            LookupError: If ``subject`` is missing from scope.
            PowerLimsError: If the API could not be reached or answered with an error.
        """
        merged = self._merge_scope(scope)
        subject = self._require(merged, "subject")
        instrument = self._instrument(merged)
        key = (subject, instrument)
        if refresh or key not in self._events:
            with ui.activity(f"Asking PowerLIMS what is scheduled for {subject} today..."):
                events = self._client.events_today(mouse_id=subject, include_trainer_state=True)
            here = [e for e in events if instrument is not None and e.instrument == instrument]
            self._announce(subject, instrument, here, events)
            self._events[key] = here or events
        return self._events[key]

    def scheduled_subjects(self) -> list[str]:
        """
        Returns the mice with an event on this rig today, in order of start time.

        Meant as ``SessionBuilder(subject_suggestions=store.scheduled_subjects)``.

        Raises:
            LookupError: If the scope has neither ``instrument`` nor ``computer_name``.
            PowerLimsError: If the API could not be reached or answered with an error.
        """
        instrument = self._instrument(self.scope)
        if instrument is None:
            raise LookupError("PowerLimsStore needs an instrument or computer_name in scope to look up the schedule.")
        return self._client.subjects_scheduled_today(instrument)

    def _candidates(self, kind: Kind[T], scope: Scope) -> Sequence[Candidate[T]]:
        """
        Returns the trainer state of each distinct behavior settings row today's events resolve for the task.

        ``task_name`` is the discriminator. A settings entry is a behavior configuration for the
        task when its row's own ``task_name`` column says so; the event type's name, which keys
        ``settings``, is not relied on. The parsed trainer state must be for that task too.

        Raises:
            InvalidTrainerStateError: If any of those rows holds a trainer state that is not valid,
                is for another task, or could not be read by the server. Bad data is never skipped.
        """
        self._require_supported(kind)
        task_name = self._require(scope, "task_name")
        candidates: list[Candidate[T]] = []
        seen: set[str | None] = set()
        for event, resolved, row in self._behavior_rows(self.events(scope=scope), task_name):
            row_id = row.get("id")
            # A "latest" row applies to every event without a pinned one, so it is offered once.
            if row_id in seen:
                continue
            seen.add(row_id)
            raw = row.get(_TRAINER_STATE)
            if raw is None:
                if row.get("has_trainer_state"):
                    # Asked for and present, yet null: the server could not parse the stored file.
                    raise InvalidTrainerStateError(row_id, task_name, "the server could not read the stored file")
                logger.warning("PowerLIMS settings row %s for %s has no trainer state.", row_id, task_name)
                continue
            try:
                # Validated as JSON, not Python: JSON turned the curriculum graph's int keys into
                # strings, and only JSON mode turns them back.
                trainer_state = kind.adapter.validate_json(json.dumps(raw))
            except ValidationError as e:
                raise InvalidTrainerStateError(row_id, task_name, f"{e.error_count()} validation error(s)") from e
            stage = getattr(trainer_state, "stage", None)
            if stage is not None and stage.task.name != task_name:
                raise InvalidTrainerStateError(row_id, task_name, f"its stage is for task {stage.task.name!r}")
            candidates.append(Candidate(self._label(resolved.get("source"), row, event), trainer_state))
        if not candidates:
            logger.warning("PowerLIMS has no %s trainer state for %s today.", task_name, scope.get("subject"))
        return candidates

    def write(self, kind: KindLike[T], value: T, *, scope: Scope | None = None) -> None:
        """
        Not supported: the store is read-only.

        Raises:
            NotImplementedError: Always. The PowerLIMS API has no write endpoint yet.
        """
        raise NotImplementedError(f"{type(self).__name__} is read-only; cannot write {as_kind(kind).name!r}.")

    @staticmethod
    def _behavior_rows(
        events: Sequence[PlannedEvent], task_name: str
    ) -> Iterator[tuple[PlannedEvent, dict[str, Any], dict[str, Any]]]:
        """Yields ``(event, settings entry, row)`` for each entry whose row is a behavior row for the task."""
        for event in events:
            for resolved in event.settings.values():
                row = resolved.get("row")
                # Only behavior rows have a task_name column; FIP and others never match.
                if row and row.get("task_name") == task_name:
                    yield event, resolved, row

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
                f"{subject} is not scheduled on {instrument or 'this rig'} today, only on {elsewhere}."
                " Using those events' settings.",
                ui.MessageLevel.WARNING,
            )
        else:
            ui.notify(f"{subject} has no events scheduled today.", ui.MessageLevel.WARNING)

    @staticmethod
    def _require_supported(kind: Kind[Any]) -> None:
        """Raises unless the kind is trainer state, the only kind this store serves."""
        if kind.name != _TRAINER_STATE:
            raise LookupError(f"PowerLimsStore only serves {_TRAINER_STATE!r}, not {kind.name!r}.")

    @staticmethod
    def _require(scope: Scope, key: str) -> str:
        """Returns the scope's value for the key, raising LookupError if it is missing or empty."""
        value = scope.get(key)
        if not value:
            raise LookupError(f"PowerLimsStore requires {key!r} in scope.")
        return value

    @staticmethod
    def _label(source: str | None, row: dict[str, Any], event: PlannedEvent) -> str:
        """Labels a row by its stage, where it came from (pinned or latest), when it was made and its event."""
        created = row.get("created_on") or "unknown date"
        at = event.start.astimezone().strftime("%H:%M") if event.start else "unknown time"
        stage = row.get("stage_name") or "unknown stage"
        return f"{stage} ({source or 'unknown source'}, {created}; event at {at} on {event.instrument})"


__all__ = [
    "InvalidTrainerStateError",
    "PlannedEvent",
    "PowerLimsClient",
    "PowerLimsError",
    "PowerLimsSettings",
    "PowerLimsStore",
]
