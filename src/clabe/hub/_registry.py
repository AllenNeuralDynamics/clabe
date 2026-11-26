"""The rigs the hub follows: those that enrolled (``POST /api/hub/enroll``), and the patterns that keep rigs out.

Both live in SQLite (``<data_dir>/hub.db``, next to the collected reports), so they survive restarts of the hub. A
rig's daemon token, once entered, is kept there too, in plain text: the API sets and deletes it, never shows it.
"""

import datetime
import fnmatch
import sqlite3
import threading
from pathlib import Path

from pydantic import BaseModel, Field

#: What a rig's name may contain (it is part of the hub's URLs: ``/rigs/<name>/``).
RIG_ID_PATTERN = r"^[A-Za-z0-9_.-]+$"


class RigConfig(BaseModel):
    """A rig the hub follows."""

    id: str = Field(description="Short name used in the hub's URLs, e.g. rig-3", pattern=RIG_ID_PATTERN)
    url: str = Field(description="The rig's daemon, e.g. http://rig-3:24070")
    clabe_version: str | None = Field(default=None, description="The clabe of the rig's daemon, when it enrolled")
    enrolled_at: datetime.datetime | None = Field(default=None, description="When it first enrolled")
    last_enrolled_at: datetime.datetime | None = Field(default=None, description="When it last enrolled")
    moved_from: str | None = Field(
        default=None,
        description="Where the rig was confirmed, when it enrolled from another address since: until someone "
        "confirms the move, the hub sends it no token and does not forward requests to it",
    )


class Exclusion(BaseModel):
    """A pattern that keeps rigs out of the hub."""

    pattern: str = Field(description="A glob (e.g. BEH.*), matched without case against a rig's name and host")
    comment: str | None = Field(default=None, description="Why it is excluded")
    added_at: datetime.datetime


def matches(pattern: str, *values: str) -> bool:
    """Whether glob ``pattern`` matches any of ``values``, ignoring case."""
    pattern = pattern.lower()
    return any(fnmatch.fnmatchcase(value.lower(), pattern) for value in values if value)


class Registry:
    """The enrolled rigs and the exclusions, in SQLite; in memory without a ``data_dir``. Safe across threads."""

    def __init__(self, data_dir: Path | None = None) -> None:
        if data_dir is not None:
            Path(data_dir).mkdir(parents=True, exist_ok=True)
        target = str(Path(data_dir) / "hub.db") if data_dir is not None else ":memory:"
        self._lock = threading.Lock()
        self._db = sqlite3.connect(target, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS rigs (id TEXT PRIMARY KEY, url TEXT NOT NULL, clabe_version TEXT, "
                "enrolled_at TEXT, last_enrolled_at TEXT, token TEXT, moved_from TEXT)"
            )
            columns = {row["name"] for row in self._db.execute("PRAGMA table_info(rigs)")}
            for column in ("token", "moved_from"):  # a hub.db made before rigs had them
                if column not in columns:
                    self._db.execute(f"ALTER TABLE rigs ADD COLUMN {column} TEXT")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS exclusions (pattern TEXT PRIMARY KEY, comment TEXT, added_at TEXT NOT NULL)"
            )

    def close(self) -> None:
        """Closes the database."""
        with self._lock:
            self._db.close()

    def rigs(self) -> list[RigConfig]:
        """The enrolled rigs, by name."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, url, clabe_version, enrolled_at, last_enrolled_at, moved_from FROM rigs ORDER BY id"
            ).fetchall()
        return [RigConfig(**dict(row)) for row in rows]

    def tokens(self) -> dict[str, str]:
        """The rigs whose daemon token is entered: their name and token."""
        with self._lock:
            rows = self._db.execute("SELECT id, token FROM rigs WHERE token IS NOT NULL").fetchall()
        return {row["id"]: row["token"] for row in rows}

    def set_token(self, rig_id: str, token: str | None) -> bool:
        """Enters a rig's daemon token, or deletes it (None); whether the rig is enrolled."""
        with self._lock, self._db:
            return self._db.execute("UPDATE rigs SET token = ? WHERE id = ?", (token, rig_id)).rowcount > 0

    def save(self, rig: RigConfig) -> None:
        """Adds a rig, or updates where it is and whether its move is confirmed (its first enrollment time and its
        token are kept)."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO rigs (id, url, clabe_version, enrolled_at, last_enrolled_at, moved_from) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET url = excluded.url, clabe_version = excluded.clabe_version, "
                "last_enrolled_at = excluded.last_enrolled_at, moved_from = excluded.moved_from",
                (
                    rig.id,
                    rig.url,
                    rig.clabe_version,
                    _text(rig.enrolled_at),
                    _text(rig.last_enrolled_at),
                    rig.moved_from,
                ),
            )

    def remove(self, rig_id: str) -> bool:
        """Removes a rig (and its token); whether it was enrolled."""
        with self._lock, self._db:
            return self._db.execute("DELETE FROM rigs WHERE id = ?", (rig_id,)).rowcount > 0

    def exclusions(self) -> list[Exclusion]:
        """The exclusions, oldest first."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM exclusions ORDER BY added_at, pattern").fetchall()
        return [Exclusion(**dict(row)) for row in rows]

    def exclude(self, pattern: str, comment: str | None = None) -> Exclusion:
        """Adds an exclusion (or updates its comment)."""
        exclusion = Exclusion(pattern=pattern, comment=comment, added_at=datetime.datetime.now(datetime.UTC))
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO exclusions (pattern, comment, added_at) VALUES (?, ?, ?) "
                "ON CONFLICT(pattern) DO UPDATE SET comment = excluded.comment",
                (pattern, comment, _text(exclusion.added_at)),
            )
            row = self._db.execute("SELECT * FROM exclusions WHERE pattern = ?", (pattern,)).fetchone()
        return Exclusion(**dict(row))

    def unexclude(self, pattern: str) -> bool:
        """Removes an exclusion; whether there was one."""
        with self._lock, self._db:
            return self._db.execute("DELETE FROM exclusions WHERE pattern = ?", (pattern,)).rowcount > 0

    def excluded_by(self, *values: str) -> Exclusion | None:
        """The first exclusion matching any of ``values`` (a rig's name, its host), if any."""
        return next((e for e in self.exclusions() if matches(e.pattern, *values)), None)


def _text(value: datetime.datetime | None) -> str | None:
    """A time as ISO text in UTC."""
    return value.astimezone(datetime.UTC).isoformat() if value is not None else None
