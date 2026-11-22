"""The fleet: rigs the hub follows, polled through their daemons' APIs."""

import asyncio
import datetime
import logging
from typing import Annotated, ClassVar, Literal

import httpx
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import NoDecode, SettingsConfigDict

from ..services import EnvSettings, split_list

logger = logging.getLogger(__name__)


class RigConfig(BaseModel):
    """A rig the hub follows."""

    id: str = Field(description="Short name used in the hub's URLs, e.g. rig-3", pattern=r"^[A-Za-z0-9_.-]+$")
    url: str = Field(description="The rig's daemon, e.g. http://rig-3:8070")
    token: SecretStr | None = Field(default=None, description="The rig daemon's token; defaults to the hub's rig_token")


class HubSettings(EnvSettings):
    """Settings of ``clabe hub run``, from environment variables without a prefix (the hub runs in its own container)
    or a ``.env`` file in the working directory (see ``docker/hub/.env.example``). Rigs are ``id=url`` items separated
    by ``;`` (or a JSON list, for per-rig tokens):

    ```bash
    TOKEN=...                             # the hub's own
    RIG_TOKEN=...                         # the rigs' daemon token (their DAEMON_CLABE_TOKEN)
    RIGS=rig-3=http://rig-3:8070;rig-4=http://rig-4:8070
    ```
    """

    __yml_section__: ClassVar[str | None] = "hub"
    __env_prefix__: ClassVar[str | None] = ""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    host: str = Field(default="0.0.0.0", description="Interface to bind")
    port: int = Field(default=8080, description="Port of the hub")
    token: SecretStr | None = Field(default=None, description="Token required by the hub's API")
    rig_token: SecretStr | None = Field(default=None, description="The rigs' daemon token, unless a rig has its own")
    rigs: Annotated[list[RigConfig], NoDecode] = Field(default_factory=list, description="The rigs to follow")
    poll_interval: float = Field(default=2.0, description="Seconds between polls of each rig")

    @model_validator(mode="after")
    def _shared_rig_token(self) -> "HubSettings":
        """Rigs without their own token use ``rig_token``."""
        for rig in self.rigs:
            if rig.token is None:
                rig.token = self.rig_token
        return self

    @field_validator("rigs", mode="before")
    @classmethod
    def _rigs(cls, value: object) -> object:
        """``rig-3=http://rig-3:8070;rig-4=...``, or a JSON list of ``{id, url, token}``."""
        value = split_list(value)
        if isinstance(value, list):
            return [_rig(item) if isinstance(item, str) else item for item in value]
        return value


def _rig(item: str) -> dict[str, str]:
    """``id=url`` as a rig."""
    rig_id, sep, url = item.partition("=")
    if not sep:
        raise ValueError(f"A rig is written id=url, got {item!r}.")
    return {"id": rig_id.strip(), "url": url.strip()}


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
    seen_at: datetime.datetime | None = None


def rig_token(rig: RigConfig) -> str | None:
    """The token of a rig's daemon (its own, else the hub's ``rig_token``)."""
    return rig.token.get_secret_value() if rig.token else None


class Fleet:
    """Polls every rig's daemon (and, when it runs an experiment, the run) and keeps what it saw."""

    def __init__(self, settings: HubSettings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.rigs = {rig.id: rig for rig in settings.rigs}
        self.status = {rig.id: RigStatus(id=rig.id, url=rig.url) for rig in settings.rigs}
        self._client = client or httpx.AsyncClient(timeout=5.0)
        self._task: asyncio.Task | None = None

    def rig(self, rig_id: str) -> RigConfig | None:
        """The rig called ``rig_id``."""
        return self.rigs.get(rig_id)

    def inbox(self) -> list[RigStatus]:
        """The rigs waiting for an answer, longest-waiting first."""
        waiting = [s for s in self.status.values() if s.waiting is not None]
        return sorted(waiting, key=lambda s: s.waiting.since)  # type: ignore[union-attr]

    async def poll_once(self) -> None:
        """Refreshes every rig's status."""
        await asyncio.gather(*(self._poll(rig) for rig in self.rigs.values()))

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

    async def _get(self, rig: RigConfig, path: str) -> httpx.Response:
        """GETs ``path`` of a rig's daemon, with its token; raises on an error status."""
        token = rig_token(rig)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = await self._client.get(f"{rig.url.rstrip('/')}{path}", headers=headers)
        response.raise_for_status()
        return response

    async def _poll(self, rig: RigConfig) -> None:
        """Refreshes one rig's status; an unreachable rig is marked offline."""
        status = RigStatus(id=rig.id, url=rig.url, seen_at=datetime.datetime.now(datetime.UTC))
        try:
            info = (await self._get(rig, "/api/daemon/info")).json()
            status.reachable = True
            status.hostname, status.clabe_version = info["hostname"], info["clabe_version"]
            status.status, status.leased_by = info["status"], info.get("leased_by")
            run = info.get("run") or {}
            if status.status == "busy":
                status.experiment, status.repository = run.get("experiment") or run.get("file"), run.get("repository")
                if run.get("status") == "running":
                    await self._poll_run(rig, status)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            status.error = str(exc) or type(exc).__name__
            previous = self.status.get(rig.id)
            if previous is not None and previous.reachable:
                logger.warning("Lost %s: %s", rig.id, status.error)
        self.status[rig.id] = status

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
