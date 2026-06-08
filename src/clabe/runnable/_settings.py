import logging
from dataclasses import dataclass, replace
from typing import Optional

#: When True, success notifications include elapsed time. Toggled by --debug-mode.
_include_timing: bool = False


def set_include_timing(value: bool) -> None:
    """Enable or disable elapsed-time suffixes on success notifications."""
    global _include_timing
    _include_timing = value


@dataclass(frozen=True)
class RunnableSpec:
    """Notification behaviour for a single ``@runnable``.

    All flags default to ``None`` (inherit) so that call-site rewraps can
    override individual fields without clobbering the rest. The decorator
    fills unset flags with ``True`` before storing the spec on the wrapper.
    """

    name: Optional[str] = None
    notify: Optional[str] = None  # custom start message
    show_activity: Optional[bool] = None
    notify_start: Optional[bool] = None
    notify_success: Optional[bool] = None
    notify_fail: Optional[bool] = None
    log_level: int = logging.INFO

    def merge(self, other: "RunnableSpec") -> "RunnableSpec":
        """Return a copy where ``other``'s non-``None`` fields win."""
        return replace(self, **{k: v for k, v in vars(other).items() if v is not None})
