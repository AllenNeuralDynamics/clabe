"""examples/behavior_launcher.py runs to the end with scripted answers (the curriculum, optional, is declined)."""

import os
from pathlib import Path
from unittest.mock import MagicMock

from clabe.workflow import LocalClient, Run, RunStatus, select_workflow
from tests.conftest import MockClient

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_the_behavior_launcher_example_runs_to_the_end(monkeypatch, tmp_path):
    monkeypatch.chdir(EXAMPLES.parent)  # it runs from the repository: its mock files go under ./local (ignored)
    monkeypatch.setenv("COMPUTERNAME", os.environ.get("COMPUTERNAME", "this-pc"))
    workflow = select_workflow(EXAMPLES / "behavior_launcher.py", name="behavior-launcher")

    def confirm(request):
        return "curriculum" not in str(getattr(request, "message", "") or getattr(request, "label", "")).lower()

    client = MockClient({"autocomplete": ["alice", "00000"], "confirm": confirm})
    launcher = MagicMock()
    launcher.session_directory = tmp_path / "session"
    launcher.session_directory.mkdir()
    launcher.repository.head.commit.hexsha = "0" * 40
    launcher.settings.debug_mode = launcher.settings.allow_dirty = launcher.settings.skip_hardware_validation = False
    run = Run(workflow, launcher=launcher)
    LocalClient(run, client).drive()
    assert run.status is RunStatus.COMPLETED, run.reason
    assert run.output("session").subject == "00000"
    assert run.output("rig").rig_name == "TestRig"
    assert set(run.output("task")) == {"behavior", "physiology"}
    assert run.output("curriculum") is None
