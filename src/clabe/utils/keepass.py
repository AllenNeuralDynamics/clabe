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
    __yml_section__: ClassVar[str] = "keepass"

    database: Path = Path(r"\\allen\aibs\mpe\keepass\sipe_sw_passwords.kdbx")
    keyfile: Optional[Path] = Path(_PROGRAMDATA) / r"AIBS_MPE\.secrets\sipe_sw_passwords.keyx"
    password: Optional[str] = None


class KeePass(Service):
    def __init__(self, settings: KeePassSettings):
        self._settings = settings
        self._keepass = PyKeePass(
            filename=self._settings.database,
            password=self._settings.password,
            keyfile=self._settings.keyfile,
        )

    def get_entry(self, title: str) -> Entry:
        entries = self._keepass.find_entries(title=title)
        if not entries:
            raise ValueError(f"No entry found with title '{title}'")
        else:
            return entries[0]
