import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import requests
from aind_behavior_services import Session
from pydantic import BaseModel, ConfigDict, Field, field_validator
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from .aind_validators import validate_username

logger = logging.getLogger(__name__)

DEFAULT_SMARTSHEET_ENDPOINT = "http://aind-behavior.corp.alleninstitute.org:8080/smartsheet"


class SmartsheetRow(BaseModel):
    """
    A parsed row of the scheduling smartsheet.

    Fields are populated from the sheet's column names (or by field name). Blank or null
    string values become None, and every column not modeled here is kept in ``model_extra``.
    """

    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True, extra="allow")

    mouse_id: str = Field(alias="Mouse ID")
    scientific_contact_username: str | None = Field(default=None, alias="validated_pi_username")
    trainer_username: str | None = Field(default=None, alias="validated_trainer_username")
    project_name: str | None = Field(default=None, alias="Project Name")
    tags: list[str] = Field(default_factory=list, alias="Tags")

    @field_validator("scientific_contact_username", "trainer_username", "project_name", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("tags", mode="before")
    @classmethod
    def _null_tags(cls, value: Any) -> Any:
        return [] if value is None else value


class SmartsheetScheduleClient:
    """
    Client for the AIND behavior scheduling smartsheet, looked up by animal.

    Rows are parsed into rows and are cached per subject, so a session only costs one request. ``get_row`` degrades to a logged warning
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
        timeout: float | tuple[float, float] | None = (1, 4),
        validator: Callable[[str], str | None] = validate_username,
    ) -> None:
        """
        Args:
            base_url: Root URL of the smartsheet service.
            timeout: Timeout in seconds for each HTTP request, or a (connect, read) pair.
            validator: Validates the scientific contact's username, returning the canonical
                name or None to reject it. Defaults to the Active Directory lookup.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.mount(
            "http://", HTTPAdapter(max_retries=Retry(total=2, backoff_factor=0.3, status_forcelist=(502, 503, 504)))
        )
        self._validator = validator
        self._rows: dict[str, SmartsheetRow | None] = {}

    def get_row(self, subject: str) -> SmartsheetRow | None:
        """
        Fetches the parsed sheet row for an animal.

        Args:
            subject: The animal (mouse) id.

        Returns:
            The row, or None if the animal is not found or the service is unreachable.
            Only found rows and definitive not-found results are cached; failures are retried on the next call.
        """
        if subject in self._rows:
            return self._rows[subject]
        try:
            response = self._session.get(f"{self._base_url}/rows/{quote(subject, safe='')}", timeout=self._timeout)
            if response.status_code == 404:
                self._rows[subject] = None  # definitive; transient failures below are not cached
                return None
            response.raise_for_status()
            self._rows[subject] = SmartsheetRow.model_validate(response.json())
            return self._rows[subject]
        except (requests.RequestException, ValueError) as e:  # pydantic's ValidationError is a ValueError
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
            ValueError: If the animal's row cannot be retrieved, the row has no scientific contact,
                or the username fails validation.
        """
        username = self._require_row(session).scientific_contact_username
        if username is None:
            raise ValueError(f"The smartsheet row for subject '{session.subject}' has no scientific contact.")
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
            ValueError: If the animal's row cannot be retrieved or has no project name.
        """
        project_name = self._require_row(session).project_name
        if project_name is None:
            raise ValueError(f"The smartsheet row for subject '{session.subject}' has no project name.")
        return project_name

    def _require_row(self, session: Session) -> SmartsheetRow:
        """Returns the session's row, raising ValueError if it could not be retrieved."""
        row = self.get_row(session.subject)
        if row is None:
            raise ValueError(
                f"No smartsheet row could be retrieved for subject '{session.subject}' "
                "(not listed, service unreachable, or malformed response; see the log)."
            )
        return row
