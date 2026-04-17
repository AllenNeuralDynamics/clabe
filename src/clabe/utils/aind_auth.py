import logging
from typing import Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

_AD_ENDPOINT = "http://aind-metadata-service/api/v2/active_directory"


def validate_aind_username(
    username: str,
    timeout: Optional[float] = 2,
) -> bool:
    """
    Validates if the given username exists in the AIND Active Directory.

    Queries the AIND metadata service to verify the username exists.
    Returns False (instead of raising) on network errors so callers can
    decide how to handle the degraded state.

    Args:
        username: The username to validate.
        timeout: Timeout in seconds for the HTTP request. Defaults to 2.

    Returns:
        bool: True if the username was found, False otherwise.

    Example:
        ```python
        is_valid = validate_aind_username("j.doe")
        ```
    """
    try:
        response = requests.get(f"{_AD_ENDPOINT}/{quote(username, safe='')}", timeout=timeout)
        return response.ok
    except requests.RequestException as e:
        logger.warning("Failed to validate username '%s': %s", username, e)
        return False
