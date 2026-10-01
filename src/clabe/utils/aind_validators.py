from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, TypeVar
from urllib.parse import quote

import pydantic
import requests
from pydantic import BaseModel

if TYPE_CHECKING:
    from aind_behavior_services.rig import Rig

    TRig = TypeVar("TRig", bound=Rig)
else:
    TRig = TypeVar("TRig")

logger = logging.getLogger(__name__)

_ACTIVEDIRECTORY_ENDPOINT = "http://aind-metadata-service/api/v2/active_directory"

_RIG_NAME_ENV_VAR = "aibs_comp_id"


class ActiveDirectoryUser(BaseModel):
    """A user record returned by the AIND Active Directory metadata service."""

    username: str
    full_name: str | None = None
    email: str | None = None


def get_aind_rig_name(*, required: bool = False) -> str | None:
    """Return the AIND rig name from the ``aibs_comp_id`` environment variable.

    Single source of truth for reading the rig identifier from the environment; prefer this
    over reading ``aibs_comp_id`` directly so the variable name lives in exactly one place.

    Args:
        required: When True, raise :class:`ValueError` if the variable is unset instead of
            returning ``None``.

    Returns:
        The rig name, or ``None`` if the variable is unset and ``required`` is False.
    """
    rig_name = os.environ.get(_RIG_NAME_ENV_VAR)
    if rig_name is None and required:
        raise ValueError(f"Environment variable '{_RIG_NAME_ENV_VAR}' is not set.")
    return rig_name


def get_active_directory_user(
    username: str,
    timeout: float | None = 2,
) -> ActiveDirectoryUser:
    """
    Fetches a user's record from the AIND Active Directory metadata service.

    Args:
        username: The username to look up.
        timeout: Timeout in seconds for the HTTP request. Defaults to 2.

    Returns:
        The user's record.

    Raises:
        requests.HTTPError: If the request fails or the user is not found.
        pydantic.ValidationError: If the response payload is malformed.

    Example:
        ```python
        user = get_active_directory_user("j.doe")
        ```
    """
    response = requests.get(f"{_ACTIVEDIRECTORY_ENDPOINT}/{quote(username, safe='')}", timeout=timeout)
    response.raise_for_status()
    return ActiveDirectoryUser.model_validate(response.json())


def validate_username(
    username: str,
    timeout: float | None = 2,
) -> str | None:
    """
    Validates if the given username exists in the AIND Active Directory.

    Queries the AIND metadata service to verify the username exists. Returns None
    (instead of raising) on network errors or an invalid username so callers can
    decide how to handle the degraded state.

    Args:
        username: The username to validate.
        timeout: Timeout in seconds for the HTTP request. Defaults to 2.

    Returns:
        The canonical username from the Active Directory record (which may differ in
        case from the input), or None if the username is invalid.

    Example:
        ```python
        canonical_username = validate_username("j.doe")
        is_valid = canonical_username is not None
        ```
    """
    try:
        return get_active_directory_user(username, timeout=timeout).username
    except (requests.RequestException, pydantic.ValidationError) as e:
        logger.warning("Failed to validate username '%s': %s", username, e)
        return None


def validate_rig_computer_name(rig: TRig) -> TRig:
    """Ensures rig and computer name are set from environment variables if available, otherwise defaults to rig configuration values."""
    rig_name = get_aind_rig_name()
    computer_name = os.environ.get("hostname", None)

    if rig_name is None:
        logger.warning(
            "'%s' environment variable not set. Defaulting to rig name from configuration. %s",
            _RIG_NAME_ENV_VAR,
            rig.rig_name,
        )
        rig_name = rig.rig_name
    if computer_name is None:
        computer_name = rig.computer_name
        logger.warning(
            "'hostname' environment variable not set. Defaulting to computer name from configuration. %s",
            rig.computer_name,
        )

    if rig_name != rig.rig_name or computer_name != rig.computer_name:
        logger.warning(
            "Rig name or computer name from environment variables do not match the rig configuration. "
            "Forcing rig name: %s and computer name: %s from environment variables.",
            rig_name,
            computer_name,
        )
    _rig = rig.model_copy(update={"rig_name": rig_name, "computer_name": computer_name})
    return _rig
