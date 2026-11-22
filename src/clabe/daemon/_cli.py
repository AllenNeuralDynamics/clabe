"""``clabe daemon``: run the daemon, or install, restart and inspect it (also over SSH)."""

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, CliApp, CliImplicitFlag, CliSubCommand

from . import _install
from ._settings import DaemonSettings

logger = logging.getLogger(__name__)


def _token(settings: DaemonSettings) -> str | None:
    """The daemon's token: its setting, else ``$CLABE_TOKEN``."""
    return settings.token.get_secret_value() if settings.token else os.environ.get("CLABE_TOKEN") or None


class _RunDaemonCli(DaemonSettings, cli_kebab_case=True):
    """Run the daemon in the foreground (what the scheduled task starts)."""

    open_browser: CliImplicitFlag[bool] = Field(default=False, description="Open the daemon's page once it serves")
    log_file: Path | None = Field(
        default_factory=lambda: _install.default_working_directory() / "daemon.log",
        description="Where the daemon logs (rotated at 5 MB, 3 kept)",
    )

    def cli_cmd(self):
        """Serve until interrupted."""
        import uvicorn

        from ._app import create_daemon

        if self.log_file is not None:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(self.log_file, maxBytes=5_000_000, backupCount=3)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            logging.getLogger().addHandler(handler)
            logging.getLogger().setLevel(logging.INFO)
        token = _token(self)
        app = create_daemon(self, token=token)
        shown = "localhost" if self.host in ("127.0.0.1", "0.0.0.0", "::") else self.host
        url = f"http://{shown}:{self.port}/" + (f"?token={token}" if token else "")
        logger.info("clabe daemon starting at http://%s:%s/ (pid %s)", shown, self.port, os.getpid())
        print(f"clabe daemon at {url}", flush=True)
        if self.open_browser:
            import webbrowser

            webbrowser.open(url)
        uvicorn.run(app, host=self.host, port=self.port, log_level="warning", log_config=None)


class _InstallCli(BaseSettings, cli_kebab_case=True):
    """Start the daemon at logon (a Windows scheduled task that restarts it on failure), and now.

    Run it as the user the daemon should run as (an SSH session of that user works). Installing again replaces the
    task; a daemon already running keeps its old command until `clabe daemon restart`.
    """

    uvx: str | None = Field(
        default=None,
        description="Start the daemon with `uvx --from <spec> clabe daemon run`, e.g. aind-clabe==0.18.0 or "
        "git+https://github.com/AllenNeuralDynamics/clabe@v0.18.0 (no install needed; the version is pinned)",
    )
    working_directory: Path | None = Field(
        default=None, description="Where the task runs and the daemon logs (default %LOCALAPPDATA%\\clabe)"
    )

    def cli_cmd(self):
        """Register the scheduled task."""
        _report(lambda: _install.install(uvx=self.uvx, working_directory=self.working_directory))


class _RestartCli(DaemonSettings, cli_kebab_case=True):
    """Restart the daemon from its task (e.g. after installing a new version) and wait until it answers.

    Refuses while it runs an experiment or is leased by another machine's run, unless --force.
    """

    force: CliImplicitFlag[bool] = Field(default=False, description="Restart even if a session would be interrupted")
    timeout: float = Field(default=60.0, description="Seconds to wait for the daemon to answer again")

    def cli_cmd(self):
        """Restart and report."""
        _report(lambda: _install.restart(self.port, _token(self), force=self.force, timeout=self.timeout))


class _StatusCli(DaemonSettings, cli_kebab_case=True):
    """Show the daemon's scheduled task, and whether the daemon answers."""

    def cli_cmd(self):
        """Print the task's state and the daemon's."""
        print(_install.status(self.port, _token(self)))


class _UninstallCli(BaseSettings):
    """Stop the daemon and remove its scheduled task."""

    def cli_cmd(self):
        """Remove the scheduled task."""
        _report(_install.uninstall)


class _UpgradeCli(BaseSettings):
    """Upgrade a `uv tool` installation of clabe; restart the daemon afterwards (with --uvx: install again)."""

    def cli_cmd(self):
        """Upgrade the tool installation."""
        _report(_install.upgrade)


def _report(action) -> None:
    """Runs ``action`` and prints its outcome; an OS error exits with status 1."""
    try:
        print(action())
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


class DaemonCli(BaseSettings, cli_kebab_case=True):
    """The clabe daemon: runs experiments on this machine for the web UI and the hub."""

    run: CliSubCommand[_RunDaemonCli]
    install: CliSubCommand[_InstallCli]
    restart: CliSubCommand[_RestartCli]
    status: CliSubCommand[_StatusCli]
    uninstall: CliSubCommand[_UninstallCli]
    upgrade: CliSubCommand[_UpgradeCli]

    def cli_cmd(self):
        """Run the selected subcommand."""
        CliApp.run_subcommand(self)
