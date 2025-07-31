import platform
from unittest.mock import MagicMock, patch

import pytest

from clabe.utils import aind_auth


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only test")
@patch("clabe.utils.aind_auth.ms_active_directory")
@patch("clabe.utils.aind_auth.ldap3")
def test_validate_aind_username_windows_valid(mock_ldap3, mock_ad):
    """Test validate_aind_username on Windows with a valid user."""
    mock_session = MagicMock()
    mock_session.find_user_by_name.return_value = True
    mock_ad.ADDomain.return_value.create_session_as_user.return_value = mock_session

    assert aind_auth.validate_aind_username("testuser")


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only test")
@patch("clabe.utils.aind_auth.ms_active_directory")
@patch("clabe.utils.aind_auth.ldap3")
def test_validate_aind_username_windows_invalid(mock_ldap3, mock_ad):
    """Test validate_aind_username on Windows with an invalid user."""
    mock_session = MagicMock()
    mock_session.find_user_by_name.return_value = None
    mock_ad.ADDomain.return_value.create_session_as_user.return_value = mock_session

    assert not aind_auth.validate_aind_username("testuser")


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only test")
@patch("clabe.utils.aind_auth.ms_active_directory")
@patch("clabe.utils.aind_auth.ldap3")
def test_validate_aind_username_windows_timeout(mock_ldap3, mock_ad):
    """Test validate_aind_username on Windows with a timeout."""
    with patch("concurrent.futures.ThreadPoolExecutor.submit") as mock_submit:
        mock_submit.side_effect = TimeoutError
        with pytest.raises(TimeoutError):
            aind_auth.validate_aind_username("testuser")
