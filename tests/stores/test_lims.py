import datetime
from typing import Any

import pytest
import requests
from aind_behavior_curriculum import Stage, Task, TaskParameters, TrainerState

from clabe import ui
from clabe.stores import CompositeStore, Kind, MemoryStore
from clabe.stores.lims import (
    LimsClient,
    LimsError,
    LimsSettings,
    LimsStore,
    SessionPlan,
)

TASK = "AindVrForaging"
TRAINER_STATE = Kind.from_trainer_state()


class _VrTask(Task):
    name: str = TASK
    task_parameters: TaskParameters = TaskParameters()


def make_trainer_state(stage: str = "stage_a") -> TrainerState:
    return TrainerState(curriculum=None, stage=Stage(name=stage, task=_VrTask()), is_on_curriculum=True)


def current_event_body(*, scheduled: bool = True, trainer_state: dict | None = None, **overrides) -> dict:
    """An answer of /planned_events/current in the shape the API documents."""
    ts = make_trainer_state().model_dump(mode="json") if trainer_state is None else trainer_state
    body = {
        "mouse_id": "789012",
        "scheduled": scheduled,
        "event": {
            "event_id": "evt-1",
            "event_name": "VR foraging",
            "mouse_id": "789012",
            "instrument": "RIG-01",
            "start": "2026-11-20T09:00:00-08:00",
            "event_types": [TASK, "FIP"],
            "trainer": "j.doe",
            "scientific_contact": "a.smith",
        },
        "settings": {
            TASK: {
                "source": "pinned",
                "row": {
                    "id": "row-1",
                    "created_on": "2026-11-19T17:00:00Z",
                    "task_name": TASK,
                    "stage_name": "stage_a",
                    "has_trainer_state": True,
                    "trainer_state": ts,
                },
            },
            "FIP": {
                "source": "latest",
                "row": {"id": "fip-1", "led_470nm": 40.0, "fibers": [{"fiber": 0, "enabled": True}]},
            },
        },
        "warnings": [],
    }
    body.update(overrides)
    return body


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
        self.headers: dict[str, str] = {}
        self.routes = routes or {}
        self.calls: list[dict] = []

    def request(self, method: str, url: str, **kwargs) -> FakeResponse:
        path = url.removeprefix("http://lims")
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
    return FakeHttp(
        {
            ("GET", "/planned_events/current"): current_event_body(),
            ("POST", "/behavior_settings"): FakeResponse(201, {"id": "row-2", "created_on": "2026-11-20T11:00:00Z"}),
        }
    )


@pytest.fixture
def client(http) -> LimsClient:
    return LimsClient(LimsSettings(base_url="http://lims"), http=http)  # type: ignore[arg-type]


def make_store(client: LimsClient, **kwargs) -> LimsStore:
    scope = {"computer_name": "RIG-01", "subject": "789012", "task_name": TASK, **kwargs.pop("scope", {})}
    return LimsStore(client=client, scope=scope, **kwargs)


def gets(http: FakeHttp, path: str) -> list[dict]:
    return [c for c in http.calls if c["method"] == "GET" and c["path"] == path]


class TestClient:
    def test_current_event_repeats_event_type_and_omits_empty_filters(self, client, http):
        client.current_event("789012", event_types=[TASK, "FIP"])
        assert gets(http, "/planned_events/current")[0]["params"] == {"mouse_id": "789012", "event_type": [TASK, "FIP"]}

    def test_current_event_parses_into_a_session_plan(self, client):
        plan = client.current_event("789012", instrument="RIG-01")
        assert isinstance(plan, SessionPlan)
        assert plan.event is not None and plan.event.scientific_contact == "a.smith"
        fip = plan.settings["FIP"].fip()
        assert fip is not None and fip.led_470nm == 40.0 and fip.fibers[0].enabled

    def test_unknown_fields_are_ignored(self, http, client):
        http.routes[("GET", "/planned_events/current")] = current_event_body(brand_new_field=1)
        client.current_event("789012")

    def test_http_errors_raise_lims_error(self, http, client):
        http.routes[("GET", "/planned_events/current")] = FakeResponse(403, {"message": "Forbidden"})
        with pytest.raises(LimsError, match="HTTP 403"):
            client.current_event("789012")

    def test_connection_errors_raise_lims_error(self, http, client):
        http.routes[("GET", "/planned_events/current")] = requests.ConnectionError("no route")
        with pytest.raises(LimsError, match="Could not reach"):
            client.current_event("789012")

    def test_planned_events_follows_every_page(self, http, client):
        pages = {
            0: {"limit": 1, "offset": 0, "next_offset": 1, "items": [{"mouse_id": "a"}]},
            1: {"limit": 1, "offset": 1, "next_offset": None, "items": [{"mouse_id": "b"}]},
        }
        http.routes[("GET", "/planned_events")] = lambda kw: pages[kw["params"]["offset"]]
        assert [e.mouse_id for e in client.planned_events(page_size=1)] == ["a", "b"]

    def test_subjects_scheduled_today_are_ordered_by_start_and_unique(self, http, client):
        http.routes[("GET", "/planned_events")] = {
            "limit": 100,
            "offset": 0,
            "items": [
                {"mouse_id": "late", "start": "2026-11-20T15:00:00-08:00"},
                {"mouse_id": "early", "start": "2026-11-20T08:00:00-08:00"},
                {"mouse_id": "late", "start": "2026-11-20T16:00:00-08:00"},
                {"mouse_id": None, "start": "2026-11-20T10:00:00-08:00"},
            ],
        }
        now = datetime.datetime(2026, 11, 20, 12, tzinfo=datetime.timezone(datetime.timedelta(hours=-8)))
        assert client.subjects_scheduled_today("RIG-01", now=now) == ["early", "late"]
        params = gets(http, "/planned_events")[0]["params"]
        assert params["instrument"] == "RIG-01"
        assert params["start_gte"] == "2026-11-20T00:00:00-08:00"
        assert params["start_lte"].startswith("2026-11-20T23:59:59")


class TestRead:
    def test_resolve_returns_the_plan_trainer_state(self, client, http):
        trainer_state = make_store(client).resolve(TRAINER_STATE)
        assert trainer_state.stage is not None and trainer_state.stage.name == "stage_a"
        params = gets(http, "/planned_events/current")[0]["params"]
        assert params == {"mouse_id": "789012", "instrument": "RIG-01", "event_type": [TASK]}

    def test_extra_event_types_are_requested_after_the_task(self, client, http):
        make_store(client, extra_event_types=["FIP", TASK]).resolve(TRAINER_STATE)
        assert gets(http, "/planned_events/current")[0]["params"]["event_type"] == [TASK, "FIP"]

    def test_instrument_scope_overrides_the_computer_name(self, client, http):
        make_store(client, scope={"instrument": "FRG.4A"}).resolve(TRAINER_STATE)
        assert gets(http, "/planned_events/current")[0]["params"]["instrument"] == "FRG.4A"

    def test_the_plan_is_fetched_once_across_scoped_views(self, client, http):
        store = make_store(client)
        store.resolve(TRAINER_STATE)
        store.scoped().plan()
        store.list(TRAINER_STATE)
        assert len(gets(http, "/planned_events/current")) == 1

    def test_refresh_fetches_again(self, client, http):
        store = make_store(client)
        store.plan()
        store.plan(refresh=True)
        assert len(gets(http, "/planned_events/current")) == 2

    def test_no_settings_row_raises_lookup_error(self, http, client):
        body = current_event_body()
        body["settings"][TASK] = {"source": "none", "row": None}
        http.routes[("GET", "/planned_events/current")] = body
        with pytest.raises(LookupError):
            make_store(client).resolve(TRAINER_STATE)

    def test_a_row_without_trainer_state_raises_lookup_error(self, http, client):
        body = current_event_body()
        body["settings"][TASK]["row"]["trainer_state"] = None
        http.routes[("GET", "/planned_events/current")] = body
        with pytest.raises(LookupError):
            make_store(client).resolve(TRAINER_STATE)

    def test_a_stageless_trainer_state_is_rejected_by_the_kind_validator(self, http, client):
        http.routes[("GET", "/planned_events/current")] = current_event_body(
            trainer_state=TrainerState.default().model_dump(mode="json")
        )
        with pytest.raises(ValueError, match="no stage"):
            make_store(client).resolve(TRAINER_STATE)

    def test_missing_subject_or_task_raises(self, client):
        with pytest.raises(LookupError, match="subject"):
            LimsStore(client=client, scope={"task_name": TASK}).resolve(TRAINER_STATE)
        with pytest.raises(LookupError, match="task_name"):
            LimsStore(client=client, scope={"subject": "789012"}).resolve(TRAINER_STATE)

    def test_other_kinds_are_refused(self, client):
        with pytest.raises(LookupError, match="only serves"):
            make_store(client).resolve(Kind(SessionPlan))

    def test_unscheduled_mice_and_warnings_are_shown(self, http, client, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        http.routes[("GET", "/planned_events/current")] = current_event_body(
            scheduled=False, warnings=["Today's event is on RIG-02."]
        )
        notices: list[tuple[str, ui.MessageLevel]] = []
        mock_frontend.notify = lambda message, level=ui.MessageLevel.INFO: notices.append((message, level))
        make_store(client).plan()
        assert ("789012 is not scheduled on RIG-01 today.", ui.MessageLevel.WARNING) in notices
        assert ("LIMS: Today's event is on RIG-02.", ui.MessageLevel.WARNING) in notices


class TestWrite:
    def posts(self, http: FakeHttp) -> list[dict]:
        return [c["json"] for c in http.calls if c["method"] == "POST"]

    def test_write_posts_the_mouse_and_trainer_state(self, client, http):
        trainer_state = make_trainer_state("stage_b")
        make_store(client).write(TRAINER_STATE, trainer_state)
        (body,) = self.posts(http)
        assert body == {"mouse_id": "789012", "trainer_state": trainer_state.model_dump(mode="json")}

    def test_write_drops_the_kept_plan(self, client, http):
        store = make_store(client)
        store.plan()
        store.write(TRAINER_STATE, make_trainer_state("stage_b"))
        store.plan()
        assert len(gets(http, "/planned_events/current")) == 2

    def test_write_rejects_other_values(self, client):
        with pytest.raises(TypeError):
            make_store(client).write(TRAINER_STATE, "not a trainer state")  # type: ignore[arg-type]

    def test_write_errors_propagate(self, http, client):
        http.routes[("POST", "/behavior_settings")] = FakeResponse(500, {"detail": "boom"})
        with pytest.raises(LimsError, match="HTTP 500"):
            make_store(client).write(TRAINER_STATE, make_trainer_state())


class TestComposition:
    def test_routes_trainer_state_through_a_composite(self, client, http):
        store = CompositeStore(default=MemoryStore(), routes={"trainer_state": LimsStore(client=client)}).scoped(
            subject="789012", task_name=TASK, computer_name="RIG-01"
        )
        assert store.resolve(TRAINER_STATE).stage is not None
        store.write(TRAINER_STATE, make_trainer_state("stage_b"))
        assert [c["method"] for c in http.calls] == ["GET", "POST"]

    def test_scheduled_subjects_uses_the_computer_name(self, client, http):
        http.routes[("GET", "/planned_events")] = {"limit": 100, "offset": 0, "items": [{"mouse_id": "789012"}]}
        assert make_store(client).scheduled_subjects() == ["789012"]
        assert gets(http, "/planned_events")[0]["params"]["instrument"] == "RIG-01"
