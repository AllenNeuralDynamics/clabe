"""The run manifest: what ran, where and how — written next to the run's events for reproducibility."""

import ctypes
import datetime
import getpass
import hashlib
import importlib.metadata
import inspect
import logging
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from .. import __version__
from ..constants import KNOWN_CONFIG_FILES
from ..git_manager import GitRepositoryMetadata
from ..utils import get_computer_name

if TYPE_CHECKING:
    from ..workflow import Workflow
    from ._base import Launcher

logger = logging.getLogger(__name__)

#: Above this size, only ``git diff --stat`` is kept for a dirty repository.
MAX_DIFF_BYTES = 5 * 1024 * 1024
_ENV_PREFIXES = ("CLABE_", "OTEL_")
_SECRET_MARKERS = ("HEADER", "TOKEN", "KEY", "SECRET", "PASSWORD", "PASS", "AUTH", "CREDENTIAL")
_SECRET_FLAG_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "PASS", "AUTH", "CREDENTIAL")
_MASK = "***"


class StepSource(BaseModel):
    """Where a step's code lives."""

    name: str
    function: str
    file: str | None = None
    source_sha256: str | None = None


class CodeInfo(BaseModel):
    """The code that ran."""

    clabe_version: str
    argv: list[str]
    cwd: str
    workflow: str
    steps: list[StepSource]
    source_files: dict[str, str] = Field(description="Path → sha256 of every file defining a step.")
    repository: GitRepositoryMetadata
    diff_file: str | None = None
    diff_truncated: bool = False
    untracked_files: list[str] = Field(default_factory=list)


class EnvironmentInfo(BaseModel):
    """The Python environment."""

    python_version: str
    python_implementation: str
    python_executable: str
    uv_version: str | None
    uv_lock_sha256: str | None
    distributions: dict[str, str]


class MachineInfo(BaseModel):
    """The computer."""

    hostname: str
    os: str
    cpu: str
    cpu_count: int | None
    memory_bytes: int | None
    disk_free_bytes: int | None
    timezone: str
    utc_offset_minutes: int
    user: str
    gpus: list[str]


class ConfigInfo(BaseModel):
    """How the launcher was configured."""

    settings: dict[str, Any]
    clabe_yml_files: dict[str, str] = Field(description="Path → sha256 of every clabe.yml file in effect.")
    env: dict[str, str]


class Outcome(BaseModel):
    """How the run ended."""

    status: str
    reason: str | None
    exit_code: int
    finished_at: datetime.datetime


class RunManifest(BaseModel):
    """Everything needed to tell exactly what ran. Sections that could not be collected are ``None``."""

    run_id: str
    started_at: datetime.datetime
    code: CodeInfo | None = None
    environment: EnvironmentInfo | None = None
    machine: MachineInfo | None = None
    config: ConfigInfo | None = None
    outcome: Outcome | None = None
    errors: list[str] = Field(default_factory=list)


def _now() -> datetime.datetime:
    """The current time, in UTC."""
    return datetime.datetime.now(datetime.UTC)


def _sha256(data: bytes) -> str:
    """Hex sha256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    """Hex sha256 of the file at ``path``."""
    return _sha256(path.read_bytes())


def _command(args: list[str]) -> str | None:
    """Output of a short command, or ``None`` if it is missing or fails."""
    if shutil.which(args[0]) is None:
        return None
    try:
        # No console window flashing up when launched from a GUI on Windows.
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        result = subprocess.run(args, capture_output=True, text=True, timeout=5, check=True, **flags)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _is_secret(name: str, markers: tuple[str, ...]) -> bool:
    """Whether ``name`` looks like it holds a secret."""
    return any(marker in name.upper() for marker in markers)


def _mask_argv(argv: list[str]) -> list[str]:
    """``argv`` with the value of every secret-looking flag (``--token x`` or ``--token=x``) masked."""
    masked: list[str] = []
    mask_next = False
    for arg in argv:
        if mask_next and not arg.startswith("-"):
            masked.append(_MASK)
            mask_next = False
            continue
        mask_next = False
        if arg.startswith("-"):
            flag, eq, _ = arg.partition("=")
            if _is_secret(flag, _SECRET_FLAG_MARKERS):
                if eq:
                    arg = f"{flag}={_MASK}"
                else:
                    mask_next = True
        masked.append(arg)
    return masked


def describe_steps(workflow: "Workflow") -> list[StepSource]:
    """Where each step's code lives (legacy experiments resolve to the decorated function).

    Args:
        workflow: The workflow whose steps are described.

    Returns:
        One entry per step, in declaration order.
    """
    sources = []
    for step in workflow.steps:
        func = inspect.unwrap(step.func)
        try:
            text, file = inspect.getsource(func), inspect.getsourcefile(func)
        except (OSError, TypeError):
            text, file = None, None
        sources.append(
            StepSource(
                name=step.name,
                function=f"{getattr(func, '__module__', type(func).__module__)}."
                f"{getattr(func, '__qualname__', type(func).__qualname__)}",
                file=file,
                source_sha256=_sha256(text.encode("utf-8")) if text else None,
            )
        )
    return sources


def _code(launcher: "Launcher", workflow: "Workflow", directory: Path) -> CodeInfo:
    """The ``code`` section; a dirty repository's diff is written to ``directory``."""
    steps = describe_steps(workflow)
    files = {s.file: _file_sha256(Path(s.file)) for s in steps if s.file and Path(s.file).is_file()}
    repository = launcher.repository
    metadata = repository.get_metadata()
    diff_file, truncated, untracked = None, False, []
    if metadata.is_dirty:
        diff = repository.git.diff("HEAD", "--submodule=diff")
        if len(diff.encode("utf-8", errors="replace")) > MAX_DIFF_BYTES:
            diff, truncated = repository.git.diff("HEAD", "--stat"), True
        if diff:  # dirty only through untracked files: nothing to diff
            (directory / "repository.diff").write_text(diff, encoding="utf-8", errors="replace")
            diff_file = "repository.diff"
        untracked = list(repository.untracked_files)
    return CodeInfo(
        clabe_version=__version__,
        argv=_mask_argv(list(sys.argv)),
        cwd=os.getcwd(),
        workflow=workflow.name,
        steps=steps,
        source_files=files,
        repository=metadata,
        diff_file=diff_file,
        diff_truncated=truncated,
        untracked_files=untracked,
    )


def _environment(launcher: "Launcher", directory: Path) -> EnvironmentInfo:
    """The ``environment`` section; the repository's ``uv.lock`` is copied to ``directory``."""
    lock_sha = None
    try:
        lock = Path(launcher.repository.working_dir) / "uv.lock"
        if lock.is_file():
            shutil.copy2(lock, directory / "uv.lock")
            lock_sha = _file_sha256(lock)
    except Exception as exc:  # the rest of the environment is still worth recording
        logger.debug("Could not copy uv.lock: %s", exc, exc_info=True)
    distributions = {
        dist.metadata["Name"]: dist.version for dist in importlib.metadata.distributions() if dist.metadata["Name"]
    }
    return EnvironmentInfo(
        python_version=sys.version,
        python_implementation=platform.python_implementation(),
        python_executable=sys.executable,
        uv_version=_command(["uv", "--version"]),
        uv_lock_sha256=lock_sha,
        distributions=dict(sorted(distributions.items())),
    )


def _total_memory() -> int | None:
    """Physical memory in bytes, without third-party dependencies."""
    if sys.platform == "win32":

        class _MemoryStatus(ctypes.Structure):
            """``MEMORYSTATUSEX``."""

            _fields_ = [  # ctypes layout
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
        return int(status.ullTotalPhys) if ok else None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def _user() -> str:
    """The logged-in user's name; never raises."""
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 -- getuser raises when no user can be determined
        return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


def _machine(launcher: "Launcher") -> MachineInfo:
    """The ``machine`` section."""
    now = datetime.datetime.now().astimezone()
    offset = now.utcoffset() or datetime.timedelta()
    gpus = _command(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"])
    return MachineInfo(
        hostname=get_computer_name(),
        os=platform.platform(),
        cpu=platform.processor() or platform.machine(),
        cpu_count=os.cpu_count(),
        memory_bytes=_total_memory(),
        disk_free_bytes=shutil.disk_usage(launcher.temp_dir).free,
        timezone=str(now.tzinfo),
        utc_offset_minutes=int(offset.total_seconds() // 60),
        user=_user(),
        gpus=gpus.splitlines() if gpus else [],
    )


def _mask_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """``settings`` with the value of every secret-looking key masked, in nested mappings too."""
    masked: dict[str, Any] = {}
    for name, value in settings.items():
        if _is_secret(name, _SECRET_MARKERS):
            masked[name] = _MASK
        elif isinstance(value, dict):
            masked[name] = _mask_settings(value)
        else:
            masked[name] = value
    return masked


def _config(launcher: "Launcher") -> ConfigInfo:
    """The ``config`` section; secret-looking settings and environment variables are masked."""
    paths = [Path(p) for p in KNOWN_CONFIG_FILES]
    if launcher.settings.clabe_yml is not None:
        paths.append(Path(launcher.settings.clabe_yml))
    env = {
        name: _MASK if _is_secret(name, _SECRET_MARKERS) else value
        for name, value in sorted(os.environ.items())
        if name.upper().startswith(_ENV_PREFIXES)
    }
    return ConfigInfo(
        settings=_mask_settings(launcher.settings.model_dump(mode="json")),
        clabe_yml_files={str(p): _file_sha256(p) for p in paths if p.is_file()},
        env=env,
    )


def collect_manifest(launcher: "Launcher", workflow: "Workflow", *, run_id: str, directory: Path) -> RunManifest:
    """Collects the manifest for a run about to start; side files (diff, ``uv.lock``) go to ``directory``.

    Never raises for a section: failures are logged and listed in ``errors``.

    Args:
        launcher: The launcher starting the run.
        workflow: The workflow about to run.
        run_id: Identifier of the run.
        directory: Where side files are written (the run's record directory).

    Returns:
        The manifest, without an outcome.
    """
    manifest = RunManifest(run_id=run_id, started_at=_now())
    sections: dict[str, Callable[[], BaseModel]] = {
        "code": lambda: _code(launcher, workflow, directory),
        "environment": lambda: _environment(launcher, directory),
        "machine": lambda: _machine(launcher),
        "config": lambda: _config(launcher),
    }
    for name, collect in sections.items():
        try:
            setattr(manifest, name, collect())
        except Exception as exc:  # provenance must never stop a launch
            logger.warning("Could not collect %s provenance: %s", name, exc, exc_info=True)
            manifest.errors.append(f"{name}: {type(exc).__name__}: {exc}")
    return manifest


def finish_manifest(manifest: RunManifest, *, status: str, reason: str | None, exit_code: int) -> None:
    """Records how the run ended.

    Args:
        manifest: The manifest to complete.
        status: How the run ended (e.g. ``completed``, ``aborted``, ``failed``).
        reason: Why, when it did not complete.
        exit_code: The launcher's exit code.
    """
    manifest.outcome = Outcome(status=status, reason=reason, exit_code=exit_code, finished_at=_now())


def write_manifest(manifest: RunManifest, directory: Path) -> Path:
    """Writes ``run.json`` to ``directory`` and returns its path.

    Args:
        manifest: The manifest to write.
        directory: The directory to write ``run.json`` into.

    Returns:
        The path of the written file.
    """
    path = directory / "run.json"
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return path
