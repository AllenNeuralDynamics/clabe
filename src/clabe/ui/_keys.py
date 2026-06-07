"""Minimal, cross-platform single-keystroke reader for interactive prompts.

Reads one key press at a time and normalizes it to a small vocabulary of names
(``UP``, ``DOWN``, ``ENTER``, …) so the console frontend can drive arrow-key
pickers without depending on a full prompt toolkit. Printable characters are
returned verbatim.
"""

import sys

#: Normalized key names returned by :func:`read_key`.
UP = "up"
DOWN = "down"
LEFT = "left"
RIGHT = "right"
ENTER = "enter"
ESCAPE = "escape"
BACKSPACE = "backspace"
TAB = "tab"
INTERRUPT = "interrupt"

_WIN_SPECIAL = {"H": UP, "P": DOWN, "K": LEFT, "M": RIGHT}
_POSIX_SEQ = {"[A": UP, "[B": DOWN, "[C": RIGHT, "[D": LEFT}


def _normalize(ch: str) -> str:
    """Map a single control/printable character to a normalized key name."""
    if ch in ("\r", "\n"):
        return ENTER
    if ch == "\t":
        return TAB
    if ch == "\x1b":
        return ESCAPE
    if ch == "\x03":
        return INTERRUPT
    if ch in ("\x08", "\x7f"):
        return BACKSPACE
    return ch


def _read_key_windows() -> str:
    """Read and normalize one key press on Windows via ``msvcrt``."""
    import msvcrt

    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        code = msvcrt.getwch()
        return _WIN_SPECIAL.get(code, "")
    return _normalize(ch)


def _read_key_posix() -> str:
    """Read and normalize one key press on POSIX via ``termios``/``tty``."""
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            if select.select([sys.stdin], [], [], 0.05)[0]:
                seq = sys.stdin.read(2)
                return _POSIX_SEQ.get(seq, ESCAPE)
            return ESCAPE
        return _normalize(ch)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_key() -> str:
    """
    Block until a single key is pressed and return its normalized name.

    Returns:
        str: One of the module-level key-name constants (e.g. :data:`UP`,
        :data:`ENTER`) for special keys, or the literal character typed for
        printable input. Unrecognized control sequences yield an empty string.
    """
    if sys.platform == "win32":
        return _read_key_windows()
    return _read_key_posix()
