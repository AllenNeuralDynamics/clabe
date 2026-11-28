"""``clabe daemon``: run the daemon, or install, restart and inspect it (also over SSH)."""

import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

from pydantic import Field
from pydantic_settings import BaseSettings, CliApp, CliImplicitFlag, CliSubCommand

from .._cli_base import CliCommand
from . import _deploy, _install
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
    watchdog: bool = Field(
        default=True, description="End the daemon when its server stops answering (its task starts a new one)"
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
        watchdog = None
        if self.watchdog:
            from ._watchdog import Watchdog

            local = "127.0.0.1" if host in ("0.0.0.0", "::", "localhost") else host
            watchdog = Watchdog(f"http://{local}:{self.port}/api/daemon/info").start()
        try:
            uvicorn.run(app, host=host, port=self.port, log_level="warning", log_config=None)
        finally:
            if watchdog is not None:
                watchdog.stop()


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


class _StopCli(DaemonSettings, cli_kebab_case=True):
    """Stop the daemon (end its task) and wait until nothing answers on its port, e.g. before updating clabe.

    Refuses while it runs an experiment or is leased by another machine's run, unless --force. It stays stopped (its
    task is disabled, also at logon) until `clabe daemon start`, `restart` or `deploy`.
    """

    force: CliImplicitFlag[bool] = Field(default=False, description="Stop even if a session would be interrupted")
    timeout: float = Field(default=15.0, description="Seconds to wait for the daemon to stop answering")
    if_installed: CliImplicitFlag[bool] = Field(
        default=False, description="Not installed is not an error (nothing to stop), e.g. on a new rig"
    )

    def cli_cmd(self):
        """Stop and report."""
        _report(
            lambda: _install.stop(
                self.port, _token(self), force=self.force, timeout=self.timeout, if_installed=self.if_installed
            )
        )


class _DeployCli(CliCommand, cli_kebab_case=True):
    """Install a clabe for the daemon (or update it, or go back to one) and switch the daemon to it. Safe to repeat.

    Each clabe gets its own environment under %LOCALAPPDATA%\\clabe\\versions, installed while the daemon keeps
    running; the daemon only restarts when its clabe or its hubs change, and refuses while it runs a session unless
    --force. If the new clabe does not start, the previous one is started again. With none of --version, --commit,
    --lock or --package: the latest release.
    """

    version: str | None = Field(default=None, description="A clabe release, e.g. 0.18.0")
    commit: str | None = Field(default=None, description="A commit of clabe's main branch: its build, e.g. c7cc84f")
    lock: Path | None = Field(default=None, description="A repository (or its uv.lock): the clabe it locks")
    package: str | None = Field(default=None, description="Any uv requirement instead, e.g. aind-clabe[frontend]==0.18")
    extras: str = Field(default=",".join(_deploy.DEFAULT_EXTRAS), description="aind-clabe's extras to install")
    hubs: str | None = Field(
        default=None, description="Hubs to enroll with, ';'-separated ('none': no hub); default: leave them as they are"
    )
    force: CliImplicitFlag[bool] = Field(default=False, description="Restart even if a session would be interrupted")
    restart: CliImplicitFlag[bool] = Field(default=False, description="Restart even when nothing changed")
    timeout: float = Field(default=120.0, description="Seconds to wait for the daemon to answer")
    python: str | None = Field(default=None, description="Python for a new environment (default: this clabe's)")
    dry_run: CliImplicitFlag[bool] = Field(default=False, description="Say what would be done, and do nothing")

    def request(self) -> "_deploy.Request":
        """The clabe asked for."""
        given = [name for name in ("version", "commit", "lock", "package") if getattr(self, name)]
        if len(given) > 1:
            raise SystemExit(f"Pass one of --version, --commit, --lock and --package (got {', '.join(given)}).")
        extras = _deploy._extras(self.extras)
        if self.version:
            return _deploy.request_version(self.version, extras)
        try:
            if self.commit:
                return _deploy.request_commit(self.commit, extras)
            if self.lock:
                return _deploy.request_lock(self.lock, extras)
        except OSError as exc:
            raise SystemExit(str(exc)) from None
        if self.package:
            return _deploy.request_package(self.package, extras)
        return _deploy.request_latest(extras)

    def cli_cmd(self):
        """Deploy and report."""
        from ..settings import split_list

        request = self.request()
        hubs = None
        if self.hubs is not None:
            hubs = [] if self.hubs.strip().lower() in ("none", "") else split_list(self.hubs)
        settings = DaemonSettings()
        _report(
            lambda: _deploy.deploy(
                request,
                port=settings.port,
                token=_token(settings),
                hubs=hubs,
                force=self.force,
                restart=self.restart,
                timeout=self.timeout,
                python=self.python,
                dry_run=self.dry_run,
            )
        )


class _StartCli(DaemonSettings, cli_kebab_case=True):
    """Start the daemon from its task and wait until it answers."""

    timeout: float = Field(default=60.0, description="Seconds to wait for the daemon to answer")

    def cli_cmd(self):
        """Start and report."""
        _report(lambda: _install.start(self.port, _token(self), timeout=self.timeout))


class _StatusCli(DaemonSettings, cli_kebab_case=True):
    """Show the daemon's scheduled task, whether the daemon answers, and the issue reports waiting for the hub."""

    def cli_cmd(self):
        """Print the task's state, the daemon's, and how many reports wait."""
        print(_install.status(self.port, _token(self)))
        print(reports_waiting(self.reports_path()))


class _OpenCli(DaemonSettings, cli_kebab_case=True):
    """Open the daemon's page in the browser, signed in (for a desktop shortcut on the rig).

    The link carries the daemon's token once; the page keeps it in a cookie and drops it from the address.
    """

    run_page: CliImplicitFlag[bool] = Field(default=False, description="Open the run's page instead of the daemon's")

    def cli_cmd(self):
        """Check that the daemon answers, then open its page with its token."""
        token = _token(self)
        answer = _install.probe(self.port, token)
        if answer is None:
            raise SystemExit(f"No daemon answers on port {self.port}: start it with `clabe daemon start`.")
        if answer.get("status") == "unauthorized":
            raise SystemExit("The daemon refuses this token: DAEMON_CLABE_TOKEN differs from the running daemon's.")
        import webbrowser

        webbrowser.open(open_url(self, run_page=self.run_page))


def open_url(settings: DaemonSettings, *, run_page: bool = False) -> str:
    """The daemon's page on this machine (or its run's), with the token in the link when the daemon has one."""
    host = "localhost" if settings.host in ("127.0.0.1", "0.0.0.0", "::") else settings.host
    token = _token(settings)
    query = f"?token={quote(token, safe='')}" if token else ""
    return f"http://{host}:{settings.port}/{'run/' if run_page else ''}{query}"


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
    """Deprecated: on Windows, run the installer instead (it stops the daemon, installs and starts it again).

    Elsewhere it runs `uv tool upgrade aind-clabe`.
    """

    def cli_cmd(self):
        """Upgrade the tool installation."""
        _report(_install.upgrade)


class _ViewCli(BaseSettings):
    """Show the terminal UI of the run the daemon hosts in this window. Closing the window stops the run."""

    def cli_cmd(self):
        """Show the run until it ends."""
        from ._local import DaemonRefused, reach
        from ._viewer import view

        daemon = reach(start=False)
        if daemon is None:
            print("The daemon on this machine does not answer.", file=sys.stderr)
            raise SystemExit(1)
        try:
            code = view(daemon)
        except (DaemonRefused, OSError, RuntimeError) as exc:
            print(f"Cannot show the run: {exc}", file=sys.stderr)
            time.sleep(5)  # this window may have been opened for it: leave the message readable
            raise SystemExit(1) from exc
        raise SystemExit(code)


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
    deploy: CliSubCommand[_DeployCli]
    install: CliSubCommand[_InstallCli]
    start: CliSubCommand[_StartCli]
    stop: CliSubCommand[_StopCli]
    restart: CliSubCommand[_RestartCli]
    status: CliSubCommand[_StatusCli]
    open: CliSubCommand[_OpenCli]
    view: CliSubCommand[_ViewCli]
    uninstall: CliSubCommand[_UninstallCli]
    upgrade: CliSubCommand[_UpgradeCli]

    def cli_cmd(self):
        """Run the selected subcommand."""
        CliApp.run_subcommand(self)


def main() -> None:
    """``clabe-daemon``: the ``clabe daemon`` commands from a windowless executable (what the scheduled task runs).

    A windowless program has no standard streams (``sys.stdout`` is None): they go to ``os.devnull``, so printing
    and logging to them never fail.
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115 - lives as long as the process
    CliApp.run(DaemonCli)
