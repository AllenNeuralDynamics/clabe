import sys
from pathlib import Path

from clabe import cli
from clabe.apps import Command, LocalExecutor
from clabe.git_manager import GitRepositoryMetadata


class TestQuote:
    def test_posix_quotes_spaces(self, monkeypatch):
        monkeypatch.setattr(cli.sys, "platform", "linux")
        assert cli._quote("no_spaces") == "no_spaces"
        assert cli._quote("has space") == "'has space'"

    def test_windows_quotes_spaces(self, monkeypatch):
        monkeypatch.setattr(cli.sys, "platform", "win32")
        assert cli._quote("no_spaces") == "no_spaces"
        assert cli._quote("has space") == '"has space"'


def _serve_cli(**overrides):
    """Builds a _ServeCli with all fields _child_command reads, plus overrides."""
    fields = {
        "experiment_path": Path("exp.py"),
        "host": "127.0.0.1",
        "port": 8089,
        "repository_directory": None,
        "debug_mode": False,
        "verbose": False,
        "quiet": False,
        "allow_dirty": False,
        "skip_hardware_validation": False,
        "clabe_yml": None,
    }
    fields.update(overrides)
    return cli._ServeCli.model_construct(**fields)


class TestServeChildCommand:
    def test_runs_experiment_with_tui_frontend(self, monkeypatch):
        monkeypatch.setattr(cli, "_quote", lambda arg: arg)
        command = _serve_cli()._child_command()
        assert "-m clabe.cli run exp.py" in command
        assert "--frontend tui" in command
        assert "--single-session" in command

    def test_forwards_only_enabled_flags(self, monkeypatch):
        monkeypatch.setattr(cli, "_quote", lambda arg: arg)
        command = _serve_cli(allow_dirty=True, skip_hardware_validation=True)._child_command()
        assert "--allow-dirty" in command
        assert "--skip-hardware-validation" in command
        assert "--debug-mode" not in command
        assert "--verbose" not in command

    def test_includes_repository_directory_when_set(self, monkeypatch):
        monkeypatch.setattr(cli, "_quote", lambda arg: arg)
        repo = Path("/repo")
        command = _serve_cli(repository_directory=repo)._child_command()
        assert "--repository-directory" in command
        assert str(repo) in command

    def test_forwards_clabe_yml_when_set(self, monkeypatch):
        monkeypatch.setattr(cli, "_quote", lambda arg: arg)
        assert "--clabe-yml" not in _serve_cli()._child_command()
        clabe_yml = Path("/configs/clabe.yml")
        command = _serve_cli(clabe_yml=clabe_yml)._child_command()
        assert f"--clabe-yml {clabe_yml}" in command


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
