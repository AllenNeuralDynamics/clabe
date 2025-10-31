import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from ._base import Command, CommandResult, ExecutableApp, identity_parser
from ._executors import _DefaultExecutorMixin

logger = logging.getLogger(__name__)


class PythonScriptApp(ExecutableApp, _DefaultExecutorMixin):
    """
    Application class for running Python scripts within a managed uv environment.

    Facilitates running Python scripts with automatic virtual environment management,
    dependency handling, and script execution. Uses the uv tool for environment and
    dependency management, ensuring isolated and reproducible Python environments.

    The app automatically validates uv installation, creates virtual environments if
    needed, and constructs proper uv run commands with all necessary flags and arguments.

    Attributes:
        command: The underlying command that will be executed

    Example:
        ```python
        # Simple script execution
        app = PythonScriptApp(script="analyze_data.py")
        result = app.run()
        
        # With project dependencies
        app = PythonScriptApp(
            script="process.py",
            project_directory="/path/to/project",
            optional_toml_dependencies=["data", "viz"]
        )
        
        # Module execution with arguments
        app = PythonScriptApp(
            script="-m pytest",
            additional_arguments="tests/ -v --cov",
            extra_uv_arguments="-q"
        )
        
        # Async execution
        result = await app.run_async()
        ```
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

        Automatically validates the presence of uv and creates a virtual environment
        if one doesn't exist (unless skip_validation is True). Constructs the full
        uv run command with all specified arguments.

        Args:
            script: The Python script command to be executed (e.g., "my_module.py" or "my_package run")
            additional_arguments: Additional arguments to pass to the script. Defaults to empty string
            project_directory: The directory where the project resides. Defaults to current directory
            extra_uv_arguments: Extra arguments to pass to the uv command (e.g., "-q" for quiet). Defaults to empty string
            optional_toml_dependencies: Additional TOML dependency groups to include (e.g., ["dev", "test"]). Defaults to None
            append_python_exe: Whether to append "python" before the script command. Defaults to False
            skip_validation: Skip uv validation and environment checks. Defaults to False

        Raises:
            RuntimeError: If uv is not installed (unless skip_validation=True)

        Example:
            ```python
            # Basic script execution
            app = PythonScriptApp(script="test.py")
            app.run()
            
            # Script with module syntax
            app = PythonScriptApp(script="-m pytest tests/")
            
            # With dependencies and arguments
            app = PythonScriptApp(
                script="my_module.py",
                additional_arguments="--verbose --output results.json",
                optional_toml_dependencies=["dev", "test"]
            )
            
            # With Python explicitly prepended
            app = PythonScriptApp(
                script="script.py",
                append_python_exe=True,
                project_directory="/path/to/project"
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

        Looks for a .venv directory within the specified project directory.

        Args:
            project_directory: The directory to check for a virtual environment

        Returns:
            bool: True if a virtual environment exists, False otherwise

        Example:
            ```python
            if PythonScriptApp._has_venv("/my/project"):
                print("Virtual environment found")
            ```
        """
        return (Path(project_directory) / ".venv").exists()

    @classmethod
    def create_environment(
        cls, project_directory: os.PathLike, run_kwargs: Optional[dict[str, Any]] = None
    ) -> subprocess.CompletedProcess:
        """
        Creates a Python virtual environment using the uv tool.

        Executes 'uv venv' to create a .venv directory in the specified project
        directory. This method is automatically called during initialization if
        no virtual environment is detected.

        Args:
            project_directory: Directory where the virtual environment will be created
            run_kwargs: Additional keyword arguments for subprocess.run. Defaults to None

        Returns:
            subprocess.CompletedProcess: The result of the environment creation process

        Raises:
            subprocess.CalledProcessError: If the environment creation fails

        Example:
            ```python
            # Create a virtual environment
            PythonScriptApp.create_environment("/path/to/project")

            # Create with custom timeout
            PythonScriptApp.create_environment(
                "/path/to/project",
                run_kwargs={"timeout": 60}
            )
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

        Converts the project directory path to an absolute path and formats it
        as a uv command-line argument.

        Args:
            project_directory: The project directory path

        Returns:
            str: The formatted --directory argument string

        Example:
            ```python
            arg = PythonScriptApp._make_uv_project_directory("/my/project")
            # Returns: "--directory /my/project"
            ```
        """

        return f"--directory {Path(project_directory).resolve()}"

    @staticmethod
    def _make_uv_optional_toml_dependencies(optional_toml_dependencies: list[str]) -> str:
        """
        Constructs the --extra arguments for the uv command based on optional TOML dependencies.

        Formats dependency groups defined in pyproject.toml [project.optional-dependencies]
        as uv command-line arguments.

        Args:
            optional_toml_dependencies: List of optional dependency group names

        Returns:
            str: The formatted --extra arguments string, or empty string if no dependencies

        Example:
            ```python
            args = PythonScriptApp._make_uv_optional_toml_dependencies(["dev", "test"])
            # Returns: "--extra dev --extra test"
            
            args = PythonScriptApp._make_uv_optional_toml_dependencies([])
            # Returns: ""
            ```
        """
        if not optional_toml_dependencies:
            return ""
        return " ".join([f"--extra {dep}" for dep in optional_toml_dependencies])

    @staticmethod
    def _validate_uv() -> None:
        """
        Validates the presence of the uv executable in the system PATH.

        Checks if the uv tool is installed and accessible. This is called during
        initialization unless skip_validation is True.

        Raises:
            RuntimeError: If uv is not installed or not found in PATH

        Example:
            ```python
            try:
                PythonScriptApp._validate_uv()
                print("uv is installed")
            except RuntimeError as e:
                print(f"uv not found: {e}")
            ```
        """
        if shutil.which("uv") is None:
            logger.error("uv executable not detected.")
            raise RuntimeError(
                "uv is not installed in this computer. Please install uv. "
                "see https://docs.astral.sh/uv/getting-started/installation/"
            )
