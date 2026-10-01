import logging
from collections.abc import Callable
from urllib.parse import quote

import requests
from aind_behavior_services import Session

from .aind_validators import validate_username

logger = logging.getLogger(__name__)

DEFAULT_SMARTSHEET_ENDPOINT = "http://aind-behavior.corp.alleninstitute.org:8080/smartsheet"

SCIENTIFIC_CONTACT_USERNAME_COLUMN = "validated_pi_username"
PROJECT_NAME_COLUMN = "Project Name"


class SmartsheetClient:
    """
    Client for the AIND behavior smartsheet service, looked up by animal.

    Rows are the raw sheet rows (column name to string value) and are cached per
    subject, so a session only costs one request. Every lookup degrades to a logged
    warning instead of raising, so an unreachable service never blocks a session.

    Example:
        ```python
        ss = SmartsheetClient()
        ss.add_scientific_contact(session)
        watchdog_settings.project_name = ss.get_project_name(session) or watchdog_settings.project_name
        ```
    """

    def __init__(
        self,
        base_url: str = DEFAULT_SMARTSHEET_ENDPOINT,
        timeout: float | None = 2,
        validator: Callable[[str], str | None] = validate_username,
    ) -> None:
        """
        Args:
            base_url: Root URL of the smartsheet service.
            timeout: Timeout in seconds for each HTTP request.
            validator: Validates the scientific contact's username, returning the canonical
                name or None to reject it. Defaults to the Active Directory lookup.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._validator = validator
        self._rows: dict[str, dict[str, str] | None] = {}

    def get_row(self, subject: str) -> dict[str, str] | None:
        """
        Fetches the raw sheet row for an animal.

        Args:
            subject: The animal (mouse) id.

        Returns:
            The row, or None if the animal is not found or the service is unreachable.
        """
        if subject not in self._rows:
            self._rows[subject] = self._fetch_row(subject)
        return self._rows[subject]

    def _fetch_row(self, subject: str) -> dict[str, str] | None:
        try:
            response = requests.get(f"{self._base_url}/rows/{quote(subject, safe='')}", timeout=self._timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("Failed to fetch smartsheet row for subject '%s': %s", subject, e)
            return None

    def add_scientific_contact(self, session: Session) -> Session:
        """
        Appends the animal's validated PI to the end of ``session.experimenter``, in place.

        The session is left untouched if the row or the PI is missing, the username fails
        validation, or the PI is already listed.

        Args:
            session: The session to update.

        Returns:
            The same session, for chaining.
        """
        username = self._column(session, SCIENTIFIC_CONTACT_USERNAME_COLUMN)
        if username is None:
            logger.warning("No scientific contact found for subject '%s'.", session.subject)
            return session
        canonical = self._validator(username)
        if canonical is None:
            logger.warning("Scientific contact '%s' for subject '%s' is not valid.", username, session.subject)
        elif canonical not in session.experimenter:
            session.experimenter.append(canonical)
        return session

    def get_project_name(self, session: Session) -> str | None:
        """
        Returns the project name recorded for the session's animal, or None if unavailable.
        """
        return self._column(session, PROJECT_NAME_COLUMN)

    def _column(self, session: Session, column: str) -> str | None:
        row = self.get_row(session.subject)
        value = (row or {}).get(column)
        return value.strip() or None if isinstance(value, str) else None
