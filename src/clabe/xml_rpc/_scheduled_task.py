"""Registration of the XML-RPC server as a Windows scheduled task.

The task runs at logon in the user's interactive session ("run only when user is logged on"),
so applications it launches can show windows and reach network shares with the user's
credentials. It can then be started remotely with ``schtasks /run /s <host> /tn <name>``.

The task is defined in Task Scheduler XML because ``schtasks /create`` flags cannot set the
working directory, restart-on-failure or an unlimited execution time.
"""

import getpass
import logging
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TASK_NAME = "ClabeXmlRpcServer"

_TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def _current_user() -> str:
    """Returns the current user as DOMAIN\\user, as Task Scheduler expects."""
    domain = os.environ.get("USERDOMAIN")
    user = getpass.getuser()
    return f"{domain}\\{user}" if domain else user


def _sub(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    """Appends a child element, optionally with text."""
    element = ET.SubElement(parent, tag)
    if text is not None:
        element.text = text
    return element


def build_task_xml(command: list[str], working_dir: Path, user: str | None = None) -> str:
    """Builds the Task Scheduler XML definition that starts the server at logon.

    Args:
        command: The command to run. The first element is the executable.
        working_dir: Directory the command starts in.
        user: Account the task runs as, as DOMAIN\\user. Defaults to the current user.

    Returns:
        The task definition as an XML string.
    """
    user = user or _current_user()
    task = ET.Element("Task", {"version": "1.2", "xmlns": _TASK_NAMESPACE})

    registration = _sub(task, "RegistrationInfo")
    _sub(registration, "Description", "Starts the clabe XML-RPC server at logon.")

    trigger = _sub(_sub(task, "Triggers"), "LogonTrigger")
    _sub(trigger, "Enabled", "true")
    _sub(trigger, "UserId", user)

    principal = _sub(_sub(task, "Principals"), "Principal")
    principal.set("id", "Author")
    _sub(principal, "UserId", user)
    # Runs in the user's interactive session: windows are visible and shares use the user's credentials
    _sub(principal, "LogonType", "InteractiveToken")
    _sub(principal, "RunLevel", "LeastPrivilege")

    settings = _sub(task, "Settings")
    _sub(settings, "MultipleInstancesPolicy", "IgnoreNew")
    _sub(settings, "DisallowStartIfOnBatteries", "false")
    _sub(settings, "StopIfGoingOnBatteries", "false")
    # The default limit (72 hours) would stop a long-lived server
    _sub(settings, "ExecutionTimeLimit", "PT0S")
    restart = _sub(settings, "RestartOnFailure")
    _sub(restart, "Interval", "PT1M")
    _sub(restart, "Count", "999")
    _sub(settings, "Enabled", "true")

    actions = _sub(task, "Actions")
    actions.set("Context", "Author")
    exec_ = _sub(actions, "Exec")
    _sub(exec_, "Command", command[0])
    if len(command) > 1:
        _sub(exec_, "Arguments", subprocess.list2cmdline(command[1:]))
    _sub(exec_, "WorkingDirectory", str(working_dir))

    return '<?xml version="1.0" encoding="UTF-16"?>\n' + ET.tostring(task, encoding="unicode")


def _schtasks(*args: str) -> subprocess.CompletedProcess[str]:
    """Runs schtasks.exe, raising with its output if it fails."""
    if sys.platform != "win32":
        raise RuntimeError("Scheduled tasks are only supported on Windows.")
    try:
        return subprocess.run(["schtasks", *args], capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"schtasks {' '.join(args)} failed: {(e.stderr or e.stdout).strip()}") from e


def register_task(name: str, command: list[str], working_dir: Path, *, start: bool = True) -> None:
    """Registers (or replaces) the scheduled task and optionally starts it.

    Args:
        name: Name of the scheduled task.
        command: The command the task runs.
        working_dir: Directory the command starts in.
        start: Whether to start the task immediately after registering it.
    """
    definition = build_task_xml(command, working_dir)
    # schtasks reads task XML as UTF-16
    with tempfile.NamedTemporaryFile("w", suffix=".xml", encoding="utf-16", delete=False) as f:
        f.write(definition)
        xml_path = f.name
    try:
        _schtasks("/create", "/tn", name, "/xml", xml_path, "/f")
        logger.info("Registered scheduled task '%s'", name)
    finally:
        os.unlink(xml_path)
    if start:
        start_task(name)


def start_task(name: str, host: str | None = None) -> None:
    """Starts the scheduled task, on this machine or on a remote one.

    Starting a task on another machine requires rights on it (typically local administrator)
    and that the firewall allows remote task scheduling.

    Args:
        name: Name of the scheduled task.
        host: Machine to start the task on. Defaults to this machine.
    """
    _schtasks("/run", *(("/s", host) if host else ()), "/tn", name)
    logger.info("Started scheduled task '%s' on %s", name, host or "this machine")


def unregister_task(name: str) -> None:
    """Removes the scheduled task.

    Args:
        name: Name of the scheduled task.
    """
    _schtasks("/delete", "/tn", name, "/f")
    logger.info("Removed scheduled task '%s'", name)
