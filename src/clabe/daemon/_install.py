"""Installing the daemon to start with the user's session: a Windows scheduled task ("at logon, restart on failure").

The daemon runs as the logged-in user (it starts experiments that show windows, e.g. Bonsai), not as a service.
Every function here works from an SSH session of that user: the task still runs in the user's desktop session.
"""

import getpass
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from xml.sax.saxutils import escape

#: The scheduled task's name.
TASK_NAME = "clabe-daemon"


def default_working_directory() -> Path:
    """Where the task runs and the daemon logs: ``%LOCALAPPDATA%\\clabe`` (``~/.clabe`` elsewhere)."""
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "clabe" if base else Path.home() / ".clabe"


def _in_uv_cache() -> bool:
    """Whether this clabe runs from a temporary environment in uv's cache (e.g. started with ``uvx``)."""
    parts = [part.lower() for part in Path(sys.prefix).resolve().parts]
    return any(part in ("archive-v0", "builds-v0") for part in parts)


def daemon_command(uvx: str | None = None) -> list[str]:
    """How the task starts the daemon.

    Args:
        uvx: A package spec (``aind-clabe==0.18.0``, ``git+https://...@v0.18.0``): the task runs
            ``uvx --from <spec> clabe daemon run``, so nothing needs installing and the version is pinned in the task.
            Without it, the installed ``clabe`` executable (``uv tool install``), else this interpreter.

    Raises:
        OSError: ``uvx`` is not on the PATH, or this clabe runs from uv's cache without ``uvx`` (the task would point
            into a cache uv may clean up).
    """
    if uvx:
        program = shutil.which("uvx")
        if program is None:
            raise OSError("uvx is not on the PATH: install uv first (https://docs.astral.sh/uv/).")
        return [program, "--from", uvx, "clabe", "daemon", "run"]
    if _in_uv_cache():
        raise OSError(
            "This clabe runs from uv's cache (uvx). Pass --uvx <package spec> (e.g. --uvx aind-clabe==X.Y.Z) so the "
            "task starts the daemon with uvx too, or install clabe with `uv tool install aind-clabe`."
        )
    executable = shutil.which("clabe")
    return [executable, "daemon", "run"] if executable else [sys.executable, "-m", "clabe.cli", "daemon", "run"]


def task_xml(command: list[str], *, user: str | None = None, working_directory: Path | None = None) -> str:
    """The Task Scheduler definition: start at logon of ``user``, restart every minute on failure, never time out."""
    user = user or getpass.getuser()
    program, *arguments = command
    args = " ".join(f'"{a}"' if " " in a else a for a in arguments)
    directory = f"<WorkingDirectory>{escape(str(working_directory))}</WorkingDirectory>" if working_directory else ""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>clabe daemon: runs experiments on this machine.</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType>
    <RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(program)}</Command><Arguments>{escape(args)}</Arguments>{directory}</Exec></Actions>
</Task>
"""


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    """Runs ``schtasks`` (Windows only)."""
    if sys.platform != "win32":
        raise OSError(
            "Installing the daemon is supported on Windows (Task Scheduler). Elsewhere, start `clabe daemon run` "
            "from your session manager (e.g. a systemd user unit)."
        )
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, check=False)


def task_state() -> str | None:
    """The task's state (``Running``, ``Ready``, ``Disabled``...), or ``None`` when it is not registered.

    Read with ``Get-ScheduledTask``: unlike ``schtasks``' text, its states are not translated.
    """
    if sys.platform != "win32":
        return None
    query = f"(Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop).State"
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", query], capture_output=True, text=True, check=False
    )
    return (done.stdout.strip() or None) if done.returncode == 0 else None


def install(*, uvx: str | None = None, working_directory: Path | None = None) -> str:
    """Registers (or replaces) the scheduled task and starts it.

    A daemon that is already running keeps running the old task's command: :func:`restart` it to use the new one.
    """
    directory = working_directory or default_working_directory()
    directory.mkdir(parents=True, exist_ok=True)
    xml = task_xml(daemon_command(uvx), working_directory=directory)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "clabe-daemon.xml"
        path.write_text(xml, encoding="utf-16")
        created = _schtasks("/Create", "/TN", TASK_NAME, "/XML", str(path), "/F")
    if created.returncode != 0:
        raise OSError(f"Could not create the scheduled task: {created.stderr.strip() or created.stdout.strip()}")
    was_running = task_state() == "Running"
    _schtasks("/Run", "/TN", TASK_NAME)
    note = (
        "The daemon was already running and keeps its old command: run `clabe daemon restart`."
        if was_running
        else f"Started (or it starts when {getpass.getuser()} logs on)."
    )
    return f"Registered the {TASK_NAME} task (working directory {directory}). {note}"


def probe(port: int, token: str | None, timeout: float = 2.0) -> dict | None:
    """The daemon's ``/api/daemon/info`` on this machine, or ``None`` when nothing answers on ``port``.

    A daemon that refuses the token still answers (``{"status": "unauthorized"}``).
    """
    import requests

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        response = requests.get(f"http://127.0.0.1:{port}/api/daemon/info", headers=headers, timeout=timeout)
    except requests.RequestException:
        return None
    if response.status_code == 401:
        return {"status": "unauthorized"}
    try:
        return response.json()
    except ValueError:
        return None


def restart(port: int, token: str | None, *, force: bool = False, timeout: float = 60.0) -> str:
    """Stops the daemon and starts it again from its task (e.g. after a new install), then waits until it answers.

    Args:
        port: The daemon's port (from its settings).
        token: The daemon's token, to read whether it is busy.
        force: Restart even while it runs an experiment or is leased by another machine's run (that session's
            processes may be stopped).
        timeout: Seconds to wait for the restarted daemon to answer.

    Raises:
        OSError: The daemon is busy (without ``force``), the task is missing, or the daemon did not come back.
    """
    if task_state() is None:
        raise OSError(f"The {TASK_NAME} task is not installed: run `clabe daemon install` first.")
    info = probe(port, token)
    if info is not None and info.get("status") in ("busy", "leased") and not force:
        what = "runs an experiment" if info["status"] == "busy" else f"is leased by {info.get('leased_by')}"
        raise OSError(f"The daemon {what}: restarting would interrupt it. Wait, or pass --force.")
    _schtasks("/End", "/TN", TASK_NAME)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and (task_state() == "Running" or probe(port, token, timeout=0.5) is not None):
        time.sleep(0.5)
    started = _schtasks("/Run", "/TN", TASK_NAME)
    if started.returncode != 0:
        raise OSError(f"Could not start the {TASK_NAME} task: {started.stderr.strip() or started.stdout.strip()}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = probe(port, token)
        if info is not None:
            version = info.get("clabe_version")
            return f"The clabe daemon answers on port {port}" + (f" (clabe {version})." if version else ".")
        time.sleep(1.0)
    state = task_state()
    if state != "Running":
        return f"The task is {state}: the daemon starts when {getpass.getuser()} logs on (it runs in their session)."
    raise OSError(
        f"The task runs but nothing answers on port {port} after {timeout:g} s. See the daemon's log: "
        f"{default_working_directory() / 'daemon.log'}"
    )


def status(port: int | None = None, token: str | None = None) -> str:
    """The scheduled task's state as Task Scheduler reports it, and whether the daemon answers on ``port``."""
    queried = _schtasks("/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V")
    if queried.returncode != 0:
        return "The clabe daemon is not installed."
    lines = [queried.stdout.strip()]
    if port is not None:
        info = probe(port, token)
        if info is None:
            lines.append(f"Nothing answers on port {port}.")
        else:
            lines.append(f"The daemon answers on port {port}: {info.get('status')}, clabe {info.get('clabe_version')}.")
    return "\n".join(lines)


def uninstall() -> str:
    """Stops and removes the scheduled task."""
    _schtasks("/End", "/TN", TASK_NAME)
    deleted = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    if deleted.returncode != 0:
        raise OSError(f"Could not remove the scheduled task: {deleted.stderr.strip() or deleted.stdout.strip()}")
    return deleted.stdout.strip()


def upgrade() -> str:
    """Upgrades a ``uv tool`` installation of clabe; restart the daemon afterwards.

    A daemon installed with ``--uvx`` is upgraded by installing again with the new package spec, then restarting.
    """
    uv = shutil.which("uv")
    if uv is None:
        raise OSError("uv is required to upgrade clabe (it is installed with `uv tool install aind-clabe`).")
    done = subprocess.run([uv, "tool", "upgrade", "aind-clabe"], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise OSError(done.stderr.strip() or done.stdout.strip())
    return (done.stdout + done.stderr).strip()


def start(host: str | None = None) -> None:
    """Starts the daemon's scheduled task, here or on ``host`` (needs rights there, typically administrator).

    Raises:
        OSError: If Task Scheduler refuses.
    """
    started = _schtasks("/Run", *(("/S", host) if host else ()), "/TN", TASK_NAME)
    if started.returncode != 0:
        raise OSError(f"Could not start {TASK_NAME} on {host or 'this machine'}: {started.stderr.strip()}")
