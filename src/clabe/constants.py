import enum
import logging
import os
import typing as t
from pathlib import Path

logger = logging.getLogger(__name__)

TMP_DIR = ".cache"


PROGRAMDATA_DIR = os.environ.get("PROGRAMDATA", "C:/ProgramData")

# The config files will be used in order, with the first one having the highest priority

KNOWN_CONFIG_FILES: t.List[str] = [
    "./local/clabe.yml",
    "./clabe.yml",
    str(Path(PROGRAMDATA_DIR) / "clabe.yml"),
]


for i, p in enumerate(KNOWN_CONFIG_FILES):
    if Path(p).exists():
        logger.debug(f"Found config file: {p} with rank priority {i}")


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
