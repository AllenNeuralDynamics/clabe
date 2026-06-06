"""A process-wide lock ensuring only one launcher session runs at a time.

Acquiring an exclusive session prevents a second launcher (for example a stray
extra browser connection to the web UI) from starting concurrently and fighting
over the rig — which could corrupt an ongoing acquisition.

The lock is an OS-level advisory lock held on a lock file (``msvcrt`` on Windows,
``fcntl`` on POSIX). The operating system releases the lock when the holding
process exits, even on a crash, so there are no stale locks to clean up.
"""

import contextlib
import logging
import sys
import tempfile
from pathlib import Path
from typing import IO, Iterator

logger = logging.getLogger(__name__)

#: Lock file backing the single-session mutex.
SESSION_LOCK_PATH = Path(tempfile.gettempdir()) / "clabe-session.lock"


class SessionAlreadyRunningError(RuntimeError):
    """Raised when another launcher session already holds the single-session lock."""


def _try_acquire(handle: IO[str]) -> None:
    """Takes a non-blocking exclusive lock on ``handle``, raising OSError if held."""
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release(handle: IO[str]) -> None:
    """Releases the lock held on ``handle``."""
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        with contextlib.suppress(OSError):
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        with contextlib.suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def single_session_lock(path: Path = SESSION_LOCK_PATH) -> Iterator[None]:
    """
    Holds a process-wide lock so only one launcher session runs at a time.

    Args:
        path: Lock file to use as the mutex.

    Yields:
        None: For the duration of the held lock.

    Raises:
        SessionAlreadyRunningError: If another session already holds the lock.
    """
    handle = open(path, "a+")
    try:
        try:
            _try_acquire(handle)
        except OSError as exc:
            raise SessionAlreadyRunningError(
                "Another CLABE session is already running on this machine. Refusing to start a "
                "second one to protect the ongoing acquisition."
            ) from exc
        logger.debug("Acquired single-session lock at %s.", path)
        try:
            yield
        finally:
            _release(handle)
    finally:
        handle.close()
