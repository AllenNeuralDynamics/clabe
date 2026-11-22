import os
from pathlib import Path
from typing import TYPE_CHECKING

from .. import checks
from ._base import Constraint

if TYPE_CHECKING:
    from aind_behavior_services import Rig


def available_storage_constraint_factory_from_rig(rig: "Rig", min_bytes: float = 2e11) -> Constraint:
    """Deprecated: use ``checks.free_space(min_bytes)`` (it takes the ``rig``) or ``steps.free_space``.

    Args:
        rig: The rig; its ``data_directory`` is checked.
        min_bytes: Free space required. Defaults to 200 GB.
    """
    return available_storage_constraint_factory(drive=rig.data_directory, min_bytes=min_bytes)


def available_storage_constraint_factory(drive: os.PathLike = Path("C:\\"), min_bytes: float = 2e11) -> Constraint:
    """Deprecated: use ``checks.free_space(min_bytes, directory=drive)``.

    Args:
        drive: A path on the drive to check (its root is measured). Defaults to ``C:\\``.
        min_bytes: Free space required. Defaults to 200 GB.
    """
    root = Path(os.path.abspath(drive)).anchor
    return Constraint.from_check("available_storage", checks.free_space(min_bytes, directory=root), drive=root)


def remote_dir_exists_constraint_factory(dir_path: os.PathLike) -> Constraint:
    """Deprecated: use ``checks.exists(dir_path)``.

    Args:
        dir_path: The directory (or share) that must exist.
    """
    return Constraint.from_check("remote_dir_exists", checks.exists(dir_path), dir_path=dir_path)
