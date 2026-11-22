"""Installing the daemon to start with the user's session: a Windows scheduled task ("at logon, restart on failure").

The daemon runs as the logged-in user (it starts experiments that show windows, e.g. Bonsai), not as a service.
"""

import getpass
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

#: The scheduled task's name.
TASK_NAME = "clabe-daemon"


def daemon_command() -> list[str]:
    """How to start the daemon: the installed ``clabe`` executable, else this interpreter."""
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


def install(working_directory: Path | None = None) -> str:
    """Registers (or replaces) the scheduled task and starts it now."""
    xml = task_xml(daemon_command(), working_directory=working_directory)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "clabe-daemon.xml"
        path.write_text(xml, encoding="utf-16")
        created = _schtasks("/Create", "/TN", TASK_NAME, "/XML", str(path), "/F")
    if created.returncode != 0:
        raise OSError(f"Could not create the scheduled task: {created.stderr.strip() or created.stdout.strip()}")
    started = _schtasks("/Run", "/TN", TASK_NAME)
    return created.stdout.strip() + ("\n" + started.stdout.strip() if started.returncode == 0 else "")


def status() -> str:
    """The scheduled task's state, as Task Scheduler reports it."""
    queried = _schtasks("/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V")
    if queried.returncode != 0:
        return "The clabe daemon is not installed."
    return queried.stdout.strip()


def uninstall() -> str:
    """Stops and removes the scheduled task."""
    _schtasks("/End", "/TN", TASK_NAME)
    deleted = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    if deleted.returncode != 0:
        raise OSError(f"Could not remove the scheduled task: {deleted.stderr.strip() or deleted.stdout.strip()}")
    return deleted.stdout.strip()


def upgrade() -> str:
    """Upgrades the clabe tool installation (``uv tool upgrade aind-clabe``); restart the daemon afterwards."""
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
