import sys
from pathlib import Path

import pytest
from pydantic_settings import CliApp

from clabe import cli
from clabe.apps import Command, LocalExecutor
from clabe.repository import GitRepositoryMetadata
from tests import REPO_ROOT


def test_repository_state_cli_executes_with_local_executor():
    """Test repository-state JSON can be captured through the command executor pattern."""
    repository_root = Path(__file__).parents[1]
    command = Command(
        cmd=[sys.executable, "-m", "clabe.cli", "repository-state", "."],
        output_parser=lambda result: GitRepositoryMetadata.model_validate_json(result.stdout),
    )

    metadata = command.execute(LocalExecutor(cwd=repository_root))

    assert metadata.name == "clabe"
    assert metadata.path == "."


def test_check_cli_fails_on_errors(tmp_path, capsys):
    bad = tmp_path / "_clabe_cli_check_bad.py"
    bad.write_text(
        "from clabe.workflow import Workflow\nwf = Workflow('w')\n"
        "@wf.step\ndef a() -> int: ...\n@wf.step\ndef b(a: str) -> None: ...\n"
    )
    with pytest.raises(SystemExit) as exit_info:
        CliApp.run(cli.CliAppSettings, cli_args=["check", str(bad)])
    assert exit_info.value.code == 1
    assert "expects str" in capsys.readouterr().out


def test_check_cli_strict_fails_on_warnings(tmp_path, capsys):
    loose = tmp_path / "_clabe_cli_check_loose.py"
    loose.write_text(
        "from clabe.workflow import Workflow\nwf = Workflow('w')\n@wf.step\ndef a(): ...\n@wf.step\ndef b(a): ...\n"
    )
    CliApp.run(cli.CliAppSettings, cli_args=["check", str(loose)])
    assert "0 issue(s)" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exit_info:
        CliApp.run(cli.CliAppSettings, cli_args=["check", str(loose), "--strict"])
    assert exit_info.value.code == 1
    assert "warning" in capsys.readouterr().out


def test_check_cli_passes_on_the_examples(capsys):
    CliApp.run(cli.CliAppSettings, cli_args=["check", str(REPO_ROOT / "examples")])
    assert "0 issue(s)" in capsys.readouterr().out
