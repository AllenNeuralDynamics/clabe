from clabe.launcher import Launcher, experiment


@experiment(name="simple_experiment")
async def simple_experiment(launcher: Launcher) -> None:
    launcher.logger.info("running simple_experiment")
