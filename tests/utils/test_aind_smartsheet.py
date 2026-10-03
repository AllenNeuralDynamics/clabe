from unittest.mock import MagicMock, patch

import pytest
import requests
from aind_behavior_services import Session

from clabe.utils.aind_smartsheet import DEFAULT_SMARTSHEET_ENDPOINT, SmartsheetRow, SmartsheetScheduleClient


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
    with patch("clabe.utils.aind_smartsheet.requests.Session.get", return_value=_response(payload=ROW)) as mock_get:
        row = SmartsheetScheduleClient(base_url="http://host/smartsheet").get_row("mouse_1")
    assert row == SmartsheetRow.model_validate(ROW)
    mock_get.assert_called_once_with("http://host/smartsheet/rows/mouse_1", timeout=(1, 4))


def test_get_row_quotes_subject():
    with patch("clabe.utils.aind_smartsheet.requests.Session.get", return_value=_response(payload=ROW)) as mock_get:
        SmartsheetScheduleClient(base_url="http://host/smartsheet").get_row("a/b c")
    assert mock_get.call_args.args[0] == "http://host/smartsheet/rows/a%2Fb%20c"


def test_get_row_caches_per_subject():
    with patch("clabe.utils.aind_smartsheet.requests.Session.get", return_value=_response(payload=ROW)) as mock_get:
        client = SmartsheetScheduleClient()
        client.get_row("mouse_1")
        client.get_row("mouse_1")
    assert mock_get.call_count == 1


def test_get_row_caches_not_found():
    with patch("clabe.utils.aind_smartsheet.requests.Session.get", return_value=_response(status=404)) as mock_get:
        client = SmartsheetScheduleClient()
        client.get_row("mouse_1")
        client.get_row("mouse_1")
    assert mock_get.call_count == 1


def test_get_row_does_not_cache_transient_failure():
    side_effect = [requests.ConnectionError("down"), _response(payload=ROW)]
    with patch("clabe.utils.aind_smartsheet.requests.Session.get", side_effect=side_effect):
        client = SmartsheetScheduleClient()
        assert client.get_row("mouse_1") is None
        assert client.get_row("mouse_1") is not None


def test_get_row_not_found_returns_none():
    with patch("clabe.utils.aind_smartsheet.requests.Session.get", return_value=_response(status=404)):
        assert SmartsheetScheduleClient().get_row("mouse_1") is None


def test_get_row_network_error_returns_none():
    with patch("clabe.utils.aind_smartsheet.requests.Session.get", side_effect=requests.ConnectionError("down")):
        assert SmartsheetScheduleClient().get_row("mouse_1") is None


def test_add_scientific_contact_returns_copy_with_contact_at_end():
    session = _session(["a.user", "b.user"])
    with patch("clabe.utils.aind_smartsheet.requests.Session.get", return_value=_response(payload=ROW)):
        result = SmartsheetScheduleClient(validator=lambda n: n).add_scientific_contact(session)
    assert result is not session
    assert result.experimenter == ["a.user", "b.user", "p.investigator"]
    assert session.experimenter == ["a.user", "b.user"]


def test_add_scientific_contact_uses_canonical_name_from_validator():
    session = _session()
    with patch("clabe.utils.aind_smartsheet.requests.Session.get", return_value=_response(payload=ROW)):
        result = SmartsheetScheduleClient(validator=lambda n: n.upper()).add_scientific_contact(session)
    assert result.experimenter == ["j.doe", "P.INVESTIGATOR"]


def test_add_scientific_contact_removes_duplicates():
    session = _session(["p.investigator", "a.user", "a.user"])
    with patch("clabe.utils.aind_smartsheet.requests.Session.get", return_value=_response(payload=ROW)):
        result = SmartsheetScheduleClient(validator=lambda n: n).add_scientific_contact(session)
    assert result.experimenter == ["p.investigator", "a.user"]
    assert session.experimenter == ["p.investigator", "a.user", "a.user"]


def test_add_scientific_contact_invalid_username_raises():
    with (
        patch("clabe.utils.aind_smartsheet.requests.Session.get", return_value=_response(payload=ROW)),
        pytest.raises(ValueError, match="not valid"),
    ):
        SmartsheetScheduleClient(validator=lambda n: None).add_scientific_contact(_session())


@pytest.mark.parametrize("extra", [{}, {"validated_pi_username": ""}, {"validated_pi_username": " "}])
def test_add_scientific_contact_missing_or_empty_column_raises(extra):
    payload = {"Mouse ID": "mouse_1", **extra}
    with (
        patch("clabe.utils.aind_smartsheet.requests.Session.get", return_value=_response(payload=payload)),
        pytest.raises(ValueError, match="has no scientific contact"),
    ):
        SmartsheetScheduleClient(validator=lambda n: n).add_scientific_contact(_session())


@pytest.mark.parametrize("error", [requests.Timeout("slow"), requests.ConnectionError("down")])
def test_add_scientific_contact_service_down_raises(error):
    with (
        patch("clabe.utils.aind_smartsheet.requests.Session.get", side_effect=error),
        pytest.raises(ValueError, match="No smartsheet row could be retrieved"),
    ):
        SmartsheetScheduleClient().add_scientific_contact(_session())


def test_add_scientific_contact_row_not_found_raises():
    with (
        patch("clabe.utils.aind_smartsheet.requests.Session.get", return_value=_response(status=404)),
        pytest.raises(ValueError, match="No smartsheet row could be retrieved"),
    ):
        SmartsheetScheduleClient().add_scientific_contact(_session())


def test_add_scientific_contact_malformed_row_raises():
    with (
        patch(
            "clabe.utils.aind_smartsheet.requests.Session.get", return_value=_response(payload={"Project Name": "x"})
        ),
        pytest.raises(ValueError, match="No smartsheet row could be retrieved"),
    ):
        SmartsheetScheduleClient().add_scientific_contact(_session())


def test_get_project_name():
    with patch("clabe.utils.aind_smartsheet.requests.Session.get", return_value=_response(payload=ROW)):
        assert SmartsheetScheduleClient().get_project_name(_session()) == "Some Project"


@pytest.mark.parametrize("extra", [{}, {"Project Name": ""}, {"Project Name": " "}])
def test_get_project_name_missing_or_empty_raises(extra):
    payload = {"Mouse ID": "mouse_1", **extra}
    with (
        patch("clabe.utils.aind_smartsheet.requests.Session.get", return_value=_response(payload=payload)),
        pytest.raises(ValueError, match="has no project name"),
    ):
        SmartsheetScheduleClient().get_project_name(_session())


def test_get_project_name_row_not_found_raises():
    with (
        patch("clabe.utils.aind_smartsheet.requests.Session.get", return_value=_response(status=404)),
        pytest.raises(ValueError, match="No smartsheet row could be retrieved"),
    ):
        SmartsheetScheduleClient().get_project_name(_session())


def test_row_parses_known_fields_and_collects_model_extra():
    row = SmartsheetRow.model_validate({**ROW, "validated_trainer_username": "t.rainer", "Tags": ["a", "b"]})
    assert row.mouse_id == "mouse_1"
    assert row.scientific_contact_username == "p.investigator"
    assert row.project_name == "Some Project"
    assert row.trainer_username == "t.rainer"
    assert row.tags == ["a", "b"]
    assert row.model_extra == {}


def test_row_defaults_and_blank_handling():
    row = SmartsheetRow.model_validate(
        {"Mouse ID": "mouse_1", "Project Name": " ", "validated_pi_username": None, "Tags": None}
    )
    assert row.project_name is None
    assert row.scientific_contact_username is None
    assert row.tags == []
    assert row.model_extra == {}


@pytest.mark.network
def test_live_endpoint_returns_parsed_row():
    rows = requests.get(f"{DEFAULT_SMARTSHEET_ENDPOINT}/rows", timeout=10).json()
    subject = next(r["Mouse ID"] for r in rows if r.get("validated_pi_username") and r.get("Project Name"))
    row = SmartsheetScheduleClient(timeout=10).get_row(subject)
    assert row is not None
    assert row.scientific_contact_username
    assert row.project_name
    assert row.trainer_username
    assert isinstance(row.tags, list)
    assert row.mouse_id == subject
    assert "validated_pi_username" not in row.model_extra
