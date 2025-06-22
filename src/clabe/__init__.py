__version__ = "0.5.0rc1"

import logging

from .logging_helper import datetime_fmt, fmt, rich_handler

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format=fmt,
    datefmt=datetime_fmt,
    handlers=[rich_handler],
)
