"""The reusable steps of clabe.steps, alone and wired into a workflow."""

from collections import namedtuple
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from clabe import steps
from clabe.workflow import LocalClient, Run, RunStatus, Workflow, check_workflow, step_defaults
from tests.conftest import MockClient

_Usage = namedtuple("_Usage", "total used free")


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
