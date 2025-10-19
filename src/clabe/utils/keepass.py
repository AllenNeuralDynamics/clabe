import importlib.util

if importlib.util.find_spec("pykeepass") is None:
    raise ImportError(
        "The 'pykeepass' package is required to use this module. \
            Install the optional dependencies defined in `project.toml' \
                by running `pip install .[aind-services]`"
    )
import os
from pathlib import Path
from typing import ClassVar, Optional

from pykeepass import Entry, PyKeePass

from ..services import Service, ServiceSettings

_PROGRAMDATA = os.getenv("PROGRAMDATA", r"C:\ProgramData")


class KeePassSettings(ServiceSettings):
    """
    Settings for the KeePass service.

    This class provides configuration settings for accessing KeePass password databases.
    It supports authentication using both keyfiles and passwords, with automatic YAML
    configuration loading through the ServiceSettings base class.

    Attributes:
        database (Path): Path to the KeePass database file (.kdbx).
        keyfile (Optional[Path]): Path to the keyfile for database authentication.
            Can be None if using password-only authentication.
        password (Optional[str]): Master password for the database. Can be None
            if using keyfile-only authentication.

    Example:
        ```python
        # Using default settings
        settings = KeePassSettings()

        # Using custom settings
        settings = KeePassSettings(
            database=Path("/path/to/database.kdbx"),
            keyfile=Path("/path/to/keyfile.key"),
            password="master_password"
        )
        ```
    """

    __yml_section__: ClassVar[Optional[str]] = "keepass"

    database: Path = Path(r"\\allen\aibs\mpe\keepass\sipe_sw_passwords.kdbx")
    keyfile: Optional[Path] = Path(_PROGRAMDATA) / r"AIBS_MPE\.secrets\sipe_sw_passwords.keyx"
    password: Optional[str] = None


class KeePass(Service):
    """
    KeePass password manager service for accessing password database entries.

    This service provides a simple interface for connecting to and retrieving entries
    from KeePass password databases. It handles authentication using the provided
    settings and supports both keyfile and password-based authentication methods.

    The service automatically establishes a connection to the database upon initialization
    and provides methods for retrieving password entries by title.

    Attributes:
        _settings (KeePassSettings): Configuration settings for the KeePass database.
        _keepass (PyKeePass): The underlying PyKeePass instance for database operations.

    Example:
        ```python
        # Create settings and service
        settings = KeePassSettings(
            database=Path("/path/to/database.kdbx"),
            password="master_password"
        )
        keepass = KeePass(settings)

        # Retrieve an entry
        entry = keepass.get_entry("my_service_credentials")
        username = entry.username
        password = entry.password
        ```
    """

    def __init__(self, settings: KeePassSettings):
        """
        Initialize the KeePass service with the provided settings.

        Creates a connection to the KeePass database using the authentication
        credentials specified in the settings. The connection is established
        immediately upon initialization.

        Args:
            settings: Configuration settings containing database path and authentication credentials

        Raises:
            FileNotFoundError: If the database file cannot be found
            CredentialsError: If the provided authentication credentials are invalid
            IOError: If there's an error reading the database or keyfile

        Example:
            ```python
            settings = KeePassSettings(database=Path("passwords.kdbx"))
            keepass = KeePass(settings)
            ```
        """
        self._settings = settings
        self._keepass = PyKeePass(
            filename=self._settings.database,
            password=self._settings.password,
            keyfile=self._settings.keyfile,
        )

    def get_entry(self, title: str) -> Entry:
        """
        Retrieve a password entry from the database by title.

        Searches the KeePass database for entries matching the specified title
        and returns the first match found. Entry titles are typically unique
        within a database, but if multiple entries share the same title,
        only the first one encountered will be returned.

        Args:
            title: The title of the entry to retrieve. This should match the entry title exactly (case-sensitive)

        Returns:
            Entry: The KeePass entry object containing username, password, and other metadata associated with the specified title

        Raises:
            ValueError: If no entry is found with the specified title

        Example:
            ```python
            # Retrieve credentials for a service
            entry = keepass.get_entry("GitHub API Token")
            token = entry.password

            # Access other entry properties
            username = entry.username
            url = entry.url
            notes = entry.notes
            ```
        """
        entries = self._keepass.find_entries(title=title)
        if not entries:
            raise ValueError(f"No entry found with title '{title}'")
        else:
            return entries[0]
