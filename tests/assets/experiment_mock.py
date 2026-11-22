from clabe.launcher import Launcher
from clabe.workflow import Workflow

simple_experiment = Workflow("simple_experiment")


@simple_experiment.step(name="experiment")
async def run(launcher: Launcher) -> None:
    launcher.logger.info("running simple_experiment")
