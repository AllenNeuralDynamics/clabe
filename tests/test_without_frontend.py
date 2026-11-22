"""The core works without the ``frontend`` extra (Textual, FastAPI, uvicorn): each check runs in a fresh Python where
those packages cannot be imported."""

import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Makes the frontend extra's packages unimportable (as if not installed), before anything else is imported.
BLOCK = textwrap.dedent(
    """
    import importlib.abc, sys

    class _NotInstalled(importlib.abc.MetaPathFinder):
        BLOCKED = ("textual", "textual_fspicker", "fastapi", "uvicorn", "starlette")

        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in self.BLOCKED:
                raise ModuleNotFoundError(f"No module named {name!r}", name=name)
            return None

    sys.meta_path.insert(0, _NotInstalled())
    """
)


def _python(code: str, *args: str) -> subprocess.CompletedProcess:
    """Runs ``code`` (after the block) in a fresh Python from the repository root."""
    return subprocess.run(
        [sys.executable, "-c", BLOCK + textwrap.dedent(code), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_the_core_imports_and_runs_a_workflow_without_the_frontend_extra():
    result = _python(
        """
        import sys
        import clabe, clabe.cli, clabe.launcher, clabe.remote, clabe.steps, clabe.checks, clabe.reports
        from clabe import ui
        from clabe._extras import has_tui, has_web
        from clabe.daemon import DaemonSettings
        from clabe.server import RunClient, SessionInfo
        from clabe.workflow import LocalClient, Run, RunStatus, Workflow
        from tests.conftest import MockClient

        assert not has_tui() and not has_web()
        assert type(ui.make_client("auto")).__name__ == "ConsoleClient"
        try:
            ui.make_client("tui")
        except ImportError as exc:
            assert "aind-clabe[frontend]" in str(exc)
        else:
            raise AssertionError("the terminal UI should need the frontend extra")

        wf = Workflow("plain")

        @wf.step
        def notes() -> str:
            return ui.text("Notes")

        run = Run(wf)
        LocalClient(run, MockClient({"text": "fine"})).drive()
        assert run.status is RunStatus.COMPLETED and run.output("notes") == "fine"
        loaded = [m for m in ("textual", "fastapi", "uvicorn", "starlette") if m in sys.modules]
        assert not loaded, loaded
        print("ok")
        """
    )
    assert result.returncode == 0 and result.stdout.strip().endswith("ok"), result.stderr[-3000:]


def test_the_daemon_and_the_hub_say_they_need_the_frontend_extra():
    for command in (["daemon", "run"], ["hub", "run"]):
        result = _python(
            """
            import sys
            from clabe.cli import main

            sys.argv = ["clabe", *sys.argv[1:]]
            main()
            """,
            *command,
        )
        assert result.returncode != 0, command
        assert "aind-clabe[frontend]" in result.stderr, (command, result.stderr[-2000:])
