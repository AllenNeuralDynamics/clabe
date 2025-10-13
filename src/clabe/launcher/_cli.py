import os
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import (
    CliImplicitFlag,
)

from ..services import ServiceSettings


class LauncherCliArgs(ServiceSettings, cli_prog_name="clabe", cli_kebab_case=True):
    """
    Base class for CLI arguments using Pydantic for validation and configuration.

    Attributes:
        data_dir (os.PathLike): The data directory where to save the data.
        repository_dir (Optional[os.PathLike]): The repository root directory.
        debug_mode (CliImplicitFlag[bool]): Whether to run in debug mode.
        allow_dirty (CliImplicitFlag[bool]): Whether to allow running with a dirty repository.
        skip_hardware_validation (CliImplicitFlag[bool]): Whether to skip hardware validation.
        subject (Optional[str]): The name of the subject. If None, will be prompted later.
        task_logic_path (Optional[os.PathLike]): Path to the task logic schema. If None, will be prompted later.
        rig_path (Optional[os.PathLike]): Path to the rig schema. If None, will be prompted later.
        temp_dir (os.PathLike): Directory used for launcher temp files.

    Example:
        # Create CLI args from command line
        args = LauncherCliArgs()

        # Create with specific values
        args = LauncherCliArgs(
            data_dir="/path/to/data",
            debug_mode=True,
            subject="mouse_001"
        )

        # Access properties
        print(f"Data directory: {args.data_dir}")
        print(f"Debug mode: {args.debug_mode}")
    """

    data_dir: os.PathLike = Field(description="The data directory where to save the data")
    repository_dir: Optional[os.PathLike] = Field(default=None, description="The repository root directory")
    debug_mode: CliImplicitFlag[bool] = Field(default=False, description="Whether to run in debug mode")
    allow_dirty: CliImplicitFlag[bool] = Field(
        default=False, description="Whether to allow the launcher to run with a dirty repository"
    )
    skip_hardware_validation: CliImplicitFlag[bool] = Field(
        default=False, description="Whether to skip hardware validation"
    )
    subject: Optional[str] = Field(default=None, description="The name of the subject. If None, will be prompted later")
    temp_dir: os.PathLike = Field(
        default=Path("local/.temp"), description="The directory used for the launcher temp files"
    )
