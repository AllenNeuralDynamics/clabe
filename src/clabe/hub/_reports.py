"""Issue reports the hub collected from its rigs: a SQLite table (``<data_dir>/hub.db``) and their zips
(``<data_dir>/reports/<id>.zip``); and exports opened for a look without being stored."""

import datetime
import json
import logging
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..reports import REPORT_FILE, REPORT_ID_PATTERN, ReportInfo, read_report_info

logger = logging.getLogger(__name__)

#: Where a report is in its triage.
ReportStatus = Literal["new", "looking", "closed"]
#: Columns the reports can be sorted by.
ReportSort = Literal[
    "created_at",
    "received_at",
    "rig_id",
    "hostname",
    "experimenter",
    "subject",
    "session_id",
    "experiment",
    "repository",
    "commit",
    "step",
    "clabe_version",
    "reported_from",
    "comment",
    "status",
    "size",
]
#: Largest file of a report shown as text.
MAX_FILE_SIZE = 20 * 1024 * 1024
#: Largest export accepted for a look.
MAX_INSPECT_SIZE = 500 * 1024 * 1024
#: Seconds an opened export is kept.
INSPECT_TTL = 3600.0

_INFO_COLUMNS = list(ReportInfo.model_fields)
_COLUMNS = [*_INFO_COLUMNS, "rig_id", "received_at", "status", "zip_path", "size"]
_TEXT_COLUMNS = [c for c in _COLUMNS if c not in ("size", "zip_path")]


class StoredReport(ReportInfo):
    """A report the hub collected: its `ReportInfo`, the rig it came from, and where it is in its triage."""

    rig_id: str = Field(description="The rig it was collected from")
    received_at: datetime.datetime = Field(description="When the hub collected it")
    status: ReportStatus = "new"
    size: int = Field(description="Size of its zip, in bytes")


class ReportPage(BaseModel):
    """A page of reports, and how many match in all."""

    total: int
    items: list[StoredReport]


class ZipEntry(BaseModel):
    """A file in a report's zip."""

    path: str
    size: int = Field(description="Uncompressed size, in bytes")


class FileTooLarge(Exception):
    """A file too large to be shown as text."""


def _quote(column: str) -> str:
    """``column`` as an SQL identifier (``commit`` is a keyword)."""
    return f'"{column}"'


def _utc(value: datetime.datetime) -> str:
    """``value`` as sortable ISO text in UTC (a naive time is taken as UTC)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC).isoformat()


def zip_entries(path: Path) -> list[ZipEntry]:
    """The files of a zip (folders left out), in its order.

    Raises:
        ValueError: Not a zip.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            return [ZipEntry(path=i.filename, size=i.file_size) for i in archive.infolist() if not i.is_dir()]
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Not a zip: {exc}") from exc


def zip_text(path: Path, name: str, limit: int = MAX_FILE_SIZE) -> str:
    """The text of file ``name`` in a zip (undecodable bytes replaced).

    Raises:
        KeyError: No such file in the zip.
        FileTooLarge: Larger than ``limit`` bytes.
    """
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo(name)  # only an entry of the zip: a path is never opened on disk
        if info.is_dir():
            raise KeyError(name)
        if info.file_size > limit:
            raise FileTooLarge(
                f"{name} is {info.file_size / 1e6:.1f} MB; files over {limit / 1e6:.0f} MB are not shown."
            )
        return archive.read(info).decode("utf-8", errors="replace")


class ReportStore:
    """The hub's collected reports: rows in SQLite, zips on disk. Safe to use from several threads."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.zips = self.data_dir / "reports"
        self.zips.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.data_dir / "hub.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        columns = ", ".join(
            f"{_quote(c)} INTEGER" if c == "size" else f"{_quote(c)} TEXT" for c in _COLUMNS if c != "report_id"
        )
        with self._lock, self._db:
            self._db.execute(f"CREATE TABLE IF NOT EXISTS reports (report_id TEXT PRIMARY KEY, {columns})")
            for column in ("rig_id", "created_at", "received_at", "status"):
                self._db.execute(f"CREATE INDEX IF NOT EXISTS reports_{column} ON reports ({_quote(column)})")

    def close(self) -> None:
        """Closes the database."""
        with self._lock:
            self._db.close()

    def has(self, report_id: str) -> bool:
        """Whether report ``report_id`` is stored."""
        with self._lock:
            return self._db.execute("SELECT 1 FROM reports WHERE report_id = ?", (report_id,)).fetchone() is not None

    def add(self, rig_id: str, data: bytes) -> StoredReport | None:
        """Stores a report's zip (its id is that of its ``report.json``); None if it was stored already.

        Raises:
            ValueError: Not a report's zip.
        """
        info = read_report_info(data)
        if not re.fullmatch(REPORT_ID_PATTERN, info.report_id):
            raise ValueError(f"Not a report id: {info.report_id!r}")
        if self.has(info.report_id):
            return None
        path = self.zips / f"{info.report_id}.zip"
        partial = path.with_suffix(".zip.partial")
        partial.write_bytes(data)
        partial.replace(path)
        report = StoredReport(
            **info.model_dump(), rig_id=rig_id, received_at=datetime.datetime.now(datetime.UTC), size=len(data)
        )
        row = self._row(report, path)
        try:
            with self._lock, self._db:
                cursor = self._db.execute(
                    f"INSERT OR IGNORE INTO reports ({', '.join(map(_quote, row))}) VALUES ({', '.join('?' * len(row))})",
                    list(row.values()),
                )
        except sqlite3.Error:
            path.unlink(missing_ok=True)
            raise
        return report if cursor.rowcount else None

    def get(self, report_id: str) -> StoredReport | None:
        """Report ``report_id``, if stored."""
        with self._lock:
            row = self._db.execute("SELECT * FROM reports WHERE report_id = ?", (report_id,)).fetchone()
        return self._report(row) if row is not None else None

    def zip_path(self, report_id: str) -> Path | None:
        """Where report ``report_id``'s zip is, if stored."""
        with self._lock:
            row = self._db.execute("SELECT zip_path FROM reports WHERE report_id = ?", (report_id,)).fetchone()
        return Path(row["zip_path"]) if row is not None else None

    def query(
        self,
        *,
        rig: str | None = None,
        experiment: str | None = None,
        status: ReportStatus | None = None,
        q: str | None = None,
        sort: ReportSort = "created_at",
        order: Literal["asc", "desc"] = "desc",
        limit: int = 100,
        offset: int = 0,
    ) -> ReportPage:
        """The reports matching every filter given, sorted, a page at a time.

        Args:
            rig: Only from this rig.
            experiment: Only of this experiment.
            status: Only in this status.
            q: Only those with this text (any case) in any text column.
            sort: The column to sort by.
            order: ``asc`` or ``desc``.
            limit: At most this many.
            offset: Skipping this many first.
        """
        where, args = [], []
        for column, value in (("rig_id", rig), ("experiment", experiment), ("status", status)):
            if value is not None:
                where.append(f"{_quote(column)} = ?")
                args.append(value)
        if q:
            pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(" + " OR ".join(f"{_quote(c)} LIKE ? ESCAPE '\\'" for c in _TEXT_COLUMNS) + ")")
            args += [pattern] * len(_TEXT_COLUMNS)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        if sort not in ReportSort.__args__:  # type: ignore[attr-defined]
            raise ValueError(f"Cannot sort by {sort!r}")
        direction = "ASC" if order == "asc" else "DESC"
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM reports{clause}", args).fetchone()[0]
            rows = self._db.execute(
                f"SELECT * FROM reports{clause} ORDER BY {_quote(sort)} {direction}, report_id {direction} "
                "LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
        return ReportPage(total=total, items=[self._report(r) for r in rows])

    def set_status(self, report_id: str, status: ReportStatus) -> StoredReport | None:
        """Moves report ``report_id`` to ``status``; None if it is not stored."""
        with self._lock, self._db:
            self._db.execute("UPDATE reports SET status = ? WHERE report_id = ?", (status, report_id))
        return self.get(report_id)

    def delete(self, report_ids: list[str]) -> int:
        """Deletes reports (rows and zips); returns how many there were."""
        deleted = 0
        for report_id in report_ids:
            path = self.zip_path(report_id)
            with self._lock, self._db:
                deleted += self._db.execute("DELETE FROM reports WHERE report_id = ?", (report_id,)).rowcount
            if path is not None:
                path.unlink(missing_ok=True)
        return deleted

    def purge(self, days: float) -> int:
        """Deletes the reports received more than ``days`` days ago; returns how many."""
        cutoff = _utc(datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days))
        with self._lock:
            rows = self._db.execute("SELECT report_id FROM reports WHERE received_at < ?", (cutoff,)).fetchall()
        deleted = self.delete([r["report_id"] for r in rows])
        if deleted:
            logger.info("Deleted %s report(s) older than %s days", deleted, days)
        return deleted

    def counts_by_rig(self) -> dict[str, int]:
        """How many reports are stored for each rig."""
        with self._lock:
            rows = self._db.execute("SELECT rig_id, COUNT(*) AS n FROM reports GROUP BY rig_id").fetchall()
        return {r["rig_id"]: r["n"] for r in rows}

    @staticmethod
    def _row(report: StoredReport, path: Path) -> dict[str, Any]:
        """``report`` as a table row."""
        row: dict[str, Any] = report.model_dump(mode="json")
        row["experimenter"] = json.dumps(report.experimenter)
        row["created_at"], row["received_at"] = _utc(report.created_at), _utc(report.received_at)
        row["zip_path"] = str(path)
        return {c: row[c] for c in _COLUMNS}

    @staticmethod
    def _report(row: sqlite3.Row) -> StoredReport:
        """A table row as a report."""
        values = {c: row[c] for c in _COLUMNS if c != "zip_path"}
        values["experimenter"] = json.loads(values["experimenter"] or "[]")
        return StoredReport.model_validate(values)


class InspectResult(BaseModel):
    """An export opened for a look (not stored): its id, its files, and its ``report.json`` if it has one."""

    inspect_id: str
    files: list[ZipEntry]
    report: ReportInfo | None = None


class InspectCache:
    """Exports opened for a look, kept in a temporary folder for `INSPECT_TTL` seconds after their last use."""

    def __init__(self, directory: Path | None = None, ttl: float = INSPECT_TTL) -> None:
        self._directory = Path(directory) if directory else None
        self.ttl = ttl
        self._lock = threading.Lock()

    @property
    def directory(self) -> Path:
        """Where the exports are kept (a temporary folder, made at first use, unless one was given)."""
        with self._lock:
            if self._directory is None:
                self._directory = Path(tempfile.mkdtemp(prefix="clabe-hub-inspect-"))
            self._directory.mkdir(parents=True, exist_ok=True)
            return self._directory

    def add(self, data: bytes) -> InspectResult:
        """Keeps an export for a look.

        Raises:
            ValueError: Not a zip.
        """
        self.expire()
        inspect_id = uuid.uuid4().hex
        path = self.directory / f"{inspect_id}.zip"
        path.write_bytes(data)
        try:
            files = zip_entries(path)
        except ValueError:
            path.unlink(missing_ok=True)
            raise
        try:
            report = read_report_info(path) if any(f.path == REPORT_FILE for f in files) else None
        except ValueError:
            report = None
        return InspectResult(inspect_id=inspect_id, files=files, report=report)

    def path(self, inspect_id: str) -> Path | None:
        """The zip of an opened export, if still kept (its use keeps it longer)."""
        self.expire()
        if not re.fullmatch(REPORT_ID_PATTERN, inspect_id):
            return None
        path = self.directory / f"{inspect_id}.zip"
        if not path.is_file():
            return None
        path.touch()
        return path

    def expire(self) -> None:
        """Forgets the exports unused for longer than the time they are kept."""
        cutoff = time.time() - self.ttl
        directory = self.directory
        with self._lock:
            for path in directory.glob("*.zip"):
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                except OSError:
                    continue

    def close(self) -> None:
        """Forgets every export."""
        if self._directory is not None:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._directory = None
