import logging
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("aind-clabe")
except PackageNotFoundError:
    __version__ = "0.0.0"

from . import logging_helper

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format=logging_helper.log_fmt,
    datefmt=logging_helper.datetime_fmt,
    handlers=[logging_helper.rich_handler],
)
