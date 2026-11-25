import datetime
import json
from typing import Any, Literal

import pytest
import requests
from aind_behavior_curriculum import (
    Metrics,
    Stage,
    StageTransition,
    Task,
    TaskParameters,
    TrainerState,
    create_curriculum,
)
from pydantic import ValidationError

from clabe import ui
from clabe.stores import CompositeStore, Kind, MemoryStore
from clabe.stores.powerlims import (
    BehaviorRow,
    EventQuery,
    FipRow,
    InvalidSettingsError,
    InvalidTrainerStateError,
    PlannedEvent,
    PowerLimsClient,
    PowerLimsError,
    PowerLimsSettings,
    PowerLimsStore,
    SettingsRow,
)

TASK = "AindVrForaging"
FIP = "FIP Fiber Photometry"
TRAINER_STATE = Kind.from_trainer_state()


class _VrTask(Task):
    name: Literal["AindVrForaging"] = TASK
    task_parameters: TaskParameters = TaskParameters()


class _DynamicForagingTask(Task):
    name: Literal["AindDynamicForaging"] = "AindDynamicForaging"
    task_parameters: TaskParameters = TaskParameters()


def make_trainer_state(stage: str = "stage_a") -> TrainerState:
    return TrainerState(curriculum=None, stage=Stage(name=stage, task=_VrTask()), is_on_curriculum=True)


def _never(metrics: Metrics) -> bool:
    return False


def curriculum_trainer_state_as_served() -> dict:
    """A trainer state with a curriculum, as the API serves it: decoded JSON, so the graph's int keys are strings."""
    curriculum = create_curriculum("Tiny", "0.1.0", [_VrTask])()
    first = Stage(name="stage_a", task=_VrTask())
    curriculum.add_stage_transition(first, Stage(name="stage_b", task=_VrTask()), StageTransition(_never))
    return json.loads(TrainerState(curriculum=curriculum, stage=first, is_on_curriculum=True).model_dump_json())


def behavior_row(row_id: str = "row-1", stage: str = "stage_a", trainer_state: Any = ...) -> dict:
    ts = make_trainer_state(stage).model_dump(mode="json") if trainer_state is ... else trainer_state
    return {
        "id": row_id,
        "created_on": "2026-11-19T17:00:00Z",
        "task_name": TASK,
        "stage_name": stage,
        "has_trainer_state": ts is not None,
        "trainer_state": ts,
    }


def event(
    *,
    instrument: str = "RIG-01",
    start: str = "2026-11-20T17:00:00Z",
    source: str = "pinned",
    row: Any = ...,
    task: str = TASK,
) -> dict:
    """An item of /planned_events in the shape the API documents: settings keyed by kind."""
    return {
        "event_id": f"evt-{instrument}-{start}",
        "event_name": None,
        "mouse_id": "789012",
        "instrument": instrument,
        "start": start,
        "status": "planned",
        "event_types": [task, FIP],
        "trainer": "Jane Doe",
        "scientific_contact": "Ann Smith",
        "settings": {
            "behavior": {"event_type": task, "source": source, "row": behavior_row() if row is ... else row},
            "fip": {
                "event_type": FIP,
                "source": "latest",
                "row": {"id": "fip-1", "led_470nm": 40.0, "fibers": [{"fiber": 0, "enabled": True, "patch_cord": "0"}]},
            },
        },
    }


def page(*items: dict, next_offset: int | None = None, offset: int = 0) -> dict:
    return {"limit": 100, "offset": offset, "next_offset": next_offset, "items": list(items)}


class FakeResponse:
    def __init__(self, status: int, body: Any):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = body
        self.text = str(body)

    def json(self) -> Any:
        return self._body


class FakeHttp:
    """Stands in for ``requests.Session``, answering from a route table and recording every request."""

    def __init__(self, routes: dict[tuple[str, str], Any] | None = None):
        self.routes = routes or {}
        self.calls: list[dict] = []

    def request(self, method: str, url: str, **kwargs) -> FakeResponse:
        path = url.removeprefix("http://powerlims")
        self.calls.append({"method": method, "path": path, **kwargs})
        answer = self.routes.get((method, path))
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer(kwargs)
        if isinstance(answer, FakeResponse):
            return answer
        if answer is None:
            return FakeResponse(404, {"detail": "Not Found"})
        return FakeResponse(200, answer)


@pytest.fixture
def http() -> FakeHttp:
    return FakeHttp({("GET", "/planned_events"): page(event())})


@pytest.fixture
def client(http) -> PowerLimsClient:
    return PowerLimsClient(PowerLimsSettings(base_url="http://powerlims"), http=http)  # type: ignore[arg-type]


def make_store(client: PowerLimsClient, **scope: str) -> PowerLimsStore:
    return PowerLimsStore(
        client=client, scope={"computer_name": "RIG-01", "subject": "789012", "task_name": TASK, **scope}
    )


def calls(http: FakeHttp) -> list[dict]:
    return [c for c in http.calls if c["path"] == "/planned_events"]


def answer_with(http: FakeHttp, *items: dict) -> None:
    http.routes[("GET", "/planned_events")] = page(*items)


class TestClient:
    def test_asks_for_the_day_and_only_the_filters_given(self, client, http):
        client.events_on(datetime.date(2026, 11, 20))
        client.events_on(mouse_id="789012", settings=[f"behavior:{TASK}", "fip"], expand_settings=True)
        first, second = (c["params"] for c in calls(http))
        assert first.keys() == {"start_gte", "start_lte", "limit", "offset"}
        assert first["start_gte"].startswith("2026-11-20T00:00:00")
        assert first["start_lte"].startswith("2026-11-20T23:59:59.999999")
        assert second["mouse_id"] == "789012" and second["expand_settings"] == "true"
        assert second["settings"] == [f"behavior:{TASK}", "fip"]

    def test_settings_are_typed_by_kind(self, client):
        (planned,) = client.events_on()
        assert isinstance(planned, PlannedEvent) and planned.scientific_contact == "Ann Smith"
        behavior, fip = planned.settings.behavior, planned.settings.fip
        assert (behavior.event_type, behavior.source, behavior.row.stage_name) == (TASK, "pinned", "stage_a")
        assert (fip.event_type, fip.row.led_470nm, fip.row.fibers[0].patch_cord) == (FIP, 40.0, "0")

    def test_unknown_fields_and_kinds_are_ignored(self, http, client):
        answer_with(http, {**event(), "brand_new_field": 1, "settings": {"imaging": {"source": "none"}}})
        (planned,) = client.events_on()
        assert planned.settings.behavior is None and planned.settings.fip is None

    def test_http_errors_raise_lims_error(self, http, client):
        http.routes[("GET", "/planned_events")] = FakeResponse(403, {"message": "Forbidden"})
        with pytest.raises(PowerLimsError, match="HTTP 403"):
            client.events_on()

    def test_connection_errors_raise_lims_error(self, http, client):
        http.routes[("GET", "/planned_events")] = requests.ConnectionError("no route")
        with pytest.raises(PowerLimsError, match="Could not reach"):
            client.events_on()

    def test_follows_every_page(self, http, client):
        pages = {0: page({"mouse_id": "a"}, next_offset=1), 1: page({"mouse_id": "b"}, offset=1)}
        http.routes[("GET", "/planned_events")] = lambda kw: pages[kw["params"]["offset"]]
        assert [e.mouse_id for e in client.events_on()] == ["a", "b"]

    def test_scheduled_subjects_are_ordered_by_start_and_unique(self, http, client):
        answer_with(
            http,
            {"mouse_id": "late", "start": "2026-11-20T23:00:00Z"},
            {"mouse_id": "early", "start": "2026-11-20T16:00:00Z"},
            {"mouse_id": "late", "start": "2026-11-21T00:00:00Z"},
            {"mouse_id": None, "start": "2026-11-20T18:00:00Z"},
        )
        assert client.scheduled_subjects("RIG-01", datetime.date(2026, 11, 20)) == ["early", "late"]
        params = calls(http)[0]["params"]
        assert params["instrument"] == "RIG-01" and params["start_gte"].startswith("2026-11-20T00:00:00")
        assert "expand_settings" not in params


class TestRead:
    def test_resolve_deserializes_the_trainer_state(self, client, http):
        trainer_state = make_store(client).resolve(TRAINER_STATE)
        assert isinstance(trainer_state, TrainerState)
        assert trainer_state.stage is not None and trainer_state.stage.name == "stage_a"
        params = calls(http)[0]["params"]
        assert params["mouse_id"] == "789012" and params["expand_settings"] == "true"
        assert "instrument" not in params  # filtered client-side, so a moved mouse is still found

    def test_events_on_this_rig_win(self, http, client):
        answer_with(
            http,
            event(instrument="RIG-02", row=behavior_row("elsewhere", "stage_x")),
            event(instrument="RIG-01", row=behavior_row("here", "stage_b")),
        )
        assert make_store(client).resolve(TRAINER_STATE).stage.name == "stage_b"

    def test_instrument_scope_overrides_the_computer_name(self, http, client):
        answer_with(
            http,
            event(instrument="RIG-01", row=behavior_row("a", "stage_a")),
            event(instrument="FRG.4A", row=behavior_row("b", "stage_b")),
        )
        assert make_store(client, instrument="FRG.4A").resolve(TRAINER_STATE).stage.name == "stage_b"

    def test_mouse_scheduled_elsewhere_still_resolves_with_a_warning(self, http, client, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        notices: list[tuple[str, ui.MessageLevel]] = []
        mock_frontend.notify = lambda message, level=ui.MessageLevel.INFO: notices.append((message, level))
        answer_with(http, event(instrument="RIG-02"))
        assert make_store(client).resolve(TRAINER_STATE).stage.name == "stage_a"
        assert any("not scheduled on RIG-01" in m and lvl == ui.MessageLevel.WARNING for m, lvl in notices)

    def test_a_latest_row_shared_by_two_events_is_offered_once(self, http, client):
        answer_with(
            http,
            event(start="2026-11-20T17:00:00Z", source="latest"),
            event(start="2026-11-20T21:00:00Z", source="latest"),
        )
        assert len(make_store(client).list(TRAINER_STATE)) == 1

    def test_distinct_rows_are_all_offered(self, http, client):
        answer_with(
            http,
            event(start="2026-11-20T17:00:00Z", row=behavior_row("a", "stage_a")),
            event(start="2026-11-20T21:00:00Z", row=behavior_row("b", "stage_b")),
        )
        assert [ts.stage.name for ts in make_store(client).list(TRAINER_STATE)] == ["stage_a", "stage_b"]

    def test_events_are_fetched_once_across_scoped_views(self, client, http):
        store = make_store(client)
        store.resolve(TRAINER_STATE)
        store.scoped().events()
        store.list(TRAINER_STATE)
        assert len(calls(http)) == 1

    def test_refresh_fetches_again(self, client, http):
        store = make_store(client)
        store.events()
        store.events(refresh=True)
        assert len(calls(http)) == 2

    def test_no_events_today_raises_lookup_error(self, http, client):
        answer_with(http)
        with pytest.raises(LookupError):
            make_store(client).resolve(TRAINER_STATE)

    def test_no_settings_row_raises_lookup_error(self, http, client):
        answer_with(http, event(source="none", row=None))
        with pytest.raises(LookupError):
            make_store(client).resolve(TRAINER_STATE)

    def test_a_row_without_trainer_state_raises_lookup_error(self, http, client):
        answer_with(http, event(row=behavior_row(trainer_state=None)))
        with pytest.raises(LookupError):
            make_store(client).resolve(TRAINER_STATE)

    def test_a_curriculum_with_string_graph_keys_deserializes(self, http, client):
        served = curriculum_trainer_state_as_served()
        assert list(served["curriculum"]["graph"]["nodes"]) == ["0", "1"]  # what JSON does to int keys
        answer_with(http, event(row=behavior_row(trainer_state=served)))
        trainer_state = make_store(client).resolve(TRAINER_STATE)
        assert trainer_state.curriculum is not None and trainer_state.stage.name == "stage_a"

    def test_a_malformed_trainer_state_raises_naming_the_row(self, http, client):
        stub = {"curriculum": {"name": "Depletion"}, "stage": {"name": "s", "task": {"name": TASK}}}
        answer_with(http, event(row=behavior_row("stub-row", trainer_state=stub)))
        with pytest.raises(InvalidTrainerStateError, match="stub-row") as raised:
            make_store(client).resolve(TRAINER_STATE)
        assert isinstance(raised.value.__cause__, ValidationError)  # pydantic's field report is kept

    def test_a_malformed_row_raises_even_next_to_a_valid_one(self, http, client):
        stub = {"stage": {"name": "s", "task": {"name": TASK}}}
        answer_with(
            http,
            event(start="2026-11-20T17:00:00Z", row=behavior_row("stub-row", trainer_state=stub)),
            event(start="2026-11-20T21:00:00Z", row=behavior_row("good-row", "stage_b")),
        )
        with pytest.raises(InvalidTrainerStateError, match="stub-row"):
            make_store(client).list(TRAINER_STATE)

    def test_a_trainer_state_the_server_could_not_read_raises(self, http, client):
        """``has_trainer_state`` with a null state means the server could not parse the stored file."""
        unreadable = {**behavior_row("bad-file-row"), "has_trainer_state": True, "trainer_state": None}
        answer_with(http, event(row=unreadable))
        with pytest.raises(InvalidTrainerStateError, match="bad-file-row"):
            make_store(client).resolve(TRAINER_STATE)

    def test_a_behavior_row_for_another_task_is_ignored(self, http, client):
        other = {**behavior_row("df-row"), "task_name": "AindDynamicForaging"}
        answer_with(http, event(task="AindDynamicForaging", row=other))
        with pytest.raises(LookupError):
            make_store(client).resolve(TRAINER_STATE)

    def test_the_store_reads_the_day_it_is_given(self, client, http):
        PowerLimsStore(client=client, scope={"subject": "789012"}, day=datetime.date(2026, 11, 20)).events()
        assert calls(http)[0]["params"]["start_gte"].startswith("2026-11-20T00:00:00")

    def test_a_trainer_state_for_another_task_raises(self, http, client):
        other = TrainerState(curriculum=None, stage=Stage(name="s", task=_DynamicForagingTask()), is_on_curriculum=True)
        answer_with(http, event(row=behavior_row("mixed-row", trainer_state=other.model_dump(mode="json"))))
        with pytest.raises(InvalidTrainerStateError, match="mixed-row.*AindDynamicForaging"):
            make_store(client).resolve(TRAINER_STATE)

    def test_a_stageless_trainer_state_is_rejected_by_the_kind_validator(self, http, client):
        answer_with(http, event(row=behavior_row(trainer_state=TrainerState.default().model_dump(mode="json"))))
        with pytest.raises(ValueError, match="no stage"):
            make_store(client).resolve(TRAINER_STATE)

    def test_missing_subject_or_task_raises(self, client):
        with pytest.raises(LookupError, match="subject"):
            PowerLimsStore(client=client, scope={"task_name": TASK}).resolve(TRAINER_STATE)
        with pytest.raises(LookupError, match="task_name"):
            PowerLimsStore(client=client, scope={"subject": "789012"}).resolve(TRAINER_STATE)

    def test_other_kinds_are_refused(self, client):
        with pytest.raises(LookupError, match="only serves"):
            make_store(client).resolve(Kind(PlannedEvent))


class TestQuery:
    def test_row_types_build_settings_filters(self):
        query = EventQuery(settings=[FipRow.where(), BehaviorRow.where(TASK)])
        assert list(query.settings) == ["fip", f"behavior:{TASK}"]
        assert query.to_params() == {"settings": ["fip", f"behavior:{TASK}"]}

    @pytest.mark.parametrize("bad", ["imaging", "fip:anything", "behavior:"])
    def test_invalid_settings_filters_are_rejected_before_sending(self, bad):
        with pytest.raises(ValidationError):
            EventQuery(settings=[bad])

    def test_a_kind_without_discriminator_cannot_be_narrowed(self):
        with pytest.raises(ValueError, match="no discriminator"):
            FipRow.where("x")

    def test_matches_mirrors_the_server_filter(self):
        planned = PlannedEvent.model_validate_json(json.dumps(event()))
        assert EventQuery(settings=[FipRow.where(), BehaviorRow.where(TASK)]).matches(planned)
        assert not EventQuery(settings=[BehaviorRow.where("AindDynamicForaging")]).matches(planned)

    def test_naive_query_times_are_local(self):
        query = EventQuery(start_gte=datetime.datetime(2026, 11, 20, 9))
        assert query.start_gte.utcoffset() == datetime.datetime(2026, 11, 20, 9).astimezone().utcoffset()

    def test_every_registered_kind_has_a_field_on_event_settings(self):
        assert set(SettingsRow.registry) == {"behavior", "fip"}


class TestTypedSettings:
    def test_trainer_state_is_a_trainer_state(self, client, http):
        (planned,) = client.events_on(expand_settings=True)
        assert isinstance(planned.settings.row(BehaviorRow).trainer_state, TrainerState)

    def test_rows_dispatch_by_type(self, client):
        (planned,) = client.events_on()
        seen = []
        for row in planned.settings.rows():
            match row:
                case FipRow():
                    seen.append(("fip", row.fibers[0].fiber))
                case BehaviorRow():
                    seen.append(("behavior", row.task_name))
        assert seen == [("behavior", TASK), ("fip", 0)]
        assert planned.kinds == ["behavior", "fip"]
        assert planned.has(BehaviorRow, TASK) and not planned.has(BehaviorRow, "Other")

    def test_an_invalid_fip_row_raises_naming_it(self, http, client):
        bad = event()
        bad["settings"]["fip"]["row"] = {"id": "fip-bad", "fibers": [{"fiber": "not a number"}]}
        answer_with(http, bad)
        with pytest.raises(InvalidSettingsError, match="fip settings row fip-bad") as raised:
            client.events_on()
        assert raised.value.kind == "fip" and not isinstance(raised.value, InvalidTrainerStateError)

    def test_events_at_keeps_only_ongoing_events(self, http, client):
        answer_with(
            http,
            {**event(start="2026-11-20T08:00:00Z"), "end": "2026-11-20T09:00:00Z", "mouse_id": "done"},
            {**event(start="2026-11-20T09:30:00Z"), "end": "2026-11-20T11:00:00Z", "mouse_id": "now"},
            {**event(start="2026-11-20T09:45:00Z"), "end": None, "mouse_id": "open"},
        )
        at = datetime.datetime(2026, 11, 20, 10, tzinfo=datetime.UTC)
        assert [e.mouse_id for e in client.events_at(at, settings=[BehaviorRow.where(TASK)])] == ["now", "open"]
        params = calls(http)[0]["params"]
        assert params["settings"] == [f"behavior:{TASK}"]
        assert datetime.datetime.fromisoformat(params["start_lte"]) == at


class TestSettingsRowsFromTheStore:
    def test_resolves_the_fip_row(self, client):
        fip = make_store(client).resolve(Kind(FipRow))
        assert isinstance(fip, FipRow) and fip.led_470nm == 40.0

    def test_behavior_rows_are_narrowed_by_task_name_in_scope(self, http, client):
        answer_with(http, event(row={**behavior_row("df"), "task_name": "AindDynamicForaging"}))
        with pytest.raises(LookupError):
            make_store(client).resolve(Kind(BehaviorRow))
        assert make_store(client, task_name="AindDynamicForaging").resolve(Kind(BehaviorRow)).id == "df"


class TestReadOnly:
    def test_write_is_not_supported_and_sends_nothing(self, client, http):
        with pytest.raises(NotImplementedError, match="read-only"):
            make_store(client).write(TRAINER_STATE, make_trainer_state())
        assert http.calls == []


class TestComposition:
    def test_routes_trainer_state_through_a_composite(self, client, http):
        store = CompositeStore(default=MemoryStore(), routes={"trainer_state": PowerLimsStore(client=client)}).scoped(
            subject="789012", task_name=TASK, computer_name="RIG-01"
        )
        assert store.resolve(TRAINER_STATE).stage is not None
        assert [c["method"] for c in http.calls] == ["GET"]

    def test_scheduled_subjects_uses_the_computer_name(self, client, http):
        answer_with(http, {"mouse_id": "789012"})
        assert make_store(client).scheduled_subjects() == ["789012"]
        assert calls(http)[0]["params"]["instrument"] == "RIG-01"
