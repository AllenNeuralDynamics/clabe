from pathlib import Path
from typing import Literal

from pydantic import Field, FilePath, model_validator
from pydantic_settings import (
    CliImplicitFlag,
)

from ..services import ServiceSettings, _read_clabe_yml, set_clabe_yml


class LauncherCliArgs(ServiceSettings, cli_prog_name="clabe", cli_kebab_case=True):
    """
    CLI arguments for the launcher using Pydantic for validation and configuration.

    Provides command-line argument parsing and validation for launcher operations.
    """

    repository_directory: Path | None = Field(
        default=None, description="The repository root directory. If None will be auto-detected."
    )
    debug_mode: CliImplicitFlag[bool] = Field(default=False, description="Whether to run in debug mode")
    frontend: Literal["auto", "tui", "console"] = Field(
        default="auto",
        description="Frontend for prompts and output: auto (TUI on a terminal, else console), tui, or console",
    )
    verbose: CliImplicitFlag[bool] = Field(
        default=False,
        description="Show informational messages in the UI and console (everything is still logged to file)",
    )
    quiet: CliImplicitFlag[bool] = Field(
        default=False, description="Only show errors in the UI and console (everything is still logged to file)"
    )
    allow_dirty: CliImplicitFlag[bool] = Field(
        default=False, description="Whether to allow the launcher to run with a dirty repository"
    )
    skip_hardware_validation: CliImplicitFlag[bool] = Field(
        default=False, description="Whether to skip hardware validation"
    )
    clabe_yml: FilePath | None = Field(
        default=None,
        description="Path to a clabe.yml file to load in addition to the known config files. "
        "It ranks right after ./local/clabe.yml and is applied to all service settings created after parsing.",
    )

    @model_validator(mode="after")
    def _install_clabe_yml(self) -> "LauncherCliArgs":
        """Installs the file passed via ``--clabe-yml`` as the process-wide clabe.yml document."""
        if self.clabe_yml is not None:
            self.clabe_yml = self.clabe_yml.resolve()
            set_clabe_yml(_read_clabe_yml(self.clabe_yml))
        return self
