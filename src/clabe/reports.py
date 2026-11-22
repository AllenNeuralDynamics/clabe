"""Debug bundles and issue reports.

A **bundle** is one zip of everything needed to understand a run so far: its records (``run.json``,
``events.jsonl``, ``outputs.jsonl``, ``launcher.log``), the tail of this machine's daemon log and a ``rig.json``
describing the machine. Users download it (Export logs) or file it as a **report** with a comment (Report an
issue): a report is saved on this machine, under `reports_dir`, until the hub collects it (and then deletes it here).
"""

import datetime
import io
import json
import os
import platform
import re
import shutil
import socket
import uuid
import zipfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "REPORT_FILE",
    "REPORT_ID_PATTERN",
    "ReportInfo",
    "count_reports",
    "delete_report",
    "list_reports",
    "make_bundle",
    "read_report_info",
    "report_path",
    "reports_dir",
    "rig_info",
    "save_report",
]

#: The summary inside every report's zip.
REPORT_FILE = "report.json"
#: What a report id looks like (a uuid4's hex); anything else is refused, so an id never escapes its folder.
REPORT_ID_PATTERN = r"^[0-9a-f]{32}$"
#: Files of a run's record directory put in a bundle (artifacts are not: they can be large).
RECORD_FILES = ("run.json", "events.jsonl", "outputs.jsonl", "launcher.log", "repository-state.json")
#: How much of the daemon's log a bundle keeps (its end).
DAEMON_LOG_TAIL = 512 * 1024


class ReportInfo(BaseModel):
    """What a report is about: who, where, when, and the user's comment. Stored as ``report.json`` in its zip."""

    report_id: str = Field(description="Unique id of the report")
    created_at: datetime.datetime
    comment: str = Field(default="", description="What the user wrote")
    reported_from: str | None = Field(default=None, description="The client the report was made from")
    hostname: str
    clabe_version: str
    run_id: str | None = None
    experiment: str | None = None
    step: str | None = Field(default=None, description="The step the run was on")
    session_id: str | None = Field(default=None, description="The session's name, once registered")
    subject: str | None = None
    experimenter: list[str] = Field(default_factory=list)
    repository: str | None = Field(default=None, description="The experiment repository's name")
    commit: str | None = None


def reports_dir() -> Path:
    """Where this machine keeps reports until the hub collects them (``%LOCALAPPDATA%\\clabe\\reports``)."""
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) / "clabe" if base else Path.home() / ".clabe") / "reports"


def rig_info() -> dict[str, Any]:
    """The machine, as put in ``rig.json``: host, OS, Python, clabe, disk, time."""
    from . import __version__

    usage = shutil.disk_usage(Path.cwd().anchor or "/")
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "clabe_version": __version__,
        "user": os.environ.get("USERNAME") or os.environ.get("USER"),
        "cwd": str(Path.cwd()),
        "disk_free_bytes": usage.free,
        "disk_total_bytes": usage.total,
        "time": datetime.datetime.now(datetime.UTC).isoformat(),
    }


def make_bundle(record_dir: Path | None, *, extra: dict[str, bytes | str] | None = None) -> bytes:
    """A zip of the run's records so far, the daemon log's tail, ``rig.json`` and ``extra`` files (name → content).

    Missing files are skipped: a bundle can be made at any moment of a run, or with no run at all.
    """
    from .daemon._install import default_working_directory

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if record_dir is not None:
            for name in RECORD_FILES:
                path = Path(record_dir) / name
                if path.is_file():
                    archive.write(path, name)
        daemon_log = default_working_directory() / "daemon.log"
        if daemon_log.is_file():
            with daemon_log.open("rb") as stream:
                stream.seek(max(0, daemon_log.stat().st_size - DAEMON_LOG_TAIL))
                archive.writestr("daemon.log", stream.read())
        archive.writestr("rig.json", json.dumps(rig_info(), indent=2))
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def save_report(record_dir: Path | None, info: dict[str, Any], *, directory: Path | None = None) -> ReportInfo:
    """Saves a report (a bundle with ``report.json``) under `reports_dir` for the hub to collect.

    Args:
        record_dir: The run's record directory, if any.
        info: The `ReportInfo` fields known to the caller (comment, run, session, ...); the id, time, host and
            clabe version are filled in.
        directory: Where to save it; by default `reports_dir`.
    """
    from . import __version__

    report = ReportInfo(
        report_id=uuid.uuid4().hex,
        created_at=datetime.datetime.now(datetime.UTC),
        hostname=socket.gethostname(),
        clabe_version=__version__,
        **info,
    )
    target = directory or reports_dir()
    target.mkdir(parents=True, exist_ok=True)
    data = make_bundle(record_dir, extra={REPORT_FILE: report.model_dump_json(indent=2)})
    partial = target / f"{report.report_id}.zip.partial"
    partial.write_bytes(data)
    partial.replace(target / f"{report.report_id}.zip")  # the hub never sees a half-written report
    return report


def list_reports(directory: Path | None = None) -> list[ReportInfo]:
    """The reports saved on this machine and not collected yet, oldest first."""
    target = directory or reports_dir()
    reports = []
    for path in sorted(target.glob("*.zip")) if target.is_dir() else []:
        try:
            with zipfile.ZipFile(path) as archive:
                reports.append(ReportInfo.model_validate_json(archive.read(REPORT_FILE)))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            continue
    return sorted(reports, key=lambda r: r.created_at)


def count_reports(directory: Path | None = None) -> int:
    """How many reports wait on this machine (their zips, without opening them)."""
    target = directory or reports_dir()
    return sum(1 for _ in target.glob("*.zip")) if target.is_dir() else 0


def report_path(report_id: str, directory: Path | None = None) -> Path:
    """The zip of report ``report_id`` on this machine (it may not exist).

    Raises:
        ValueError: ``report_id`` is not a report id.
    """
    if not re.fullmatch(REPORT_ID_PATTERN, report_id):
        raise ValueError(f"Not a report id: {report_id!r}")
    return (directory or reports_dir()) / f"{report_id}.zip"


def delete_report(report_id: str, directory: Path | None = None) -> bool:
    """Deletes report ``report_id`` from this machine; False if there was no such report."""
    path = report_path(report_id, directory)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def read_report_info(data: bytes | Path) -> ReportInfo:
    """The `ReportInfo` of a report's zip (its bytes or its path).

    Raises:
        ValueError: Not a zip, or no valid ``report.json`` in it.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data) if isinstance(data, bytes) else data) as archive:
            return ReportInfo.model_validate_json(archive.read(REPORT_FILE))
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ValueError(f"Not a report: {exc}") from exc
