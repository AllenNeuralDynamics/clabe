"""The daemon's report API: the hub lists a rig's issue reports, downloads them, then deletes them."""

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clabe.daemon import DaemonSettings, Host, create_daemon_app
from clabe.daemon._cli import reports_waiting
from clabe.reports import REPORT_FILE, ReportInfo, save_report

_AUTH = {"Authorization": "Bearer d4emon"}


@pytest.fixture
def reports(tmp_path: Path) -> Path:
    return tmp_path / "reports"


@pytest.fixture
def client(reports: Path, tmp_path: Path):
    settings = DaemonSettings(repository_roots=[], runs_directory=tmp_path / "runs", reports_directory=reports)
    with TestClient(create_daemon_app(Host(settings), token="d4emon", static_dir=None)) as client:
        yield client


def test_the_reports_directory_comes_from_the_environment(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DAEMON_CLABE_REPORTS_DIRECTORY", str(tmp_path / "elsewhere"))
    assert DaemonSettings().reports_path() == tmp_path / "elsewhere"


def test_reports_are_listed_downloaded_and_deleted(client: TestClient, reports: Path):
    assert client.get("/api/daemon/reports").status_code == 401
    assert client.get("/api/daemon/reports", headers=_AUTH).json() == []
    assert client.get("/api/daemon/info", headers=_AUTH).json()["reports_waiting"] == 0

    first = save_report(None, {"comment": "camera froze", "experiment": "demo"}, directory=reports)
    second = save_report(None, {"comment": "again"}, directory=reports)
    listed = [ReportInfo.model_validate(r) for r in client.get("/api/daemon/reports", headers=_AUTH).json()]
    assert [r.report_id for r in listed] == [first.report_id, second.report_id]
    assert listed[0].comment == "camera froze"
    assert client.get("/api/daemon/info", headers=_AUTH).json()["reports_waiting"] == 2

    downloaded = client.get(f"/api/daemon/reports/{first.report_id}", headers=_AUTH)
    assert downloaded.status_code == 200 and downloaded.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        assert ReportInfo.model_validate_json(archive.read(REPORT_FILE)) == first
        assert "rig.json" in archive.namelist()

    assert client.delete(f"/api/daemon/reports/{first.report_id}", headers=_AUTH).status_code == 204
    assert client.delete(f"/api/daemon/reports/{first.report_id}", headers=_AUTH).status_code == 404
    assert client.get(f"/api/daemon/reports/{first.report_id}", headers=_AUTH).status_code == 404
    assert [r["report_id"] for r in client.get("/api/daemon/reports", headers=_AUTH).json()] == [second.report_id]
    assert reports_waiting(reports) == f"1 issue report waiting for the hub in {reports}."


@pytest.mark.parametrize("report_id", ["..%2F..%2Fsecret", "..", "ABCDEF" * 6, "a" * 31, "report.zip", "%2E%2E"])
def test_a_report_id_never_reaches_outside_the_reports(client: TestClient, reports: Path, report_id: str):
    reports.mkdir(parents=True)
    secret = reports.parent / "secret.zip"
    secret.write_bytes(b"nope")
    for method in ("GET", "DELETE"):
        response = client.request(method, f"/api/daemon/reports/{report_id}", headers=_AUTH)
        assert response.status_code in (404, 405, 422), (method, report_id, response.status_code)
    assert secret.is_file()


def test_status_counts_the_reports_waiting(tmp_path: Path):
    assert reports_waiting(tmp_path / "none") == f"0 issue reports waiting for the hub in {tmp_path / 'none'}."
    for _ in range(3):
        save_report(None, {}, directory=tmp_path)
    assert reports_waiting(tmp_path).startswith("3 issue reports waiting")
