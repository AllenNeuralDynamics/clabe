"""What a run is about, at a glance: the workflow, where it runs, the rig, the subject and the experimenters."""

import logging
import os
import socket
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ._run import Run

logger = logging.getLogger(__name__)

#: The environment variable naming the rig on AIND machines (see `clabe.aind.validators.get_aind_rig_name`).
RIG_NAME_ENV_VAR = "aibs_comp_id"


class RunContext(BaseModel):
    """What a run is about, as far as known so far: every client shows it prominently (the TUI's header, the web
    UI's summary strip).

    The subject and experimenters are known once the session is (``launcher.register_session``, or a step returning
    a session). The rig is the ``aibs_comp_id`` environment variable of the machine running the run, and only that:
    a workflow cannot set it.
    """

    workflow: str
    hostname: str
    rig: str | None = Field(default=None, description="The rig's name, once known")
    subject: str | None = Field(default=None, description="The subject's id, once the session is known")
    experimenter: list[str] = Field(default_factory=list, description="The experimenters, once the session is known")


def describe_run(run: "Run", outputs: list[Any]) -> RunContext:
    """The context of ``run`` from its launcher, the environment and the outputs of its finished steps; never raises.

    Args:
        run: The run.
        outputs: The outputs of its finished steps, in workflow order (a later step's session wins).
    """
    subject: str | None = None
    experimenter: list[str] = []
    for output in outputs:
        found_subject = _text(getattr(output, "subject", None))
        if found_subject is not None:
            subject = found_subject
            experimenter = _names(getattr(output, "experimenter", None))
    known = _launcher_session(run.launcher)
    subject = known.get("subject") or subject
    experimenter = known.get("experimenter") or experimenter
    return RunContext(
        workflow=run.workflow.name,
        hostname=socket.gethostname(),
        rig=os.environ.get(RIG_NAME_ENV_VAR) or None,
        subject=subject,
        experimenter=experimenter,
    )


def _launcher_session(launcher: Any) -> dict[str, Any]:
    """The subject and experimenters of the session the launcher registered, if any."""
    try:
        session = getattr(launcher, "session", None)  # a launcher raises until a session is registered
    except Exception:  # noqa: BLE001 -- e.g. ValueError before a session is registered; cosmetic either way
        return {}
    if session is None:
        return {}
    try:
        subject = _text(getattr(session, "subject", None))
        return {"subject": subject, "experimenter": _names(getattr(session, "experimenter", None))}
    except Exception:  # cosmetic: a session that cannot be read leaves the context as it was
        logger.debug("Could not read the launcher's session", exc_info=True)
        return {}


def _text(value: Any) -> str | None:
    """``value`` (text or a number) as non-empty text, or ``None``."""
    if not isinstance(value, str | int) or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _names(value: Any) -> list[str]:
    """``value`` (a name or a list of names) as a list of non-empty names."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    try:
        return [name for name in (_text(item) for item in value) if name is not None]
    except TypeError:
        return []
