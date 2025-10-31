import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from ..apps._base import Command, CommandResult, ExecutableApp, identity_parser

logger = logging.getLogger(__name__)


class PythonScriptApp(ExecutableApp):
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
        skip_validation: bool = False,
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
        if not skip_validation:
            self._validate_uv()
            if not self._has_venv(project_directory):
                logger.warning("Python environment not found. Creating one...")
                self.create_environment(project_directory)

        self._command = Command[CommandResult](cmd="", output_parser=identity_parser)

        self.command.append_arg(
            [
                "uv run",
                extra_uv_arguments,
                self._make_uv_optional_toml_dependencies(optional_toml_dependencies or []),
                self._make_uv_project_directory(project_directory),
                "python" if append_python_exe else "",
                script,
                additional_arguments,
            ]
        )

    @property
    def command(self) -> Command[CommandResult]:
        """Get the command to execute."""
        return self._command

    @staticmethod
    def _has_venv(project_directory: os.PathLike) -> bool:
        """
        Checks if a virtual environment exists in the project directory.

        Returns:
            bool: True if a virtual environment exists, False otherwise
        """
        return (Path(project_directory) / ".venv").exists()

    @classmethod
    def create_environment(
        cls, project_directory: os.PathLike, run_kwargs: Optional[dict[str, Any]] = None
    ) -> subprocess.CompletedProcess:
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
        logger.info("Creating Python environment with uv venv at %s...", project_directory)
        run_kwargs = run_kwargs or {}
        try:
            proc = subprocess.run(
                f"uv venv {cls._make_uv_project_directory(project_directory)} ",
                shell=False,
                capture_output=True,
                text=True,
                check=True,
                cwd=project_directory,
                **run_kwargs,
            )
            proc.check_returncode()
        except subprocess.CalledProcessError as e:
            logger.error("Error creating Python environment. %s", e)
            raise
        return proc

    @staticmethod
    def _make_uv_project_directory(project_directory: str | os.PathLike) -> str:
        """
        Constructs the --directory argument for the uv command.

        Returns:
            str: The --directory argument
        """

        return f"--directory {Path(project_directory).resolve()}"

    @staticmethod
    def _make_uv_optional_toml_dependencies(optional_toml_dependencies: list[str]) -> str:
        """
        Constructs the --extra arguments for the uv command based on optional TOML dependencies.

        Returns:
            str: The --extra arguments
        """
        if not optional_toml_dependencies:
            return ""
        return " ".join([f"--extra {dep}" for dep in optional_toml_dependencies])

    @staticmethod
    def _validate_uv() -> None:
        """
        Validates the presence of the uv executable.

        Returns:
            bool: True if uv is installed

        Raises:
            RuntimeError: If uv is not installed
        """
        if not shutil.which("uv") is not None:
            logger.error("uv executable not detected.")
            raise RuntimeError(
                "uv is not installed in this computer. Please install uv. "
                "see https://docs.astral.sh/uv/getting-started/installation/"
            )
