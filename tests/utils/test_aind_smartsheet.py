from unittest.mock import MagicMock, patch

import requests
from aind_behavior_services import Session

from clabe.utils.aind_smartsheet import SmartsheetClient


def _session(experimenter: list[str] | None = None, subject: str = "mouse_1") -> Session:
    return Session(subject=subject, experimenter=experimenter or ["j.doe"], commit_hash="abc", allow_dirty_repo=True)


def _response(status: int = 200, payload: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.ok = status < 400
    response.json.return_value = payload or {}
    response.raise_for_status.side_effect = requests.HTTPError(f"{status}") if status >= 400 else None
    return response


ROW = {"Mouse ID": "mouse_1", "validated_pi_username": "p.investigator", "Project Name": "Some Project"}


def test_get_row_requests_subject_endpoint():
    with patch("clabe.utils.aind_smartsheet.requests.get", return_value=_response(payload=ROW)) as mock_get:
        row = SmartsheetClient(base_url="http://host/smartsheet").get_row("mouse_1")
    assert row == ROW
    mock_get.assert_called_once_with("http://host/smartsheet/rows/mouse_1", timeout=2)


def test_get_row_quotes_subject():
    with patch("clabe.utils.aind_smartsheet.requests.get", return_value=_response(payload=ROW)) as mock_get:
        SmartsheetClient(base_url="http://host/smartsheet").get_row("a/b c")
    assert mock_get.call_args.args[0] == "http://host/smartsheet/rows/a%2Fb%20c"


def test_get_row_caches_per_subject():
    with patch("clabe.utils.aind_smartsheet.requests.get", return_value=_response(payload=ROW)) as mock_get:
        client = SmartsheetClient()
        client.get_row("mouse_1")
        client.get_row("mouse_1")
    assert mock_get.call_count == 1


def test_get_row_not_found_returns_none():
    with patch("clabe.utils.aind_smartsheet.requests.get", return_value=_response(status=404)):
        assert SmartsheetClient().get_row("mouse_1") is None


def test_get_row_network_error_returns_none():
    with patch("clabe.utils.aind_smartsheet.requests.get", side_effect=requests.ConnectionError("down")):
        assert SmartsheetClient().get_row("mouse_1") is None


def test_add_scientific_contact_returns_copy_with_contact_at_end():
    session = _session(["a.user", "b.user"])
    with patch("clabe.utils.aind_smartsheet.requests.get", return_value=_response(payload=ROW)):
        result = SmartsheetClient(validator=lambda n: n).add_scientific_contact(session)
    assert result is not session
    assert result.experimenter == ["a.user", "b.user", "p.investigator"]
    assert session.experimenter == ["a.user", "b.user"]


def test_add_scientific_contact_uses_canonical_name_from_validator():
    session = _session()
    with patch("clabe.utils.aind_smartsheet.requests.get", return_value=_response(payload=ROW)):
        result = SmartsheetClient(validator=lambda n: n.upper()).add_scientific_contact(session)
    assert result.experimenter == ["j.doe", "P.INVESTIGATOR"]


def test_add_scientific_contact_skips_duplicate():
    session = _session(["p.investigator"])
    with patch("clabe.utils.aind_smartsheet.requests.get", return_value=_response(payload=ROW)):
        result = SmartsheetClient(validator=lambda n: n).add_scientific_contact(session)
    assert result.experimenter == ["p.investigator"]


def test_add_scientific_contact_invalid_username_returns_unchanged_session():
    session = _session()
    with patch("clabe.utils.aind_smartsheet.requests.get", return_value=_response(payload=ROW)):
        result = SmartsheetClient(validator=lambda n: None).add_scientific_contact(session)
    assert result.experimenter == ["j.doe"]


def test_add_scientific_contact_missing_or_empty_column_returns_unchanged_session():
    for payload in ({"Project Name": "x"}, {"validated_pi_username": ""}, {"validated_pi_username": "  "}):
        session = _session()
        with patch("clabe.utils.aind_smartsheet.requests.get", return_value=_response(payload=payload)):
            result = SmartsheetClient(validator=lambda n: n).add_scientific_contact(session)
        assert result.experimenter == ["j.doe"]


def test_add_scientific_contact_service_down_returns_unchanged_session():
    session = _session()
    with patch("clabe.utils.aind_smartsheet.requests.get", side_effect=requests.Timeout("slow")):
        result = SmartsheetClient().add_scientific_contact(session)
    assert result.experimenter == ["j.doe"]


def test_get_project_name():
    with patch("clabe.utils.aind_smartsheet.requests.get", return_value=_response(payload=ROW)):
        assert SmartsheetClient().get_project_name(_session()) == "Some Project"


def test_get_project_name_missing_returns_none():
    for payload in ({}, {"Project Name": ""}):
        with patch("clabe.utils.aind_smartsheet.requests.get", return_value=_response(payload=payload)):
            assert SmartsheetClient().get_project_name(_session()) is None
