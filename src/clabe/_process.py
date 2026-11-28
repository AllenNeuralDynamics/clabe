"""How clabe starts processes, and how a run takes the processes it started with it when it ends.

On Windows a process outlives the process that started it, so a run that ends (stopped, crashed, killed from the
Task Manager) would leave Bonsai and the like running, holding the rig's hardware. A run therefore puts itself in a
job object (`contain_children`): Windows ends every process in it when the run's process ends. The few programs
meant to outlive a run are started with `start_detached`, outside the job and the run's console.
"""

import logging
import subprocess
import sys
import webbrowser
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)

#: The creation flag that keeps a console program's window from showing (0 off Windows).
NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_CREATE_SUSPENDED = 0x4
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_DETACHED_PROCESS = 0x8
_KILL_ON_JOB_CLOSE = 0x2000
_BREAKAWAY_OK = 0x800
_EXTENDED_LIMIT_INFORMATION = 9


class Job:
    """A Windows job object: processes added to it, and the processes they start, belong to it.

    Args:
        kill_on_close: End every process in the job when its last handle closes (when its owner's process ends).

    Processes started with `start_detached` may leave it.
    """

    def __init__(self, *, kill_on_close: bool) -> None:
        import ctypes
        from ctypes import wintypes

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = self._kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        info = _ExtendedLimits()
        info.basic.flags = _BREAKAWAY_OK | (_KILL_ON_JOB_CLOSE if kill_on_close else 0)
        if not self._kernel32.SetInformationJobObject(
            wintypes.HANDLE(handle), _EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
        ):
            raise ctypes.WinError(ctypes.get_last_error())

    def add(self, process: int) -> None:
        """Adds the process of handle ``process`` (``-1``: this one)."""
        import ctypes
        from ctypes import wintypes

        if not self._kernel32.AssignProcessToJobObject(wintypes.HANDLE(self._handle), wintypes.HANDLE(process)):
            raise ctypes.WinError(ctypes.get_last_error())

    def contains(self, pid: int) -> bool:
        """Whether process ``pid`` is in the job."""
        import ctypes
        from ctypes import wintypes

        process = self._kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not process:
            return False
        try:
            inside = wintypes.BOOL()
            self._kernel32.IsProcessInJob(wintypes.HANDLE(process), wintypes.HANDLE(self._handle), ctypes.byref(inside))
            return bool(inside.value)
        finally:
            self._kernel32.CloseHandle(wintypes.HANDLE(process))

    def terminate(self, exit_code: int = 1) -> None:
        """Ends every process in the job."""
        from ctypes import wintypes

        self._kernel32.TerminateJobObject(wintypes.HANDLE(self._handle), exit_code)

    def close(self) -> None:
        """Lets go of the job (a ``kill_on_close`` job then ends its processes)."""
        from ctypes import wintypes

        if self._handle:
            self._kernel32.CloseHandle(wintypes.HANDLE(self._handle))
            self._handle = None


def _limit_structures() -> tuple[type, type]:
    """JOBOBJECT_BASIC_LIMIT_INFORMATION and JOBOBJECT_EXTENDED_LIMIT_INFORMATION."""
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        """JOBOBJECT_BASIC_LIMIT_INFORMATION."""

        _fields_ = [
            ("process_time", ctypes.c_int64),
            ("job_time", ctypes.c_int64),
            ("flags", wintypes.DWORD),
            ("min_working_set", ctypes.c_size_t),
            ("max_working_set", ctypes.c_size_t),
            ("active_processes", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority_class", wintypes.DWORD),
            ("scheduling_class", wintypes.DWORD),
        ]

    class Extended(ctypes.Structure):
        """JOBOBJECT_EXTENDED_LIMIT_INFORMATION."""

        _fields_ = [
            ("basic", Basic),
            ("io", ctypes.c_uint64 * 6),
            ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t),
            ("peak_process_memory", ctypes.c_size_t),
            ("peak_job_memory", ctypes.c_size_t),
        ]

    return Basic, Extended


if sys.platform == "win32":
    _, _ExtendedLimits = _limit_structures()

#: The job this process put itself in (`contain_children`); never closed, or it would end this process.
_own_job: Job | None = None


def contain_children() -> Job | None:
    """Puts this process in a job that ends, with it, every process it starts (Windows; elsewhere nothing).

    Called once, at the start of a run. Returns the job, or ``None`` off Windows or when Windows refused.
    """
    global _own_job
    if sys.platform != "win32":
        return None
    if _own_job is not None:
        return _own_job
    try:
        job = Job(kill_on_close=True)
        job.add(-1)
    except OSError as exc:
        logger.warning("The processes this run starts will not end with it: %s", exc)
        return None
    _own_job = job
    return job


def popen_in_job(job: Job | None, args: Sequence[str] | str, **kwargs: Any) -> subprocess.Popen:
    """Starts ``args`` (as `subprocess.Popen`) already in ``job``: nothing it starts can escape it by being early."""
    if job is None or sys.platform != "win32":
        return subprocess.Popen(args, **kwargs)
    import ctypes

    kwargs["creationflags"] = kwargs.get("creationflags", 0) | _CREATE_SUSPENDED
    process = subprocess.Popen(args, **kwargs)
    handle = int(process._handle)  # type: ignore[attr-defined]  # the Windows process handle Popen keeps
    try:
        job.add(handle)
    except OSError as exc:
        logger.warning("%s is not in its job: %s", args, exc)
    finally:
        ctypes.WinDLL("ntdll").NtResumeProcess(ctypes.c_void_p(handle))
    return process


def start_detached(args: Sequence[str], **kwargs: Any) -> subprocess.Popen:
    """Starts ``args`` so that it outlives this process: outside its job and its console, in a session of its own.

    For programs meant to stay open after the run that started them (Waterlog, a browser). Standard streams are
    not inherited.
    """
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL} | kwargs
    if sys.platform != "win32":
        return subprocess.Popen(args, start_new_session=True, **kwargs)
    flags = kwargs.pop("creationflags", 0) | _DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        return subprocess.Popen(args, creationflags=flags | _CREATE_BREAKAWAY_FROM_JOB, **kwargs)
    except PermissionError:  # in a job that does not allow leaving it (not one of clabe's): start it inside
        return subprocess.Popen(args, creationflags=flags, **kwargs)


def open_in_browser(url: str) -> None:
    """Opens ``url`` in the default browser, which stays open when this process ends."""
    if sys.platform != "win32":
        webbrowser.open(url)
        return
    try:
        start_detached(["explorer.exe", url])  # the shell opens it: the browser is not a child of this run
    except OSError as exc:
        logger.debug("Could not open %s through the shell (%s); using webbrowser", url, exc)
        webbrowser.open(url)
