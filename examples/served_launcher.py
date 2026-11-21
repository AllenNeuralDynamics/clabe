import asyncio
import logging
from pathlib import Path

from _mocks import (
    LIB_CONFIG,
    RIG,
    SUGGESTION,
    DemoAindDataSchemaSessionDataMapper,
    MockTask,
    create_fake_rig,
    create_fake_subjects,
)
from pydantic_settings import CliApp

from clabe import resource_monitor
from clabe.apps import CurriculumApp, CurriculumSettings, PythonScriptApp
from clabe.cache_manager import CacheManager
from clabe.launcher import Launcher, LauncherCliArgs, experiment
from clabe.runnable import runnable
from clabe.session import SessionBuilder
from clabe.stores import LocalFileStore

logger = logging.getLogger(__name__)


@experiment()
async def demo_experiment(launcher: Launcher) -> None:
    """Demo experiment showcasing CLABE functionality."""
    # Seed the mock rig/subjects/cache here so the demo also works when launched
    # via ``clabe run`` (which call this function but not main()).
    create_fake_subjects()
    create_fake_rig()
    _seed_cache()

    session = SessionBuilder(launcher, experimenter_validator=lambda name: name).build()
    store = LocalFileStore(LIB_CONFIG).scoped(subject=session.subject)

    rig = store.resolve(RIG)
    launcher.register_session(session, rig.data_directory)
    trainer_state = store.resolve(SUGGESTION)
    task = MockTask.model_validate_json(trainer_state.stage.task.model_dump_json())
    _temp_trainer_state_path = launcher.save_temp_model(trainer_state)

    resource_monitor.ResourceMonitor(
        constrains=[
            resource_monitor.available_storage_constraint_factory_from_rig(rig, 1e9),
        ]
    ).run()

    def fmt(value: str) -> list[str]:
        return ["python", "-c", f"import time; print('Hello {value}'); time.sleep(2); print('DONE')"]

    app_1 = PythonScriptApp(script=fmt("Behavior"))
    app_2 = PythonScriptApp(script=fmt("Physiology"))

    _app_1_result, _app_2_result = await asyncio.gather(
        runnable(app_1.run_async, name="Running Behavior App")(),
        runnable(app_2.run_async, name="Running Physiology App")(),
    )

    suggestion = CurriculumApp(
        settings=CurriculumSettings(
            curriculum="template",
            data_directory=Path("demo"),
            project_directory=Path("./tests/assets/Aind.Behavior.VrForaging"),
            input_trainer_state=_temp_trainer_state_path,
        )
    ).run()

    DemoAindDataSchemaSessionDataMapper(
        rig,
        session,
        task,
        repository=launcher.repository,
        script_path=Path("./mock/script.py"),
        output_parameters={"suggestion": suggestion.model_dump()},
    ).map()


def _seed_cache() -> None:
    """Pre-populate the selection caches so autocompletion has options to filter."""
    cache = CacheManager.get_instance()
    cache.register_cache("subjects", max_history=20)
    cache.register_cache("experimenters", max_history=20)
    for subject in ["00000", "123456", "mouse_42", "mouse_77", "test_subject", "demo_animal", "alpha_01", "beta_02"]:
        cache.add_to_cache("subjects", subject)
    for experimenter in ["bruno.cruz", "jane.doe", "john.smith", "alex.kim"]:
        cache.add_to_cache("experimenters", experimenter)


def main():
    # Run the demo while serving it over HTTP: answer its prompts from the console *or* from the browser
    # (any machine that can reach the port). `clabe run examples/served_launcher.py` does the same.
    settings = CliApp.run(
        LauncherCliArgs,
        cli_args=["--allow-dirty", "--skip-hardware-validation", "--serve", "--open-browser"],
    )
    Launcher(settings=settings).run_experiment(demo_experiment)


if __name__ == "__main__":
    main()
