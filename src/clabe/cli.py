import logging
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, CliApp, CliPositionalArg, CliSubCommand

from clabe.launcher._experiments import _select_experiment

from .cache_manager import _CacheManagerCli
from .launcher import Launcher, LauncherCliArgs
from .ui import DefaultUIHelper
from .xml_rpc._server import _XmlRpcServerStartCli

logger = logging.getLogger(__name__)


class _RunCli(LauncherCliArgs):
    """CLI arguments for running a CLABE experiment from a Python file."""

    experiment_path: CliPositionalArg[Path] = Field(
        description="Path to the Python file containing the CLABE experiment to run"
    )

    def cli_cmd(self):
        """Run the specified experiment."""

        launcher = Launcher(settings=self, ui_helper=DefaultUIHelper())
        experiment_metadata = _select_experiment(self.experiment_path, ui_helper=launcher.ui_helper)
        launcher.run_experiment(experiment_metadata.func)
        return None


class CliAppSettings(BaseSettings, cli_prog_name="clabe", cli_kebab_case=True):
    """CLI application settings."""

    xml_rpc_server: CliSubCommand[_XmlRpcServerStartCli]
    cache: CliSubCommand[_CacheManagerCli]
    run: CliSubCommand[_RunCli]

    def cli_cmd(self):
        """Run the selected subcommand."""
        CliApp.run_subcommand(self)
