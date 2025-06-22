from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
from importlib import metadata
from pathlib import Path
from typing import Dict, List, Union

import pydantic
from aind_behavior_services import (
    AindBehaviorRigModel,
)
from aind_behavior_services.rig.cameras import CameraController, CameraTypes
from aind_behavior_services.utils import get_fields_of_type

logger = logging.getLogger(__name__)


def get_cameras(
    rig_instance: AindBehaviorRigModel, exclude_without_video_writer: bool = True
) -> Dict[str, CameraTypes]:
    """
    Retrieves a dictionary of cameras from the given rig instance.

    Extracts camera information from camera controllers within the rig model,
    optionally filtering based on video writer availability.

    Args:
        rig_instance: The rig model instance containing camera controllers
        exclude_without_video_writer: If True, exclude cameras without a video writer

    Returns:
        Dict[str, CameraTypes]: A dictionary mapping camera names to their types
    """
    cameras: dict[str, CameraTypes] = {}
    camera_controllers = [x[1] for x in get_fields_of_type(rig_instance, CameraController)]

    for controller in camera_controllers:
        if exclude_without_video_writer:
            these_cameras = {k: v for k, v in controller.cameras.items() if v.video_writer is not None}
        else:
            these_cameras = controller.cameras
        cameras.update(these_cameras)
    return cameras


ISearchable = Union[pydantic.BaseModel, Dict, List]


def snapshot_python_environment() -> Dict[str, str]:
    """
    Captures a snapshot of the current Python environment, including installed packages.

    Creates a record of all currently installed Python packages and their versions,
    useful for reproducibility and debugging purposes.

    Returns:
        Dict[str, str]: A dictionary of package names and their versions

    Examples:
        ```python
        # Capture the current Python environment:
        env_snapshot = snapshot_python_environment()
        # Returns: {'numpy': '1.24.3', 'pandas': '2.0.1', 'aind-data-schema': '0.15.0', ...}

        # Use for debugging package versions:
        packages = snapshot_python_environment()
        print(f"NumPy version: {packages.get('numpy', 'Not installed')}")
        # Prints: NumPy version: 1.24.3
        ```
    """
    return {dist.name: dist.version for dist in metadata.distributions()}


def snapshot_bonsai_environment(
    config_file: os.PathLike = Path("./bonsai/bonsai.config"),
) -> Dict[str, str]:
    """
    Captures a snapshot of the Bonsai environment from the given configuration file.

    Parses the Bonsai configuration file to extract information about installed
    packages and their versions, creating a snapshot of the Bonsai environment.

    Args:
        config_file: Path to the Bonsai configuration file

    Returns:
        Dict[str, str]: A dictionary of package IDs and their versions

    Examples:
        ```python
        # Capture Bonsai environment from default config:
        bonsai_env = snapshot_bonsai_environment()
        # Returns: {'Bonsai.Core': '2.7.0', 'Bonsai.Vision': '2.8.0', 'Bonsai.Spinnaker': '0.3.0', ...}

        # Capture from custom config file:
        custom_env = snapshot_bonsai_environment("./custom/bonsai.config")
        # Returns: {'Bonsai.Core': '2.6.0', 'Bonsai.Arduino': '2.7.0', ...}
        ```
    """
    tree = ET.parse(Path(config_file))
    root = tree.getroot()
    packages = root.findall("Packages/Package")
    return {leaf.attrib["id"]: leaf.attrib["version"] for leaf in packages}
