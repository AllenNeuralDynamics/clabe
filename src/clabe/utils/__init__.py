import logging
import os
import socket
from pathlib import Path

from aind_behavior_services.utils import format_datetime, model_from_json_file, utcnow

logger = logging.getLogger(__name__)

__all__ = [
    "abspath",
    "format_datetime",
    "get_computer_name",
    "is_this_computer",
    "model_from_json_file",
    "utcnow",
]


def get_computer_name() -> str:
    """
    Returns this machine's name.

    Returns:
        str: The machine name.
    """
    return os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or socket.gethostname()


def is_this_computer(name: str) -> bool:
    """
    Returns whether a computer name, e.g. a rig's ``computer_name``, names this machine.

    The comparison ignores case and any domain suffix, so ``RIG-A`` and ``rig-a.corp.org`` both match ``rig-a``.

    Args:
        name: The computer name to check.

    Returns:
        bool: True if the name is this machine's.
    """
    names = {get_computer_name(), socket.gethostname(), "localhost"}
    return name.split(".")[0].lower() in {n.split(".")[0].lower() for n in names}


def abspath(path: os.PathLike) -> Path:
    """
    Helper method that converts a path to an absolute path.

    Args:
        path: The path to convert

    Returns:
        Path: The absolute path
    """
    return Path(path).resolve()
