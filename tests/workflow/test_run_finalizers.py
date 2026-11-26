"""Run finalizers: called once with the final status, before RunFinished."""

from clabe.workflow import Abort, Run, RunStatus, Workflow
from clabe.workflow.events import RunFinished


def test_finalizers_get_the_final_status_and_one_failing_does_not_stop_the_others():
    calls = []
    wf = Workflow("demo")

    @wf.step
    def a(run):
        run.add_finalizer(lambda status: calls.append(("first", status, run.events.since(0)[-1].type)))
        run.add_finalizer(lambda status: 1 / 0)
        run.add_finalizer(lambda status: calls.append(("third", status)))
        raise Abort("stop")

    run = Run(wf)
    run.start()
    assert run.wait(10) and run.status is RunStatus.ABORTED
    assert calls[0][:2] == ("first", RunStatus.ABORTED) and calls[0][2] != "run_finished"
    assert calls[1] == ("third", RunStatus.ABORTED)
    assert isinstance(run.events.since(0)[-1], RunFinished)
