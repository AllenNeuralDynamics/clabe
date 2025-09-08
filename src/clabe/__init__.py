__version__ = "0.6.13"

import logging

from . import logging_helper

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format=logging_helper.log_fmt,
    datefmt=logging_helper.datetime_fmt,
    handlers=[logging_helper.rich_handler],
)
