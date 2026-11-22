"""Sessions the hub saw on its rigs: one row per run, filled in as the run goes (``<data_dir>/hub.db``, table
``sessions``). The hub polls each busy rig's ``/run/api/session``; what is learned later (the session id once
registered, the outcome) is added to the row, and nothing already known is erased."""

import datetime
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

#: Columns the sessions can be sorted by.
SessionSort = Literal[
    "started_at",
    "finished_at",
    "last_seen",
    "rig_id",
    "experiment",
    "subject",
    "session_id",
    "experimenter",
    "status",
    "repository",
]
#: A session the hub stopped seeing before it ended (the rig went on to something else, or the process vanished).
ENDED = "ended"
_FINAL = ("completed", "aborted", "failed", ENDED)
_FACTS = ("session_id", "subject", "experimenter", "repository", "commit", "hostname", "clabe_version")


class StoredSession(BaseModel):
    """A run the hub saw on a rig, as far as it knows."""

    key: str = Field(description="rig_id/run_id: the row's id")
    rig_id: str
    run_id: str
    experiment: str
    status: str = Field(description="running, completed, aborted, failed, or ended (seen no more before it finished)")
    waiting: bool = False
    current_step: str | None = None
    steps_done: int = 0
    steps_total: int = 0
    started_at: datetime.datetime | None = None
    finished_at: datetime.datetime | None = None
    first_seen: datetime.datetime
    last_seen: datetime.datetime
    reason: str | None = None
    hostname: str | None = None
    clabe_version: str | None = None
    session_id: str | None = None
    subject: str | None = None
    experimenter: list[str] = Field(default_factory=list)
    repository: str | None = None
    commit: str | None = None
    reports: int = Field(default=0, description="Issue reports the hub stores for this run")


class SessionPage(BaseModel):
    """A page of sessions, and how many match in all."""

    total: int
    items: list[StoredSession]


_COLUMNS = [c for c in StoredSession.model_fields if c not in ("key", "reports")]
_TEXT = ["rig_id", "run_id", "experiment", "status", "current_step", "reason", "hostname", *_FACTS[:5]]


def _quote(column: str) -> str:
    return f'"{column}"'


def _utc(value: datetime.datetime | str | None) -> str | None:
    """A time as sortable ISO text in UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC).isoformat()


class SessionStore:
    """The sessions the hub saw. Safe to use from several threads."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.data_dir / "hub.db", check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        columns = ", ".join(
            f"{_quote(c)} INTEGER" if c in ("waiting", "steps_done", "steps_total") else f"{_quote(c)} TEXT"
            for c in _COLUMNS
            if c not in ("rig_id", "run_id")
        )
        with self._lock, self._db:
            self._db.execute(
                f"CREATE TABLE IF NOT EXISTS sessions (rig_id TEXT, run_id TEXT, {columns}, PRIMARY KEY (rig_id, run_id))"
            )
            for column in ("started_at", "last_seen", "status", "experiment"):
                self._db.execute(f"CREATE INDEX IF NOT EXISTS sessions_{column} ON sessions ({_quote(column)})")

    def close(self) -> None:
        """Closes the database."""
        with self._lock:
            self._db.close()

    def record(self, rig_id: str, info: dict[str, Any], seen: datetime.datetime | None = None) -> None:
        """Adds or updates the session of a rig's run from its ``/api/session`` (``SessionInfo``)."""
        now = _utc(seen or datetime.datetime.now(datetime.UTC))
        row: dict[str, Any] = {
            "rig_id": rig_id,
            "run_id": str(info["run_id"]),
            "experiment": str(info.get("experiment") or ""),
            "status": str(info.get("status") or "running"),
            "waiting": int(bool(info.get("waiting"))),
            "current_step": info.get("current_step"),
            "steps_done": int(info.get("steps_done") or 0),
            "steps_total": int(info.get("steps_total") or 0),
            "started_at": _utc(info.get("started_at")) or now,
            "finished_at": _utc(info.get("finished_at")),
            "first_seen": now,
            "last_seen": now,
            "reason": info.get("reason"),
        }
        for fact in _FACTS:
            value = info.get(fact)
            row[fact] = json.dumps(list(value)) if fact == "experimenter" and value else value or None
        names = ", ".join(map(_quote, row))

        # a later poll never erases what an earlier one learned (e.g. the session id); the start is the first seen
        def update(column: str) -> str:
            if column == "started_at":
                return f"{_quote(column)} = COALESCE({_quote(column)}, excluded.{_quote(column)})"
            if column in _FACTS:
                return f"{_quote(column)} = COALESCE(excluded.{_quote(column)}, {_quote(column)})"
            return f"{_quote(column)} = excluded.{_quote(column)}"

        updates = ", ".join(update(c) for c in row if c not in ("rig_id", "run_id", "first_seen"))
        with self._lock, self._db:
            self._db.execute(
                f"INSERT INTO sessions ({names}) VALUES ({', '.join('?' * len(row))}) "
                f"ON CONFLICT (rig_id, run_id) DO UPDATE SET {updates}",
                list(row.values()),
            )

    def end_others(self, rig_id: str, current_run_id: str | None) -> int:
        """Marks the rig's sessions still running, other than ``current_run_id``, as ended (seen no more); returns
        how many."""
        with self._lock, self._db:
            return self._db.execute(
                "UPDATE sessions SET status = ?, waiting = 0, finished_at = COALESCE(finished_at, last_seen) "
                "WHERE rig_id = ? AND run_id IS NOT ? AND status NOT IN (?, ?, ?, ?)",
                (ENDED, rig_id, current_run_id, *_FINAL),
            ).rowcount

    def query(
        self,
        *,
        rig: str | None = None,
        experiment: str | None = None,
        status: str | None = None,
        q: str | None = None,
        sort: SessionSort = "started_at",
        order: Literal["asc", "desc"] = "desc",
        limit: int = 100,
        offset: int = 0,
    ) -> SessionPage:
        """The sessions matching every filter given, sorted, a page at a time (``status`` ``live`` is any not
        finished)."""
        where, args = [], []
        for column, value in (("rig_id", rig), ("experiment", experiment)):
            if value is not None:
                where.append(f"{_quote(column)} = ?")
                args.append(value)
        if status == "live":
            where.append(f"status NOT IN ({', '.join('?' * len(_FINAL))})")
            args += list(_FINAL)
        elif status is not None:
            where.append("status = ?")
            args.append(status)
        if q:
            pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(" + " OR ".join(f"{_quote(c)} LIKE ? ESCAPE '\\'" for c in _TEXT) + ")")
            args += [pattern] * len(_TEXT)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        if sort not in SessionSort.__args__:  # type: ignore[attr-defined]
            raise ValueError(f"Cannot sort by {sort!r}")
        direction = "ASC" if order == "asc" else "DESC"
        reports = (
            "(SELECT COUNT(*) FROM reports r WHERE r.run_id = s.run_id AND r.rig_id = s.rig_id)"
            if self._has_reports()
            else "0"
        )
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM sessions s{clause}", args).fetchone()[0]
            rows = self._db.execute(
                f"SELECT s.*, {reports} AS reports FROM sessions s{clause} "
                f"ORDER BY {_quote(sort)} {direction}, last_seen {direction} LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
        return SessionPage(total=total, items=[self._session(r) for r in rows])

    def experiments(self) -> list[str]:
        """The experiments seen, for a filter."""
        with self._lock:
            rows = self._db.execute("SELECT DISTINCT experiment FROM sessions ORDER BY experiment").fetchall()
        return [r[0] for r in rows if r[0]]

    def delete(self, keys: list[str]) -> int:
        """Deletes sessions by key (``rig_id/run_id``); returns how many there were."""
        deleted = 0
        with self._lock, self._db:
            for key in keys:
                rig_id, _, run_id = key.partition("/")
                deleted += self._db.execute(
                    "DELETE FROM sessions WHERE rig_id = ? AND run_id = ?", (rig_id, run_id)
                ).rowcount
        return deleted

    def _has_reports(self) -> bool:
        with self._lock:
            return (
                self._db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'reports'").fetchone()
                is not None
            )

    @staticmethod
    def _session(row: sqlite3.Row) -> StoredSession:
        data = dict(row)
        data["experimenter"] = json.loads(data["experimenter"]) if data.get("experimenter") else []
        data["waiting"] = bool(data.get("waiting"))
        data["key"] = f"{data['rig_id']}/{data['run_id']}"
        return StoredSession(**data)
