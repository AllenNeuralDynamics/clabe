from pathlib import Path

from clabe import cli


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
    fields = dict(
        experiment_path=Path("exp.py"),
        host="127.0.0.1",
        port=8089,
        repository_directory=None,
        debug_mode=False,
        verbose=False,
        quiet=False,
        allow_dirty=False,
        skip_hardware_validation=False,
    )
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
