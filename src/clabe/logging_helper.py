"""Deprecated alias for :mod:`clabe.logging`.

``clabe.logging_helper`` was renamed to :mod:`clabe.logging`. This module re-exports the
same public names so existing imports keep working, but emits a :class:`DeprecationWarning`
on import. Update ``from clabe.logging_helper import ...`` (or ``clabe.logging_helper.x``) to
``clabe.logging`` — this alias will be removed in a future release.
"""

import warnings

from .logging import (
    _DEFAULT_CONSOLE_LEVEL,
    add_file_handler,
    clabe_console,
    close_file_handlers,
    configure_console_logging,
    datetime_fmt,
    log_fmt,
    rich_handler,
    set_console_level,
    shutdown_logger,
)

warnings.warn(
    "clabe.logging_helper has been renamed to clabe.logging and will be removed in a "
    "future release. Update your import to 'from clabe import logging' or "
    "'from clabe.logging import ...'.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "_DEFAULT_CONSOLE_LEVEL",
    "add_file_handler",
    "clabe_console",
    "close_file_handlers",
    "configure_console_logging",
    "datetime_fmt",
    "log_fmt",
    "rich_handler",
    "set_console_level",
    "shutdown_logger",
]
