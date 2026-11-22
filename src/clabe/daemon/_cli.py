"""``clabe daemon``: run the daemon, or install it to start with the user's session."""

import logging
import os
import sys

from pydantic import Field
from pydantic_settings import BaseSettings, CliApp, CliImplicitFlag, CliSubCommand

from . import _install
from ._settings import DaemonSettings

logger = logging.getLogger(__name__)


class _RunDaemonCli(DaemonSettings, cli_kebab_case=True):
    """Run the daemon in the foreground (what the scheduled task starts)."""

    open_browser: CliImplicitFlag[bool] = Field(default=False, description="Open the daemon's page once it serves")

    def cli_cmd(self):
        """Serve until interrupted."""
        import uvicorn

        from ._app import create_daemon

        token = self.token.get_secret_value() if self.token else os.environ.get("CLABE_TOKEN") or None
        app = create_daemon(self, token=token)
        shown = "localhost" if self.host in ("127.0.0.1", "0.0.0.0", "::") else self.host
        url = f"http://{shown}:{self.port}/" + (f"?token={token}" if token else "")
        print(f"clabe daemon at {url}", flush=True)
        if self.open_browser:
            import webbrowser

            webbrowser.open(url)
        uvicorn.run(app, host=self.host, port=self.port, log_level="warning")


class _InstallCli(BaseSettings):
    """Start the daemon at logon (a Windows scheduled task that restarts it on failure), and now."""

    def cli_cmd(self):
        """Register the scheduled task."""
        _report(_install.install)


class _StatusCli(BaseSettings):
    """Show the daemon's scheduled task."""

    def cli_cmd(self):
        """Print the task's state."""
        print(_install.status())


class _UninstallCli(BaseSettings):
    """Stop the daemon and remove its scheduled task."""

    def cli_cmd(self):
        """Remove the scheduled task."""
        _report(_install.uninstall)


class _UpgradeCli(BaseSettings):
    """Upgrade clabe (`uv tool upgrade aind-clabe`); restart the daemon afterwards."""

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
    status: CliSubCommand[_StatusCli]
    uninstall: CliSubCommand[_UninstallCli]
    upgrade: CliSubCommand[_UpgradeCli]

    def cli_cmd(self):
        """Run the selected subcommand."""
        CliApp.run_subcommand(self)
