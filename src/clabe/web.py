"""Serve a CLABE experiment's terminal UI over a local web port.

Thin wrapper around :mod:`textual_serve` so the existing Textual TUI can be
reached from a browser without building a separate web frontend. The server
binds to localhost by default; for remote access, forward the port over SSH
(``ssh -L``) rather than exposing it to the network.

This is an optional feature: install the ``web`` extra (``pip install
'aind-clabe[web]'``) to make it available.
"""

import asyncio
import logging
import socket
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8089

_RESTART_BUTTON = '<button type="button" onClick="refresh()">Restart</button>'
_FINISH_BUTTON = '<button type="button" onClick="finishSession()">Finish</button>'
_FINISH_SCRIPT = """    <script>
      async function finishSession() {
        try { await fetch("/finish", { method: "POST", mode: "no-cors" }); } catch (e) {}
        const dialog = document.querySelector(".closed-dialog .intro");
        if (dialog) {
          dialog.innerHTML = '<div class="message">Server stopped. You can close this tab.</div>';
        }
      }
    </script>
"""


def _reachable_host(host: str) -> str:
    """Maps a wildcard bind address to a host a browser can actually reach."""
    return "127.0.0.1" if host in ("0.0.0.0", "", "::") else host


def _patched_templates_dir() -> Optional[Path]:
    """Builds a templates directory that adds a "Finish" button to the web UI.

    Copies textual-serve's index template and injects a Finish button next to
    Restart (in the "Session ended" dialog) plus the script that calls the
    server's shutdown route.

    Returns:
        Optional[Path]: A directory holding the patched template, or ``None`` if
        the upstream template changed shape and the button could not be
        inserted (in which case the default template is used unchanged).
    """
    from textual_serve import server as textual_server

    source = Path(textual_server.__file__).parent / "templates" / "app_index.html"
    html = source.read_text(encoding="utf-8")
    if _RESTART_BUTTON not in html or "</head>" not in html:
        logger.warning("Could not add the Finish button: the textual-serve template changed.")
        return None

    html = html.replace(_RESTART_BUTTON, _RESTART_BUTTON + "\n        " + _FINISH_BUTTON, 1)
    html = html.replace("</head>", _FINISH_SCRIPT + "  </head>", 1)
    target = Path(tempfile.mkdtemp(prefix="clabe-web-"))
    (target / "app_index.html").write_text(html, encoding="utf-8")
    return target


def _open_browser_when_ready(host: str, port: int) -> threading.Thread:
    """Opens the web UI in a browser once the server starts accepting connections.

    Runs on a background thread so it does not block the (blocking) server; waits
    for the port to accept a connection before opening, and gives up quietly if
    the server never comes up.

    Args:
        host: The interface the server is bound to.
        port: The port the server is listening on.

    Returns:
        threading.Thread: The (already started) daemon thread doing the wait/open.
    """
    target = _reachable_host(host)
    url = f"http://{target}:{port}"

    def _wait_then_open() -> None:
        """Polls the port, then opens the browser once it is reachable."""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                probe.settimeout(0.5)
                if probe.connect_ex((target, port)) == 0:
                    break
            time.sleep(0.2)
        webbrowser.open(url)

    thread = threading.Thread(target=_wait_then_open, name="clabe-open-browser", daemon=True)
    thread.start()
    return thread


def _announce(host: str, port: int) -> None:
    """Prints the local URL and an SSH port-forwarding hint to the terminal."""
    from .logging_helper import clabe_console

    url = f"http://{host}:{port}"
    clabe_console.rule("CLABE web UI")
    clabe_console.print(f"Serving the launcher TUI at [bold]{url}[/] (localhost only).")
    clabe_console.print(
        f"Remote access: on your machine run [bold]ssh -L {port}:localhost:{port} <this-host>[/], "
        f"then open [bold]{url}[/]."
    )
    clabe_console.rule()


def serve(
    command: str,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    title: str = "CLABE",
    open_browser: bool = False,
) -> None:
    """
    Serves a CLABE TUI command over a local web port.

    Each browser connection launches ``command`` as its own subprocess. The
    ``clabe serve`` command guards against this by passing ``--single-session``,
    so a stray second connection refuses to start rather than fighting the live
    session over the rig.

    Args:
        command: Shell command that starts the CLABE TUI, e.g.
            ``"python -m clabe.cli run experiment.py --frontend tui"``.
        host: Interface to bind. Defaults to localhost; keep it there and use
            SSH port forwarding for remote access.
        port: TCP port to listen on.
        title: Title shown in the browser tab.
        open_browser: When set, open the web UI in the local default browser
            once the server is ready. Leave off for headless/remote hosts.

    Raises:
        ImportError: If the optional ``web`` extra is not installed.
        RuntimeError: If the server cannot bind (e.g. the port is in use).
    """
    try:
        from textual_serve.server import Server
    except ImportError as exc:
        raise ImportError(
            "Serving the web UI requires the optional 'web' extra. Install it with: pip install 'aind-clabe[web]'"
        ) from exc

    from aiohttp import web as aiohttp_web

    templates_dir = _patched_templates_dir()
    kwargs = {"host": host, "port": port, "title": title}
    if templates_dir is not None:
        kwargs["templates_path"] = templates_dir
    server = Server(command, **kwargs)
    original_make_app = server._make_app

    async def _make_app_with_finish():
        """Builds textual-serve's app and adds the Finish shutdown route."""
        app = await original_make_app()

        async def _handle_finish(request):
            """Schedules a graceful shutdown and acknowledges the request."""
            asyncio.get_running_loop().call_later(0.25, server.request_exit)
            return aiohttp_web.json_response({"status": "stopping"})

        app.router.add_post("/finish", _handle_finish)
        return app

    server._make_app = _make_app_with_finish

    _announce(host, port)
    if open_browser:
        _open_browser_when_ready(host, port)
    try:
        server.serve()
    except OSError as exc:
        raise RuntimeError(
            f"Could not start the web server on {host}:{port} ({exc}). "
            "Is the port already in use? Try a different --port."
        ) from exc
