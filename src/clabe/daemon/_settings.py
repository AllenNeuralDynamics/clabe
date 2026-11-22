"""The daemon's settings: where it listens and which experiment repositories it offers (``daemon:`` in clabe.yml)."""

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field, SecretStr

from ..services import ServiceSettings


class RepositoryConfig(BaseModel):
    """An experiment repository the daemon can run experiments from."""

    path: Path = Field(description="The repository's root (where its pyproject.toml and environment are)")
    name: str | None = Field(default=None, description="Shown in the UI; defaults to the folder's name")
    experiments: str = Field(
        default="experiments", description="The experiment file, or directory of experiment files, inside the repo"
    )

    @property
    def display_name(self) -> str:
        """The repository's name in the UI and the API."""
        return self.name or self.path.name


class DaemonSettings(ServiceSettings):
    """Settings of ``clabe daemon run``.

    Example ``clabe.yml``::

        daemon:
          host: 0.0.0.0
          token: !env CLABE_TOKEN
          repositories:
            - path: C:/git/Aind.Experiment.VrForaging
            - path: C:/git/Aind.Experiment.DynamicForaging
              experiments: src/experiments
    """

    __yml_section__: ClassVar[str | None] = "daemon"

    host: str = Field(default="127.0.0.1", description="Interface to bind (0.0.0.0 to reach it from the LAN)")
    port: int = Field(default=8070, description="Port of the daemon (runs are proxied under /run/)")
    token: SecretStr | None = Field(
        default=None, description="Shared token required by every API call (defaults to $CLABE_TOKEN)"
    )
    repositories: list[RepositoryConfig] = Field(default_factory=list, description="The experiment repositories")
    command: list[str] = Field(
        default=["uv", "run", "--directory", "{repository}", "clabe"],
        description="How to call clabe in a repository's own environment; {repository} is its path",
    )
    run_arguments: list[str] = Field(
        default_factory=list, description="Extra arguments for every `clabe run`, e.g. --skip-hardware-validation"
    )
    runs_directory: Path | None = Field(
        default=None, description="Where launched runs' output is logged; defaults to a temporary directory"
    )
