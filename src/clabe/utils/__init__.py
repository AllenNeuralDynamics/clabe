import logging
import os
from pathlib import Path

from aind_behavior_services.utils import format_datetime, model_from_json_file, utcnow

logger = logging.getLogger(__name__)

__all__ = [
    "abspath",
    "format_datetime",
    "model_from_json_file",
    "utcnow",
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
