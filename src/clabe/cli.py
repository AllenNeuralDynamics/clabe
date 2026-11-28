import logging
import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, CliApp, CliImplicitFlag, CliPositionalArg, CliSubCommand

from ._cli_base import CliCommand
from .agents._cli import McpCli
from .cache import _CacheManagerCli
from .daemon._cli import DaemonCli
from .hub._cli import HubCli
from .launcher import Launcher, LauncherCliArgs
from .repository import GitRepository
from .workflow import select_workflow

logger = logging.getLogger(__name__)


class _RunCli(LauncherCliArgs):
    """CLI arguments for running a CLABE experiment from a Python file."""

    experiment_path: CliPositionalArg[Path] = Field(
        default=Path("main.py"), description="The Python file declaring the workflows"
    )
    single_session: CliImplicitFlag[bool] = Field(
        default=False,
        description="Refuse to start if another CLABE session is already running on this machine",
    )
    serve: CliImplicitFlag[bool] = Field(
        default=True, description="Serve the run over HTTP (web UI and API) while it executes; --no-serve to disable"
    )
    experiment_name: str | None = Field(
        default=None,
        description="Name of the workflow to run when the file defines more than one. "
        "If omitted, prompts interactively (or runs the only one found).",
    )
    here: CliImplicitFlag[bool] = Field(
        default=False,
        description="Run in this terminal. On a Windows rig a run otherwise goes to the daemon, which shows it here "
        "and keeps it if this window is lost",
    )

    def _run(self):
        """Builds the launcher, selects the workflow and runs it."""
        launcher = Launcher(settings=self)
        workflow = select_workflow(self.experiment_path, name=self.experiment_name)
        launcher.run_experiment(workflow)

    def cli_cmd(self):
        """Run the specified experiment (through this machine's daemon, on a rig), optionally under a
        single-session lock."""
        from ._process import contain_children
        from .daemon._handoff import run_through_daemon

        code = run_through_daemon(self)
        if code is not None:
            raise SystemExit(code)
        contain_children()  # what the run starts ends with it
        if not self.single_session:
            self._run()
            return

        from .launcher._session_lock import SessionAlreadyRunningError, single_session_lock

        try:
            with single_session_lock():
                self._run()
        except SessionAlreadyRunningError as exc:
            logger.error(str(exc))
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        return


class _ReplayCli(LauncherCliArgs):
    """Run an experiment answering its prompts with a recorded session's answers (its events.jsonl)."""

    events: CliPositionalArg[Path] = Field(description="The recorded session's events.jsonl")
    experiment_path: CliPositionalArg[Path] = Field(description="Path to the Python file containing the experiment")
    experiment_name: str | None = Field(default=None, description="Name of the experiment, when the file has several")

    def cli_cmd(self):
        """Replay the session; exit with status 1 if the run asks something the recording did not answer."""
        from .workflow import ReplayClient

        client = ReplayClient(self.events)
        launcher = Launcher(settings=self, client=client)
        launcher.run_experiment(select_workflow(self.experiment_path, name=self.experiment_name))
        print(f"Replayed {client.replayed} answer(s); {client.remaining} recorded answer(s) left unused.")


class _RepositoryStateCli(CliCommand):
    """Print the current Git repository state as JSON."""

    path: CliPositionalArg[Path] = Field(description="Path to the Git repository to inspect")

    def cli_cmd(self):
        """Print repository metadata to standard output as JSON."""
        metadata = GitRepository(path=self.path).get_metadata()
        print(metadata.model_dump_json(indent=2))


class _CheckCli(CliCommand):
    """Statically check the workflows of an experiment file or directory without running them."""

    path: CliPositionalArg[Path] = Field(description="Experiment file, or a directory of experiment files")
    strict: CliImplicitFlag[bool] = Field(
        default=False, description="Also fail on unannotated or undecidable wiring (reported as warnings)"
    )

    def cli_cmd(self):
        """Print the issues found and exit with status 1 if any fail the check."""
        from .workflow import check_path

        issues = check_path(self.path, strict=self.strict)
        for issue in issues:
            print(issue)
        print(f"{len(issues)} issue(s) in {self.path}")
        if any(issue.severity == "error" or self.strict for issue in issues):
            raise SystemExit(1)


class _ListCli(CliCommand):
    """List the workflows of an experiment file or directory (as JSON with --json)."""

    path: CliPositionalArg[Path] = Field(
        default=Path("main.py"), description="Experiment file, or a directory of experiment files"
    )
    json_output: CliImplicitFlag[bool] = Field(default=False, alias="json", description="Print JSON")

    def cli_cmd(self):
        """Print the workflows found; exit with status 1 if a file failed to import."""
        from .workflow import list_experiments

        listing = list_experiments(self.path)
        if self.json_output:
            print(listing.model_dump_json(indent=2))
        else:
            for experiment in listing.experiments:
                print(f"{experiment.name}  ({experiment.file}): {', '.join(s.name for s in experiment.steps)}")
            for error in listing.errors:
                print(f"error: {error.file}: {error.error}", file=sys.stderr)
        if listing.errors:
            raise SystemExit(1)


class _ShortcutCli(CliCommand, cli_kebab_case=True):
    """Put an icon on the desktop that runs an experiment of this repository (its main.py) in a terminal."""

    experiment_name: str | None = Field(default=None, description="The workflow to run, when main.py offers several")
    name: str | None = Field(default=None, description="The icon's name (default: the experiment's)")
    repository: Path = Field(default=Path("."), description="The experiment repository")

    def cli_cmd(self):
        """Create the shortcut and print where it is."""
        from ._shortcut import create_shortcut

        try:
            path = create_shortcut(self.repository, experiment=self.experiment_name, name=self.name)
        except (OSError, RuntimeError) as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"Created {path}")


class CliAppSettings(BaseSettings, cli_prog_name="clabe", cli_kebab_case=True):
    """CLI application settings."""

    cache: CliSubCommand[_CacheManagerCli]
    run: CliSubCommand[_RunCli]
    repository_state: CliSubCommand[_RepositoryStateCli]
    check: CliSubCommand[_CheckCli]
    list: CliSubCommand[_ListCli]
    replay: CliSubCommand[_ReplayCli]
    shortcut: CliSubCommand[_ShortcutCli]
    daemon: CliSubCommand[DaemonCli]
    hub: CliSubCommand[HubCli]
    mcp: CliSubCommand[McpCli]

    def cli_cmd(self):
        """Run the selected subcommand."""
        CliApp.run_subcommand(self)


def main():
    """Entry point for the CLABE CLI application."""
    CliApp().run(CliAppSettings)


if __name__ == "__main__":
    main()
