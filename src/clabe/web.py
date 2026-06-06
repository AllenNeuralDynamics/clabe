"""Serve a CLABE experiment's terminal UI over a local web port.

Thin wrapper around :mod:`textual_serve` so the existing Textual TUI can be
reached from a browser without building a separate web frontend. The server
binds to localhost by default; for remote access, forward the port over SSH
(``ssh -L``) rather than exposing it to the network.

This is an optional feature: install the ``web`` extra (``pip install
'aind-clabe[web]'``) to make it available.
"""

import logging
import socket
import threading
import time
import webbrowser

logger = logging.getLogger(__name__)

#: Default interface to bind. Localhost only — use SSH forwarding for remote.
DEFAULT_HOST = "127.0.0.1"
#: Default TCP port for the web UI.
DEFAULT_PORT = 8089


def _reachable_host(host: str) -> str:
    """Maps a wildcard bind address to a host a browser can actually reach."""
    return "127.0.0.1" if host in ("0.0.0.0", "", "::") else host


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

    _announce(host, port)
    if open_browser:
        _open_browser_when_ready(host, port)
    try:
        Server(command, host=host, port=port, title=title).serve()
    except OSError as exc:
        raise RuntimeError(
            f"Could not start the web server on {host}:{port} ({exc}). "
            "Is the port already in use? Try a different --port."
        ) from exc
