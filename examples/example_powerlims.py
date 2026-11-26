"""
End-to-end check of :mod:`clabe.stores.powerlims` against a running PowerLIMS API.

It walks through what a rig asks PowerLIMS:

1. What is planned on this rig right now?
2. What is planned on this rig right now *and* has a behavior row for the task?
3. For a planned event, which types of settings does it have, and what are they? FIP settings
   are used as they are; a behavior row's trainer state is deserialized and its stage read.
4. The path a launcher takes: pick a subject from the day's schedule, then resolve its trainer
   state and FIP settings through a ``CompositeStore``.

Nothing is written. Run it against a local server reading the Dev environment (see the
powerlims-api server README). The defaults are a Dev test session (Test_222222 on FRG.12-B,
2026-11-19 09:30, VR foraging with FIP):

    uv run python examples/example_powerlims.py
    # A subject picked from FRG.12-A's schedule:
    uv run python examples/example_powerlims.py --instrument FRG.12-A --subject "" --at 2026-12-03T10:45
    # Dev's dynamic foraging trainer states are incomplete stubs, so this shows the INVALID path (exit 2):
    uv run python examples/example_powerlims.py --instrument FRG.3-D --subject 614175 --task-name AindDynamicForaging --at 2026-12-03T14:45

Exit code: 0 a trainer state was resolved, 1 none applies, 2 PowerLIMS holds invalid settings,
3 the API could not be reached or answered with an error.
"""

import datetime
import sys

from pydantic import Field
from pydantic_settings import BaseSettings, CliApp, SettingsConfigDict

from clabe import ui
from clabe.stores import CompositeStore, Kind, MemoryStore
from clabe.stores.powerlims import (
    BehaviorRow,
    FipRow,
    InvalidSettingsError,
    PlannedEvent,
    PowerLimsClient,
    PowerLimsError,
    PowerLimsSettings,
    PowerLimsStore,
)


class ExampleArgs(BaseSettings):
    """Where to read from and what to resolve."""

    model_config = SettingsConfigDict(cli_kebab_case=True, cli_implicit_flags=True)

    # The defaults point at a test session in the PowerLIMS Dev environment that has a VR foraging
    # trainer state and FIP settings, so the example fetches something when run without arguments.
    base_url: str = Field(default="http://127.0.0.1:8000", description="The PowerLIMS API's root URL.")
    instrument: str = Field(default="FRG.12-B", description="The rig's instrument name, e.g. this machine's.")
    task_name: str = Field(default="AindVrForaging", description="The task whose trainer state to resolve.")
    subject: str | None = Field(
        default="Test_222222", description="The subject. Pass an empty value to pick one from the schedule."
    )
    at: datetime.datetime | None = Field(
        default=datetime.datetime(2026, 11, 19, 9, 30),
        description="The moment to treat as 'now', in local time. Its day is the day the store reads.",
    )
    timeout: float = Field(default=120.0, description="Seconds to wait for each request.")


def describe(event: PlannedEvent) -> None:
    """Story 3: what types of settings an event has, and what they say."""
    start = f"{event.start.astimezone():%H:%M}" if event.start else "?"
    end = f"{event.end.astimezone():%H:%M}" if event.end else "?"
    print(f"  {event.mouse_id}, {start}-{end} on {event.instrument}: settings types {event.settings_types or 'none'}")
    # One case per settings type; a new settings type is a new case.
    for row in event.settings.rows():
        match row:
            case FipRow():
                leds = {
                    nm: v for nm, v in (("415", row.led_415nm), ("470", row.led_470nm), ("565", row.led_565nm)) if v
                }
                fibers = [f"{f.fiber}->{f.patch_cord}" for f in row.fibers if f.enabled]
                print(f"    fip:      LEDs {leds or 'unset'}, fibers {fibers or 'none enabled'}")
            case BehaviorRow():
                trainer_state = row.trainer_state  # already a TrainerState, validated when fetched
                if trainer_state is None:
                    state = "trainer state not fetched" if row.has_trainer_state else "no trainer state"
                else:
                    stage = trainer_state.stage.name if trainer_state.stage else None
                    state = f"stage {stage}, on curriculum: {trainer_state.is_on_curriculum}"
                print(f"    behavior: {row.task_name}, {state}")


def run(args: ExampleArgs) -> int:
    settings = PowerLimsSettings(base_url=args.base_url, timeout=args.timeout)
    client = PowerLimsClient(settings)
    print(f"PowerLIMS: {client.healthcheck()} at {args.base_url}")
    at = (args.at or datetime.datetime.now()).astimezone()

    # 1. Everything planned on this rig right now. Trainer states are large, so not fetched.
    now = client.events_at(at, instrument=args.instrument)
    print(f"\nPlanned on {args.instrument} at {at:%Y-%m-%d %H:%M}: {len(now)} event(s)")
    for event in now:
        describe(event)

    # 2. Narrowed on the server to events with a behavior row for the task, trainer states included.
    with_task = client.events_at(
        at, instrument=args.instrument, settings=[BehaviorRow.where(args.task_name)], expand_settings=True
    )
    print(f"\n...of which with a {args.task_name} behavior row: {len(with_task)}")
    for event in with_task:
        describe(event)

    # 4. The launcher's path, through the store: a subject, then its settings for the session.
    powerlims = PowerLimsStore(client=client, scope={"instrument": args.instrument}, day=at.date())
    scheduled = powerlims.scheduled_subjects()
    print(f"\nScheduled on {args.instrument} on {at:%Y-%m-%d}: {scheduled or 'nobody'}")
    subject = args.subject or None
    if subject is None and scheduled:
        subject = ui.prompt_pick(ui.PickRequest(label="Subject", options=scheduled, default=scheduled[0]))
    if subject is None:
        print("No subject given or picked; pass --subject.")
        return 1

    store = CompositeStore(
        default=MemoryStore(), routes={"trainer_state": powerlims, Kind(FipRow).name: powerlims}
    ).scoped(subject=subject, task_name=args.task_name)

    trainer_state = store.resolve(Kind.from_trainer_state())
    assert trainer_state.stage is not None  # the trainer_state kind rejects states without one
    print(f"\n{args.task_name} trainer state of {subject}:")
    print(f"  stage:         {trainer_state.stage.name}")
    print(f"  curriculum:    {trainer_state.curriculum.name if trainer_state.curriculum else None}")
    print(f"  on curriculum: {trainer_state.is_on_curriculum}")

    # FIP is optional: not every session records photometry.
    fip = store.list(Kind(FipRow))
    print(f"  fip:           {[f'{f.fiber}->{f.patch_cord}' for f in fip[0].fibers if f.enabled] if fip else 'none'}")
    return 0


def main() -> None:
    args = CliApp.run(ExampleArgs)
    ui.set_current_frontend(ui.ConsoleFrontend())
    try:
        code = run(args)
    except InvalidSettingsError as e:
        print(f"\nINVALID: {e}" + (f"\n  cause: {e.__cause__}" if e.__cause__ else ""))
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
