from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import (
    CliImplicitFlag,
)

from ..services import ServiceSettings


class LauncherCliArgs(ServiceSettings, cli_prog_name="clabe", cli_kebab_case=True):
    """
    CLI arguments for the launcher using Pydantic for validation and configuration.

    Provides command-line argument parsing and validation for launcher operations.
    """

    data_directory: Path = Field(default=Path("C:/data"), description="The data directory where to save the data")
    repository_directory: Optional[Path] = Field(
        default=None, description="The repository root directory. If None will be auto-detected."
    )
    debug_mode: CliImplicitFlag[bool] = Field(default=False, description="Whether to run in debug mode")
    allow_dirty: CliImplicitFlag[bool] = Field(
        default=False, description="Whether to allow the launcher to run with a dirty repository"
    )
    skip_hardware_validation: CliImplicitFlag[bool] = Field(
        default=False, description="Whether to skip hardware validation"
    )
