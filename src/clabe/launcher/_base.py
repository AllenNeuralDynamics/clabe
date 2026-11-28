import contextlib
import datetime
import hashlib
import logging
import os
import random
import shutil
import socket
import threading
import time
from collections.abc import Callable, Generator
from pathlib import Path, PureWindowsPath
from typing import Self, TypeVar

import git.exc
import pydantic
from aind_behavior_services import Session

from .. import __version__, reports, ui
from .. import logging as clabe_logging
from ..logging.otel import bind_session, record_exception, run_span
from ..repository import GitRepository
from ..runnable import set_include_timing
from ..settings import TMP_DIR
from ..ui import Client, ClientSession, MessageLevel, PromptCancelledError, make_client
from ..utils import abspath, format_datetime, utcnow
from ..workflow import ExperimentCallable, LocalClient, Run, RunStatus, Workflow, as_workflow
from ._cli import LauncherCliArgs
from ._manifest import RunManifest, collect_manifest, finish_manifest, write_manifest

logger = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=pydantic.BaseModel)
TLauncher = TypeVar("TLauncher", bound="Launcher")


class Launcher:
    """
    Base class for experiment launchers. Provides functionality for managing
    configuration files, directories, and experiment execution.

    This class serves as the foundation for launcher implementations, providing
    session management, directory handling, validation, and lifecycle management.

    Properties:
        logger: The logger instance used by the launcher
        settings: The launcher configuration settings
        session: The registered session model
        session_directory: The path to the session directory

    Methods:
        register_session: Registers a session model with the launcher
        run_experiment: Main entry point for launcher execution
        copy_logs: Closes file handlers and copies temporary data
        make_header: Creates a formatted header string for the launcher
        validate: Validates dependencies required for the launcher
        save_temp_model: Saves a temporary JSON representation of a schema model
        create_directory: Creates a directory at the specified path
    """

    def __init__(
        self,
        *,
        settings: LauncherCliArgs,
        attached_logger: logging.Logger | None = None,
        client: Client | None = None,
    ) -> None:
        """
        Initializes the Launcher instance.

        Args:
            settings: The settings for the launcher
            attached_logger: An attached logger instance. Defaults to None
            client: The user interface (console, TUI, ...). Defaults to the backend selected by
                ``settings.frontend``
        """
        self._settings = settings
        # Outside a run, library code asks and informs the user through the launcher's client (see ``clabe.ui``);
        # a run binds itself while it executes.
        self._ui_session = ClientSession(client or make_client(settings.frontend))
        self._ui_binding = ui.bind(self._ui_session)
        self._ui_binding.__enter__()
        self.temp_dir = Path(TMP_DIR) / format_datetime(utcnow())
        self.computer_name = os.environ.get("COMPUTERNAME") or socket.gethostname()
        self._data_directory: Path | None = None
        repository_dir = (
            Path(self.settings.repository_directory) if self.settings.repository_directory is not None else None
        )
        self.repository = GitRepository() if repository_dir is None else GitRepository(path=repository_dir)

        self._ensure_directory_structure()

        # Solve logger.
        if attached_logger:
            _logger = clabe_logging.add_file_handler(attached_logger, self.temp_dir / "launcher.log")
        else:
            root_logger = logging.getLogger()
            _logger = clabe_logging.add_file_handler(root_logger, self.temp_dir / "launcher.log")

        # Map verbosity flags to the console log level; debug mode also times every runnable's notification.
        set_include_timing(settings.debug_mode)
        if settings.debug_mode:
            display_level = logging.DEBUG
        elif settings.verbose:
            display_level = logging.INFO
        elif settings.quiet:
            display_level = logging.ERROR
        else:
            display_level = logging.WARNING

        _logger.setLevel(logging.DEBUG if settings.debug_mode else logging.INFO)
        clabe_logging.set_console_level(display_level)

        self._logger = _logger

        self._session: Session | None = None
        self._extra_session_directories: list[Path] = []  # see add_session_directory
        self._has_copied_logs = False
        self._manifest: RunManifest | None = None
        self._manifest_lock = threading.Lock()
        self._has_run = False
        self._serving = contextlib.ExitStack()  # the run server, kept up until the launcher exits (see _exit)
        self._served_run: Run | None = None
        self._last_run: Run | None = None  # the run an issue report from the exit prompt is about
        self._outcome: str | None = None  # how the run ended: completed, aborted or failed (see _record_outcome)

    @property
    def client(self) -> Client:
        """The user interface this launcher asks and informs through outside a run (and drives the run with)."""
        return self._ui_session.client

    @client.setter
    def client(self, client: Client) -> None:
        """Switches the user interface (used before a run starts)."""
        self._ui_session.client = client

    @property
    def data_directory(self) -> Path:
        """
        Returns the root data directory path.
        """
        if self._data_directory is None:
            raise ValueError("Data directory is not set.")
        return self._data_directory

    @property
    def session_directory(self) -> Path:
        """
        Returns the session directory path.

        Returns:
            Path: The session directory path

        Raises:
            ValueError: If session_name is not set in the session schema
        """
        session = self.session
        if session.session_name is None:
            raise ValueError("session.session_name is not set.")
        else:
            return Path(self.data_directory) / session.session_name

    @property
    def session_directories(self) -> list[Path | PureWindowsPath]:
        """Every session folder of this run: this machine's, then its others, then every other machine's through its
        network share.

        - This machine's other folders are the ones added with `add_session_directory`, in that order.
        - Another machine's folder is listed once it was registered with
          [`RemoteRepository.register_session`][clabe.remote.RemoteRepository.register_session] in a step of this
          run.

        Give it to the watchdog to upload the data of every folder:
        ``WatchdogDataTransferService(source=launcher.session_directories, ...)``.

        Raises:
            ValueError: If session_name is not set in the session schema
        """
        directories: list[Path | PureWindowsPath] = [self.session_directory, *self._extra_session_directories]
        workers = self._manifest.workers if self._manifest is not None else []
        directories += [PureWindowsPath(w.session_directory_unc) for w in workers if w.session_directory_unc]
        return directories

    def add_session_directory(self, path: os.PathLike | str) -> Path:
        """Adds another session folder on this machine to `session_directories`, e.g. where an app on this PC records
        to its own data folder. The folder is created if missing (an existing one, e.g. found after a crash, is fine).

        It is recorded in the run's ``run.json`` (``extra_session_directories``). Logs are still copied to
        `session_directory` only. Adding the session folder, or a folder already added, does nothing.

        Args:
            path: The folder, e.g. ``Path(rig.data_directory) / session.session_name``.

        Returns:
            The folder, as given.

        Raises:
            ValueError: If no session is registered yet.
        """
        folder = Path(path)
        known = [self.session_directory, *self._extra_session_directories]  # raises without a session
        if any(os.path.normcase(os.path.abspath(folder)) == os.path.normcase(os.path.abspath(k)) for k in known):
            return folder
        folder.mkdir(parents=True, exist_ok=True)
        self._extra_session_directories.append(folder)
        logger.info("Added session directory %s", folder)
        if self._manifest is not None:
            self.update_manifest(lambda manifest: manifest.extra_session_directories.append(str(folder)))
        return folder

    def register_session(self, session: Session, data_directory: os.PathLike) -> Self:
        """
        Registers the session model with the launcher, creates the session data
        directory structure, and saves repository-state metadata.

        Args:
            session: The session model to register
            data_directory: The root data directory for the session

        Returns:
            Self: The updated instance

        Raises:
            ValueError: If a session is already registered
        """
        if self._session is None:
            self._session = session
            self._data_directory = Path(data_directory)
            self._ensure_directory_structure()
            self._save_repository_state()
            logger.debug("Creating session directory at: %s", self.session_directory)
            bind_session(session)
        else:
            raise ValueError("Session already registered.")
        return self

    def _save_repository_state(self) -> Path:
        """Save the repository and submodule state with the launcher output."""
        destination = self.temp_dir / "repository-state.json"
        destination.write_text(self.repository.get_metadata().model_dump_json(indent=2), encoding="utf-8")
        logger.debug("Saved repository state to %s", destination)
        return destination

    @property
    def session(self) -> Session:
        """
        Returns the registered session model.

        Returns:
            Session: The session model

        Raises:
            ValueError: If session is not set
        """
        if self._session is None:
            raise ValueError("Session is not set.")
        else:
            return self._session

    def run_experiment(self, experiment: "Workflow | ExperimentCallable") -> None:
        """
        Main entry point for the launcher execution.

        Accepts a [`Workflow`][clabe.workflow.Workflow], or a ``(launcher)`` function (sync or async) run as a workflow of
        one step, and executes it as a run driven by this launcher's client. Unless in debug mode,
        `validate` runs as the run's first step (``clabe_preflight``). The run's records are written to
        ``temp_dir``. Orchestrates the complete launcher workflow including validation, execution and cleanup.

        Steps (including a function's single step) run on a dedicated run thread, not the main thread; code that must
        run on the main thread (e.g. ``signal.signal``, some GUI toolkits) cannot be called from a step.

        A launcher runs a single experiment: create a new launcher for another run.

        Args:
            experiment: The workflow, or the callable taking the launcher as its argument, to run

        Raises:
            RuntimeError: If this launcher has already run an experiment.

        Example:
            ```python
            async def my_experiment(launcher: Launcher):
                # Experiment logic here (sync or async)
                pass

            launcher = Launcher(...)
            launcher.run_experiment(my_experiment)
            ```
        """
        if self._has_run:
            raise RuntimeError("A Launcher runs a single experiment; create a new Launcher for another run.")
        self._has_run = True
        workflow = as_workflow(experiment)
        _code = 0
        # Handle inside run_span so teardown logs stay under the root span (and its attributes).
        with run_span(self, experiment_name=workflow.name):
            try:
                ui.header(self.make_header())
                set_experiment = getattr(self.client, "set_experiment", None)
                if callable(set_experiment):
                    set_experiment(workflow.name)
                logger.info(self._generate_diagnostic_info())

                _code = self._finish(self._run_workflow(workflow))

            except KeyboardInterrupt as e:
                # A second Ctrl+C (the first one aborts the run), or one outside the run.
                logger.error("User interrupted the process.")
                ui.notify("Interrupted by user.", MessageLevel.WARNING)
                record_exception(e)
                self._record_outcome("aborted", "Interrupted by user.", -1)
                _code = -1
            except Exception as e:  # pylint: disable=broad-except
                logger.exception("Launcher failed")
                ui.notify(f"Launcher failed: {e}", MessageLevel.ERROR)
                record_exception(e)
                self._record_outcome("failed", str(e), -1)
                _code = -1
            finally:
                try:
                    self.copy_logs()
                except ValueError as ve:  # In the case session_directory fails
                    ui.notify(f"Failed to copy logs from {self.temp_dir}: {ve}", MessageLevel.ERROR)
                    _code = -1
                    self._record_exit_code(_code)
        try:
            self._exit(_code, timeout=workflow.exit_prompt_timeout, failed=self._outcome == "failed")
        finally:
            self._stop_serving()

    def _run_workflow(self, workflow: Workflow) -> Run:
        """Executes ``workflow`` (with the preflight step) with this launcher, driven by its client."""
        run = Run(self._with_preflight(workflow), launcher=self, record_dir=self.temp_dir)
        self._last_run = run
        self._manifest = collect_manifest(self, run.workflow, run_id=run.id, directory=self.temp_dir)
        self._manifest.extra_session_directories = [str(folder) for folder in self._extra_session_directories]
        self._write_manifest()
        # The server stays up after the run, for its closing screen, until the launcher exits (see _exit).
        self._serving.enter_context(self._served(run))
        LocalClient(run, self.client).drive()
        return run

    @contextlib.contextmanager
    def _served(self, run: Run) -> Generator[None, None, None]:
        """Serves ``run`` over HTTP while the block executes, when the settings ask for it.

        A served run keeps its session open after it finishes (see [`Run.keep_open`][clabe.workflow.Run.keep_open]):
        browsers show its closing screen until `_exit` closes it.
        """
        if not self.settings.serve:
            yield
            return
        from .._extras import FRONTEND_HINT, has_web

        if not has_web():
            # the core works without the frontend extra: the run is just not served
            ui.notify(
                f"Not served in the browser: the web server is not installed ({FRONTEND_HINT}).", MessageLevel.INFO
            )
            yield
            return
        from ..server import RunServer

        token = self.settings.token or os.environ.get("CLABE_TOKEN") or None
        server = RunServer(run, host=self.settings.host, port=self.settings.port, token=token)
        try:
            url = server.start()
        except (OSError, RuntimeError) as exc:
            logger.exception("Could not serve the run")
            ui.notify(f"Could not serve the run over HTTP: {exc}", MessageLevel.WARNING)
            yield
            return
        run.keep_open()
        self._served_run = run
        ui.notify(f"Run served at {url}", MessageLevel.INFO)
        if not getattr(self.client, "interactive", True):
            # a headless client only logs its messages: print the address, so a closed tab can be reopened
            print(f"Run served at {url}", flush=True)
        if self.settings.open_browser:
            from .._process import open_in_browser

            open_in_browser(url)  # the browser stays open after the run
        from ..daemon._announce import announce

        withdraw = announce(
            port=server.port,
            token=token,
            repository=Path(self.repository.working_dir).name,
            experiment=run.workflow.name,
        )
        try:
            yield
        finally:
            self._served_run = None
            if run.wait(0):
                run.close(client="launcher")  # ends the event streams of browsers still open; no-op if closed
            if withdraw is not None:
                withdraw()
            server.stop()

    def _stop_serving(self) -> None:
        """Stops serving the run (closing its session first); idempotent."""
        try:
            self._serving.close()
        except Exception:
            logger.exception("Could not stop serving the run")

    def _with_preflight(self, workflow: Workflow) -> Workflow:
        """``workflow`` with `validate` as its first step, ``clabe_preflight`` (none in debug mode)."""
        if self.settings.debug_mode:
            return workflow

        def clabe_preflight(launcher: "Launcher") -> None:
            launcher.validate()

        variant = workflow.variant(workflow.name)
        if variant.steps:
            return variant.insert_before(variant.steps[0].name, clabe_preflight, title="Preflight checks")
        variant.step(clabe_preflight, title="Preflight checks")
        return variant

    def _finish(self, run: Run) -> int:
        """Reports how ``run`` ended, records it in the run manifest and returns the launcher exit code."""
        code = self._report(run)
        self._record_outcome(run.status.value, run.reason, code)
        return code

    def _report(self, run: Run) -> int:
        """Tells the user how ``run`` ended and returns the launcher exit code."""
        if run.status is RunStatus.COMPLETED:
            return 0
        if run.status is RunStatus.ABORTED:
            logger.error("Run aborted: %s", run.reason)
            ui.notify(f"Run aborted: {run.reason}", MessageLevel.WARNING)
            # Keep the trace red and filterable by type, as a Ctrl+C was before runs existed.
            record_exception(run.error or KeyboardInterrupt(run.reason))
        else:
            logger.error("Run failed: %s", run.reason, exc_info=run.error)
            ui.notify(f"Launcher failed: {run.reason}", MessageLevel.ERROR)
            record_exception(run.error or RuntimeError(run.reason))
        return -1

    def _record_outcome(self, status: str, reason: str | None, exit_code: int) -> None:
        """Adds the outcome to the run manifest, if one was started, and rewrites it."""
        self._outcome = status
        if self._manifest is None:
            return
        finish_manifest(self._manifest, status=status, reason=reason, exit_code=exit_code)
        self._write_manifest()

    def _record_exit_code(self, exit_code: int) -> None:
        """Changes the exit code of the outcome already in the run manifest, if any, and rewrites it."""
        if self._manifest is None or self._manifest.outcome is None:
            return
        self._manifest.outcome.exit_code = exit_code
        self._write_manifest()

    @property
    def manifest(self) -> RunManifest | None:
        """The run manifest (``run.json``) of the run going on, once it has started."""
        return self._manifest

    def update_manifest(self, change: Callable[[RunManifest], None]) -> None:
        """Applies ``change`` to the run manifest and rewrites ``run.json`` (nothing happens without a run).

        For what only becomes known during the run, e.g. the other machines it used (see [`clabe.remote`][clabe.remote]).
        Thread-safe.
        """
        with self._manifest_lock:
            if self._manifest is None:
                return
            change(self._manifest)
            self._write_manifest()

    def _write_manifest(self) -> None:
        """Writes the run manifest; a failure is logged, never raised."""
        if self._manifest is None:
            return
        try:
            write_manifest(self._manifest, self.temp_dir)
        except Exception:
            logger.exception("Could not write the run manifest")

    def copy_logs(self, dst: os.PathLike | None = None, suffix: str = "Behavior/Logs") -> None:
        """
        Closes the file handlers of the launcher and copies the temporary data to the session directory.

        This method is typically called at the end of the launcher by a registered callable that transfers data.

        Args:
            dst: Destination path for logs. If None, uses session_directory/suffix. Defaults to None
            suffix: Suffix to append to session directory path. Defaults to "Behavior/Logs"
        """
        if self._has_copied_logs:
            return

        clabe_logging.close_file_handlers(self._logger)
        if dst is not None:
            out = self._copy_tmp_directory(dst)
        else:
            out = self._copy_tmp_directory(self.session_directory / suffix)
        ui.notify(f"Copied logs to {out}", MessageLevel.SUCCESS)
        self._has_copied_logs = True

    @property
    def logger(self) -> logging.Logger:
        """
        Returns the logger instance used by the launcher.

        Returns:
            logging.Logger: The logger instance
        """
        return self._logger

    @property
    def settings(self) -> LauncherCliArgs:
        """
        Returns the launcher settings.

        Returns:
            LauncherCliArgs: The launcher settings
        """
        return self._settings

    def make_header(self) -> str:
        """
        Creates a formatted header string for the launcher.

        Generates a header containing the CLABE ASCII art logo and version information
        for the launcher and schema models.

        Returns:
            str: The formatted header string
        """
        _HEADER = r"""

         ██████╗██╗      █████╗ ██████╗ ███████╗
        ██╔════╝██║     ██╔══██╗██╔══██╗██╔════╝
        ██║     ██║     ███████║██████╔╝█████╗
        ██║     ██║     ██╔══██║██╔══██╗██╔══╝
        ╚██████╗███████╗██║  ██║██████╔╝███████╗
        ╚═════╝╚══════╝╚═╝  ╚═╝╚═════╝ ╚══════╝

        Command-line-interface Launcher for AIND Behavior Experiments
        Press Control+C to exit at any time.
        """

        _str = (
            f"-------------------------------\n{_HEADER}\nCLABE Version: {__version__}\n-------------------------------"
        )

        return _str

    def _exit(self, code: int = 0, _force: bool = False, timeout: float | None = None, failed: bool = False) -> None:
        """
        Exits the launcher with the specified exit code.

        Performs cleanup operations and exits the application, optionally
        prompting the user before exit.

        When the run is served, its closing screen is shown to every client while the launcher waits: the local
        exit prompt and a browser's Close (``POST /api/close``) race and the first one wins. A headless launcher
        (no one at its screen) waits ``timeout`` seconds for a browser to close the session, and exits at once when
        ``timeout`` is ``None`` or ``0`` (so daemon runs never hang). The run server stops after the wait.

        A run that failed (an error, not an abort) is never closed under someone's eyes: at the launcher's screen
        the prompt waits for them; headless, the closing screen waits while a browser watches the run, and closes
        once none has for ``timeout`` seconds (at once with ``None`` or ``0``).

        Args:
            code: The exit code to use. Defaults to 0
            _force: Whether to force exit without user prompt. Defaults to False
            timeout: Seconds to wait at the exit prompt before exiting anyway.
                ``None`` waits until the user presses Enter; ``0`` skips the
                prompt after a successful run (after a failure or abort, it waits as ``None`` does, so the user sees
                what went wrong). Defaults to None
            failed: The run failed with an error: someone watching has to close it.
        """
        logger.debug("Exiting with code %s", code)
        unwatched_timeout = timeout or 0  # headless, failed: how long nobody may watch before it closes
        if (code != 0 and timeout == 0) or failed:
            timeout = None
        if logger is not None:
            clabe_logging.shutdown_logger(logger)
        run = self._served_run
        interactive = getattr(self.client, "interactive", True)
        if not interactive:
            _force = True  # nobody is at the screen to dismiss an exit prompt
        if run is not None and not interactive and failed:
            self._wait_for_close_while_watched(run, unwatched_timeout)  # headless: whoever watches closes it
        elif run is not None and not interactive and timeout:
            self._wait_for_close(run, timeout)  # headless: give browsers a chance to close the session
        elif not _force and timeout != 0:
            if run is not None:
                run.begin_closing(_deadline(timeout))
            closed_by = "local"
            while True:
                with self._cancel_prompt_after(timeout, run=run):
                    try:
                        answer = ui.text(self._exit_label(timeout), field="exit")
                    except KeyboardInterrupt:
                        break  # a second Ctrl+C at the exit prompt should just exit
                    except PromptCancelledError:
                        closed_by = "timeout"
                        logger.debug("Exit prompt closed: timed out after %s s, or closed by a remote client", timeout)
                        break
                if self._last_run is None or str(answer).strip().lower() != "report":
                    break
                self._report_issue(self._last_run)
                timeout = None  # someone is at the screen: wait for them to leave
            if run is not None:
                run.close(client=closed_by)  # no-op when a remote client closed it first
        self._stop_serving()
        # Tear down the client (e.g. leave the persistent TUI) and stop asking through it.
        self.client.close()
        self._ui_binding.__exit__(None, None, None)

    def _exit_label(self, timeout: float | None) -> str:
        """The exit prompt: Enter exits; after a run, typing ``report`` reports an issue first."""
        label = "Press Enter to exit"
        if self._last_run is not None:
            label += ", or type 'report' to report an issue"
        if timeout is not None:
            label += f" (closing automatically in {timeout:g} s)"
        return label + "..."

    @staticmethod
    def _report_issue(run: Run) -> None:
        """Asks what went wrong and files an issue report about ``run`` (the hub collects it from this machine)."""
        try:
            comment = ui.text("What went wrong? (the run's logs and records are attached)", field="report")
        except (KeyboardInterrupt, PromptCancelledError):
            return
        try:
            report = reports.report_run(run, comment, reported_from="local")
        except Exception as exc:
            logger.exception("Could not save the issue report")
            ui.notify(f"Could not save the issue report: {exc}", MessageLevel.ERROR)
            return
        ui.notify(f"Issue reported ({report.report_id}); the hub collects it from this PC.", MessageLevel.SUCCESS)

    @staticmethod
    def _wait_for_close(run: Run, timeout: float) -> None:
        """Shows the closing screen of the served ``run`` for up to ``timeout`` seconds, until a client closes it."""
        run.begin_closing(_deadline(timeout))
        if not run.wait_closed(timeout):
            run.close(client="timeout")

    @staticmethod
    def _wait_for_close_while_watched(run: Run, unwatched: float, poll: float = 0.5) -> None:
        """Shows the closing screen of the failed, served ``run`` until a client closes it: with no deadline while a
        browser watches the run, so the failure is seen; once none has for ``unwatched`` seconds, it closes, so a
        rig nobody looks at is not kept busy."""
        run.begin_closing(None)
        since: float | None = None  # when the run was last watched
        while not run.wait_closed(poll):
            if run.viewers > 0:
                since = None
                continue
            now = time.monotonic()
            if since is None:
                since = now
            if now - since >= unwatched:
                run.close(client="timeout")
                return

    @contextlib.contextmanager
    def _cancel_prompt_after(self, timeout: float | None, run: Run | None = None) -> Generator[None]:
        """Cancels the client prompt running inside this block once ``timeout`` seconds pass, or once a remote
        client closes ``run``'s session.

        Keeps cancelling until the block exits, so a cancellation that lands before
        the prompt is on screen is not lost. ``None`` never times out.
        """
        if timeout is None and run is None:
            yield
            return
        done = threading.Event()
        deadline = time.monotonic() + timeout if timeout is not None else None

        def _cancel_when_due() -> None:
            while not done.is_set():
                if deadline is not None and time.monotonic() >= deadline:
                    break
                if run is not None and run.closed:
                    break
                done.wait(0.05 if deadline is None else min(0.05, max(0.0, deadline - time.monotonic())))
            while not done.is_set():
                self.client.cancel()
                done.wait(0.1)

        timer = threading.Thread(target=_cancel_when_due, name="clabe-exit-timeout", daemon=True)
        timer.start()
        try:
            yield
        finally:
            done.set()
            timer.join()

    def report_info(self) -> dict[str, object]:
        """What this launcher knows about the session, as [`ReportInfo`][clabe.reports.ReportInfo] fields.

        Session id, subject and experimenter once a session is registered; the repository's folder name and head
        commit. Never raises: what cannot be read is left out.
        """
        info: dict[str, object] = {}
        if self._session is not None:
            info["session_id"] = self._session.session_name
            info["subject"] = self._session.subject
            info["experimenter"] = list(self._session.experimenter or [])
        try:
            info["repository"] = Path(self.repository.working_dir).name
            commit = self.repository.head.commit.hexsha
            if isinstance(commit, str):
                info["commit"] = commit
        except Exception:  # a report must be made even from a broken checkout
            logger.debug("Could not read the repository for a report", exc_info=True)
        return info

    def _generate_diagnostic_info(self) -> str:
        """
        Generates diagnostic information for debugging purposes.

        Outputs detailed information about the launcher state including
        directories, settings, and configuration for troubleshooting.
        """

        try:
            tag = str(self.repository.git.describe("--tags"))
        except git.exc.GitCommandError:
            tag = None

        branch = self.repository.active_branch.name if not self.repository.head.is_detached else "DETACHED"
        separator = "-------------------------------"

        return "\n".join(
            [
                separator,
                f"Current Directory: {os.getcwd()}",
                f"Repository Directory: {self.repository.working_dir}",
                f"Repository Branch: {branch}",
                f"Repository SHA: {self.repository.head.commit.hexsha}",
                f"Repository Tag: {tag}",
                f"Computer Name: {self.computer_name}",
                f"Temporary Directory: {self.temp_dir}",
                f"Settings: {self.settings}",
                separator,
            ]
        )

    def validate(self) -> None:
        """
        Validates the dependencies required for the launcher to run.

        Checks Git repository state, handles dirty repository conditions,
        and ensures all prerequisites are met for experiment execution.

        Example:
            launcher = MyLauncher(...)
            try:
                launcher.validate()
                print("Validation successful")
            except Exception as e:
                print(f"Validation failed: {e}")
        """
        if self.repository.is_dirty():
            logger.warning("Uncommitted files: %s", self.repository.uncommitted_changes())
            ui.notify(
                "Git repository is dirty. Discard changes before continuing unless you know what you are doing!",
                MessageLevel.WARNING,
            )
            if not self.settings.allow_dirty:
                self.repository.try_prompt_full_reset(force_reset=False)
                if self.repository.is_dirty_with_submodules():
                    ui.notify(
                        "Dirty repository not allowed. Exiting. Consider running with --allow-dirty flag.",
                        MessageLevel.ERROR,
                    )
                    raise RuntimeError("Dirty repository not allowed.")

    def _ensure_directory_structure(self) -> None:
        """
        Creates the required directory structure for the launcher.

        Creates data and temporary directories needed for launcher operation,
        exiting with an error code if creation fails.
        """
        # Note: This function should be idempotent!!!

        try:
            # Each session has its own folder: the data directory existing (from earlier sessions) says nothing about it
            if self._data_directory is not None and not os.path.exists(self.session_directory):
                self.create_directory(self.session_directory)

            if not os.path.exists(self.temp_dir):
                self.create_directory(self.temp_dir)

        except (OSError, TimeoutError) as e:
            logger.error("Failed to create directory structure: %s", e)
            raise

    @staticmethod
    def create_directory(directory: os.PathLike) -> None:
        """
        Creates a directory at the specified path if it does not already exist.

        To prevent deadlocks from network issues/auth, this function will run on a separate thread
        and timeout after 2 seconds.

        Args:
            directory: The path of the directory to create

        Raises:
            OSError: If the directory creation fails
            TimeoutError: If the directory creation times out after 2 seconds
        """

        def _create_directory_with_timeout():
            if not os.path.exists(abspath(directory)):
                logger.debug("Creating  %s", directory)
                try:
                    os.makedirs(directory)
                except OSError as e:
                    logger.error("Failed to create directory %s: %s", directory, e)
                    raise

        thread = threading.Thread(target=_create_directory_with_timeout)
        thread.start()
        thread.join(timeout=2.0)

        if thread.is_alive():
            logger.error("Directory creation timed out after 2 seconds")
            raise TimeoutError(f"Failed to create directory {directory} within 2 seconds")

    def _copy_tmp_directory(self, dst: os.PathLike) -> Path:
        """
        Copies the temporary directory to the specified destination.

        Args:
            dst: The destination path for copying the temporary directory

        Returns:
            Path: The destination path where files were copied
        """
        dst = Path(dst) / ".launcher"

        def _copy_with_log_append(src: str, dst: str) -> str:
            """Helper function to append to existing log file instead of overwriting."""
            if Path(src).name == "launcher.log" and Path(dst).exists():
                with open(src, "rb") as src_file, open(dst, "ab") as dst_file:
                    dst_file.write(b"\n")
                    shutil.copyfileobj(src_file, dst_file)
                return dst
            return shutil.copy2(src, dst)

        shutil.copytree(self.temp_dir, dst, dirs_exist_ok=True, copy_function=_copy_with_log_append)
        return dst

    def save_temp_model(self, model: pydantic.BaseModel, directory: os.PathLike | None = None) -> Path:
        """
        Saves a temporary JSON representation of a schema model.

        Args:
            model: The schema model to save
            directory: The directory to save the file in. Defaults to temp_dir if None

        Returns:
            Path: The path to the saved file
        """
        directory = Path(directory) if directory is not None else Path(self.temp_dir)
        os.makedirs(directory, exist_ok=True)

        random_data = str(random.random()).encode("utf-8")
        sha_hash = hashlib.sha256(random_data).hexdigest()[:8]

        fname = f"{model.__class__.__name__}_{sha_hash}.json"
        fpath = os.path.join(directory, fname)
        with open(fpath, "w+", encoding="utf-8") as f:
            f.write(model.model_dump_json(indent=2))
        return Path(fpath)


def _deadline(timeout: float | None) -> datetime.datetime | None:
    """When a wait of ``timeout`` seconds started now ends (``None``: it never does)."""
    if timeout is None:
        return None
    return datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=timeout)
