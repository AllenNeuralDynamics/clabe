"""How the daemon starts processes: never with a console window of their own.

The daemon runs without a console (``clabe-daemon.exe``, a windowless executable). On Windows, a console program it
starts (uv, git, PowerShell, an experiment's ``clabe run``) would then open a console window of its own, on the rig's
screen, unless it is started with ``CREATE_NO_WINDOW``. Windows that programs open themselves (Bonsai) still show.
"""

import subprocess
import sys

#: The creation flag that keeps a console program's window from showing (0 off Windows).
NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
