"""Rigs enroll themselves with the hub; the hub keeps them, and the exclusions that keep rigs out, in its database."""

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from clabe.daemon import DaemonSettings
from clabe.daemon._enroll import enroll_body, enroll_forever, enroll_once, enroll_url
from clabe.hub import EnrollBody, Excluded, Fleet, HubSettings, Registry, create_hub_app

_HUB = {"Authorization": "Bearer hub-secret"}


def _hub(fleet: Fleet) -> TestClient:
    app = create_hub_app(fleet, token="hub-secret", static_dir=None, poll=False)
    return TestClient(app)


def _ids(client: TestClient) -> list[str]:
    return sorted(r["id"] for r in client.get("/api/hub/rigs", headers=_HUB).json())


def test_any_rig_can_enroll_but_the_rest_of_the_api_needs_the_hubs_token():
    client = _hub(Fleet(HubSettings()))
    body = {"id": "BEH.B-Box1", "host": "wxvs-syslogic7", "port": 24070, "clabe_version": "1.2.3"}
    enrolled = client.post("/api/hub/enroll", json=body)
    assert enrolled.status_code == 200, enrolled.text
    assert enrolled.json()["url"] == "http://wxvs-syslogic7:24070" and enrolled.json()["moved_from"] is None
    assert client.get("/api/hub/rigs").status_code == 401
    assert _ids(client) == ["BEH.B-Box1"]


@pytest.mark.parametrize(
    "change",
    [
        {"id": "has space"},
        {"id": "../up"},
        {"host": "http://evil:1/x"},
        {"host": "user@evil"},
        {"host": "evil/path"},
        {"port": 0},
        {"port": 70000},
    ],
)
def test_an_enrollment_names_a_plain_rig_and_host(change: dict):
    client = _hub(Fleet(HubSettings()))
    body = {"id": "rig-3", "host": "rig-3", "port": 24070} | change
    assert client.post("/api/hub/enroll", json=body).status_code == 422
    assert _ids(client) == []


def test_enrolling_again_from_the_same_address_keeps_the_rig_followed():
    fleet = Fleet(HubSettings())
    first = fleet.enroll(EnrollBody(id="rig-3", host="rig-3", port=24070))
    again = fleet.enroll(EnrollBody(id="rig-3", host="rig-3", port=24070, clabe_version="2"))
    assert (again.moved_from, again.clabe_version, again.enrolled_at) == (None, "2", first.enrolled_at)
    assert again.last_enrolled_at >= first.last_enrolled_at


def test_a_rig_enrolling_from_another_address_is_held_until_its_move_is_confirmed(tmp_path: Path):
    seen: list[tuple[str, str | None]] = []

    def rigs(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers.get("authorization")))
        return httpx.Response(200, json={"hostname": request.url.host, "clabe_version": "0", "status": "idle"})

    fleet = Fleet(HubSettings(), registry=Registry(tmp_path))
    fleet._client = httpx.AsyncClient(transport=httpx.MockTransport(rigs))
    client = _hub(fleet)
    fleet.enroll(EnrollBody(id="rig-3", host="rig-3", port=24070))
    fleet.set_token("rig-3", "rig-3-token")

    # someone enrolls under rig-3's name from elsewhere: held, never polled nor sent the token, not reachable
    moved = client.post("/api/hub/enroll", json={"id": "rig-3", "host": "laptop", "port": 24070})
    assert moved.json()["moved_from"] == "http://rig-3:24070" and fleet.rig_token("rig-3") is None
    asyncio.run(fleet.poll_once())
    assert seen == []
    status = client.get("/api/hub/rigs", headers=_HUB).json()[0]
    assert (status["moved_from"], status["reachable"], status["url"]) == (
        "http://rig-3:24070",
        False,
        "http://laptop:24070",
    )
    assert client.get("/rigs/rig-3/api/daemon/info", headers=_HUB).status_code == 409
    assert client.get("/api/hub/inbox", headers=_HUB).json() == []

    # moving on again still remembers the confirmed address; held across a restart too
    client.post("/api/hub/enroll", json={"id": "rig-3", "host": "laptop-2", "port": 24070})
    assert Fleet(HubSettings(), registry=Registry(tmp_path)).rigs["rig-3"].moved_from == "http://rig-3:24070"

    # back at its confirmed address: followed again, no click needed
    back = client.post("/api/hub/enroll", json={"id": "rig-3", "host": "rig-3", "port": 24070})
    assert back.json()["moved_from"] is None and fleet.rig_token("rig-3") == "rig-3-token"

    # a real move, confirmed on the page
    client.post("/api/hub/enroll", json={"id": "rig-3", "host": "10.0.0.7", "port": 24071})
    assert client.post("/api/hub/rigs/rig-3/confirm").status_code == 401
    assert client.post("/api/hub/rigs/nope/confirm", headers=_HUB).status_code == 404
    assert client.post("/api/hub/rigs/rig-3/confirm", headers=_HUB).status_code == 204
    assert fleet.rigs["rig-3"].moved_from is None and fleet.rig_token("rig-3") == "rig-3-token"
    asyncio.run(fleet.poll_once())
    assert seen == [("10.0.0.7", "Bearer rig-3-token")]
    assert Fleet(HubSettings(), registry=Registry(tmp_path)).rigs["rig-3"].url == "http://10.0.0.7:24071"


def test_an_exclusion_removes_matching_rigs_and_refuses_them(tmp_path: Path):
    fleet = Fleet(HubSettings(), registry=Registry(tmp_path))
    client = _hub(fleet)
    for rig_id, host in (("BEH.B-Box1", "wxvs-syslogic7"), ("BEH.B-Box2", "wxvs-syslogic8"), ("NP.1-Acq", "np1")):
        assert client.post("/api/hub/enroll", json={"id": rig_id, "host": host, "port": 24070}).is_success

    added = client.post("/api/hub/exclusions", json={"pattern": "beh.*", "comment": "behavior has its own hub"},
                        headers=_HUB)  # fmt: skip
    assert added.status_code == 200, added.text
    assert sorted(added.json()["removed"]) == ["BEH.B-Box1", "BEH.B-Box2"]  # case-insensitive glob
    assert _ids(client) == ["NP.1-Acq"]
    refused = client.post("/api/hub/enroll", json={"id": "BEH.B-Box3", "host": "x", "port": 1})
    assert refused.status_code == 403 and "beh.*" in refused.json()["detail"]

    # an exclusion matches the host too
    assert client.post("/api/hub/exclusions", json={"pattern": "np1"}, headers=_HUB).json()["removed"] == ["NP.1-Acq"]
    with pytest.raises(Excluded):
        fleet.enroll(EnrollBody(id="renamed", host="NP1", port=24070))

    listed = client.get("/api/hub/exclusions", headers=_HUB).json()
    assert [(e["pattern"], e["comment"]) for e in listed] == [("beh.*", "behavior has its own hub"), ("np1", None)]
    assert client.delete("/api/hub/exclusions", params={"pattern": "beh.*"}, headers=_HUB).status_code == 204
    assert client.delete("/api/hub/exclusions", params={"pattern": "beh.*"}, headers=_HUB).status_code == 404
    assert client.post("/api/hub/enroll", json={"id": "BEH.B-Box3", "host": "x", "port": 1}).is_success
    assert client.get("/api/hub/exclusions").status_code == 401


def test_removing_a_rig_drops_it_until_it_enrolls_again():
    fleet = Fleet(HubSettings())
    client = _hub(fleet)
    fleet.enroll(EnrollBody(id="rig-3", host="rig-3", port=24070))
    assert client.delete("/api/hub/rigs/rig-3").status_code == 401
    assert client.delete("/api/hub/rigs/rig-3", headers=_HUB).status_code == 204
    assert client.delete("/api/hub/rigs/rig-3", headers=_HUB).status_code == 404
    assert _ids(client) == [] and fleet.registry.rigs() == []
    fleet.enroll(EnrollBody(id="rig-3", host="rig-3", port=24070))
    assert _ids(client) == ["rig-3"]


def test_rigs_and_exclusions_survive_a_restart_of_the_hub(tmp_path: Path):
    fleet = Fleet(HubSettings(), registry=Registry(tmp_path))
    fleet.enroll(EnrollBody(id="rig-3", host="rig-3", port=24070))
    fleet.exclude("rig-9", "broken")
    fleet.registry.close()

    restarted = Fleet(HubSettings(), registry=Registry(tmp_path))
    assert list(restarted.rigs) == ["rig-3"] and restarted.status["rig-3"].status == "offline"
    assert [(e.pattern, e.comment) for e in restarted.registry.exclusions()] == [("rig-9", "broken")]
    with pytest.raises(Excluded):
        restarted.enroll(EnrollBody(id="rig-9", host="rig-9", port=24070))


def test_a_rigs_token_entered_in_the_hub_is_used_and_never_shown(tmp_path: Path):
    seen: dict[str, str | None] = {}

    def rigs(request: httpx.Request) -> httpx.Response:
        seen[request.url.host] = request.headers.get("authorization")
        return httpx.Response(200, json={"hostname": request.url.host, "clabe_version": "0", "status": "idle"})

    fleet = Fleet(HubSettings(), registry=Registry(tmp_path))
    fleet._client = httpx.AsyncClient(transport=httpx.MockTransport(rigs))
    client = _hub(fleet)
    for rig_id in ("rig-3", "rig-4"):
        assert client.post("/api/hub/enroll", json={"id": rig_id, "host": rig_id, "port": 24070})

    put = "/api/hub/rigs/rig-3/token"
    assert client.put(put, json={"token": "Xq7-rig3-secret"}).status_code == 401
    assert client.put("/api/hub/rigs/nope/token", json={"token": "x"}, headers=_HUB).status_code == 404
    assert client.put(put, json={"token": " Xq7-rig3-secret "}, headers=_HUB).status_code == 204
    asyncio.run(fleet.poll_once())
    assert seen == {"rig-3": "Bearer Xq7-rig3-secret", "rig-4": None}  # no token entered: none sent

    listed = client.get("/api/hub/rigs", headers=_HUB)
    assert {r["id"]: r["has_token"] for r in listed.json()} == {"rig-3": True, "rig-4": False}
    assert "Xq7-rig3-secret" not in listed.text and "Xq7-rig3-secret" not in client.get("/openapi.json").text
    again = client.post("/api/hub/enroll", json={"id": "rig-3", "host": "rig-3", "port": 24070})
    assert "Xq7-rig3-secret" not in again.text
    assert fleet.rig_token("rig-3") == "Xq7-rig3-secret"  # enrolling again keeps it

    restarted = Fleet(HubSettings(), registry=Registry(tmp_path))
    assert restarted.rig_token("rig-3") == "Xq7-rig3-secret" and restarted.rig_token("rig-4") is None

    assert client.put(put, json={"token": None}, headers=_HUB).status_code == 204
    assert fleet.rig_token("rig-3") is None
    fleet.set_token("rig-4", "four")
    fleet.remove("rig-4")
    fleet.enroll(EnrollBody(id="rig-4", host="rig-4", port=24070))
    assert fleet.rig_token("rig-4") is None  # removing a rig drops its token


def test_a_rig_that_refuses_the_hubs_token_says_it_needs_one():
    def rig(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != "Bearer rig-3-token":
            return httpx.Response(401, json={"detail": "A valid token is required."})
        return httpx.Response(200, json={"hostname": "rig-3", "clabe_version": "0", "status": "idle"})

    fleet = Fleet(HubSettings())
    fleet._client = httpx.AsyncClient(transport=httpx.MockTransport(rig))
    fleet.enroll(EnrollBody(id="rig-3", host="rig-3", port=24070))
    asyncio.run(fleet.poll_once())
    status = fleet.statuses()[0]
    assert (status.needs_token, status.reachable, status.status) == (True, False, "offline")
    assert "token" in status.error
    fleet.set_token("rig-3", "rig-3-token")
    asyncio.run(fleet.poll_once())
    status = fleet.statuses()[0]
    assert (status.needs_token, status.reachable, status.status, status.has_token) == (False, True, "idle", True)


def test_a_hub_db_from_before_rig_tokens_opens(tmp_path: Path):
    import sqlite3

    with sqlite3.connect(tmp_path / "hub.db") as db:
        db.execute(
            "CREATE TABLE rigs (id TEXT PRIMARY KEY, url TEXT NOT NULL, clabe_version TEXT, enrolled_at TEXT, "
            "last_enrolled_at TEXT)"
        )
        db.execute("INSERT INTO rigs (id, url) VALUES ('rig-3', 'http://rig-3:24070')")
    db.close()
    fleet = Fleet(HubSettings(), registry=Registry(tmp_path))
    assert list(fleet.rigs) == ["rig-3"] and fleet.set_token("rig-3", "own")
    assert fleet.rig_token("rig-3") == "own"


def test_a_rig_removed_while_it_is_polled_stays_removed():
    fleet = Fleet(HubSettings())

    async def slow(request: httpx.Request) -> httpx.Response:
        fleet.remove("rig-3")  # e.g. excluded from the page while the poll was on its way
        return httpx.Response(200, json={"hostname": "rig-3", "clabe_version": "0", "status": "idle"})

    fleet._client = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    fleet.enroll(EnrollBody(id="rig-3", host="rig-3", port=24070))
    asyncio.run(fleet.poll_once())
    assert "rig-3" not in fleet.status and "rig-3" not in fleet.rigs


def test_the_daemon_enrolls_with_each_hub_under_its_name(monkeypatch):
    monkeypatch.setattr("socket.gethostname", lambda: "wxvs-syslogic7")
    seen: list[tuple[str, dict, str | None]] = []

    def hub(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), json.loads(request.content), request.headers.get("authorization")))
        if request.url.host == "down":
            raise httpx.ConnectError("refused", request=request)
        if request.url.host == "strict":
            return httpx.Response(403, json={"detail": "Excluded by 'wxvs-*'"})
        return httpx.Response(200, json={})

    settings = DaemonSettings(port=24070, hubs=["https://vm/clabe/", "http://down:8080", "http://strict:8080"])
    client = httpx.AsyncClient(transport=httpx.MockTransport(hub))
    outcome = asyncio.run(enroll_once(settings, client))
    assert outcome["https://vm/clabe/"] is None
    assert outcome["http://down:8080"].startswith("not reachable")
    assert outcome["http://strict:8080"] == "403 Excluded by 'wxvs-*'"
    url, body, auth = seen[0]
    assert url == "https://vm/clabe/api/hub/enroll" and auth is None  # enrolling needs no secret
    assert (body["id"], body["host"], body["port"]) == ("wxvs-syslogic7", "wxvs-syslogic7", 24070)

    named = DaemonSettings(port=9000, enroll_name="BEH.B-Box1", enroll_host="10.0.0.7")
    assert {k: enroll_body(named)[k] for k in ("id", "host", "port")} == {
        "id": "BEH.B-Box1",
        "host": "10.0.0.7",
        "port": 9000,  # the port it listens on
    }
    assert enroll_url("http://hub:8080") == "http://hub:8080/api/hub/enroll"


def test_the_page_groups_excludes_removes_and_sets_tokens(tmp_path: Path):
    playwright_api = pytest.importorskip("playwright.sync_api")
    import threading
    import time

    import uvicorn
    from fastapi import FastAPI

    from clabe.server import free_port
    from clabe.server._app import STATIC_DIR

    if not (STATIC_DIR / "index.html").is_file():
        pytest.skip("the web UI is not built")

    live = FastAPI()  # a rig's daemon that answers, idle

    @live.get("/api/daemon/info")
    def info() -> dict:
        return {"hostname": "live-rig", "clabe_version": "0", "status": "idle"}

    def serve(app, port: int):
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        while not server.started:
            time.sleep(0.02)
        return server, thread

    live_port = free_port("127.0.0.1", 18730)
    fleet = Fleet(HubSettings(poll_interval=0.2), registry=Registry(tmp_path))
    fleet.enroll(EnrollBody(id="live-rig", host="127.0.0.1", port=live_port))
    for rig_id in ("BEH.B-Box1", "NP.1-Acq", "old-rig"):  # nobody there: offline
        fleet.enroll(EnrollBody(id=rig_id, host="127.0.0.1", port=free_port("127.0.0.1", 18700)))
    port = free_port("127.0.0.1", 18720)
    servers = [serve(live, live_port), serve(create_hub_app(fleet, token="hub-secret"), port)]
    try:
        with playwright_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel=os.environ.get("CLABE_TEST_BROWSER_CHANNEL") or None)
            except playwright_api.Error as exc:
                pytest.skip(f"no browser for Playwright: {exc}")
            page = browser.new_page()
            page.set_default_timeout(30_000)
            page.goto(f"http://127.0.0.1:{port}/?token=hub-secret")
            online = page.locator(".grid[aria-label='Online rigs']")
            online.get_by_role("article", name="live-rig").wait_for()  # the rig that answers is above the offline ones
            page.get_by_role("heading", name="Offline").wait_for()
            assert online.get_by_role("article").count() == 1

            page.get_by_text("Excluded", exact=True).click()
            page.get_by_label("Pattern").fill("beh.*")
            page.get_by_label("Reason").fill("its own hub")
            page.get_by_role("button", name="Exclude", exact=True).click()
            page.get_by_text("removed BEH.B-Box1").wait_for()
            page.get_by_role("article", name="BEH.B-Box1").wait_for(state="detached")

            page.get_by_role("button", name="Token of NP.1-Acq").click()
            page.get_by_label("New token of NP.1-Acq").fill("np-own-token")
            page.get_by_role("button", name="Save").click()
            page.get_by_role("button", name="Token of NP.1-Acq").filter(has_text="Token set").wait_for()

            page.get_by_role("button", name="Remove old-rig").click()
            page.get_by_role("button", name="Remove", exact=True).click()
            page.get_by_role("article", name="old-rig").wait_for(state="detached")

            page.get_by_role("button", name="Remove live-rig").click()  # a rig that answers can be removed too
            page.get_by_role("button", name="Remove and exclude").click()
            page.get_by_role("article", name="live-rig").wait_for(state="detached")
            browser.close()
        assert list(fleet.rigs) == ["NP.1-Acq"] and fleet.rig_token("NP.1-Acq") == "np-own-token"
        patterns = [(e.pattern, e.comment) for e in fleet.registry.exclusions()]
        assert patterns == [("beh.*", "its own hub"), ("live-rig", None)]
    finally:
        for server, thread in servers:
            server.should_exit = True
            thread.join(5)


def test_daemon_open_opens_the_page_signed_in(monkeypatch):
    from clabe.daemon import _cli, _install

    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    answers: list[dict | None] = [None, {"status": "unauthorized"}, {"status": "idle"}, {"status": "idle"}]
    monkeypatch.setattr(_install, "probe", lambda port, token, timeout=2.0: answers.pop(0))
    command = _cli._OpenCli(port=24070, token="a b/c")
    with pytest.raises(SystemExit, match="No daemon answers on port 24070"):
        command.cli_cmd()
    with pytest.raises(SystemExit, match="refuses this token"):
        command.cli_cmd()
    command.cli_cmd()
    _cli._OpenCli(port=24070, token="a b/c", run_page=True).cli_cmd()
    assert opened == ["http://localhost:24070/?token=a%20b%2Fc", "http://localhost:24070/run/?token=a%20b%2Fc"]
    assert _cli.open_url(DaemonSettings(port=1, host="10.0.0.7")) == "http://10.0.0.7:1/"


def test_the_daemon_keeps_enrolling_until_stopped(monkeypatch):
    calls: list[str] = []

    def hub(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    async def flow() -> None:
        settings = DaemonSettings(hubs=["http://hub:8080"], enroll_interval=0.01)
        task = asyncio.ensure_future(enroll_forever(settings, httpx.AsyncClient(transport=httpx.MockTransport(hub))))
        while len(calls) < 3:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(asyncio.wait_for(flow(), timeout=10))
    assert set(calls) == {"/api/hub/enroll"}
