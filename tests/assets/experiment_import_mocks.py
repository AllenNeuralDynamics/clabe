from mock_third_party_pkg import mock_constant

from clabe.launcher import Launcher
from clabe.workflow import Workflow

first_experiment = Workflow("first_experiment")
second_experiment = Workflow("second_experiment")


@first_experiment.step(name="experiment")
def run_first(launcher: Launcher) -> None:  # pragma: no cover - behavior not important
    launcher.logger.info("running first_experiment with mock_constant=%s", mock_constant)


@second_experiment.step(name="experiment")
def run_second(launcher: Launcher) -> None:  # pragma: no cover
    launcher.logger.info("running second_experiment")
