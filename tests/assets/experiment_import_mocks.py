from clabe.launcher import Launcher, experiment
from mock_third_party_pkg import mock_constant

@experiment(name="first_experiment")
def first_experiment(launcher: Launcher) -> None:  # pragma: no cover - behavior not important
    launcher.logger.info(f"running first_experiment with mock_constant={mock_constant}")


@experiment(name="second_experiment")
def second_experiment(launcher: Launcher) -> None:  # pragma: no cover
    launcher.logger.info("running second_experiment")

