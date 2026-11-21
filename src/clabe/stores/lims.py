"""
A store over the PowerLIMS API, which serves what a rig should run for a mouse and takes back
the curriculum's next suggestion.

The API is read through one call per session, ``GET /planned_events/current``, whose answer is
kept as a :class:`SessionPlan`: today's event (trainer, scientific contact, notes) and the
settings row that applies to each event type the rig runs, trainer state included. Every read
afterwards, the trainer state and the event alike, comes from that one snapshot.

The curriculum's suggestion is written back as ``{"mouse_id", "trainer_state"}``.

Note:
    The write endpoint is not in the API yet (spec version 0.0.0); its path here,
    ``POST /behavior_settings``, is a placeholder until it is.
"""

import datetime
import logging
from collections.abc import Iterable, Sequence
from typing import Any, ClassVar, Literal, TypeVar

import requests
from aind_behavior_curriculum import TrainerState
from pydantic import BaseModel, ConfigDict, Field

from .. import ui
from ..services import ServiceSettings
from ..utils import get_computer_name
from ._base import Candidate, Kind, KindLike, Scope, StoreBase, as_kind

logger = logging.getLogger(__name__)

T = TypeVar("T")

_TRAINER_STATE = "trainer_state"


class LimsSettings(ServiceSettings):
    """Settings for :class:`LimsClient`, read from the ``lims`` section of clabe.yml."""

    __yml_section__: ClassVar[str | None] = "lims"

    base_url: str = Field(
        default="https://lims.example.invalid",
        description="The API's root URL. The default is a placeholder; set the real one in clabe.yml.",
    )
    timeout: float = Field(default=10.0, description="Seconds to wait for each request.")


class LimsError(RuntimeError):
    """A request to the LIMS API failed: the server was unreachable or answered with an error."""


class _ApiModel(BaseModel):
    # The API is young and grows fields often; unknown ones are ignored rather than rejected.
    model_config = ConfigDict(extra="ignore")


class ScheduledEvent(_ApiModel):
    """A mouse's planned event: when and where it runs, and who runs it."""

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


class BehaviorSettingsRow(_ApiModel):
    """A row of the behavior curriculum settings table."""

    id: str | None = None
    created_on: datetime.datetime | None = None
    task_name: str | None = None
    stage_name: str | None = None
    curriculum_name: str | None = None
    on_curriculum: bool | None = None
    has_trainer_state: bool = False
    trainer_state: dict[str, Any] | None = None


class FipFiber(_ApiModel):
    """One fiber of a FIP settings row."""

    fiber: int
    enabled: bool | None = None
    patch_cord: int | None = None
    patch_cord_label: str | None = None


class FipSettingsRow(_ApiModel):
    """A row of the FIP settings table."""

    id: str | None = None
    created_on: datetime.datetime | None = None
    led_415nm: float | None = None
    led_470nm: float | None = None
    led_565nm: float | None = None
    fibers: list[FipFiber] = Field(default_factory=list)


class ResolvedSettings(_ApiModel):
    """
    The settings row that applies to one event type.

    ``row`` is kept raw because the API's behavior and FIP rows share no required field, so the
    shape cannot be told apart reliably; the caller knows which one it asked for and reads it
    through :meth:`behavior` or :meth:`fip`.
    """

    source: Literal["pinned", "latest", "none"]
    row: dict[str, Any] | None = None

    def behavior(self) -> BehaviorSettingsRow | None:
        """The row read as behavior settings, or None when no row applies."""
        return None if self.row is None else BehaviorSettingsRow.model_validate(self.row)

    def fip(self) -> FipSettingsRow | None:
        """The row read as FIP settings, or None when no row applies."""
        return None if self.row is None else FipSettingsRow.model_validate(self.row)


class PlannedEvent(ScheduledEvent):
    """A planned event together with the settings row that applies to each of its event types."""

    settings: dict[str, ResolvedSettings] = Field(default_factory=dict)


class SessionPlan(_ApiModel):
    """
    What a rig should run for a mouse right now: the answer of ``GET /planned_events/current``.

    The plan never blocks acquisition: when the mouse has no event today, or its event is on
    another instrument, ``scheduled`` is False and ``warnings`` says why, but the mouse's newest
    settings are still in ``settings``.
    """

    mouse_id: str
    scheduled: bool
    event: ScheduledEvent | None = None
    settings: dict[str, ResolvedSettings] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class _PlannedEventsPage(_ApiModel):
    limit: int
    offset: int
    next_offset: int | None = None
    items: list[PlannedEvent] = Field(default_factory=list)


def _today_bounds(now: datetime.datetime | None = None) -> tuple[datetime.datetime, datetime.datetime]:
    """Returns the start and end of today in this machine's local time, offsets included."""
    now = (now or datetime.datetime.now()).astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + datetime.timedelta(days=1) - datetime.timedelta(microseconds=1)


class LimsClient:
    """A thin client for the PowerLIMS API endpoints a rig uses."""

    def __init__(self, settings: LimsSettings | None = None, *, http: requests.Session | None = None) -> None:
        """
        Args:
            settings: Which server to use. Read from clabe.yml when omitted.
            http: A ready HTTP session, e.g. a fake in tests. A new one is made when omitted.
        """
        self.settings = settings if settings is not None else LimsSettings()
        self._http = http if http is not None else requests.Session()

    def __str__(self) -> str:
        return f"{type(self).__name__}({self.settings.base_url})"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Sends a request and returns the decoded JSON body, raising :class:`LimsError` on any failure."""
        url = self.settings.base_url.rstrip("/") + path
        try:
            response = self._http.request(method, url, timeout=self.settings.timeout, **kwargs)
        except requests.RequestException as e:
            raise LimsError(f"Could not reach the LIMS API at {url}: {e}") from e
        if not response.ok:
            raise LimsError(f"{method} {path} failed with HTTP {response.status_code}: {response.text[:500]}")
        return response.json()

    def healthcheck(self) -> dict[str, Any]:
        """Returns the server's health status and version."""
        return self._request("GET", "/healthcheck")

    def current_event(
        self, mouse_id: str, *, instrument: str | None = None, event_types: Iterable[str] = ()
    ) -> SessionPlan:
        """
        Asks what a rig should run for a mouse right now.

        Args:
            mouse_id: The subject.
            instrument: The rig's instrument name (its SIPE comp_id). An event on another
                instrument is returned but is not ``scheduled``.
            event_types: Event types the rig runs; their settings are returned even when today's
                event lacks them or there is no event.
        """
        params: dict[str, Any] = {"mouse_id": mouse_id}
        if instrument:
            params["instrument"] = instrument
        if types := list(event_types):
            params["event_type"] = types  # requests repeats the key, as the API expects
        return SessionPlan.model_validate(self._request("GET", "/planned_events/current", params=params))

    def planned_events(
        self,
        *,
        instrument: str | None = None,
        mouse_id: str | None = None,
        start_gte: datetime.datetime | None = None,
        start_lte: datetime.datetime | None = None,
        page_size: int = 100,
    ) -> list[PlannedEvent]:
        """Returns every planned event matching the filters, newest start first, following all pages."""
        params: dict[str, Any] = {"limit": page_size, "offset": 0}
        if instrument:
            params["instrument"] = instrument
        if mouse_id:
            params["mouse_id"] = mouse_id
        if start_gte is not None:
            params["start_gte"] = start_gte.isoformat()
        if start_lte is not None:
            params["start_lte"] = start_lte.isoformat()
        events: list[PlannedEvent] = []
        while True:
            page = _PlannedEventsPage.model_validate(self._request("GET", "/planned_events", params=params))
            events.extend(page.items)
            if page.next_offset is None:
                return events
            params["offset"] = page.next_offset

    def subjects_scheduled_today(self, instrument: str, *, now: datetime.datetime | None = None) -> list[str]:
        """Returns the mice with an event on this instrument today, in order of start time."""
        start, end = _today_bounds(now)
        events = self.planned_events(instrument=instrument, start_gte=start, start_lte=end)
        far_future = datetime.datetime.max.replace(tzinfo=datetime.UTC)
        events.sort(key=lambda e: e.start or far_future)
        return list(dict.fromkeys(e.mouse_id for e in events if e.mouse_id))

    def push_trainer_state(self, mouse_id: str, trainer_state: TrainerState) -> None:
        """Writes the curriculum's suggestion for the mouse's next session."""
        payload = {"mouse_id": mouse_id, "trainer_state": trainer_state.model_dump(mode="json")}
        self._request("POST", "/behavior_settings", json=payload)


class LimsStore(StoreBase):
    """
    A store over the PowerLIMS API, serving trainer state.

    Reads come from the session's :class:`SessionPlan`, fetched once per subject and kept, so the
    trainer state and the event the launcher shows are the same snapshot; :meth:`plan` exposes it.
    Writes append the curriculum's suggestion as a new behavior settings row.

    Scope:
        ``subject``: Required.
        ``task_name``: Required for trainer state. Sent as an event type, so it must match the
            name ``/event_types`` uses for the task.
        ``instrument``: The rig's instrument name. Defaults to ``computer_name``, which defaults
            to this machine's name.

    Example:
        ```python
        lims = LimsStore(extra_event_types=["FIP"])
        store = CompositeStore(
            default=LocalFileStore(root=VR_LIB),
            routes={"trainer_state": lims},
        ).scoped(subject=session.subject, task_name="AindVrForaging")

        trainer_state = store.resolve(Kind.from_trainer_state())
        plan = lims.scoped(subject=session.subject, task_name="AindVrForaging").plan()
        ```
    """

    def __init__(
        self,
        settings: LimsSettings | None = None,
        *,
        client: LimsClient | None = None,
        scope: Scope | None = None,
        extra_event_types: Iterable[str] = (),
    ) -> None:
        """
        Args:
            settings: Which server to use. Read from clabe.yml when omitted. Ignored when ``client`` is given.
            client: A ready client. Built from ``settings`` when omitted.
            scope: Initial scope. ``computer_name`` defaults to this machine's.
            extra_event_types: Event types to resolve settings for besides ``task_name``, e.g. FIP,
                so their settings are in the plan too.
        """
        super().__init__(scope={"computer_name": get_computer_name(), **(scope or {})})
        self._client = client if client is not None else LimsClient(settings)
        self._extra_event_types = tuple(extra_event_types)
        # Shared by every scoped view (scoped() copies attributes by reference), so a plan is fetched once.
        self._plans: dict[tuple[str, str | None, tuple[str, ...]], SessionPlan] = {}

    def __str__(self) -> str:
        return f"{type(self).__name__}({self._client.settings.base_url})"

    @property
    def client(self) -> LimsClient:
        """The underlying API client."""
        return self._client

    def plan(self, *, scope: Scope | None = None, refresh: bool = False) -> SessionPlan:
        """
        Returns the plan for the subject in scope, fetching it on first use.

        Its warnings, and whether the mouse is unscheduled here, are shown to the user when fetched.

        Args:
            scope: Layered over the store's own scope.
            refresh: Fetch again even if a plan is already kept.

        Raises:
            LookupError: If ``subject`` is missing from scope.
            LimsError: If the API could not be reached or answered with an error.
        """
        merged = self._merge_scope(scope)
        subject = self._require(merged, "subject")
        instrument = self._instrument(merged)
        event_types = self._event_types(merged)
        key = (subject, instrument, event_types)
        if refresh or key not in self._plans:
            with ui.activity(f"Asking LIMS what to run for {subject}..."):
                plan = self._client.current_event(subject, instrument=instrument, event_types=event_types)
            self._announce(plan, instrument)
            self._plans[key] = plan
        return self._plans[key]

    def scheduled_subjects(self) -> list[str]:
        """
        Returns the mice with an event on this rig today, in order of start time.

        Meant as ``SessionBuilder(subject_suggestions=store.scheduled_subjects)``.

        Raises:
            LimsError: If the API could not be reached or answered with an error.
        """
        instrument = self._instrument(self.scope)
        if instrument is None:
            raise LookupError("LimsStore needs an instrument or computer_name in scope to look up the schedule.")
        return self._client.subjects_scheduled_today(instrument)

    def _candidates(self, kind: Kind[T], scope: Scope) -> Sequence[Candidate[T]]:
        """Returns the trainer state the plan resolved for the task in scope, or nothing."""
        self._require_supported(kind)
        task_name = self._require(scope, "task_name")
        resolved = self.plan(scope=scope).settings.get(task_name)
        row = resolved.behavior() if resolved is not None else None
        if resolved is None or row is None:
            logger.warning("LIMS has no %s settings for %s.", task_name, scope.get("subject"))
            return []
        if row.trainer_state is None:
            logger.warning("LIMS settings row %s for %s has no trainer state.", row.id, task_name)
            return []
        trainer_state = kind.adapter.validate_python(row.trainer_state)
        return [Candidate(self._label(resolved.source, row), trainer_state)]

    def write(self, kind: KindLike[T], value: T, *, scope: Scope | None = None) -> None:
        """
        Writes the trainer state as the subject's next settings.

        The kept plan for the subject is dropped, so a later read sees the new one.

        Raises:
            TypeError: If the value is not a trainer state.
            LimsError: If the API could not be reached or answered with an error.
        """
        self._require_supported(as_kind(kind))
        if not isinstance(value, TrainerState):
            raise TypeError(f"Expected a TrainerState, got {type(value).__name__}.")
        subject = self._require(self._merge_scope(scope), "subject")
        logger.info("Pushing trainer state to LIMS for subject %s.", subject)
        self._client.push_trainer_state(subject, value)
        for key in [k for k in self._plans if k[0] == subject]:
            del self._plans[key]

    def _event_types(self, scope: Scope) -> tuple[str, ...]:
        """The task in scope followed by the extra event types, without repeats."""
        task_name = scope.get("task_name")
        return tuple(dict.fromkeys([*([task_name] if task_name else []), *self._extra_event_types]))

    @staticmethod
    def _instrument(scope: Scope) -> str | None:
        return scope.get("instrument") or scope.get("computer_name") or None

    @staticmethod
    def _announce(plan: SessionPlan, instrument: str | None) -> None:
        """Surfaces what the plan says about the schedule, so the operator sees it before running."""
        if plan.scheduled and plan.event is not None:
            ui.notify(
                f"{plan.mouse_id} is scheduled for {plan.event.event_name or 'an event'}"
                f" on {plan.event.instrument or instrument} (trainer: {plan.event.trainer or 'unknown'})."
            )
        elif not plan.scheduled:
            ui.notify(
                f"{plan.mouse_id} is not scheduled on {instrument or 'any instrument'} today.", ui.MessageLevel.WARNING
            )
        for warning in plan.warnings:
            ui.notify(f"LIMS: {warning}", ui.MessageLevel.WARNING)

    @staticmethod
    def _require_supported(kind: Kind[Any]) -> None:
        if kind.name != _TRAINER_STATE:
            raise LookupError(f"LimsStore only serves {_TRAINER_STATE!r}, not {kind.name!r}.")

    @staticmethod
    def _require(scope: Scope, key: str) -> str:
        value = scope.get(key)
        if not value:
            raise LookupError(f"LimsStore requires {key!r} in scope.")
        return value

    @staticmethod
    def _label(source: str, row: BehaviorSettingsRow) -> str:
        created = row.created_on.isoformat(timespec="minutes") if row.created_on else "unknown date"
        return f"{row.stage_name or 'unknown stage'} ({source}, {created})"


__all__ = [
    "BehaviorSettingsRow",
    "FipFiber",
    "FipSettingsRow",
    "LimsClient",
    "LimsError",
    "LimsSettings",
    "LimsStore",
    "PlannedEvent",
    "ResolvedSettings",
    "ScheduledEvent",
    "SessionPlan",
]
