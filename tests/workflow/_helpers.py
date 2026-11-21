"""Headless helpers for driving a Run in tests."""

import threading
from typing import Any

from clabe.workflow import Run, Workflow
from clabe.workflow.events import PromptOpened


class ScriptedClient:
    """Answers each prompt with the next scripted action, on its own thread.

    An action is either a value (answered with ``client="test"``) or a callable ``(run, prompt_event)``
    whose return value is recorded in ``results``.
    """

    def __init__(self, run: Run, script) -> None:
        self.run = run
        self.script = list(script)
        self.prompts: list[PromptOpened] = []
        self.results: list[Any] = []
        self.threads: list[threading.Thread] = []
        run.events.subscribe(self._on_event)

    def _on_event(self, event) -> None:
        if isinstance(event, PromptOpened):
            self.prompts.append(event)
            thread = threading.Thread(target=self._respond, args=(event,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def _respond(self, event: PromptOpened) -> None:
        if not self.script:
            self.results.append(AssertionError(f"no scripted answer for prompt {event.kind}: {event.spec}"))
            return
        action = self.script.pop(0)
        if callable(action):
            self.results.append(action(self.run, event))
        else:
            self.results.append(self.run.answer(event.prompt_id, action, client="test"))


def execute(workflow: Workflow, script=(), *, timeout: float = 5.0, **run_kwargs) -> tuple[Run, ScriptedClient]:
    """Runs ``workflow`` to completion, answering prompts from ``script``."""
    run = Run(workflow, **run_kwargs)
    client = ScriptedClient(run, script)
    run.start()
    finished = run.wait(timeout)
    for thread in client.threads:
        thread.join(timeout)
    errors = [result for result in client.results if isinstance(result, Exception)]
    if errors:
        raise errors[0]
    assert finished, "the run did not finish"
    return run, client


def events_of(run: Run, *types) -> list:
    """The run's events of the given types, in order."""
    return [event for event in run.events.since(0) if isinstance(event, types)]
