"""``clabe run`` at the rig hands the run to this machine's daemon and shows it in its own window.

The run then belongs to the daemon: closing the window stops it cleanly, and a window that dies without closing
(killed) leaves it going, to be shown again by the next ``clabe run`` (the desktop icon). When a run is already
going, ``clabe run`` asks whether to resume it or abort it first. Without a daemon that answers (after trying to
start its task), or outside a configured repository, the run happens in the window, as before.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from ..server._server import ANNOUNCE_ENV
from ._host import RunInfo
from ._local import DaemonRefused, LocalDaemon, reach

logger = logging.getLogger(__name__)

#: The run arguments forwarded to a run the daemon launches, when set: (field, flag).
_FLAGS = (
    ("debug_mode", "--debug-mode"),
    ("verbose", "--verbose"),
    ("quiet", "--quiet"),
    ("allow_dirty", "--allow-dirty"),
    ("skip_hardware_validation", "--skip-hardware-validation"),
)


def applies(cli: Any) -> bool:
    """Whether this ``clabe run`` hands its run to the daemon: on a Windows rig, in a terminal, showing the
    terminal UI, not started by the daemon itself, and not told to run here (``--here``)."""
    if sys.platform != "win32" or getattr(cli, "here", False) or os.environ.get(ANNOUNCE_ENV):
        return False
    if getattr(cli, "frontend", "auto") not in ("auto", "tui"):
        return False
    return sys.stdin is not None and sys.stdin.isatty() and sys.stdout is not None and sys.stdout.isatty()


def arguments(cli: Any) -> list[str]:
    """The ``clabe run`` arguments given here that the daemon's run needs too."""
    forwarded = [flag for field, flag in _FLAGS if getattr(cli, field, False)]
    if getattr(cli, "clabe_yml", None):
        forwarded.append(f"--clabe-yml={Path(cli.clabe_yml).resolve()}")
    return forwarded


def locate(repositories: list[tuple[str, str]], experiment: Path) -> tuple[str, str] | None:
    """The repository (name) the daemon offers that holds ``experiment``, and the file's path in it."""
    target = experiment.resolve()
    best: tuple[int, str, str] | None = None
    for name, path in repositories:
        root = Path(path).resolve()
        if target.is_relative_to(root) and (best is None or len(root.parts) > best[0]):
            best = (len(root.parts), name, target.relative_to(root).as_posix())
    return (best[1], best[2]) if best else None


def describe(run: RunInfo) -> str:
    """What the question about a going run says about it."""
    started = run.started_at.astimezone().strftime("%H:%M")
    what = " · ".join(part for part in (run.repository, run.experiment, f"started {started}") if part)
    if run.from_rig:
        return f"A previous run was not closed: {what}"
    return f"A run started remotely is in progress: {what}"


def ask(run: RunInfo, read_key=None) -> str:
    """Asks what to do about the going ``run``: ``"resume"``, ``"abort"`` or ``"quit"``."""
    if read_key is None:
        import msvcrt

        read_key = msvcrt.getwch
    print(f"\n {describe(run)}\n\n   [R] Resume    [A] Abort it and start a new run    [Q] Quit\n", flush=True)
    choices = {"r": "resume", "a": "abort", "q": "quit", "\x1b": "quit"}
    while (key := read_key().lower()) not in choices:
        pass
    return choices[key]


def run_through_daemon(cli: Any) -> int | None:
    """Runs ``cli``'s experiment through this machine's daemon and shows it here; its exit code, or ``None`` when
    it should run here instead."""
    if not applies(cli):
        return None
    daemon = reach(start=True)
    if daemon is None:
        return None
    try:
        return _hand_off(daemon, cli)
    except (httpx.HTTPError, DaemonRefused) as exc:
        logger.warning("The daemon could not take the run (%s): running it here.", exc)
        print(f"The daemon could not take the run ({exc}): running it here.", flush=True)
        return None


def _hand_off(daemon: LocalDaemon, cli: Any) -> int | None:
    """Launches the run through ``daemon`` (asking first about one going) and shows it."""
    from ._viewer import view

    if not daemon.info().terminals:
        return None  # an older daemon, or one without pywinpty
    location = locate([(r.name, r.path) for r in daemon.repositories()], Path(cli.experiment_path))
    if location is None:
        return None
    repository, file = location
    body = {
        "repository": repository,
        "file": file,
        "experiment": getattr(cli, "experiment_name", None),
        "frontend": "tui",
        "window": False,  # this window shows it
        "from_rig": True,
        "arguments": arguments(cli),
    }
    for _ in range(3):
        current = daemon.current()
        if current is not None and current.status != "exited":
            choice = ask(current)
            if choice == "quit":
                return 0
            if choice == "resume":
                return _resume(daemon, current)
            print("Stopping the previous run…", flush=True)
            daemon.stop(wait=True)
        try:
            daemon.launch(body)
        except DaemonRefused as exc:
            if exc.status == 409:  # a run started meanwhile: ask about it
                continue
            if exc.status == 423:  # the rig is reserved remotely: the rig wins, the run happens here
                print(f"{exc}: running here.", flush=True)
                return None
            raise
        return view(daemon)
    return None


def _resume(daemon: LocalDaemon, run: RunInfo) -> int:
    """Shows the going ``run``: here when it shows the terminal UI, otherwise its page in the browser."""
    from ._viewer import view

    if run.terminal:
        return view(daemon)
    from .._process import open_in_browser
    from ._cli import open_url
    from ._settings import DaemonSettings

    open_in_browser(open_url(DaemonSettings(), run_page=True))
    print("The run is shown in the browser.", flush=True)
    return 0
