"""examples/demo/main.py (what scripts/demo.ps1 runs) goes through every step with scripted answers."""

from pathlib import Path
from unittest.mock import MagicMock

from clabe.workflow import LocalClient, Run, RunStatus, check_path, select_workflow
from tests.conftest import MockClient

DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo" / "main.py"


def test_the_demo_passes_the_static_check():
    assert check_path(DEMO, strict=True) == []


def test_the_demo_runs_to_the_end(monkeypatch, tmp_path):
    workflow = select_workflow(DEMO, name="clabe-demo")
    module = __import__(workflow.steps[0].func.__module__)
    monkeypatch.setattr(module, "OUTPUT", tmp_path)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    client = MockClient(
        {
            "autocomplete": ["alice", "mouse-7"],  # experimenter, then animal
            "text": "",  # notes
            "form": {"trials": 10, "reward_ul": 2.0, "stage": "training", "calibration": "measure"},
            "pick": ["retry"],  # the calibration fails once
        }
    )
    launcher = MagicMock()
    launcher.session_directory = tmp_path / "session"
    launcher.session_directory.mkdir()
    launcher.repository.head.commit.hexsha = "0" * 40
    launcher.settings.debug_mode = launcher.settings.allow_dirty = launcher.settings.skip_hardware_validation = False
    run = Run(workflow, launcher=launcher)
    LocalClient(run, client).drive()
    assert run.status is RunStatus.COMPLETED, run.reason
    assert run.output("last_calibration") is None and run.output("calibrate") == 3.1
    assert len(run.output("acquire")) == 10
    assert run.output("session").subject == "mouse-7"
    assert (tmp_path / "session" / "licks.svg").read_text(encoding="utf-8").startswith("<svg")
    kinds = [request.prompt_kind for request in client.asked]
    assert "media" in kinds and kinds.count("media") == 2  # the image and the report
