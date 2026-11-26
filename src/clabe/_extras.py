"""Optional dependencies: the ``frontend`` extra (the terminal UI and the browser page) and how to ask for it."""

import importlib.util

#: How to install the terminal UI and the web server (the run's browser page, the daemon and the hub).
FRONTEND_HINT = 'uv add "aind-clabe[frontend]"'


def available(module: str) -> bool:
    """Whether ``module`` can be imported (without importing it)."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def has_tui() -> bool:
    """Whether the terminal UI (Textual) is installed."""
    return available("textual") and available("textual_fspicker")


def has_web() -> bool:
    """Whether the web server (FastAPI, uvicorn) is installed: serving a run, the daemon and the hub need it."""
    return available("fastapi") and available("uvicorn")


def require_web(what: str) -> None:
    """Raises a clear error when the web server is not installed.

    Args:
        what: What needs it, e.g. "The daemon".

    Raises:
        ImportError: FastAPI or uvicorn is missing.
    """
    if not has_web():
        raise ImportError(f"{what} needs the web server, from the frontend extra: {FRONTEND_HINT}")


def require_tui() -> None:
    """Raises a clear error when the terminal UI is not installed.

    Raises:
        ImportError: Textual is missing.
    """
    if not has_tui():
        raise ImportError(f"The terminal UI needs the frontend extra: {FRONTEND_HINT} (or use --frontend console)")
