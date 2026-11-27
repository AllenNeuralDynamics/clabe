"""``clabe daemon deploy``: installs the requested clabe for the daemon next to the others, then switches the daemon to
it — or does nothing when it already runs it.

Each clabe lives in its own environment under ``%LOCALAPPDATA%\\clabe\\versions\\<key>`` (e.g. ``0.18.0-frontend-otel``
or ``git-c7cc84fb0ad1-frontend-otel``), and the ``clabe-daemon`` task runs that environment's ``clabe.exe``. So:

- a new clabe installs while the daemon keeps running: on Windows a running ``clabe.exe`` locks its environment's
  files, but it is another environment's;
- an environment is installed in a staging folder and renamed into place once it works (uv's relocatable
  environments), so a folder that is there is complete;
- going back is pointing the task at the previous folder, still there (the last :data:`KEEP` are kept).
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import _install

#: The distribution the daemon runs.
PACKAGE = "aind-clabe"

#: The extras the daemon needs by default: its web server, and its logs through OpenTelemetry.
DEFAULT_EXTRAS = ("frontend", "otel")

#: The file each complete environment carries: what it was installed from.
MARKER = "clabe-deploy.json"

#: Environments kept, the daemon's included (the others can be gone back to).
KEEP = 3

#: The daemon's settings file entry for the hubs.
HUBS_VARIABLE = "DAEMON_CLABE_HUBS"


def versions_directory() -> Path:
    """Where the daemon's environments are: ``%LOCALAPPDATA%\\clabe\\versions``."""
    return _install.default_working_directory() / "versions"


def _extras(extras: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Extras, normalized: lower case, sorted, once each (``"otel, frontend"`` -> ``("frontend", "otel")``)."""
    if extras is None:
        return DEFAULT_EXTRAS
    items = extras.split(",") if isinstance(extras, str) else extras
    return tuple(sorted({item.strip().lower() for item in items if item.strip()}))


def _extras_part(extras: tuple[str, ...]) -> str:
    """The extras as a requirement's part: ``[frontend,otel]``, or nothing."""
    return f"[{','.join(extras)}]" if extras else ""


def environment_key(
    version: str | None, extras: tuple[str, ...], *, commit: str | None = None, url: str | None = None
) -> str:
    """The folder of an environment: its release (or git commit, or other source) and its extras."""
    if commit:
        base = f"git-{commit[:12].lower()}"
    elif url:
        base = f"{version}-url-{hashlib.sha256(url.encode()).hexdigest()[:8]}"
    else:
        base = str(version)
    key = "-".join((base, *extras))
    return re.sub(r"[^A-Za-z0-9._+-]", "_", key)


@dataclass(frozen=True)
class Request:
    """The clabe to deploy: a uv requirement, and its environment's folder when known before installing it (a pinned
    release or git commit)."""

    spec: str
    extras: tuple[str, ...]
    key: str | None = None


def request_version(version: str, extras: tuple[str, ...] = DEFAULT_EXTRAS) -> Request:
    """A clabe release, e.g. ``0.18.0``."""
    version = version.strip().removeprefix("v")
    return Request(f"{PACKAGE}{_extras_part(extras)}=={version}", extras, environment_key(version, extras))


def request_latest(extras: tuple[str, ...] = DEFAULT_EXTRAS) -> Request:
    """The latest clabe release (installed to find out which it is)."""
    return Request(f"{PACKAGE}{_extras_part(extras)}", extras)


def request_package(spec: str, extras: tuple[str, ...] = DEFAULT_EXTRAS) -> Request:
    """Any uv requirement, e.g. ``aind-clabe[frontend,otel] @ git+https://github.com/...@<sha>``; ``extras`` names its
    environment."""
    return Request(spec.strip(), extras)


def request_lock(path: Path, extras: tuple[str, ...] = DEFAULT_EXTRAS) -> Request:
    """The clabe a repository's ``uv.lock`` pins (``path`` is the file, or the repository): its release, or its git
    commit.

    Raises:
        OSError: There is no such file, it has no aind-clabe, or aind-clabe comes from elsewhere (a path...).
    """
    lock = path / "uv.lock" if path.is_dir() else path
    try:
        data = tomllib.loads(lock.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise OSError(f"Cannot read {lock}: {exc}") from exc
    packages = [p for p in data.get("package", []) if re.sub(r"[._]", "-", str(p.get("name", "")).lower()) == PACKAGE]
    if not packages:
        raise OSError(f"{PACKAGE} is not in {lock}.")
    package = packages[0]
    source = package.get("source", {})
    if "registry" in source:
        return request_version(package["version"], extras)
    # A git commit (or a path) is built from source on the rig, without the web UI (built by CI for releases only).
    kind = next(iter(source), "unknown")
    raise OSError(
        f"{lock} pins {PACKAGE} from a {kind} source ({source.get(kind)}), not a release: the daemon runs released "
        "clabes only (built with their web UI). Lock a release, or pass --version."
    )


@dataclass(frozen=True)
class Environment:
    """A complete environment of the daemon: where it is and what it was installed from."""

    path: Path
    version: str
    spec: str
    extras: tuple[str, ...]
    commit: str | None = None
    activated: float | None = None

    @property
    def clabe(self) -> Path:
        """The environment's ``clabe`` executable, which the task runs."""
        return _scripts(self.path) / ("clabe.exe" if os.name == "nt" else "clabe")

    def describe(self) -> str:
        """The environment's clabe, for messages: ``clabe 0.18.0`` (and its git commit, if any)."""
        return f"clabe {self.version}" + (f" (git {self.commit[:12]})" if self.commit else "")


def _scripts(environment: Path) -> Path:
    """An environment's executables folder (``Scripts`` on Windows, ``bin`` elsewhere)."""
    return environment / ("Scripts" if os.name == "nt" else "bin")


def _python(environment: Path) -> Path:
    """An environment's Python."""
    return _scripts(environment) / ("python.exe" if os.name == "nt" else "python")


def read_environment(path: Path) -> Environment | None:
    """The complete environment at ``path``, or ``None`` (nothing there, unfinished, or broken)."""
    try:
        marker = json.loads((path / MARKER).read_text(encoding="utf-8"))
        environment = Environment(
            path=path,
            version=marker["version"],
            spec=marker["spec"],
            extras=tuple(marker.get("extras", ())),
            commit=marker.get("commit"),
            activated=marker.get("activated"),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return environment if environment.clabe.is_file() else None


def environments() -> list[Environment]:
    """The complete environments, most recently used by the daemon first."""
    root = versions_directory()
    found = [e for p in (root.iterdir() if root.is_dir() else ()) if (e := read_environment(p)) is not None]
    return sorted(found, key=lambda e: e.activated or e.path.stat().st_mtime, reverse=True)


def find_uv() -> str:
    """The ``uv`` executable (on PATH, else where uv's installer puts it).

    Raises:
        OSError: uv is not installed.
    """
    found = shutil.which("uv") or shutil.which(str(Path.home() / ".local" / "bin" / "uv"))
    if not found:
        raise OSError("uv is required to deploy the daemon: https://docs.astral.sh/uv/getting-started/installation/")
    return found


def _run(command: list[str]) -> None:
    """Runs ``command`` (uv, or an environment's Python); raises with its output when it fails."""
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise OSError(f"`{' '.join(command)}` failed: {(done.stderr or done.stdout).strip()}")


#: Run with the new environment's Python: what got installed, and whether the daemon can start from it.
_PROBE = """
import json
from importlib import metadata
dist = metadata.distribution("aind-clabe")
direct = json.loads(dist.read_text("direct_url.json") or "null") or {}
import clabe.daemon._app  # the daemon's web server: fails without the frontend extra
from clabe.server._app import STATIC_DIR
if not (STATIC_DIR / "index.html").is_file():  # built from source: releases carry the web UI CI builds
    raise SystemExit(f"this clabe has no web UI ({STATIC_DIR} is empty): it was built from source, use a release")
print(json.dumps({"version": dist.version, "url": direct.get("url"),
                  "commit": (direct.get("vcs_info") or {}).get("commit_id")}))
"""


def _probe(environment: Path) -> dict:
    """What is installed in ``environment``; raises when the daemon cannot start from it (no web server or UI)."""
    done = subprocess.run([str(_python(environment)), "-I", "-c", _PROBE], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise OSError(f"The installed clabe cannot run the daemon: {(done.stderr or done.stdout).strip()[-2000:]}")
    return json.loads(done.stdout.strip().splitlines()[-1])


def install_environment(request: Request, *, python: str | None = None) -> tuple[Environment, bool]:
    """The environment of ``request``: the one already installed, or a new one. Returns it and whether it is new.

    Installs into a staging folder, checks the daemon can start from it, then renames it into place: an interrupted
    install leaves no folder that looks complete. The running daemon is not touched.

    Raises:
        OSError: uv is missing, the install fails, or the installed clabe cannot run the daemon.
    """
    root = versions_directory()
    if request.key and (existing := read_environment(root / request.key)) is not None:
        return existing, False
    uv = find_uv()
    root.mkdir(parents=True, exist_ok=True)
    _remove_stale_staging(root)
    staging = root / f".staging-{os.getpid()}-{time.time_ns()}"
    python = python or f"{sys.version_info.major}.{sys.version_info.minor}"
    try:
        _run([uv, "venv", "--quiet", "--relocatable", "--python", python, str(staging)])
        _run(
            [
                uv,
                "pip",
                "install",
                "--quiet",
                "--refresh-package",
                PACKAGE,
                "--python",
                str(_python(staging)),
                request.spec,
            ]
        )
        found = _probe(staging)
        commit = found.get("commit")
        url = None if commit or not found.get("url") else found["url"]
        key = request.key or environment_key(found["version"], request.extras, commit=commit, url=url)
        target = root / key
        if (existing := read_environment(target)) is not None:
            shutil.rmtree(staging, ignore_errors=True)
            return existing, False
        marker = {"version": found["version"], "spec": request.spec, "extras": list(request.extras), "commit": commit}
        (staging / MARKER).write_text(json.dumps(marker, indent=2), encoding="utf-8")
        if target.exists():  # an unfinished leftover: no marker, or no clabe
            shutil.rmtree(target)
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    environment = read_environment(target)
    if environment is None:
        raise OSError(f"The environment installed in {target} is incomplete.")
    return environment, True


def _remove_stale_staging(root: Path, age: float = 3600.0) -> None:
    """Removes staging folders an interrupted deploy left behind (older than ``age`` seconds)."""
    for path in root.glob(".staging-*"):
        try:
            if time.time() - path.stat().st_mtime > age:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def _mark_activated(environment: Environment) -> None:
    """Records that the daemon now runs ``environment`` (for :func:`prune`'s order)."""
    path = environment.path / MARKER
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
        marker["activated"] = time.time()
        path.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass


def prune(keep: int = KEEP, *, active: Path | None = None) -> list[Path]:
    """Removes all but the ``keep`` most recently used environments (never ``active``); returns those removed."""
    removed = []
    others = [e for e in environments() if active is None or not _same_path(e.path, active)]
    for environment in others[max(keep - 1, 0) :]:
        shutil.rmtree(environment.path, ignore_errors=True)
        if not environment.path.exists():
            removed.append(environment.path)
    return removed


def _same_path(a: Path | None, b: Path | None) -> bool:
    """Whether ``a`` and ``b`` are the same path (case-insensitively on Windows); ``None`` is never the same."""
    if a is None or b is None:
        return False
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _environment_of(executable: Path | None) -> Path | None:
    """The environment folder a task's ``clabe.exe`` is in, when it is one of the daemon's environments."""
    if executable is None:
        return None
    environment = executable.parent.parent
    return environment if _same_path(environment.parent, versions_directory()) else None


# --- the hubs, in the daemon's settings file -------------------------------------------------------------------------


def _env_file() -> Path:
    """The daemon's settings file (``daemon.env``), which ``DaemonSettings`` reads."""
    from ._settings import ENV_FILE

    return ENV_FILE


def _user_variable(name: str) -> str | None:
    """This user's persistent environment variable ``name`` (Windows: ``HKCU\\Environment``), or ``None``."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return None


def _remove_user_variable(name: str) -> None:
    """Removes this user's persistent environment variable ``name`` (Windows only)."""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.DeleteValue(key, name)


def set_hubs(hubs: list[str], *, dry_run: bool = False) -> tuple[bool, list[str]]:
    """Saves the hubs the daemon enrolls with in its settings file (``daemon.env``; an empty list: no hub). Returns
    whether that changes the daemon's settings (it must restart), and what was done.

    A ``DAEMON_CLABE_HUBS`` user variable would win over the file: it is removed (older deploys set it there).
    """
    path = _env_file()
    line = f'{HUBS_VARIABLE}="{";".join(hubs)}"'
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    pattern = re.compile(rf"^\s*(export\s+)?{HUBS_VARIABLE}\s*=")
    kept = [existing for existing in lines if not pattern.match(existing)]
    current = [existing for existing in lines if pattern.match(existing)]
    notes = []
    changed = current != [line]
    if changed:
        notes.append(f"Hubs: {', '.join(hubs) or '(none)'} (in {path})")
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join([*kept, line]) + "\n", encoding="utf-8")
    if _user_variable(HUBS_VARIABLE) is not None:
        notes.append(f"Removed the {HUBS_VARIABLE} user variable: {path} sets the hubs now")
        if not dry_run:
            _remove_user_variable(HUBS_VARIABLE)
        changed = True
    return changed, notes


# --- deploying -------------------------------------------------------------------------------------------------------


def deploy(
    request: Request,
    *,
    port: int,
    token: str | None,
    hubs: list[str] | None = None,
    force: bool = False,
    restart: bool = False,
    timeout: float = 120.0,
    python: str | None = None,
    dry_run: bool = False,
) -> str:
    """Makes the daemon run ``request``: installs it if needed, then switches the daemon to it (or only starts it, or
    nothing when it already runs it with these settings). Safe to repeat; works the same on a new rig.

    Args:
        request: The clabe to run.
        port: The daemon's port.
        token: The daemon's token, to read whether it is busy.
        hubs: The hubs to enroll with (``[]``: none); ``None`` leaves the setting as it is.
        force: Restart even while the daemon runs a session (it is interrupted).
        restart: Restart even when nothing changed.
        timeout: Seconds to wait for the daemon to answer.
        python: The Python of a new environment (default: this clabe's).
        dry_run: Say what would be done, and do nothing.

    Raises:
        OSError: The daemon is busy (without ``force``; nothing was changed), the install fails (the daemon keeps
            running), or the daemon does not start (the previous clabe is started again when there is one).
    """
    log: list[str] = []
    info = _install.probe(port, token)
    if info is not None and info.get("status") in ("busy", "leased") and not force:
        what = "runs an experiment" if info["status"] == "busy" else f"is leased by {info.get('leased_by')}"
        raise OSError(f"The daemon {what}: deploying would interrupt it. Wait, or pass --force. Nothing was changed.")

    previous = _install.registered_executable() if _install.task_state() is not None else None
    previous_environment = _environment_of(previous)
    if info is not None and previous is None:
        raise OSError(
            f"Something answers on port {port} but the {_install.TASK_NAME} task is not installed (a daemon started "
            "by hand?): stop it first. Nothing was changed."
        )

    if dry_run:
        existing = read_environment(versions_directory() / request.key) if request.key else None
        log.append(
            f"Would use {existing.describe()} in {existing.path}" if existing else f"Would install {request.spec}"
        )
        if hubs is not None:
            log.extend(f"Would set: {n}" for n in set_hubs(hubs, dry_run=True)[1])
        log.append(f"The daemon now runs {previous or 'nothing (no task)'}; answers: {info is not None}")
        return "\n".join(log)

    environment, new = install_environment(request, python=python)
    log.append(f"{'Installed' if new else 'Already installed:'} {environment.describe()} in {environment.path}")

    settings_changed = False
    if hubs is not None:
        settings_changed, notes = set_hubs(hubs)
        log.extend(notes)

    runs_it = _same_path(previous, environment.clabe)
    if runs_it and info is not None and not settings_changed and not restart:
        log.append(f"The daemon already runs {environment.describe()}: nothing to restart.")
        _mark_activated(environment)
        return "\n".join(log)

    if previous is not None and (info is not None or _install.task_state() == "Running"):
        log.append(_install.stop(port, token, force=True))
    try:
        _install.register([str(environment.clabe), "daemon", "run"])
        log.append(f"The {_install.TASK_NAME} task runs {environment.clabe}")
        log.append(_install.start(port, token, timeout=timeout))
    except OSError as failure:
        if previous is None or _same_path(previous, environment.clabe):
            raise
        try:
            _install.stop(port, token, force=True)
        except OSError:
            pass
        try:
            _install.register([str(previous), "daemon", "run"])
            _install.start(port, token, timeout=timeout)
        except OSError as again:
            raise OSError(
                f"{failure}\nStarting the previous clabe ({previous}) failed too: {again}. The daemon is down."
            ) from failure
        raise OSError(f"{failure}\nThe daemon runs its previous clabe again ({previous}).") from failure
    _mark_activated(environment)
    for removed in prune(active=environment.path):
        log.append(f"Removed {removed}")
    if previous is not None and previous_environment is None and not runs_it:
        log.append(f"(The daemon ran {previous} before: that install is no longer used by the daemon.)")
    return "\n".join(log)
