"""Terminal clients open HTML pages and web pages in the OS browser."""

import logging
import tempfile
from pathlib import Path

from ._requests import MediaRequest

logger = logging.getLogger(__name__)


def browser_target(request: MediaRequest) -> str | None:
    """What a browser should open for ``request``: its URL, its file's ``file://`` URI, or (inline HTML) a temporary
    file's. ``None`` for an image, or a file that does not exist."""
    if request.media not in ("html", "url"):
        return None
    if request.location == "url":
        return str(request.source)
    if request.location == "inline":
        with tempfile.NamedTemporaryFile("w", suffix=".html", prefix="clabe-", delete=False, encoding="utf-8") as page:
            page.write(str(request.source))
        return Path(page.name).as_uri()
    path = request.local_file()
    return path.as_uri() if path is not None else None


def open_page(request: MediaRequest) -> str | None:
    """Opens an HTML or web page prompt in the default browser, outside the run's job so it stays open after the run.

    Returns:
        What was opened, or ``None``: an image, a missing file, or opening failed (logged, never raised).
    """
    try:
        target = browser_target(request)
        if target is None:
            return None
        from .. import _process

        _process.open_in_browser(target)
        return target
    except Exception:
        logger.warning("Could not open the %s in the browser: %s", request.media, request.describe(), exc_info=True)
        return None
