"""
End-to-end check of :mod:`clabe.stores.powerlims` against a running PowerLIMS API.

It walks the path a rig takes at the start of a session:

1. Ask the API for the mice scheduled on this rig on the day (the subject picker's list).
2. Pick a subject: the one given, or one of those scheduled.
3. Fetch the subject's events that day, and show every settings entry on them.
4. Resolve the trainer state, using the task name as the discriminator: only a behavior
   settings row whose ``task_name`` is that task is used, and its trainer state is validated
   as a ``TrainerState`` for that task.

Nothing is written. Run it against a local server (see the powerlims-api server README):

    uv run python examples/example_powerlims.py --instrument FRG.12-B
    uv run python examples/example_powerlims.py --instrument FRG.12-B --subject Test_333333
    uv run python examples/example_powerlims.py --instrument FRG.3-D --task-name AindDynamicForaging
    uv run python examples/example_powerlims.py --instrument FRG.12-A --date 2026-11-20

Exit code: 0 a trainer state was resolved, 1 none applies, 2 PowerLIMS holds an invalid one,
3 the API could not be reached or answered with an error.
"""

import datetime
import logging
import sys
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, CliApp, SettingsConfigDict

from clabe import ui
from clabe.stores import CompositeStore, Kind, MemoryStore
from clabe.stores.powerlims import (
    InvalidTrainerStateError,
    PlannedEvent,
    PowerLimsClient,
    PowerLimsError,
    PowerLimsSettings,
    PowerLimsStore,
)
from clabe.utils import get_computer_name

logger = logging.getLogger(__name__)


class ExampleArgs(BaseSettings):
    """Where to read from and what to resolve."""

    model_config = SettingsConfigDict(cli_kebab_case=True, cli_implicit_flags=True)

    base_url: str = Field(default="http://127.0.0.1:8000", description="The PowerLIMS API's root URL.")
    instrument: str = Field(default_factory=get_computer_name, description="The rig's instrument name.")
    task_name: str = Field(default="AindVrForaging", description="The task whose trainer state to resolve.")
    subject: str | None = Field(default=None, description="The subject. Picked from the schedule when omitted.")
    date: datetime.date | None = Field(default=None, description="The day to look at. Today when omitted.")
    timeout: float = Field(default=120.0, description="Seconds to wait for each request.")


class _OnDayClient(PowerLimsClient):
    """A client whose "today" is a given day, so past and future schedules can be checked."""

    def __init__(self, settings: PowerLimsSettings, day: datetime.datetime) -> None:
        super().__init__(settings)
        self._day = day

    def events_today(self, **kwargs: Any) -> list[PlannedEvent]:
        if kwargs.get("now") is None:
            kwargs["now"] = self._day
        return super().events_today(**kwargs)


def _describe(event: PlannedEvent) -> None:
    """Prints an event and each of its settings entries, as the API sent them."""
    start = event.start.astimezone().strftime("%Y-%m-%d %H:%M") if event.start else "?"
    print(f"  event {event.event_id} at {start} on {event.instrument}: {', '.join(event.event_types)}")
    for event_type, entry in event.settings.items():
        row = entry.get("row") or {}
        if "task_name" in row:
            what = (
                f"behavior row {row.get('id')}, task {row.get('task_name')}, stage {row.get('stage_name')}, "
                f"has_trainer_state={row.get('has_trainer_state')}"
            )
        elif row:
            what = f"row {row.get('id')} ({', '.join(sorted(k for k in row if k not in ('id', 'created_on')))})"
        else:
            what = "no row"
        print(f"    settings[{event_type!r}]: {entry.get('source')} -> {what}")


def run(args: ExampleArgs) -> int:
    day = datetime.datetime.combine(args.date or datetime.date.today(), datetime.time(12)).astimezone()
    client = _OnDayClient(PowerLimsSettings(base_url=args.base_url, timeout=args.timeout), day)
    powerlims = PowerLimsStore(client=client, scope={"instrument": args.instrument})

    print(f"PowerLIMS: {client.healthcheck()} at {args.base_url}")

    # 1. The mice scheduled on this rig that day.
    scheduled = powerlims.scheduled_subjects()
    print(f"\nScheduled on {args.instrument} on {day:%Y-%m-%d}: {scheduled or 'nobody'}")

    # 2. The subject.
    subject = args.subject
    if subject is None:
        if not scheduled:
            print("No subject given and nobody is scheduled; pass --subject.")
            return 1
        subject = ui.prompt_pick(ui.PickRequest(label="Subject", options=scheduled, default=scheduled[0]))
        if subject is None:
            return 1

    # 3. The subject's events that day and their settings, as the API sent them.
    store = powerlims.scoped(subject=subject, task_name=args.task_name)
    print(f"\nEvents of {subject}:")
    for event in store.events():
        _describe(event)

    # 4. The trainer state, routed through a CompositeStore the way a launcher does it.
    #    The task name is the discriminator: only a behavior row for that task is used.
    composite = CompositeStore(default=MemoryStore(), routes={"trainer_state": powerlims}).scoped(
        subject=subject, task_name=args.task_name
    )
    print(f"\nResolving the {args.task_name} trainer state of {subject}:")
    trainer_state = composite.resolve(Kind.from_trainer_state())
    assert trainer_state.stage is not None  # the trainer_state kind rejects states without one
    print(f"  stage:      {trainer_state.stage.name}")
    print(f"  task:       {trainer_state.stage.task.name}")
    print(f"  curriculum: {trainer_state.curriculum.name if trainer_state.curriculum else None}")
    print(f"  on curriculum: {trainer_state.is_on_curriculum}")
    return 0


def main() -> None:
    args = CliApp.run(ExampleArgs)
    ui.set_current_frontend(ui.ConsoleFrontend())
    try:
        code = run(args)
    except InvalidTrainerStateError as e:
        print(f"\nINVALID: {e}")
        if e.__cause__ is not None:
            print(f"  cause: {e.__cause__}")
        code = 2
    except LookupError as e:
        print(f"\nNONE: {e}")
        code = 1
    except PowerLimsError as e:
        print(f"\nAPI ERROR: {e}")
        code = 3
    sys.exit(code)


if __name__ == "__main__":
    main()
