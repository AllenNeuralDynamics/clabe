"""``clabe daemon``: run the daemon, or install, restart and inspect it (also over SSH)."""

import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, CliApp, CliImplicitFlag, CliSubCommand

from .._cli_base import CliCommand
from . import _install
from ._settings import DaemonSettings

logger = logging.getLogger(__name__)


def _token(settings: DaemonSettings) -> str | None:
    """The daemon's token (``DAEMON_CLABE_TOKEN``)."""
    return (settings.token.get_secret_value() if settings.token else None) or None


def _utc_formatter() -> logging.Formatter:
    """The daemon log's format, its times in UTC (``2026-11-23T00:39:17.867Z``), like the launcher's log."""
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )
    formatter.converter = time.gmtime
    return formatter


def _need_web() -> None:
    """Stops with a clear message when the web server (the ``frontend`` extra) is not installed."""
    from .._extras import require_web

    try:
        require_web("The daemon")
    except ImportError as exc:
        raise SystemExit(str(exc)) from None


def _send_logs_to_otel(endpoint: str | None) -> None:
    """Sends the daemon's logs through OpenTelemetry, as service ``clabe-daemon``, when an endpoint is set
    (``DAEMON_CLABE_OTEL_ENDPOINT``)."""
    if not endpoint:
        return
    try:
        from ..logging.otel import OtelSettings
        from ..logging.otel._setup import configure
    except ImportError:
        logger.warning("DAEMON_CLABE_OTEL_ENDPOINT is set but the otel extra is not installed (aind-clabe[otel]).")
        return
    try:
        configure(OtelSettings(enabled=True, endpoint=endpoint, service_name="clabe-daemon"))
        logger.info("Sending the daemon's logs to %s", endpoint)
    except Exception:  # telemetry must never keep the daemon from starting
        logger.exception("Could not set up OpenTelemetry for the daemon")


class _RunDaemonCli(DaemonSettings, cli_kebab_case=True):
    """Run the daemon in the foreground (what the scheduled task starts)."""

    open_browser: CliImplicitFlag[bool] = Field(default=False, description="Open the daemon's page once it serves")
    log_file: Path | None = Field(
        default_factory=lambda: _install.default_working_directory() / "daemon.log",
        description="Where the daemon logs (rotated at 5 MB, 3 kept)",
    )

    def cli_cmd(self):
        """Serve until interrupted."""
        _need_web()
        import uvicorn

        from ._app import create_daemon

        if self.log_file is not None:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(self.log_file, maxBytes=5_000_000, backupCount=3)
            handler.setFormatter(_utc_formatter())
            logging.getLogger().addHandler(handler)
            logging.getLogger().setLevel(logging.INFO)
        token = _token(self)
        _send_logs_to_otel(self.otel_endpoint)
        app = create_daemon(self, token=token)
        host = self.host
        shown = "localhost" if host in ("127.0.0.1", "0.0.0.0", "::") else host
        url = f"http://{shown}:{self.port}/" + (f"?token={token}" if token else "")
        logger.info("clabe daemon starting at http://%s:%s/ (pid %s)", shown, self.port, os.getpid())
        print(f"clabe daemon at {url}", flush=True)
        if self.open_browser:
            import webbrowser

            webbrowser.open(url)
        uvicorn.run(app, host=host, port=self.port, log_level="warning", log_config=None)


class _InstallCli(CliCommand, cli_kebab_case=True):
    """Start the daemon at logon (a Windows scheduled task that restarts it on failure), and now.

    Run it as the user the daemon should run as (an SSH session of that user works). Installing again replaces the
    task; a daemon already running keeps its old command until `clabe daemon restart`.
    """

    working_directory: Path | None = Field(
        default=None, description="Where the task runs and the daemon logs (default %LOCALAPPDATA%\\clabe)"
    )

    def cli_cmd(self):
        """Register the scheduled task."""
        _need_web()
        _report(lambda: _install.install(working_directory=self.working_directory))


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
    """Show the daemon's scheduled task, whether the daemon answers, and the issue reports waiting for the hub."""

    def cli_cmd(self):
        """Print the task's state, the daemon's, and how many reports wait."""
        print(_install.status(self.port, _token(self)))
        print(reports_waiting(self.reports_path()))


def reports_waiting(directory: Path) -> str:
    """How many issue reports wait in ``directory`` for the hub to collect them, as a sentence."""
    from ..reports import count_reports

    count = count_reports(directory)
    return f"{count} issue report{'' if count == 1 else 's'} waiting for the hub in {directory}."


class _UninstallCli(BaseSettings):
    """Stop the daemon and remove its scheduled task."""

    def cli_cmd(self):
        """Remove the scheduled task."""
        _report(_install.uninstall)


class _UpgradeCli(BaseSettings):
    """Upgrade the `uv tool` installation of clabe; restart the daemon afterwards (the installer does both)."""

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
