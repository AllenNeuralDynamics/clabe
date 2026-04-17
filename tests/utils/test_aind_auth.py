from unittest.mock import MagicMock, patch

import requests

from clabe.utils import aind_auth


def test_validate_aind_username_valid():
    """Returns True when the metadata service finds the user."""
    with patch("clabe.utils.aind_auth.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "username": "j.doe",
            "full_name": "Jane Doe",
            "email": "j.doe@alleninstitute.org",
        }
        mock_get.return_value = mock_response

        assert aind_auth.validate_aind_username("j.doe") is True
        mock_get.assert_called_once_with(
            "http://aind-metadata-service/api/v2/active_directory/j.doe",
            timeout=2,
        )


def test_validate_aind_username_invalid():
    """Returns False when the metadata service does not find the user."""
    with patch("clabe.utils.aind_auth.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.ok = False
        mock_get.return_value = mock_response

        assert aind_auth.validate_aind_username("no.one") is False


def test_validate_aind_username_request_exception():
    """Returns False (with a logged warning) on a network error."""
    with patch("clabe.utils.aind_auth.requests.get", side_effect=requests.RequestException("timeout")):
        assert aind_auth.validate_aind_username("j.doe") is False


def test_validate_aind_username_custom_timeout():
    """Passes the timeout argument through to requests.get."""
    with patch("clabe.utils.aind_auth.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"username": "j.doe"}
        mock_get.return_value = mock_response

        aind_auth.validate_aind_username("j.doe", timeout=10)
        mock_get.assert_called_once_with(
            "http://aind-metadata-service/api/v2/active_directory/j.doe",
            timeout=10,
        )


def test_validate_aind_username_encodes_special_chars():
    """URL-encodes special characters in the username."""
    with patch("clabe.utils.aind_auth.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.ok = False
        mock_get.return_value = mock_response

        aind_auth.validate_aind_username("../admin")
        mock_get.assert_called_once_with(
            "http://aind-metadata-service/api/v2/active_directory/..%2Fadmin",
            timeout=2,
        )
