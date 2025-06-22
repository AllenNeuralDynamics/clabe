import os
import shutil
from pathlib import Path

from ._base import Constraint


def available_storage_constraint_factory(drive: os.PathLike = Path(r"C:\\"), min_bytes: float = 2e11) -> Constraint:
    """
    Creates a constraint to check if a drive has sufficient available storage.

    Args:
        drive (os.PathLike): The drive to check. Defaults to "C:\\".
        min_bytes (float): Minimum required free space in bytes. Defaults to 200GB.

    Returns:
        Constraint: A constraint object for available storage.
    """
    if not os.path.ismount(drive):
        drive = os.path.splitdrive(drive)[0] + "\\"
    if drive is None:
        raise ValueError("Drive is not valid.")
    return Constraint(
        name="available_storage",
        constraint=lambda drive, min_bytes: shutil.disk_usage(drive).free >= min_bytes,
        args=[],
        kwargs={"drive": drive, "min_bytes": min_bytes},
        fail_msg_handler=lambda drive, min_bytes: f"Drive {drive} does not have enough space.",
    )


def remote_dir_exists_constraint_factory(dir_path: os.PathLike) -> Constraint:
    """
    Creates a constraint to check if a remote directory exists.

    Args:
        dir_path (os.PathLike): The path of the directory to check.

    Returns:
        Constraint: A constraint object for directory existence.
    """
    return Constraint(
        name="remote_dir_exists",
        constraint=lambda dir_path: os.path.exists(dir_path),
        args=[],
        kwargs={"dir_path": dir_path},
        fail_msg_handler=lambda dir_path: f"Directory {dir_path} does not exist.",
    )
