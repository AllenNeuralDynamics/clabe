import contextlib
import hashlib
import logging
import os
import random
import shutil
import socket
import threading
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Self, TypeVar

import git.exc
import pydantic
from aind_behavior_services import Session

from .. import __version__, ui
from .. import logging as clabe_logging
from ..constants import TMP_DIR
from ..git_manager import GitRepository
from ..logging.otel import bind_session, record_exception, run_span
from ..runnable import set_include_timing
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
        self._has_copied_logs = False
        self._manifest: RunManifest | None = None
        self._manifest_lock = threading.Lock()
        self._has_run = False

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

        Accepts a :class:`~clabe.workflow.Workflow`, an ``@experiment`` function or a plain ``(launcher)``
        callable (sync or async), and executes it as a run driven by this launcher's client. Unless in debug mode,
        :meth:`validate` runs as the run's first step (``clabe_preflight``). The run's records are written to
        ``temp_dir``. Orchestrates the complete launcher workflow including validation, execution and cleanup.

        Steps (including legacy experiments) run on a dedicated run thread, not the main thread; code that must
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
        self._exit(_code, timeout=workflow.exit_prompt_timeout)

    def _run_workflow(self, workflow: Workflow) -> Run:
        """Executes ``workflow`` (with the preflight step) with this launcher, driven by its client."""
        run = Run(self._with_preflight(workflow), launcher=self, record_dir=self.temp_dir)
        self._manifest = collect_manifest(self, run.workflow, run_id=run.id, directory=self.temp_dir)
        self._write_manifest()
        with self._served(run):
            LocalClient(run, self.client).drive()
        return run

    @contextlib.contextmanager
    def _served(self, run: Run) -> Generator[None, None, None]:
        """Serves ``run`` over HTTP while the block executes, when the settings ask for it."""
        if not self.settings.serve:
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
        ui.notify(f"Run served at {url}", MessageLevel.INFO)
        if self.settings.open_browser:
            import webbrowser

            webbrowser.open(url)
        try:
            yield
        finally:
            server.stop()

    def _with_preflight(self, workflow: Workflow) -> Workflow:
        """``workflow`` with :meth:`validate` as its first step, ``clabe_preflight`` (none in debug mode)."""
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

    def update_manifest(self, change: Callable[[RunManifest], None]) -> None:
        """Applies ``change`` to the run manifest and rewrites ``run.json`` (nothing happens without a run).

        For what only becomes known during the run, e.g. the other machines it used (see :mod:`clabe.remote`).
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

    def _exit(self, code: int = 0, _force: bool = False, timeout: float | None = None) -> None:
        """
        Exits the launcher with the specified exit code.

        Performs cleanup operations and exits the application, optionally
        prompting the user before exit.

        Args:
            code: The exit code to use. Defaults to 0
            _force: Whether to force exit without user prompt. Defaults to False
            timeout: Seconds to wait at the exit prompt before exiting anyway.
                ``None`` waits until the user presses Enter; ``0`` skips the
                prompt. Defaults to None
        """
        logger.debug("Exiting with code %s", code)
        if logger is not None:
            clabe_logging.shutdown_logger(logger)
        if not getattr(self.client, "interactive", True):
            _force = True  # nobody is at the screen to dismiss an exit prompt
        if not _force and timeout != 0:
            label = "Press Enter to exit..."
            if timeout is not None:
                label = f"Press Enter to exit (closing automatically in {timeout:g} s)..."
            with self._cancel_prompt_after(timeout):
                try:
                    ui.text(label, field="exit")
                except KeyboardInterrupt:
                    pass  # a second Ctrl+C at the exit prompt should just exit
                except PromptCancelledError:
                    logger.debug("Exit prompt timed out after %s s", timeout)
        # Tear down the client (e.g. leave the persistent TUI) and stop asking through it.
        self.client.close()
        self._ui_binding.__exit__(None, None, None)

    @contextlib.contextmanager
    def _cancel_prompt_after(self, timeout: float | None) -> Generator[None]:
        """Cancels the client prompt running inside this block once ``timeout`` seconds pass.

        Keeps cancelling until the block exits, so a cancellation that lands before
        the prompt is on screen is not lost. ``None`` never cancels.
        """
        if timeout is None:
            yield
            return
        done = threading.Event()

        def _cancel_when_due() -> None:
            if done.wait(timeout):
                return
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
            if self._data_directory is not None and not os.path.exists(self._data_directory):
                # if _data_directory exists, session_directory is guaranteed to exist as well
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
