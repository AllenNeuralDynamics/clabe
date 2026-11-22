import os
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
    frontend: Literal["auto", "tui", "console", "headless"] = Field(
        default="auto",
        description="Frontend for prompts and output: auto (TUI on a terminal, else console), tui, console, or "
        "headless (no screen: answer from the web UI; needs --serve)",
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
    serve: CliImplicitFlag[bool] = Field(
        default=False,
        description="Serve the run over HTTP (web UI and API) while it executes, so other machines can follow and "
        "answer it",
    )
    host: str = Field(
        default="127.0.0.1",
        description="Interface the run server binds; keep localhost and use SSH port forwarding, or 0.0.0.0 for the LAN",
    )
    port: int = Field(default=8090, description="Preferred port of the run server (the next free one is used)")
    token: str | None = Field(
        default=None,
        description="Shared token required by the run server (defaults to $CLABE_TOKEN; none keeps it open)",
    )
    open_browser: CliImplicitFlag[bool] = Field(
        default=False, description="Open the run's web UI in the local browser once it is served"
    )
    clabe_yml: FilePath | None = Field(
        default=None,
        description="Path to a clabe.yml file to load in addition to the known config files. "
        "It ranks right after ./local/clabe.yml and is applied to all service settings created after parsing.",
    )

    @model_validator(mode="after")
    def _headless_needs_serving(self) -> "LauncherCliArgs":
        """A headless run can only be answered through the run server."""
        if self.frontend == "headless" and not self.serve:
            raise ValueError("--frontend headless needs --serve: nothing else could answer its prompts.")
        if self.frontend == "headless":
            from .._extras import require_web

            require_web("--frontend headless")  # its prompts are answered in the browser
        return self

    @model_validator(mode="after")
    def _install_clabe_yml(self) -> "LauncherCliArgs":
        """Installs the file passed via ``--clabe-yml`` as the process-wide clabe.yml document."""
        if self.clabe_yml is None and os.environ.get("CLABE_YML"):
            self.clabe_yml = Path(os.environ["CLABE_YML"])
        if self.clabe_yml is not None:
            self.clabe_yml = self.clabe_yml.resolve()
            set_clabe_yml(_read_clabe_yml(self.clabe_yml))
        return self
