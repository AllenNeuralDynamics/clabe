"""Issue reports in the hub: the store, collection from a rig's daemon, and the hub's report API."""

import asyncio
import datetime
import io
import json
import os
import shutil
import threading
import time
import zipfile
from pathlib import Path

import httpx
import pytest
import requests
import uvicorn
from fastapi.testclient import TestClient

from clabe.daemon import DaemonSettings, Host, create_daemon_app
from clabe.hub import EnrollBody, Fleet, HubSettings, InspectCache, ReportStore, create_hub_app
from clabe.reports import REPORT_FILE, ReportInfo, list_reports, make_bundle, save_report
from clabe.server import free_port

_AUTH = {"Authorization": "Bearer hub-secret"}


def _report(directory: Path, **info) -> tuple[ReportInfo, bytes]:
    """A report saved in ``directory``, and its zip."""
    report = save_report(None, info, directory=directory)
    return report, (directory / f"{report.report_id}.zip").read_bytes()


def _serve(app, port: int):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    return server, thread


@pytest.fixture
def store(tmp_path: Path):
    store = ReportStore(tmp_path / "hub-data")
    yield store
    store.close()


def test_the_store_keeps_rows_and_zips(store: ReportStore, tmp_path: Path):
    report, data = _report(tmp_path / "made", comment="Camera froze", experimenter=["ana", "bo"], subject="m-1")
    stored = store.add("rig-3", data)
    assert stored is not None and (stored.rig_id, stored.status, stored.size) == ("rig-3", "new", len(data))
    assert store.add("rig-3", data) is None  # the same id again
    assert (store.data_dir / "hub.db").is_file()
    assert store.zip_path(report.report_id) == store.data_dir / "reports" / f"{report.report_id}.zip"
    got = store.get(report.report_id)
    assert got is not None and got.experimenter == ["ana", "bo"] and got.comment == "Camera froze"
    assert got.created_at == report.created_at
    assert store.set_status(report.report_id, "looking").status == "looking"
    assert store.set_status("0" * 32, "closed") is None
    assert store.counts_by_rig() == {"rig-3": 1}
    assert store.delete([report.report_id, "f" * 32]) == 1
    assert store.get(report.report_id) is None and not list((store.data_dir / "reports").iterdir())
    with pytest.raises(ValueError):
        store.add("rig-3", b"not a zip")


def test_the_store_filters_searches_sorts_and_pages(store: ReportStore, tmp_path: Path):
    made = tmp_path / "made"
    a, data_a = _report(made, comment="Camera froze", experiment="vr-foraging", subject="m-1")
    b, data_b = _report(made, comment="100% wrong", experiment="vr-foraging", experimenter=["zoe"])
    c, data_c = _report(made, comment="olfactometer", experiment="dynamic-foraging", step="calibrate")
    store.add("rig-3", data_a), store.add("rig-4", data_b), store.add("rig-3", data_c)
    store.set_status(c.report_id, "closed")

    def ids(**query) -> list[str]:
        return [r.report_id for r in store.query(**query).items]

    assert ids() == [c.report_id, b.report_id, a.report_id]  # newest first
    assert ids(order="asc") == [a.report_id, b.report_id, c.report_id]
    assert ids(rig="rig-3", order="asc") == [a.report_id, c.report_id]
    assert ids(experiment="vr-foraging", rig="rig-4") == [b.report_id]
    assert ids(status="closed") == [c.report_id]
    assert ids(q="CAMERA") == [a.report_id]  # any case
    assert ids(q="zoe") == [b.report_id]  # experimenter
    assert ids(q="calib") == [c.report_id]
    assert ids(q="%") == [b.report_id]  # a literal %, not a wildcard
    assert ids(q="rig-4") == [b.report_id]
    assert ids(sort="experiment", order="asc")[0] == c.report_id
    page = store.query(limit=2, offset=1, order="asc")
    assert page.total == 3 and [r.report_id for r in page.items] == [b.report_id, c.report_id]
    with pytest.raises(ValueError):
        store.query(sort="zip_path")  # type: ignore[arg-type]


def test_old_reports_are_deleted_after_the_retention(store: ReportStore, tmp_path: Path):
    old, old_data = _report(tmp_path / "made")
    recent, recent_data = _report(tmp_path / "made")
    store.add("rig-3", old_data), store.add("rig-3", recent_data)
    long_ago = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=40)).isoformat()
    with store._db:
        store._db.execute("UPDATE reports SET received_at = ? WHERE report_id = ?", (long_ago, old.report_id))
    fleet = Fleet(HubSettings(report_retention_days=30), reports=store)
    asyncio.run(fleet.poll_once())
    assert store.get(old.report_id) is None and store.get(recent.report_id) is not None
    assert not (store.data_dir / "reports" / f"{old.report_id}.zip").exists()


@pytest.fixture
def rig(tmp_path: Path):
    """A rig's daemon (with a token), keeping its reports in a folder of its own."""
    reports = tmp_path / "rig-reports"
    settings = DaemonSettings(
        port=free_port("127.0.0.1", 18370), repository_roots=[], runs_directory=tmp_path / "runs",
        reports_directory=reports,
    )  # fmt: skip
    server, thread = _serve(create_daemon_app(Host(settings), token="rig-secret", static_dir=None), settings.port)
    yield f"http://127.0.0.1:{settings.port}", reports
    server.should_exit = True
    thread.join(5)


def test_the_hub_collects_a_rigs_reports_then_deletes_them_there(rig, store: ReportStore, tmp_path: Path):
    url, rig_reports = rig
    report, _ = _report(rig_reports, comment="Lick detector stuck", experiment="vr-foraging")
    already, data = _report(tmp_path / "made", comment="collected before")
    store.add("rig-3", data)
    shutil.copy(tmp_path / "made" / f"{already.report_id}.zip", rig_reports)  # e.g. a delete that failed last time
    (rig_reports / ("e" * 32 + ".zip")).write_bytes(b"broken")  # never listed: no report.json

    settings = HubSettings(port=free_port("127.0.0.1", 18380), poll_interval=0.2, report_interval=0.2)
    fleet = Fleet(settings, reports=store)
    fleet.enroll(EnrollBody(id="rig-3", host="127.0.0.1", port=httpx.URL(url).port))
    fleet.set_token("rig-3", "rig-secret")
    hub, thread = _serve(create_hub_app(fleet, token="hub-secret"), settings.port)
    base = f"http://127.0.0.1:{settings.port}"
    try:
        deadline = time.monotonic() + 30
        while list_reports(rig_reports) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert list_reports(rig_reports) == []
        stored = store.get(report.report_id)
        assert stored is not None and (stored.rig_id, stored.comment) == ("rig-3", "Lick detector stuck")
        assert (store.data_dir / "reports" / f"{report.report_id}.zip").is_file()
        assert store.query().total == 2  # the duplicate was not stored twice

        rigs = requests.get(f"{base}/api/hub/rigs", headers=_AUTH, timeout=10).json()
        assert rigs[0]["reports_stored"] == 2
        page = requests.get(f"{base}/api/hub/reports", params={"q": "lick"}, headers=_AUTH, timeout=10).json()
        assert page["total"] == 1 and page["items"][0]["report_id"] == report.report_id
        assert requests.get(f"{base}/api/hub/reports", timeout=10).status_code == 401
    finally:
        hub.should_exit = True
        thread.join(5)


def test_an_unreachable_rig_or_failed_download_never_stops_polling(store: ReportStore):
    port = free_port("127.0.0.1", 18395)
    fleet = Fleet(HubSettings(), reports=store)
    fleet.enroll(EnrollBody(id="gone", host="127.0.0.1", port=port))
    assert asyncio.run(fleet.collect(fleet.rigs["gone"])) == 0
    asyncio.run(Fleet(fleet.settings, reports=store, registry=fleet.registry).poll_once(collect=True))
    assert store.query().total == 0


@pytest.fixture
def hub(store: ReportStore):
    app = create_hub_app(Fleet(HubSettings(), reports=store), token="hub-secret", static_dir=None, poll=False)
    with TestClient(app, headers=_AUTH) as client:
        yield client


def test_the_hub_api_triages_reads_and_deletes_reports(hub: TestClient, store: ReportStore, tmp_path: Path):
    made = tmp_path / "made"
    report, data = _report(made, comment="Camera froze", experiment="vr-foraging")
    store.add("rig-3", data)
    others = [store.add("rig-4", _report(made)[1]) for _ in range(2)]
    rid = report.report_id

    page = hub.get("/api/hub/reports", params={"rig": "rig-3"}).json()
    assert page["total"] == 1 and page["items"][0]["comment"] == "Camera froze"
    assert hub.get("/api/hub/reports", params={"sort": "zip_path"}).status_code == 422
    assert hub.get(f"/api/hub/reports/{rid}").json()["status"] == "new"
    assert hub.patch(f"/api/hub/reports/{rid}", json={"status": "looking"}).json()["status"] == "looking"
    assert hub.patch(f"/api/hub/reports/{rid}", json={"status": "bogus"}).status_code == 422
    assert hub.get("/api/hub/reports", params={"status": "looking"}).json()["total"] == 1

    zipped = hub.get(f"/api/hub/reports/{rid}/zip")
    assert zipped.headers["content-type"] == "application/zip" and zipped.content == data
    files = {f["path"]: f["size"] for f in hub.get(f"/api/hub/reports/{rid}/files").json()}
    assert REPORT_FILE in files and "rig.json" in files
    assert json.loads(hub.get(f"/api/hub/reports/{rid}/files/{REPORT_FILE}").text)["report_id"] == rid
    assert hub.get(f"/api/hub/reports/{rid}/files/nope.txt").status_code == 404
    assert hub.get(f"/api/hub/reports/{rid}/files/../hub.db").status_code in (404, 422)
    assert hub.get("/api/hub/reports/" + "0" * 32).status_code == 404
    assert hub.get("/api/hub/reports/..%2Fhub.db").status_code in (404, 422)

    assert hub.post("/api/hub/reports/delete", json={"ids": [o.report_id for o in others]}).json() == {"deleted": 2}
    assert hub.delete(f"/api/hub/reports/{rid}").status_code == 204
    assert hub.delete(f"/api/hub/reports/{rid}").status_code == 404
    assert hub.get("/api/hub/reports").json() == {"total": 0, "items": []}


def test_a_file_too_large_is_not_shown(hub: TestClient, store: ReportStore):
    info = ReportInfo(
        report_id="a" * 32, created_at=datetime.datetime.now(datetime.UTC), hostname="rig", clabe_version="0"
    )
    big = make_bundle(None, extra={REPORT_FILE: info.model_dump_json(), "huge.log": b"x" * (21 * 1024 * 1024)})
    store.add("rig-3", big)
    response = hub.get(f"/api/hub/reports/{'a' * 32}/files/huge.log")
    assert response.status_code == 413 and "MB" in response.json()["detail"]


def test_an_export_is_inspected_without_being_stored(hub: TestClient, store: ReportStore, tmp_path: Path):
    _, data = _report(tmp_path / "made", comment="look only")
    opened = hub.post("/api/hub/inspect", content=data, headers={"Content-Type": "application/zip"}).json()
    assert opened["report"]["comment"] == "look only"
    assert REPORT_FILE in [f["path"] for f in opened["files"]]
    text = hub.get(f"/api/hub/inspect/{opened['inspect_id']}/files/{REPORT_FILE}").text
    assert json.loads(text)["comment"] == "look only"
    assert store.query().total == 0

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("events.jsonl", '{"type": "x"}\n')
    form = hub.post("/api/hub/inspect", files={"file": ("export.zip", buffer.getvalue(), "application/zip")}).json()
    assert form["report"] is None and form["files"] == [{"path": "events.jsonl", "size": 14}]
    assert hub.get(f"/api/hub/inspect/{form['inspect_id']}/files/events.jsonl").text == '{"type": "x"}\n'

    assert hub.post("/api/hub/inspect", content=b"not a zip").status_code == 400
    assert hub.get(f"/api/hub/inspect/{'b' * 32}/files/events.jsonl").status_code == 404


def test_an_opened_export_expires(tmp_path: Path):
    cache = InspectCache(tmp_path / "inspect", ttl=3600)
    opened = cache.add(_report(tmp_path / "made")[1])
    assert cache.path(opened.inspect_id) is not None
    cache.ttl = -1  # everything is now too old
    assert cache.path(opened.inspect_id) is None and not list((tmp_path / "inspect").iterdir())
    cache.close()


def test_report_routes_answer_503_without_a_store():
    app = create_hub_app(Fleet(HubSettings()), static_dir=None, poll=False)
    with TestClient(app) as client:
        assert client.get("/api/hub/reports").status_code == 503


def test_a_report_has_an_address_that_opens_it(store: ReportStore, tmp_path: Path):
    playwright_api = pytest.importorskip("playwright.sync_api")
    from clabe.server._app import STATIC_DIR

    if not (STATIC_DIR / "index.html").is_file():
        pytest.skip("the web UI is not built")
    report, data = _report(tmp_path / "made", comment="Camera froze", experiment="vr-foraging")
    store.add("rig-3", data)
    port = free_port("127.0.0.1", 18660)
    app = create_hub_app(Fleet(HubSettings(), reports=store), token="hub-secret", poll=False)
    server, thread = _serve(app, port)
    base = f"http://127.0.0.1:{port}"
    try:
        with playwright_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel=os.environ.get("CLABE_TEST_BROWSER_CHANNEL") or None)
            except playwright_api.Error as exc:
                pytest.skip(f"no browser for Playwright: {exc}")
            page = browser.new_page()
            page.set_default_timeout(30_000)
            page.goto(f"{base}/?token=hub-secret&tab=reports")
            page.get_by_text("Camera froze").first.click()
            page.get_by_role("button", name="Copy link").wait_for()
            address = page.url
            assert f"report={report.report_id}" in address and "token" not in address
            page.get_by_role("button", name="Copy link").click()
            page.get_by_role("button", name="Link copied").wait_for()
            # another person, signed in on their own, opens the shared address
            other = browser.new_context().new_page()
            other.goto(address)
            other.get_by_label("Token").fill("hub-secret")
            other.keyboard.press("Enter")
            other.get_by_role("button", name="Copy link").wait_for()
            other.get_by_text("Camera froze").first.wait_for()
            # back to the table: the address no longer names the report
            other.get_by_role("button", name="Reports").first.click()
            other.get_by_text("Camera froze").first.wait_for()
            assert "report=" not in other.url
            browser.close()
    finally:
        server.should_exit = True
        thread.join(5)
