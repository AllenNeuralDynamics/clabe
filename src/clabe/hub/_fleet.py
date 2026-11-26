"""The fleet: rigs the hub follows, polled through their daemons' APIs."""

import asyncio
import datetime
import logging
import math
import re
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

import httpx
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import SettingsConfigDict

from ..reports import REPORT_ID_PATTERN, read_report_info
from ..server._models import SessionInfo
from ..settings import EnvSettings
from ._registry import RIG_ID_PATTERN, Exclusion, Registry, RigConfig

if TYPE_CHECKING:
    from ._reports import ReportStore

logger = logging.getLogger(__name__)


class HubSettings(EnvSettings):
    """Settings of ``clabe hub run``, from environment variables without a prefix (the hub runs in its own container)
    or a ``.env`` file in the working directory (see ``docker/hub/.env.example``). Rigs are not listed here: each
    rig's daemon enrolls itself (its ``DAEMON_CLABE_HUBS``), and the hub keeps them in its database:

    ```bash
    TOKEN=...                             # the hub's own
    RIG_TOKEN=...                         # the daemon token of rigs without their own (set on the page)
    ENROLL_TOKEN=...                      # what rigs present to enroll (their DAEMON_CLABE_ENROLL_TOKEN)
    ```
    """

    __yml_section__: ClassVar[str | None] = "hub"
    __env_prefix__: ClassVar[str | None] = ""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    host: str = Field(default="0.0.0.0", description="Interface to bind")
    port: int = Field(default=8080, description="Port of the hub")
    token: SecretStr | None = Field(default=None, description="Token required by the hub's API")
    rig_token: SecretStr | None = Field(
        default=None, description="The daemon token (DAEMON_CLABE_TOKEN) of rigs without one of their own"
    )
    enroll_token: SecretStr | None = Field(
        default=None, description="Token rigs present to enroll; any rig that reaches the hub can enroll when unset"
    )
    poll_interval: float = Field(default=2.0, description="Seconds between polls of each rig")
    data_dir: Path = Field(
        default=Path("hub-data"), description="Where the hub keeps collected issue reports (hub.db and reports/)"
    )
    report_interval: float = Field(default=5.0, description="Seconds between collections of a rig's issue reports")
    report_retention_days: int | None = Field(
        default=None, description="Collected reports older than this many days are deleted; kept forever if unset"
    )


class EnrollBody(BaseModel):
    """What a rig's daemon sends to enroll: its name, and where the hub reaches it."""

    id: str = Field(description="The rig's name in the hub (its URLs: /rigs/<id>/)", pattern=RIG_ID_PATTERN)
    host: str = Field(description="Where the hub reaches the daemon: a hostname or an IP address", min_length=1)
    port: int = Field(description="The daemon's port", ge=1, le=65535)
    clabe_version: str | None = Field(default=None, description="The daemon's clabe")

    @field_validator("host")
    @classmethod
    def _plain_host(cls, value: str) -> str:
        """A host only: no scheme, port, path or credentials."""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+|\[[0-9A-Fa-f:.]+\]", value):
            raise ValueError(f"Not a hostname or an IP address: {value!r}")
        return value


class Excluded(Exception):
    """A rig an exclusion keeps out."""

    def __init__(self, exclusion: Exclusion) -> None:
        super().__init__(
            f"Excluded by {exclusion.pattern!r}" + (f" ({exclusion.comment})" if exclusion.comment else "")
        )
        self.exclusion = exclusion


class Waiting(BaseModel):
    """A prompt a rig's run is waiting on."""

    prompt_id: str
    kind: str
    label: str
    step: str | None
    since: datetime.datetime


class RigStatus(BaseModel):
    """What the hub last saw of a rig."""

    id: str
    url: str
    reachable: bool = False
    error: str | None = None
    hostname: str | None = None
    clabe_version: str | None = None
    status: Literal["offline", "idle", "busy", "leased"] = "offline"
    leased_by: str | None = None
    experiment: str | None = None
    repository: str | None = None
    run_status: str | None = None
    current_step: str | None = None
    current_step_title: str | None = None
    header: str | None = None
    waiting: Waiting | None = None
    session: SessionInfo | None = Field(
        default=None, description="The run's session so far (subject, experimenter, session id, progress), when it runs"
    )
    seen_at: datetime.datetime | None = None
    reports_waiting: int = Field(default=0, description="Issue reports on the rig, not collected yet")
    reports_stored: int = Field(default=0, description="Issue reports from this rig stored in the hub")
    has_token: bool = Field(default=False, description="Whether this rig's token is entered in the hub")
    needs_token: bool = Field(default=False, description="Whether the rig answers but refuses the hub's token")


class Fleet:
    """Polls every enrolled rig's daemon (and, when it runs an experiment, the run) and keeps what it saw.

    Rigs enroll themselves (`enroll`); the registry keeps them, and the exclusions that keep rigs out, across
    restarts. With a report store, the fleet also collects each rig's issue reports (every ``report_interval``
    seconds, when the rig has some): downloads each, stores it, and only then deletes it on the rig.
    """

    def __init__(
        self,
        settings: HubSettings,
        client: httpx.AsyncClient | None = None,
        reports: "ReportStore | None" = None,
        registry: Registry | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry if registry is not None else Registry()
        self.rigs = {rig.id: rig for rig in self.registry.rigs()}
        self.status = {rig.id: RigStatus(id=rig.id, url=rig.url) for rig in self.rigs.values()}
        self._tokens = self.registry.tokens()
        self.reports = reports
        self._client = client or httpx.AsyncClient(timeout=5.0)
        self._task: asyncio.Task | None = None
        self._collected_at: dict[str, float] = {}
        self._purged_at: float | None = None
        self._refused: set[str] = set()

    def rig(self, rig_id: str) -> RigConfig | None:
        """The rig called ``rig_id``."""
        return self.rigs.get(rig_id)

    def rig_token(self, rig_id: str) -> str | None:
        """The token the hub presents to a rig's daemon: the one entered for the rig, else the hub's ``rig_token``."""
        own = self._tokens.get(rig_id)
        if own is not None:
            return own
        token = self.settings.rig_token
        return (token.get_secret_value() if token else None) or None

    def set_token(self, rig_id: str, token: str | None) -> bool:
        """Enters a rig's daemon token (its DAEMON_CLABE_TOKEN), or deletes it (None: the hub's ``rig_token``);
        whether the rig is enrolled."""
        if not self.registry.set_token(rig_id, token):
            return False
        if token is None:
            self._tokens.pop(rig_id, None)
        else:
            self._tokens[rig_id] = token
        return True

    def enroll(self, body: EnrollBody) -> RigConfig:
        """Adds a rig, or updates where it is (a rig enrolls again every few minutes, and after it moves).

        Raises:
            Excluded: An exclusion matches the rig's name or host.
        """
        exclusion = self.registry.excluded_by(body.id, body.host)
        if exclusion is not None:
            raise Excluded(exclusion)
        now = datetime.datetime.now(datetime.UTC)
        known = self.rigs.get(body.id)
        rig = RigConfig(
            id=body.id,
            url=f"http://{body.host}:{body.port}",
            clabe_version=body.clabe_version,
            enrolled_at=known.enrolled_at if known is not None else now,
            last_enrolled_at=now,
        )
        self.registry.save(rig)
        if known is None:
            logger.info("%s enrolled at %s", rig.id, rig.url)
        elif known.url != rig.url:
            logger.info("%s moved from %s to %s", rig.id, known.url, rig.url)
        self.rigs[rig.id] = rig
        if known is None or known.url != rig.url:
            self.status[rig.id] = RigStatus(id=rig.id, url=rig.url)
        return rig

    def remove(self, rig_id: str) -> bool:
        """Removes a rig and its token (it is followed again if its daemon enrolls again); whether it was followed."""
        self.rigs.pop(rig_id, None)
        self.status.pop(rig_id, None)
        self._tokens.pop(rig_id, None)
        return self.registry.remove(rig_id)

    def exclude(self, pattern: str, comment: str | None = None) -> tuple[Exclusion, list[str]]:
        """Keeps out the rigs matching ``pattern`` (now and when they enroll); the exclusion, and the rigs removed."""
        exclusion = self.registry.exclude(pattern, comment)
        excluded = self.registry.excluded_by
        removed = [r.id for r in list(self.rigs.values()) if excluded(r.id, httpx.URL(r.url).host) is not None]
        for rig_id in removed:
            self.remove(rig_id)
        return exclusion, removed

    def statuses(self, statuses: list[RigStatus] | None = None) -> list[RigStatus]:
        """Every rig as last polled (or those given), with how many of its reports the hub stores and whether it has
        its own token."""
        statuses = list(self.status.values()) if statuses is None else statuses
        counts = self.reports.counts_by_rig() if self.reports is not None else {}
        return [
            s.model_copy(update={"reports_stored": counts.get(s.id, 0), "has_token": s.id in self._tokens})
            for s in statuses
        ]

    def inbox(self) -> list[RigStatus]:
        """The rigs waiting for an answer, longest-waiting first."""
        waiting = [s for s in self.status.values() if s.waiting is not None]
        return sorted(waiting, key=lambda s: s.waiting.since)  # type: ignore[union-attr]

    async def poll_once(self, *, collect: bool = False) -> None:
        """Refreshes every rig's status, and collects reports where due (or everywhere, with ``collect``)."""
        await self._purge()
        await asyncio.gather(*(self._poll(rig, collect) for rig in list(self.rigs.values())))

    async def _purge(self) -> None:
        """Deletes the reports older than ``report_retention_days``, at most once an hour."""
        days = self.settings.report_retention_days
        now = time.monotonic()
        if self.reports is None or days is None or (self._purged_at is not None and now - self._purged_at < 3600):
            return
        self._purged_at = now
        try:
            await asyncio.to_thread(self.reports.purge, days)
        except Exception:
            logger.exception("Could not delete old reports")

    async def run(self) -> None:
        """Polls forever."""
        while True:
            await self.poll_once()
            await asyncio.sleep(self.settings.poll_interval)

    def start(self) -> None:
        """Starts polling in the background (on the running loop)."""
        if self._task is None:
            self._task = asyncio.ensure_future(self.run())

    async def stop(self) -> None:
        """Stops polling."""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _get(self, rig: RigConfig, path: str, timeout: float | None = None) -> httpx.Response:
        """GETs ``path`` of a rig's daemon, with its token; raises on an error status."""
        return await self._request(rig, "GET", path, timeout)

    async def _request(self, rig: RigConfig, method: str, path: str, timeout: float | None = None) -> httpx.Response:
        """Sends ``method path`` to a rig's daemon, with its token; raises on an error status."""
        token = self.rig_token(rig.id)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        extra = {"timeout": timeout} if timeout is not None else {}
        response = await self._client.request(method, f"{rig.url.rstrip('/')}{path}", headers=headers, **extra)
        response.raise_for_status()
        return response

    async def _poll(self, rig: RigConfig, collect: bool = False) -> None:
        """Refreshes one rig's status (an unreachable rig is marked offline), then collects its reports if due."""
        status = RigStatus(id=rig.id, url=rig.url, seen_at=datetime.datetime.now(datetime.UTC))
        try:
            info = (await self._get(rig, "/api/daemon/info")).json()
            status.reachable = True
            status.hostname, status.clabe_version = info["hostname"], info["clabe_version"]
            status.status, status.leased_by = info["status"], info.get("leased_by")
            status.reports_waiting = int(info.get("reports_waiting") or 0)
            run = info.get("run") or {}
            if status.status == "busy":
                status.experiment, status.repository = run.get("experiment") or run.get("file"), run.get("repository")
                if run.get("status") == "running":
                    await self._poll_run(rig, status)
                    status.session = await self._session(rig)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                status.needs_token = True
                status.error = "It refuses the hub's token: set this rig's token (its DAEMON_CLABE_TOKEN)"
            else:
                status.error = str(exc) or type(exc).__name__
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            status.error = str(exc) or type(exc).__name__
        if status.error is not None:
            previous = self.status.get(rig.id)
            if previous is not None and previous.reachable:
                logger.warning("Lost %s: %s", rig.id, status.error)
        if self.rigs.get(rig.id) is not rig:
            return  # removed, excluded or moved while it was polled: what was seen is not this rig's any more
        self.status[rig.id] = status
        now = time.monotonic()
        due = collect or now - self._collected_at.get(rig.id, -math.inf) >= self.settings.report_interval
        if self.reports is not None and status.reachable and status.reports_waiting and due:
            self._collected_at[rig.id] = now
            await self.collect(rig)

    async def collect(self, rig: RigConfig) -> int:
        """Collects a rig's issue reports: downloads each one not stored yet, stores it, then deletes it on the rig
        (one already stored is just deleted there). Failures are logged and retried next time.

        Returns:
            How many reports were stored.
        """
        if self.reports is None:
            return 0
        try:
            listed = (await self._get(rig, "/api/daemon/reports")).json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Could not list the reports of %s: %s", rig.id, exc)
            return 0
        stored = 0
        for item in listed:
            report_id = str(item.get("report_id", "")) if isinstance(item, dict) else ""
            if not re.fullmatch(REPORT_ID_PATTERN, report_id) or report_id in self._refused:
                continue
            try:
                stored += await self._collect_one(rig, report_id)
            except (httpx.HTTPError, OSError, sqlite3.Error, ValueError) as exc:  # retried next time
                logger.warning("Could not collect report %s of %s: %s", report_id, rig.id, exc)
        return stored

    async def _collect_one(self, rig: RigConfig, report_id: str) -> bool:
        """Stores report ``report_id`` of a rig (unless stored already), then deletes it there."""
        assert self.reports is not None
        added = None
        if not await asyncio.to_thread(self.reports.has, report_id):
            response = await self._get(rig, f"/api/daemon/reports/{report_id}", timeout=120.0)
            try:
                info = read_report_info(response.content)
                if info.report_id != report_id:
                    raise ValueError(f"its report.json is report {info.report_id}")
            except ValueError as exc:
                self._refused.add(report_id)
                logger.warning("Report %s of %s is not a valid report (%s); left on the rig.", report_id, rig.id, exc)
                return False
            added = await asyncio.to_thread(self.reports.add, rig.id, response.content)
            if added is not None:
                logger.info("Collected report %s from %s", report_id, rig.id)
        await self._request(rig, "DELETE", f"/api/daemon/reports/{report_id}")
        return added is not None

    async def _session(self, rig: RigConfig) -> SessionInfo | None:
        """The rig's run as a session (its ``/run/api/session``); None if it cannot tell (e.g. an older clabe)."""
        try:
            return SessionInfo.model_validate((await self._get(rig, "/run/api/session")).json())
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("No session from %s: %s", rig.id, exc)
            return None

    async def _poll_run(self, rig: RigConfig, status: RigStatus) -> None:
        """Adds the run's step, header and open prompt."""
        state = (await self._get(rig, "/run/api/state")).json()
        status.run_status = state["status"]
        status.experiment = state.get("workflow") or status.experiment
        status.current_step = state.get("current_step")
        titles = {s["name"]: s["title"] for s in state.get("steps", [])}
        status.current_step_title = titles.get(status.current_step or "")
        prompt = state.get("prompt")
        if prompt is not None:
            spec = prompt.get("spec") or {}
            status.waiting = Waiting(
                prompt_id=prompt["prompt_id"],
                kind=prompt["kind"],
                label=str(spec.get("label") or spec.get("title") or spec.get("message") or prompt["kind"]),
                step=prompt.get("step"),
                since=prompt["ts"],
            )
