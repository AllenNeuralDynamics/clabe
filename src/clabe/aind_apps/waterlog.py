import os
import shutil
from pathlib import Path
from typing import ClassVar, Optional

from pydantic import Field
from pydantic_settings import CliApp, SettingsConfigDict

from clabe.apps._base import identity_parser

from ..apps import Command, CommandResult, ExecutableApp
from ..apps._executors import _DefaultExecutorMixin
from ..services import ServiceSettings


class WaterlogSettings(ServiceSettings):
    """Settings for the waterlog service."""

    model_config = SettingsConfigDict(cli_kebab_case=True)
    # This should be ok since the subclass initializes first the and toml sources get appended to the settings dict
    __yml_section__: ClassVar[Optional[str]] = "waterlog"

    username: Optional[str] = Field(default=None, description="Username for the waterlog service")
    mouse_id: Optional[str] = Field(default=None, description="Mouse ID for the waterlog service")
    mouse_weight: Optional[float] = Field(default=None, description="Mouse weight for the waterlog service")
    comment: Optional[str] = Field(default=None, description="Comment for the waterlog service")
    earned_water: Optional[float] = Field(default=None, description="Water earned during behavior task (mL)")
    water_supplement_ml: Optional[float] = Field(default=None, description="Water supplement amount (mL)")
    water_supplement_delivered: Optional[bool] = Field(
        default=None, description="Flag indicating if the water supplement has been delivered"
    )


class WaterlogApp(ExecutableApp, _DefaultExecutorMixin):
    """App for logging water consumption and related information."""

    _EXECUTABLE: Optional[Path] = Path(os.getenv("PROGRAMDATA", r"C:\ProgramData")) / r"AIBS_MPE\waterlog\waterlog.exe"

    def __init__(self, settings: WaterlogSettings):
        """Initialize the WaterlogApp with the given settings."""
        self._executable = str(self._EXECUTABLE)
        self._settings = settings
        self.validate()
        _cmd = [self._executable] + CliApp.serialize(settings)
        self._command = Command[CommandResult](cmd=_cmd, output_parser=identity_parser)

    def validate(self) -> None:
        """Validates the settings and checks for the presence of the waterlog executable."""
        if not Path(self._executable).exists():
            if (loc := shutil.which("waterlog.exe")) is None:
                raise FileNotFoundError(
                    f"'{self._EXECUTABLE}' command not found. Please ensure it is installed and available."
                )
            self._executable = loc

    @property
    def command(self) -> Command[CommandResult]:
        """Get the command to execute."""
        return self._command
