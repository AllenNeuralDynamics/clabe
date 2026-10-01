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


class SmartsheetScheduleClient:
    """
    Client for the AIND behavior scheduling smartsheet, looked up by animal.

    Rows are the raw sheet rows (column name to string value) and are cached per
    subject, so a session only costs one request. ``get_row`` degrades to a logged warning
    and ``None`` when the service is unreachable, but ``add_scientific_contact`` and
    ``get_project_name`` require their values and raise ``ValueError`` without them.

    Example:
        ```python
        ss = SmartsheetScheduleClient()
        session = ss.add_scientific_contact(session)
        watchdog_settings.project_name = ss.get_project_name(session)
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
        """Requests the row from the service, returning None (and logging) on any failure."""
        try:
            response = requests.get(f"{self._base_url}/rows/{quote(subject, safe='')}", timeout=self._timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("Failed to fetch smartsheet row for subject '%s': %s", subject, e)
            return None

    def add_scientific_contact(self, session: Session) -> Session:
        """
        Returns a copy of the session with the animal's validated scientific contact added
        to the end of ``experimenter``, with duplicate names removed (first occurrence kept).
        The given session is never modified.

        The scientific contact is required: this raises instead of degrading.

        Args:
            session: The session to build on.

        Returns:
            The updated copy of the session, so callers must use the return value.

        Raises:
            ValueError: If the animal's row or its scientific contact cannot be found, or the
                username fails validation.
        """
        username = self._column(session, SCIENTIFIC_CONTACT_USERNAME_COLUMN)
        if username is None:
            raise ValueError(
                f"No scientific contact ('{SCIENTIFIC_CONTACT_USERNAME_COLUMN}') found for subject '{session.subject}'."
            )
        canonical = self._validator(username)
        if canonical is None:
            raise ValueError(f"Scientific contact '{username}' for subject '{session.subject}' is not valid.")
        experimenter = list(dict.fromkeys([*session.experimenter, canonical]))
        return session.model_copy(update={"experimenter": experimenter})

    def get_project_name(self, session: Session) -> str:
        """
        Returns the project name recorded for the session's animal.

        The project name is required: this raises instead of degrading.

        Args:
            session: The session whose animal to look up.

        Returns:
            The project name.

        Raises:
            ValueError: If the animal's row or its project name cannot be found.
        """
        project_name = self._column(session, PROJECT_NAME_COLUMN)
        if project_name is None:
            raise ValueError(f"No project name ('{PROJECT_NAME_COLUMN}') found for subject '{session.subject}'.")
        return project_name

    def _column(self, session: Session, column: str) -> str | None:
        """Returns the stripped value of a column in the session's row, or None if absent or blank."""
        row = self.get_row(session.subject)
        value = (row or {}).get(column)
        return value.strip() or None if isinstance(value, str) else None
