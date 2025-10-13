"""Constants used throughout the CLABE package."""

import enum
import os
import typing as t
from pathlib import Path

from aind_behavior_services.utils import format_datetime, model_from_json_file, utcnow

__all__ = [
    "abspath",
    "format_datetime",
    "model_from_json_file",
    "utcnow",
    "ByAnimalFiles",
]

PROGRAMDATA_DIR = os.environ.get("PROGRAMDATA", "C:/ProgramData")

# The config files will be used in order, with the first one having the highest priority
KNOWN_CONFIG_FILES: t.List[str] = [
    "./local/clabe.yml",
    "./clabe.yml",
    str(Path(PROGRAMDATA_DIR) / "clabe.yml"),
]


def abspath(path: os.PathLike) -> Path:
    """
    Helper method that converts a path to an absolute path.

    Args:
        path: The path to convert

    Returns:
        Path: The absolute path
    """
    return Path(path).resolve()


class ByAnimalFiles(enum.StrEnum):
    """
    Enum for file types associated with animals in the experiment.

    Defines the standard file types that can be associated with individual
    animals/subjects in behavior experiments.

    Example:
        ```python
        # Use the task logic file type
        filename = f"{ByAnimalFiles.TASK_LOGIC}.json"
        ```
    """

    TASK_LOGIC = "task_logic"
    TRAINER_STATE = "trainer_state"
