import logging
import shlex
import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, CliApp, CliImplicitFlag, CliPositionalArg, CliSubCommand

from clabe.launcher._experiments import _select_experiment

from .cache_manager import _CacheManagerCli
from .launcher import Launcher, LauncherCliArgs
from .xml_rpc._server import _XmlRpcServerStartCli

logger = logging.getLogger(__name__)


def _quote(arg: str) -> str:
    """Quotes a command-line argument for the platform's shell."""
    if sys.platform == "win32":
        return f'"{arg}"' if " " in arg else arg
    return shlex.quote(arg)


class _RunCli(LauncherCliArgs):
    """CLI arguments for running a CLABE experiment from a Python file."""

    experiment_path: CliPositionalArg[Path] = Field(
        description="Path to the Python file containing the CLABE experiment to run"
    )
    single_session: CliImplicitFlag[bool] = Field(
        default=False,
        description="Refuse to start if another CLABE session is already running (used when serving the web UI)",
    )

    def _run(self):
        """Builds the launcher, selects the experiment and runs it."""
        launcher = Launcher(settings=self)
        experiment_metadata = _select_experiment(self.experiment_path, frontend=launcher.frontend)
        launcher.run_experiment(experiment_metadata.func)

    def cli_cmd(self):
        """Run the specified experiment, optionally under a single-session lock."""
        if not self.single_session:
            self._run()
            return None

        from .launcher._session_lock import SessionAlreadyRunningError, single_session_lock

        try:
            with single_session_lock():
                self._run()
        except SessionAlreadyRunningError as exc:
            logger.error(str(exc))
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        return None


class _ServeCli(LauncherCliArgs):
    """CLI arguments for serving a CLABE experiment's TUI over the web."""

    experiment_path: CliPositionalArg[Path] = Field(
        description="Path to the Python file containing the CLABE experiment to run"
    )
    host: str = Field(
        default="127.0.0.1",
        description="Interface to bind. Keep localhost and use SSH port forwarding (ssh -L) for remote access.",
    )
    port: int = Field(default=8089, description="TCP port to serve the web UI on")
    open_browser: CliImplicitFlag[bool] = Field(
        default=False, description="Open the web UI in the local browser once the server is ready"
    )

    def _child_command(self) -> str:
        """Builds the command serving the experiment with the TUI frontend."""
        parts = [
            _quote(sys.executable),
            "-m",
            "clabe.cli",
            "run",
            _quote(str(self.experiment_path)),
            "--frontend",
            "tui",
            "--single-session",
        ]
        if self.repository_directory is not None:
            parts += ["--repository-directory", _quote(str(self.repository_directory))]
        forwarded = (
            ("--debug-mode", self.debug_mode),
            ("--verbose", self.verbose),
            ("--quiet", self.quiet),
            ("--allow-dirty", self.allow_dirty),
            ("--skip-hardware-validation", self.skip_hardware_validation),
        )
        parts += [flag for flag, enabled in forwarded if enabled]
        return " ".join(parts)

    def cli_cmd(self):
        """Serve the experiment's TUI over a local web port."""
        from .web import serve

        serve(self._child_command(), host=self.host, port=self.port, open_browser=self.open_browser)
        return None


class CliAppSettings(BaseSettings, cli_prog_name="clabe", cli_kebab_case=True):
    """CLI application settings."""

    xml_rpc_server: CliSubCommand[_XmlRpcServerStartCli]
    cache: CliSubCommand[_CacheManagerCli]
    run: CliSubCommand[_RunCli]
    serve: CliSubCommand[_ServeCli]

    def cli_cmd(self):
        """Run the selected subcommand."""
        CliApp.run_subcommand(self)


def main():
    """Entry point for the CLABE CLI application."""
    CliApp().run(CliAppSettings)


if __name__ == "__main__":
    main()
