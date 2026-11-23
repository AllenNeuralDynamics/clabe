"""The reusable steps of clabe.steps, alone and wired into a workflow."""

import asyncio
from collections import namedtuple
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock

import pytest
from aind_behavior_curriculum import Stage, TrainerState
from aind_behavior_services.task import Task
from pydantic import BaseModel

from clabe import steps
from clabe.remote import RemoteRepository
from clabe.workflow import LocalClient, Run, RunStatus, Workflow, check_workflow, step_defaults
from tests.conftest import MockClient

_Usage = namedtuple("_Usage", "total used free")


class _Task(Task):
    """A task for trainer states."""

    name: Literal["Task"] = "Task"
    version: Literal["0.0.0"] = "0.0.0"


class _Rig(BaseModel):
    """A rig with a data directory."""

    name: str
    data_directory: Path


class TestStepDefaults:
    def test_a_factory_s_defaults_apply_and_declared_options_win(self):
        wf = Workflow("demo")
        wf.step(lambda: _Rig(name="r", data_directory=Path(".")), name="rig")
        wf.step(lambda: "s", name="session")
        wf.step(steps.register())
        assert wf.get("register").barrier and wf.get("register").title == "Register session"
        other = Workflow("other")
        other.step(lambda: 1, name="rig")
        other.step(lambda: 2, name="session")
        other.step(steps.register(), barrier=False, title="Make the folder")
        assert not other.get("register").barrier and other.get("register").title == "Make the folder"

    def test_any_function_can_carry_defaults(self):
        @step_defaults(on_error="warn")
        def flaky():
            raise RuntimeError("x")

        wf = Workflow("demo")
        wf.step(flaky)
        assert wf.get("flaky").on_error == "warn"


class TestReview:
    def _workflow(self):
        calls = []
        wf = Workflow("demo")

        @wf.step
        def subject() -> str:
            calls.append("subject")
            return "mouse-7"

        @wf.step
        def rig() -> _Rig:
            calls.append("rig")
            return _Rig(name="rig-3", data_directory=Path("D:/data"))

        wf.step(steps.review("subject", "rig"))
        return wf, calls

    def test_it_takes_exactly_the_reviewed_outputs_and_shows_them(self):
        wf, _ = self._workflow()
        assert wf.get("review").params == ("subject", "rig")
        client = MockClient()
        run = Run(wf)
        LocalClient(run, client).drive()
        table = client.asked[-1]
        assert table.title == "Confirm session"
        assert ["subject", "mouse-7"] in table.rows and ["rig.name", "rig-3"] in table.rows
        assert run.status is RunStatus.COMPLETED

    def test_going_back_reruns_the_first_reviewed_step(self):
        wf, calls = self._workflow()
        run = Run(wf)
        LocalClient(run, MockClient({"table": [False, True]})).drive()
        # going back to subject re-runs it; rig does not depend on it and keeps its output
        assert calls == ["subject", "rig", "subject"]
        assert run.status is RunStatus.COMPLETED

    def test_it_needs_a_step_to_show(self):
        with pytest.raises(ValueError):
            steps.review()


class TestFreeSpace:
    def test_short_of_space_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clabe.checks.shutil.disk_usage", lambda path: _Usage(100, 90, 10e9))
        check = steps.free_space(20e9)
        with pytest.raises(RuntimeError, match=r"10\.0 GB free"):
            check(rig=_Rig(name="r", data_directory=tmp_path / "not-yet" / "created"))
        steps.free_space(5e9)(rig=_Rig(name="r", data_directory=tmp_path))

    def test_a_custom_directory(self, tmp_path, monkeypatch):
        seen = []
        monkeypatch.setattr("clabe.checks.shutil.disk_usage", lambda path: seen.append(path) or _Usage(1, 0, 1e12))
        steps.free_space(1, directory=lambda rig: tmp_path)(rig=object())
        assert seen == [tmp_path]


class TestRegisterAndWaterlog:
    def test_register_creates_the_session_directory(self):
        launcher = MagicMock()
        launcher.session_directory = Path("D:/data/mouse-7_2026")
        rig = _Rig(name="r", data_directory=Path("D:/data"))
        assert steps.register()(launcher=launcher, session="s", rig=rig) == launcher.session_directory
        launcher.register_session.assert_called_once_with("s", Path("D:/data"))

    def test_waterlog_only_warns(self):
        wf = Workflow("demo")
        wf.step(lambda: "s", name="session")
        wf.step(steps.waterlog(lambda launcher: 0.5))
        assert wf.get("waterlog").on_error == "warn"


def test_a_workflow_of_shared_steps_passes_the_static_check():
    wf = Workflow("demo")
    wf.step(steps.session(experimenter_validator=None))

    @wf.step
    def rig() -> _Rig:
        return _Rig(name="r", data_directory=Path("D:/data"))

    wf.step(steps.review("session", "rig"))
    wf.step(steps.free_space(1e9))
    wf.step(steps.register())
    assert [i for i in check_workflow(wf, strict=True) if i.severity == "error"] == []


def test_mappers_report_the_environment_the_run_manifest_recorded():
    import datetime
    import types

    from clabe.data_mapper import snapshot_python_environment
    from clabe.launcher._manifest import EnvironmentInfo, RunManifest
    from clabe.workflow import Run

    manifest = RunManifest(run_id="r", started_at=datetime.datetime.now(datetime.UTC))
    manifest.environment = EnvironmentInfo(
        python_version="3.12",
        python_implementation="CPython",
        python_executable="python",
        uv_version=None,
        uv_lock_sha256=None,
        distributions={"aind-clabe": "9.9.9"},
    )
    wf = Workflow("demo")
    wf.step(snapshot_python_environment, name="environment")
    run = Run(wf, launcher=types.SimpleNamespace(manifest=manifest))
    run.start()
    assert run.wait(10) and run.output("environment") == {"aind-clabe": "9.9.9"}
    assert "pydantic" in snapshot_python_environment()  # outside a run: the live environment


class TestRemote:
    def _wf(self, **options):
        wf = Workflow("demo")
        wf.step(lambda: "", name="mode")

        @wf.step(when=lambda mode: mode == "ephys")
        def ephys_rig() -> _Rig:
            return _Rig(name="rig-b", data_directory=Path("D:/data"))

        wf.step(steps.remote(lambda ephys_rig: ephys_rig.name, **options), when=lambda mode: mode == "ephys")

        @wf.step(when=lambda mode: mode == "ephys")
        def acquire(remote: RemoteRepository | None) -> None: ...

        return wf

    def test_it_connects_starts_and_verifies_the_other_computer(self, monkeypatch):
        from clabe import utils

        made = []

        def connect(cls, host, **kwargs):
            other = MagicMock(spec=RemoteRepository)
            made.append((host, kwargs, other))
            return other

        monkeypatch.setattr(RemoteRepository, "from_computer_name", classmethod(connect))
        monkeypatch.setattr(utils, "is_this_computer", lambda name: False)
        step = steps.remote(lambda rig: rig.name, allow_dirty=True, repository="Aind.Experiment.VrForaging")
        other = step(rig=_Rig(name="rig-b", data_directory=Path(".")))
        host, kwargs, mock = made[0]
        assert other is mock and host == "rig-b" and kwargs["repository"] == "Aind.Experiment.VrForaging"
        mock.ensure_running.assert_called_once_with()
        mock.verify.assert_called_once_with(allow_dirty=True)

        monkeypatch.setattr(utils, "is_this_computer", lambda name: True)
        assert steps.remote(lambda rig: rig.name)(rig=_Rig(name="here", data_directory=Path("."))) is None

    def test_it_is_wired_by_the_function_s_parameters_and_checks_cleanly(self):
        wf = self._wf()
        step = wf.get("remote")
        assert step.deps == ("ephys_rig", "mode") and step.title == "Other computer"
        assert check_workflow(wf, strict=True) == []


class TestCurriculum:
    @pytest.fixture
    def fake_app(self, monkeypatch):
        """A CurriculumApp that suggests the next stage without running anything."""
        from aind_behavior_curriculum import Metrics

        from clabe import apps

        ran = []

        class FakeCurriculumApp:
            def __init__(self, settings):
                self.settings = settings

            async def run_async(self):
                ran.append(self.settings)

            def process_suggestion(self):
                state = TrainerState.model_validate_json(Path(self.settings.input_trainer_state).read_text())
                next_state = state.model_copy(update={"stage": state.stage.model_copy(update={"name": "Next"})})
                return apps.CurriculumSuggestion(
                    trainer_state=next_state, metrics=Metrics(), version="1", dsl_version="1"
                )

        monkeypatch.setattr(apps, "CurriculumApp", FakeCurriculumApp)
        return ran

    @staticmethod
    def _state(on_curriculum: bool = True) -> TrainerState:
        return TrainerState.model_construct(
            curriculum=None, is_on_curriculum=on_curriculum, stage=Stage(name="First", task=_Task(task_parameters={}))
        )

    def _run(self, step, tmp_path, state, store):
        launcher = MagicMock(session_directory=tmp_path)
        return asyncio.run(step(launcher=launcher, session="s", store=store, trainer_state=state))

    def test_it_runs_saves_and_uploads_the_suggestion(self, fake_app, tmp_path):
        from clabe.stores import Kind, MemoryStore

        store, hooked = MemoryStore(), []
        outcome = self._run(
            steps.curriculum(before=lambda session: hooked.append(session)), tmp_path, self._state(), store
        )
        assert hooked == ["s"] and outcome.uploaded
        assert (tmp_path / "behavior" / "trainer_state.json").is_file()
        assert (
            outcome.suggestion_path == tmp_path / "Behavior" / "Logs" / "suggestion.json"
            and outcome.suggestion_path.is_file()
        )
        assert fake_app[0].data_directory == tmp_path
        assert store.list(Kind.from_trainer_state())[0].stage.name == "Next"

    def test_upload_can_be_declined_and_off_curriculum_does_nothing(self, fake_app, tmp_path):
        from clabe.stores import Kind, MemoryStore

        store = MemoryStore()
        outcome = self._run(steps.curriculum(upload=lambda: False), tmp_path, self._state(), store)
        assert not outcome.uploaded and store.list(Kind.from_trainer_state()) == []
        assert self._run(steps.curriculum(), tmp_path / "other", self._state(on_curriculum=False), store) is None
        assert fake_app and len(fake_app) == 1

    def test_an_existing_starting_trainer_state_is_kept(self, fake_app, tmp_path):
        from clabe.stores import MemoryStore

        saved = tmp_path / "behavior" / "trainer_state.json"
        saved.parent.mkdir(parents=True)
        original = self._state().model_copy(update={"stage": Stage(name="Original", task=_Task(task_parameters={}))})
        saved.write_text(original.model_dump_json())
        outcome = self._run(steps.curriculum(upload=False), tmp_path, self._state(), MemoryStore())
        assert outcome.input_trainer_state == saved and "Original" in saved.read_text()

    def test_it_checks_cleanly_in_a_workflow(self):
        from aind_behavior_services.session import Session

        from clabe.apps import CurriculumOutcome
        from clabe.stores import MemoryStore, Store

        wf = Workflow("demo")
        @wf.step
        def session() -> Session:
            return Session(subject="1", experiment="demo")

        @wf.step
        def store() -> Store:
            return MemoryStore()

        @wf.step
        def trainer_state() -> TrainerState:
            return self._state()

        wf.step(steps.curriculum())

        @wf.step
        def mappers(curriculum: CurriculumOutcome | None) -> None: ...

        assert check_workflow(wf, strict=True) == []
