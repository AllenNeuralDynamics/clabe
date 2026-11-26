"""``clabe shortcut``: a desktop icon that runs one experiment of a repository in a terminal."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

#: Writes the shortcut; its values come from the environment, so nothing needs quoting.
_SCRIPT = r"""
$path = Join-Path ([Environment]::GetFolderPath('Desktop')) ($env:CLABE_SHORTCUT_NAME + '.lnk')
if ($env:CLABE_SHORTCUT_DIR) { $path = Join-Path $env:CLABE_SHORTCUT_DIR ($env:CLABE_SHORTCUT_NAME + '.lnk') }
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
$shortcut.TargetPath = $env:CLABE_SHORTCUT_TARGET
$shortcut.Arguments = $env:CLABE_SHORTCUT_ARGUMENTS
$shortcut.WorkingDirectory = $env:CLABE_SHORTCUT_WORKDIR
$shortcut.Description = $env:CLABE_SHORTCUT_DESCRIPTION
$shortcut.Save()
Write-Output $path
"""


def shortcut_command(repository: Path, experiment: str | None, file: str = "main.py") -> tuple[str, str]:
    """The program and arguments the shortcut runs: ``uv run clabe run`` in the repository, in a terminal."""
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is not on PATH: install it first (https://docs.astral.sh/uv/).")
    arguments = f'/k ""{uv}" run --directory "{repository}" clabe run {file}'
    if experiment:
        arguments += f' --experiment-name "{experiment}"'
    return os.environ.get("ComSpec", "cmd.exe"), arguments + '"'


def create_shortcut(
    repository: Path, *, experiment: str | None = None, name: str | None = None, directory: Path | None = None
) -> Path:
    """Creates a desktop shortcut running ``experiment`` of ``repository`` (its ``main.py``) in a terminal.

    Args:
        repository: The experiment repository.
        experiment: The workflow to run, when ``main.py`` offers several.
        name: The shortcut's name; by default the experiment's, else the repository's.
        directory: Where to put it; by default the user's desktop.

    Returns:
        The shortcut's path.
    """
    if sys.platform != "win32":
        raise RuntimeError("Desktop shortcuts are only made on Windows.")
    repository = repository.resolve()
    if not (repository / "main.py").is_file():
        raise FileNotFoundError(f"{repository} has no main.py.")
    target, arguments = shortcut_command(repository, experiment)
    title = name or experiment or repository.name
    env = os.environ | {
        "CLABE_SHORTCUT_NAME": title,
        "CLABE_SHORTCUT_DIR": str(directory) if directory is not None else "",
        "CLABE_SHORTCUT_TARGET": target,
        "CLABE_SHORTCUT_ARGUMENTS": arguments,
        "CLABE_SHORTCUT_WORKDIR": str(repository),
        "CLABE_SHORTCUT_DESCRIPTION": f"Run {title} with clabe",
    }
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(f"Could not create the shortcut: {done.stderr.strip()}")
    return Path(done.stdout.strip().splitlines()[-1])
