import logging
import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, CliApp, CliImplicitFlag, CliPositionalArg, CliSubCommand

from .cache_manager import _CacheManagerCli
from .daemon._cli import DaemonCli
from .git_manager import GitRepository
from .launcher import Launcher, LauncherCliArgs
from .workflow import select_workflow

logger = logging.getLogger(__name__)


class _RunCli(LauncherCliArgs):
    """CLI arguments for running a CLABE experiment from a Python file."""

    experiment_path: CliPositionalArg[Path] = Field(
        description="Path to the Python file containing the CLABE experiment to run"
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
        description="Name of the @experiment to run when the file defines more than one. "
        "If omitted, prompts interactively (or runs the only one found).",
    )

    def _run(self):
        """Builds the launcher, selects the workflow and runs it."""
        launcher = Launcher(settings=self)
        workflow = select_workflow(self.experiment_path, name=self.experiment_name)
        launcher.run_experiment(workflow)

    def cli_cmd(self):
        """Run the specified experiment, optionally under a single-session lock."""
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


class _RepositoryStateCli(BaseSettings):
    """Print the current Git repository state as JSON."""

    path: CliPositionalArg[Path] = Field(description="Path to the Git repository to inspect")

    def cli_cmd(self):
        """Print repository metadata to standard output as JSON."""
        metadata = GitRepository(path=self.path).get_metadata()
        print(metadata.model_dump_json(indent=2))


class _CheckCli(BaseSettings):
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


class _ListCli(BaseSettings):
    """List the workflows of an experiment file or directory (as JSON with --json)."""

    path: CliPositionalArg[Path] = Field(description="Experiment file, or a directory of experiment files")
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


class CliAppSettings(BaseSettings, cli_prog_name="clabe", cli_kebab_case=True):
    """CLI application settings."""

    cache: CliSubCommand[_CacheManagerCli]
    run: CliSubCommand[_RunCli]
    repository_state: CliSubCommand[_RepositoryStateCli]
    check: CliSubCommand[_CheckCli]
    list: CliSubCommand[_ListCli]
    daemon: CliSubCommand[DaemonCli]

    def cli_cmd(self):
        """Run the selected subcommand."""
        CliApp.run_subcommand(self)


def main():
    """Entry point for the CLABE CLI application."""
    CliApp().run(CliAppSettings)


if __name__ == "__main__":
    main()
