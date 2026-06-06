import asyncio
import logging
import sys
from pathlib import Path

from _mocks import (
    LIB_CONFIG,
    DemoAindDataSchemaSessionDataMapper,
    MockTask,
    RigModel,
    Session,
    create_fake_rig,
    create_fake_subjects,
)

from clabe import resource_monitor
from clabe.apps import CurriculumApp, CurriculumSettings, PythonScriptApp
from clabe.cache_manager import CacheManager
from clabe.launcher import Launcher, experiment
from clabe.pickers import DefaultBehaviorPicker, DefaultBehaviorPickerSettings
from clabe.runnable import runnable
from clabe.web import serve

logger = logging.getLogger(__name__)


@experiment()
async def demo_experiment(launcher: Launcher) -> None:
    """Demo experiment showcasing CLABE functionality."""
    # Seed the mock rig/subjects/cache here so the demo also works when launched
    # via ``clabe run``/``clabe serve`` (which call this function but not main()).
    create_fake_subjects()
    create_fake_rig()
    _seed_cache()

    picker = DefaultBehaviorPicker(
        launcher=launcher,
        settings=DefaultBehaviorPickerSettings(config_library_dir=LIB_CONFIG),
        experimenter_validator=lambda _: True,
    )

    session = picker.pick_session(Session)
    rig = picker.pick_rig(RigModel)
    launcher.register_session(session, rig.data_directory)
    trainer_state, task = picker.pick_trainer_state(MockTask)
    _temp_trainer_state_path = launcher.save_temp_model(trainer_state)

    resource_monitor.ResourceMonitor(
        constrains=[
            resource_monitor.available_storage_constraint_factory_from_rig(rig, 2e11),
        ]
    ).run()

    def fmt(value: str) -> list[str]:
        return ["python", "-c", f"import time; print('Hello {value}'); time.sleep(2); print('DONE')"]

    app_1 = PythonScriptApp(script=fmt("Behavior"))
    app_2 = PythonScriptApp(script=fmt("Physiology"))

    app_1_result, app_2_result = await asyncio.gather(
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
    return


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
    # Serve this experiment's TUI over a local web port and pop open the browser.
    # Each browser connection runs `clabe run <this file> --frontend tui` as its
    # own subprocess, so the seeding/experiment above runs there.
    this_file = f'"{Path(__file__).resolve()}"'
    serve(
        f"{sys.executable} -m clabe.cli run {this_file} --allow-dirty --skip-hardware-validation --frontend tui",
        open_browser=True,
    )
    return None


if __name__ == "__main__":
    main()
