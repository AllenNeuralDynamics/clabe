import enum
import logging


class MessageLevel(enum.Enum):
    """
    Severity/intent of a message surfaced to the user by a frontend.

    This is intentionally separate from :mod:`logging` levels: it describes how
    a message should be *presented* to the user, not how it should be recorded.
    The frontend maps each level to an appropriate logging level when writing
    the message to the persistent transcript.
    """

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"

    @property
    def logging_level(self) -> int:
        """The standard ``logging`` level used when recording this message."""
        return _LOGGING_LEVELS[self]


_LOGGING_LEVELS = {
    MessageLevel.INFO: logging.INFO,
    MessageLevel.SUCCESS: logging.INFO,
    MessageLevel.WARNING: logging.WARNING,
    MessageLevel.ERROR: logging.ERROR,
}
