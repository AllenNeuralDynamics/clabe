from unittest.mock import MagicMock, patch

import pytest
import requests

from clabe.utils import aind_validators


@pytest.fixture
def mock_rig():
    rig = MagicMock()
    rig.rig_name = "rig_1"
    rig.computer_name = "host_1"

    def _model_copy(update=None):
        new_rig = MagicMock()
        new_rig.rig_name = (update or {}).get("rig_name", rig.rig_name)
        new_rig.computer_name = (update or {}).get("computer_name", rig.computer_name)
        return new_rig

    rig.model_copy.side_effect = _model_copy
    return rig


def test_validate_username_valid():
    """Returns True when the metadata service finds the user."""
    with patch("clabe.utils.aind_validators.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "username": "j.doe",
            "full_name": "Jane Doe",
            "email": "j.doe@alleninstitute.org",
        }
        mock_get.return_value = mock_response

        assert aind_validators.validate_username("j.doe") is True
        mock_get.assert_called_once_with(
            "http://aind-metadata-service/api/v2/active_directory/j.doe",
            timeout=2,
        )


def test_validate_username_invalid():
    """Returns False when the metadata service does not find the user."""
    with patch("clabe.utils.aind_validators.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.ok = False
        mock_get.return_value = mock_response

        assert aind_validators.validate_username("no.one") is False


def test_validate_username_request_exception():
    """Returns False (with a logged warning) on a network error."""
    with patch("clabe.utils.aind_validators.requests.get", side_effect=requests.RequestException("timeout")):
        assert aind_validators.validate_username("j.doe") is False


def test_validate_username_custom_timeout():
    """Passes the timeout argument through to requests.get."""
    with patch("clabe.utils.aind_validators.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"username": "j.doe"}
        mock_get.return_value = mock_response

        aind_validators.validate_username("j.doe", timeout=10)
        mock_get.assert_called_once_with(
            "http://aind-metadata-service/api/v2/active_directory/j.doe",
            timeout=10,
        )


def test_validate_username_encodes_special_chars():
    """URL-encodes special characters in the username."""
    with patch("clabe.utils.aind_validators.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.ok = False
        mock_get.return_value = mock_response

        aind_validators.validate_username("../admin")
        mock_get.assert_called_once_with(
            "http://aind-metadata-service/api/v2/active_directory/..%2Fadmin",
            timeout=2,
        )


# --- validate_rig_computer_name ---


def test_validate_rig_no_env_vars_returns_original_values(mock_rig):
    """When env vars are absent the returned rig keeps the original values."""
    with patch.dict("os.environ", {}, clear=True):
        result = aind_validators.validate_rig_computer_name(mock_rig)

    assert result.rig_name == "rig_1"
    assert result.computer_name == "host_1"


def test_validate_rig_matching_env_vars_returns_same_values(mock_rig):
    """When env vars match the rig config the returned rig has the same values."""
    with patch.dict("os.environ", {"aibs_comp_id": "rig_1", "hostname": "host_1"}):
        result = aind_validators.validate_rig_computer_name(mock_rig)

    assert result.rig_name == "rig_1"
    assert result.computer_name == "host_1"


def test_validate_rig_differing_env_vars_returns_updated_rig(mock_rig):
    """When env vars differ from the rig config the returned rig has the env var values."""
    with patch.dict("os.environ", {"aibs_comp_id": "rig_2", "hostname": "host_2"}):
        result = aind_validators.validate_rig_computer_name(mock_rig)

    assert result.rig_name == "rig_2"
    assert result.computer_name == "host_2"


def test_validate_rig_returns_new_object_not_original(mock_rig):
    """The function always returns a new rig copy, never mutates the input."""
    with patch.dict("os.environ", {"aibs_comp_id": "rig_1", "hostname": "host_1"}):
        result = aind_validators.validate_rig_computer_name(mock_rig)

    assert result is not mock_rig
