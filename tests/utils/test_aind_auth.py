import platform
from unittest.mock import MagicMock, patch

import pytest

from clabe.utils import aind_auth


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only test")
def test_validate_aind_username_windows_valid():
    """Test validate_aind_username on Windows with a valid user."""
    with (
        patch("ms_active_directory.ADDomain") as mock_ad_domain,
        patch("ldap3.SASL", "SASL"),
        patch("ldap3.GSSAPI", "GSSAPI"),
    ):
        mock_session = MagicMock()
        mock_session.find_user_by_name.return_value = True
        mock_ad_domain.return_value.create_session_as_user.return_value = mock_session

        assert aind_auth.validate_aind_username("testuser")


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only test")
def test_validate_aind_username_windows_invalid():
    """Test validate_aind_username on Windows with an invalid user."""
    with (
        patch("ms_active_directory.ADDomain") as mock_ad_domain,
        patch("ldap3.SASL", "SASL"),
        patch("ldap3.GSSAPI", "GSSAPI"),
    ):
        mock_session = MagicMock()
        mock_session.find_user_by_name.return_value = None
        mock_ad_domain.return_value.create_session_as_user.return_value = mock_session

        assert not aind_auth.validate_aind_username("testuser")


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only test")
def test_validate_aind_username_windows_timeout():
    """Test validate_aind_username on Windows with a timeout."""
    with (
        patch("ms_active_directory.ADDomain"),
        patch("ldap3.SASL", "SASL"),
        patch("ldap3.GSSAPI", "GSSAPI"),
        patch("concurrent.futures.ThreadPoolExecutor.submit") as mock_submit,
    ):
        mock_submit.side_effect = TimeoutError
        with pytest.raises(TimeoutError):
            aind_auth.validate_aind_username("testuser")
