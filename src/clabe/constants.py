import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

TMP_DIR = ".cache"


PROGRAMDATA_DIR = os.environ.get("PROGRAMDATA", "C:/ProgramData")

# The config files will be used in order, with the first one having the highest priority

KNOWN_CONFIG_FILES: list[str] = [
    "./local/clabe.yml",
    "./clabe.yml",
    str(Path(PROGRAMDATA_DIR) / "clabe.yml"),
]


for i, p in enumerate(KNOWN_CONFIG_FILES):
    if Path(p).exists():
        logger.debug("Found config file: %s with rank priority %s", p, i)
