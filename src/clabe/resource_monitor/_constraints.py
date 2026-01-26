import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aind_behavior_services import AindBehaviorRigModel

from ._base import Constraint


def available_storage_constraint_factory_from_rig(rig: "AindBehaviorRigModel", min_bytes: float = 2e11) -> Constraint:
    """
    Creates a constraint to check if the rig's data directory has sufficient available storage.

    This factory function creates a constraint that validates whether the data directory
    specified in the rig model has enough free space to meet the minimum requirements.

    Args:
        rig: The rig model containing the data directory path
        min_bytes: Minimum required free space in bytes. Defaults to 200GB
    Returns:
        Constraint: A constraint object for available storage validation
    Example:
        ```python
        from aind_behavior_services import AindBehaviorRigModel
        from clabe.resource_monitor import available_storage_constraint_factory_from_rig
        rig = AindBehaviorRigModel(rig_name="example_rig", version="1.0", data_directory="D:/data")
        storage_constraint = available_storage_constraint_factory_from_rig(rig, min_bytes=5e11)  # 500GB
        monitor = ResourceMonitor()
        monitor.add_constraint(storage_constraint)
        ```
    """
    return available_storage_constraint_factory(drive=rig.data_directory, min_bytes=min_bytes)


def available_storage_constraint_factory(drive: os.PathLike = Path(r"C:\\"), min_bytes: float = 2e11) -> Constraint:
    """
    Creates a constraint to check if a drive has sufficient available storage.

    This factory function creates a constraint that validates whether the specified
    drive has enough free space to meet the minimum requirements.

    Args:
        drive: The drive to check. Defaults to "C:\\":
        min_bytes: Minimum required free space in bytes. Defaults to 200GB

    Returns:
        Constraint: A constraint object for available storage validation

    Raises:
        ValueError: If the drive path is not valid

    Example:
        ```python
        # Check for default 200GB free space on C: drive
        default_storage_constraint = available_storage_constraint_factory()

        # Check for 1TB free space on D: drive
        large_storage_constraint = available_storage_constraint_factory(
            drive="D:\\",
            min_bytes=1e12  # 1TB
        )

        # Use in resource monitor
        monitor = ResourceMonitor()
        monitor.add_constraint(large_storage_constraint)
        ```
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

    This factory function creates a constraint that validates whether the specified
    directory path exists and is accessible.

    Args:
        dir_path: The path of the directory to check

    Returns:
        Constraint: A constraint object for directory existence validation

    Example:
        ```python
        # Check if network share exists
        network_constraint = remote_dir_exists_constraint_factory(
            "\\\\server\\shared_folder"
        )

        # Check if local directory exists
        local_constraint = remote_dir_exists_constraint_factory(
            "/data/experiments"
        )

        # Use in resource monitor
        monitor = ResourceMonitor()
        monitor.add_constraint(network_constraint)
        monitor.add_constraint(local_constraint)

        if monitor.validate():
            print("All directories accessible")
        ```
    """
    return Constraint(
        name="remote_dir_exists",
        constraint=os.path.exists,
        args=[],
        kwargs={"dir_path": dir_path},
        fail_msg_handler=lambda dir_path: f"Directory {dir_path} does not exist.",
    )
