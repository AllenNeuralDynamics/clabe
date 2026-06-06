import logging
from typing import Optional

from ._frontend import Frontend
from ._messages import MessageLevel

logger = logging.getLogger(__name__)

_current_frontend: Optional[Frontend] = None


def set_current_frontend(frontend: Optional[Frontend]) -> None:
    """
    Registers the process-wide frontend used by :func:`notify`.

    The launcher sets this so that library modules (which are otherwise
    UI-agnostic) can surface key events to the user without taking a frontend
    dependency. Set to ``None`` to unregister.

    Args:
        frontend: The frontend to register, or ``None`` to clear it.
    """
    global _current_frontend
    _current_frontend = frontend


def current_frontend() -> Optional[Frontend]:
    """Returns the currently registered frontend, or ``None`` if unset."""
    return _current_frontend


def notify(message: str, level: MessageLevel = MessageLevel.INFO) -> None:
    """
    Surfaces a message to the user via the registered frontend, if any.

    This is a no-op when no frontend is registered (e.g. when a library module
    is used outside a launcher), so it is always safe to call.

    Args:
        message: The message to surface.
        level: The presentation level/intent of the message.
    """
    frontend = _current_frontend
    if frontend is not None:
        frontend.notify(message, level)
