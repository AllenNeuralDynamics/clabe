"""The daemon's settings: where it listens and which experiment repositories it offers (``daemon:`` in clabe.yml)."""

import logging
import os
import re
import tomllib
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field, SecretStr, field_validator

from ..services import ServiceSettings

logger = logging.getLogger(__name__)


#: The file of a repository that declares (or imports) its workflows.
MAIN_FILE = "main.py"


class RepositoryConfig(BaseModel):
    """An experiment repository the daemon can run experiments from: its workflows are those of its ``main.py``."""

    path: Path = Field(description="The repository's root (where its main.py, pyproject.toml and environment are)")
    name: str | None = Field(default=None, description="Shown in the UI; defaults to the folder's name")

    @property
    def display_name(self) -> str:
        """The repository's name in the UI and the API."""
        return self.name or self.path.name


def _default_roots() -> list[Path]:
    """Where experiment repositories are usually cloned: ``C:/git`` on Windows, ``~/git`` elsewhere."""
    return [Path("C:/git")] if os.name == "nt" else [Path.home() / "git"]


def is_experiment_repository(path: Path) -> bool:
    """Whether ``path`` is a clabe experiment repository: a ``main.py`` and a ``pyproject.toml`` depending on clabe."""
    pyproject = path / "pyproject.toml"
    if not (path / MAIN_FILE).is_file() or not pyproject.is_file():
        return False
    try:
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    groups = project.get("dependency-groups", {})
    requirements = [
        *project.get("project", {}).get("dependencies", []),
        *(r for group in groups.values() for r in group if isinstance(r, str)),
    ]
    return any(re.match(r"\s*aind-clabe(?![\w.-])", r, re.IGNORECASE) for r in requirements)


class DaemonSettings(ServiceSettings):
    """Settings of ``clabe daemon run``. Every setting has a default; a rig usually needs none.

    Repositories are found under ``repository_roots``: every folder right under a root with a ``main.py`` and a
    ``pyproject.toml`` depending on ``aind-clabe``. List others (any layout) in ``repositories``.

    Example ``clabe.yml``:

    ```yaml
    daemon:
      repository_roots: [C:/git, D:/experiments]
      repositories: [C:/somewhere/else/Aind.Experiment.Special]
    ```
    """

    __yml_section__: ClassVar[str | None] = "daemon"

    host: str | None = Field(
        default=None,
        description="Interface to bind; by default the network (0.0.0.0) when a token is set, else only this PC",
    )
    port: int = Field(default=8070, description="Port of the daemon (runs are proxied under /run/)")
    token: SecretStr | None = Field(
        default=None, description="Shared token required by every API call (defaults to $CLABE_TOKEN)"
    )
    repository_roots: list[Path] = Field(
        default_factory=_default_roots, description="Folders whose experiment repositories are found automatically"
    )
    repositories: list[RepositoryConfig] = Field(
        default_factory=list, description="More repositories, by path (or path and name), found or not under a root"
    )
    command: list[str] = Field(
        default=["uv", "run", "--directory", "{repository}", "clabe"],
        description="How to call clabe in a repository's own environment; {repository} is its path",
    )
    run_arguments: list[str] = Field(
        default_factory=list, description="Extra arguments for every `clabe run`, e.g. --skip-hardware-validation"
    )
    lease_grace: float = Field(
        default=300.0,
        description="Worker role: seconds after a lease expires during which only the same run may take it back "
        "(a restarted launcher reattaching to its jobs)",
    )
    max_jobs: int = Field(default=4, description="Worker role: jobs run at the same time for a remote run")
    max_file_size: int = Field(default=50 * 1024 * 1024, description="Worker role: largest file transferred (bytes)")
    file_transfer_dir: Path | None = Field(
        default=None, description="Worker role: where transferred files go; defaults to a temporary directory"
    )
    runs_directory: Path | None = Field(
        default=None, description="Where launched runs' output is logged; defaults to a temporary directory"
    )

    @field_validator("repositories", mode="before")
    @classmethod
    def _paths_are_repositories(cls, value: object) -> object:
        """Accepts plain paths in the list: ``repositories: [C:/git/Aind.Experiment.Special]``."""
        if isinstance(value, list):
            return [{"path": item} if isinstance(item, str | os.PathLike) else item for item in value]
        return value

    def bind_host(self, token: str | None) -> str:
        """The interface to bind: ``host`` when set, else the network with a token and only this PC without."""
        if self.host is not None:
            return self.host
        return "0.0.0.0" if token else "127.0.0.1"

    def find_repositories(self) -> list[RepositoryConfig]:
        """The listed repositories, then those found under the roots (sorted); a name is used once."""
        found: dict[str, RepositoryConfig] = {}
        for repository in self.repositories:
            found.setdefault(repository.display_name, repository)
        for root in self.repository_roots:
            try:
                candidates = sorted(p for p in root.iterdir() if p.is_dir())
            except OSError:
                continue
            for path in candidates:
                if not is_experiment_repository(path):
                    continue
                if path.name in found:
                    if found[path.name].path.resolve() != path.resolve():
                        logger.warning("Ignoring %s: a repository named %s is already offered.", path, path.name)
                    continue
                found[path.name] = RepositoryConfig(path=path)
        return list(found.values())
