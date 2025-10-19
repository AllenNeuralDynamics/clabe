from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional, Self

from typing_extensions import override

from ._base import App

logger = logging.getLogger(__name__)

_HAS_UV = shutil.which("uv") is not None


class PythonScriptApp(App[subprocess.CompletedProcess]):
    """
    Application class for running Python scripts within a managed uv environment.

    Facilitates running Python scripts with automatic virtual environment management,
    dependency handling, and script execution. Uses the uv tool for environment and
    dependency management.

    Methods:
        run: Executes the Python script
        get_result: Retrieves the result of the script execution
        add_app_settings: Adds or updates application settings
        create_environment: Creates or synchronizes the virtual environment
    """

    def __init__(
        self,
        /,
        script: str,
        additional_arguments: str = "",
        project_directory: os.PathLike = Path("."),
        extra_uv_arguments: str = "",
        optional_toml_dependencies: Optional[list[str]] = None,
        append_python_exe: bool = False,
        timeout: Optional[float] = None,
    ) -> None:
        """
        Initializes the PythonScriptApp with the specified parameters.

        Args:
            script: The Python script to be executed
            additional_arguments: Additional arguments to pass to the script. Defaults to empty string
            project_directory: The directory where the project resides. Defaults to current directory
            extra_uv_arguments: Extra arguments to pass to the uv command. Defaults to empty string
            optional_toml_dependencies: Additional TOML dependencies to include. Defaults to None
            append_python_exe: Whether to append the Python executable to the command. Defaults to False
            timeout: Timeout for the script execution. Defaults to None

        Example:
            ```python
            # Initialize with basic script
            app = PythonScriptApp(script="test.py")

            # Initialize with dependencies and arguments
            app = PythonScriptApp(
                script="test.py",
                additional_arguments="--verbose",
                optional_toml_dependencies=["dev", "test"]
            )
            ```
        """
        self._validate_uv()
        self._script = script
        self._project_directory = project_directory
        self._timeout = timeout
        self._optional_toml_dependencies = optional_toml_dependencies if optional_toml_dependencies else []
        self._append_python_exe = append_python_exe
        self._additional_arguments = additional_arguments
        self._extra_uv_arguments = extra_uv_arguments

        self._completed_process: Optional[subprocess.CompletedProcess] = None

    @override
    def add_app_settings(self, **kwargs) -> Self:
        """
        Adds application-specific settings to the script execution.

        Args:
            **kwargs: Additional keyword arguments to pass to the script

        Returns:
            Self: The updated instance

        Example:
            ```python
            app.add_app_settings(verbose=True, output="results.json")
            ```
        """
        self._additional_arguments = " ".join([self._additional_arguments] + [f"--{k} {v}" for k, v in kwargs.items()])
        return self

    def get_result(self, *, allow_stderr: bool = True) -> subprocess.CompletedProcess:
        """
        Retrieves the result of the executed script.

        Args:
            allow_stderr: Whether to allow stderr in the output. Defaults to True

        Returns:
            subprocess.CompletedProcess: The result of the executed script

        Raises:
            RuntimeError: If the script has not been run yet
        """
        if self._completed_process is None:
            raise RuntimeError("The app has not been run yet.")
        return self._process_process_output(allow_stderr=allow_stderr)

    @override
    def run(self) -> Self:
        """
        Executes the Python script within the managed environment.

        Creates a virtual environment if one doesn't exist, then runs the script
        using uv with the configured settings and dependencies.

        Returns:
            Self: The updated instance

        Raises:
            subprocess.CalledProcessError: If the script execution fails
        """
        logger.info("Starting python script %s...", self._script)

        if not self._has_venv():
            logger.warning("Python environment not found. Creating one...")
            self.create_environment()

        _script = f"{self._script} {self._additional_arguments}"
        _python_exe = "python" if self._append_python_exe else ""
        command = f"uv run {self._extra_uv_arguments} {self._add_uv_optional_toml_dependencies()} {self._add_uv_project_directory()} {_python_exe} {_script}"

        try:
            proc = subprocess.run(
                command,
                shell=False,
                capture_output=True,
                text=True,
                check=True,
                cwd=Path(self._project_directory).resolve(),
            )
        except subprocess.CalledProcessError as e:
            logger.error("Error running the Python script. %s", e)
            raise

        logger.info("Python script completed.")
        self._completed_process = proc
        return self

    def _process_process_output(self, *, allow_stderr: Optional[bool] = True) -> subprocess.CompletedProcess:
        """
        Processes the output of the executed script.

        Args:
            allow_stderr: Whether to allow stderr in the output. Defaults to True

        Returns:
            subprocess.CompletedProcess: The completed process result

        Raises:
            RuntimeError: If the app has not been run yet
            subprocess.CalledProcessError: If the script execution fails or stderr is present when not allowed
        """
        proc = self._completed_process
        if proc is None:
            raise RuntimeError("The app has not been run yet.")

        try:
            proc.check_returncode()
        except subprocess.CalledProcessError:
            self._log_process_std_output(self._script, proc)
            raise
        else:
            self._log_process_std_output(self._script, proc)
            if len(proc.stderr) > 0 and allow_stderr is False:
                raise subprocess.CalledProcessError(1, proc.args)
        return proc

    @staticmethod
    def _log_process_std_output(process_name: str, proc: subprocess.CompletedProcess) -> None:
        """
        Logs the stdout and stderr of a completed process.

        Args:
            process_name: The name of the process
            proc: The completed process
        """
        if len(proc.stdout) > 0:
            logger.info("%s full stdout dump: \n%s", process_name, proc.stdout)
        if len(proc.stderr) > 0:
            logger.error("%s full stderr dump: \n%s", process_name, proc.stderr)

    def _has_venv(self) -> bool:
        """
        Checks if a virtual environment exists in the project directory.

        Returns:
            bool: True if a virtual environment exists, False otherwise
        """
        return (Path(self._project_directory) / ".venv").exists()

    def create_environment(self, run_kwargs: Optional[dict[str, Any]] = None) -> subprocess.CompletedProcess:
        """
        Creates a Python virtual environment using the uv tool.

        Args:
            run_kwargs: Additional arguments for the subprocess.run call. Defaults to None

        Returns:
            subprocess.CompletedProcess: The result of the environment creation process

        Raises:
            subprocess.CalledProcessError: If the environment creation fails

        Example:
            ```python
            # Create a virtual environment
            app.create_environment()

            # Create with custom run kwargs
            app.create_environment(run_kwargs={"timeout": 30})
            ```
        """
        logger.info("Creating Python environment with uv venv at %s...", self._project_directory)
        run_kwargs = run_kwargs or {}
        try:
            proc = subprocess.run(
                f"uv venv {self._add_uv_project_directory()} ",
                shell=False,
                capture_output=True,
                text=True,
                check=True,
                cwd=self._project_directory,
                **run_kwargs,
            )
            proc.check_returncode()
        except subprocess.CalledProcessError as e:
            logger.error("Error creating Python environment. %s", e)
            raise
        return proc

    def _add_uv_project_directory(self) -> str:
        """
        Constructs the --directory argument for the uv command.

        Returns:
            str: The --directory argument
        """
        return f" --directory {Path(self._project_directory).resolve()}"

    def _add_uv_optional_toml_dependencies(self) -> str:
        """
        Constructs the --extra arguments for the uv command based on optional TOML dependencies.

        Returns:
            str: The --extra arguments
        """
        return " ".join([f"--extra {dep}" for dep in self._optional_toml_dependencies])

    @staticmethod
    def _validate_uv() -> bool:
        """
        Validates the presence of the uv executable.

        Returns:
            bool: True if uv is installed

        Raises:
            RuntimeError: If uv is not installed
        """
        if not _HAS_UV:
            logger.error("uv executable not detected.")
            raise RuntimeError(
                "uv is not installed in this computer. Please install uv. see https://docs.astral.sh/uv/getting-started/installation/"
            )
        return True
