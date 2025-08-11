import logging
import os
import subprocess
import typing as t
from pathlib import Path

import aind_behavior_curriculum.trainer
import pydantic

from ..launcher import Launcher
from ..launcher._callable_manager import _Promise
from ..services import ServiceSettings
from ._base import App
from ._python_script import PythonScriptApp

if t.TYPE_CHECKING:
    from ..launcher import Launcher
else:
    Launcher = t.Any

P = t.ParamSpec("P")


logger = logging.getLogger(__name__)


class CurriculumSuggestion(pydantic.BaseModel):
    trainer_state: pydantic.SerializeAsAny[aind_behavior_curriculum.trainer.TrainerState]
    metrics: pydantic.SerializeAsAny[aind_behavior_curriculum.Metrics]
    version: str
    dsl_version: str


class CurriculumSettings(ServiceSettings):
    __yml_section__: t.ClassVar[t.Literal["curriculum"]] = "curriculum"

    module_path: os.PathLike
    input_trainer_state: t.Optional[os.PathLike] = None
    data_directory: t.Optional[os.PathLike] = None


class CurriculumApp(App):
    def __init__(self, settings: CurriculumSettings):
        self._settings = settings
        module_path = Path(settings.module_path).resolve()
        if module_path.exists():
            if module_path.is_dir():
                module_name = module_path.name
                script = f"-m {module_name} run"
            elif module_path.is_file():
                script = f"{module_path} run"
            else:
                raise ValueError("Invalid module path. It is not a directory or a file.")

            project_directory = _find_project_root(module_path)
            self._python_script_app = PythonScriptApp(
                script=script, project_directory=project_directory, extra_uv_arguments="-q"
            )
        else:
            raise ValueError("Module path does not exist.")

    def run(self) -> subprocess.CompletedProcess:
        if self._settings.input_trainer_state is None:
            raise ValueError("Input trainer state is not set.")
        if self._settings.data_directory is None:
            raise ValueError("Data directory is not set.")

        kwargs = {  # Must use kebab casing
            "data-directory": self._settings.data_directory,
            "input-trainer-state": self._settings.input_trainer_state,
        }
        self._python_script_app.add_app_settings(**kwargs)
        return self._python_script_app.run()

    def output_from_result(self, *, allow_stderr: bool | None = None) -> t.Self:
        self._python_script_app.output_from_result(allow_stderr=allow_stderr)
        return self

    def add_app_settings(self, **kwargs) -> t.Self:
        self._python_script_app.add_app_settings(**kwargs)
        return self

    @property
    def result(self) -> subprocess.CompletedProcess:
        return self._python_script_app.result

    def build_runner(  # type: ignore[override]
        self,
        input_trainer_state: _Promise[P, aind_behavior_curriculum.trainer.TrainerState],
        *,
        allow_std_error: bool = False,
    ) -> t.Callable[[Launcher], CurriculumSuggestion]:
        def _run(launcher: Launcher) -> CurriculumSuggestion:
            data_directory = launcher.session_directory
            input_path = launcher.save_temp_model(input_trainer_state.result)
            self._settings.input_trainer_state = Path(input_path)
            self._settings.data_directory = Path(data_directory)
            try:
                self.run()
                self.output_from_result(allow_stderr=allow_std_error)
            except subprocess.CalledProcessError as e:
                logger.critical(f"App {self.__class__.__name__} failed with error: {e}")
                raise

            return self.get_suggestion()

        return _run

    def get_suggestion(self) -> CurriculumSuggestion:
        return CurriculumSuggestion.model_validate_json(self.result.stdout)


def _find_project_root(path: os.PathLike, n_attempts: int = 10) -> Path:
    current_path = Path(path).resolve()
    if current_path.is_file():
        current_path = current_path.parent
    while current_path != current_path.parent and n_attempts > 0:
        if (current_path / "pyproject.toml").exists():
            return current_path
        current_path = current_path.parent
        n_attempts -= 1

    raise FileNotFoundError(f"No pyproject.toml found in any parent directory going up {n_attempts} directories.")
