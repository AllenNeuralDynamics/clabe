"""Reporting an issue without a browser: the exit prompt, LocalClient's reporter, and `reports.report_run`."""

import zipfile

from clabe import reports
from clabe.reports import REPORT_FILE, ReportInfo, list_reports
from clabe.workflow import LocalClient, Run, Workflow

from ..conftest import MockClient


def _finished_run(record_dir=None) -> Run:
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    run = Run(wf, record_dir=record_dir)
    run.start()
    assert run.wait(5)
    return run


def test_report_run_saves_the_comment_and_what_is_known_about_the_run(tmp_path):
    run = _finished_run(tmp_path / "record")
    report = reports.report_run(run, "camera froze", reported_from="tui", directory=tmp_path / "reports")
    assert (report.comment, report.reported_from, report.run_id, report.experiment) == (
        "camera froze",
        "tui",
        run.id,
        "demo",
    )
    with zipfile.ZipFile(tmp_path / "reports" / f"{report.report_id}.zip") as bundle:
        assert ReportInfo.model_validate_json(bundle.read(REPORT_FILE)).comment == "camera froze"


def test_report_run_adds_what_the_launcher_knows(tmp_path):
    class _Launcher:
        def report_info(self):
            return {"subject": "mouse-1", "repository": "vr"}

    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    run = Run(wf, launcher=_Launcher())
    run.start()
    assert run.wait(5)
    report = reports.report_run(run, directory=tmp_path)
    assert (report.subject, report.repository) == ("mouse-1", "vr")


def test_local_client_gives_its_client_a_reporter_for_the_run():
    class _ReportingClient(MockClient):
        reporter = None

        def set_reporter(self, reporter):
            self.reporter = reporter

    client = _ReportingClient()
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    run = Run(wf)
    LocalClient(run, client).drive()
    assert client.reporter is not None
    report_id = client.reporter("from the TUI")  # still works after the run, for the closing prompt
    (report,) = list_reports()
    assert (report.report_id, report.comment, report.reported_from, report.run_id) == (
        report_id,
        "from the TUI",
        "local",
        run.id,
    )


def test_typing_report_at_the_exit_prompt_files_a_report_then_asks_again(mock_base_launcher, mock_frontend):
    mock_frontend._ask_text_mock.side_effect = ["report", "the valve stuck", ""]
    mock_base_launcher._last_run = _finished_run()
    mock_base_launcher._exit(0, timeout=None)

    labels = [call.args[0].label for call in mock_frontend._ask_text_mock.call_args_list]
    assert labels == [
        "Press Enter to exit, or type 'report' to report an issue...",
        "What went wrong? (the run's logs and records are attached)",
        "Press Enter to exit, or type 'report' to report an issue...",
    ]
    (report,) = list_reports()
    assert (report.comment, report.run_id) == ("the valve stuck", mock_base_launcher._last_run.id)


def test_after_a_report_the_exit_prompt_waits_for_the_user(mock_base_launcher, mock_frontend):
    mock_frontend._ask_text_mock.side_effect = ["REPORT ", "x", ""]
    mock_base_launcher._last_run = _finished_run()
    mock_base_launcher._exit(0, timeout=30)

    labels = [call.args[0].label for call in mock_frontend._ask_text_mock.call_args_list]
    assert labels[0] == "Press Enter to exit, or type 'report' to report an issue (closing automatically in 30 s)..."
    assert labels[2] == "Press Enter to exit, or type 'report' to report an issue..."
    assert len(list_reports()) == 1


def test_a_failing_report_is_shown_and_the_launcher_still_exits(mock_base_launcher, mock_frontend, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(reports, "report_run", fail)
    mock_frontend._ask_text_mock.side_effect = ["report", "x", ""]
    mock_base_launcher._last_run = _finished_run()
    notified = []
    monkeypatch.setattr(mock_frontend, "notify", lambda message, level=None: notified.append(message))
    mock_base_launcher._exit(0, timeout=None)
    assert any("disk full" in message for message in notified)


def test_without_a_run_the_exit_prompt_does_not_offer_a_report(mock_base_launcher, mock_frontend):
    mock_base_launcher._exit(0, timeout=None)
    assert mock_frontend._ask_text_mock.call_args.args[0].label == "Press Enter to exit..."
