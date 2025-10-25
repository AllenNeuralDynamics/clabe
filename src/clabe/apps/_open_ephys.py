import asyncio
import logging
import os
import subprocess
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Literal, Optional, Self

import pydantic
import requests
from pydantic import BaseModel, Field
from typing_extensions import override

from ..services import ServiceSettings
from ..ui import DefaultUIHelper, UiHelper
from ._base import App

logger = logging.getLogger(__name__)


class OpenEphysAppSettings(ServiceSettings):
    """
    Settings for the BonsaiApp.

    Configuration for Bonsai workflow execution including paths, modes, and
    execution parameters.
    """

    __yml_section__: ClassVar[Optional[str]] = "open_ephys_app"

    signal_chain: os.PathLike
    executable: os.PathLike = Path("./.open_ephys/open_ephys.exe")
    cwd: Optional[os.PathLike] = None
    timeout: Optional[float] = None

    @pydantic.field_validator("signal_chain", "executable", mode="after", check_fields=True)
    @classmethod
    def _resolve_path(cls, value: os.PathLike) -> os.PathLike:
        """
        Resolves the path to an absolute path.

        Args:
            value: The path to resolve

        Returns:
            os.PathLike: The absolute path
        """
        return Path(value).resolve()


class OpenEphysApp(App[None]):
    """
    A class to manage the execution of Open Ephys GUI.

    Handles Open Ephys GUI execution, configuration management, and process
    monitoring for ephys experiments.

    Methods:
        run: Executes the Open Ephys GUI
        get_result: Retrieves the result of the Open Ephys execution
        add_app_settings: Adds or updates application settings
        validate: Validates the Open Ephys application configuration
    """

    def __init__(
        self,
        /,
        settings: OpenEphysAppSettings,
        ui_helper: Optional[UiHelper] = None,
        client: Optional["_OpenEphysGuiClient"] = None,
        **kwargs,
    ) -> None:
        """
        Initializes the OpenEphysApp instance.

        Args:
            settings: Settings for the Open Ephys App
            ui_helper: UI helper instance. Defaults to DefaultUIHelper
            client: Optional Open Ephys GUI client
            **kwargs: Additional keyword arguments

        Example:
            ```python
            # Create and run a Open Ephys app
            app = OpenEphysApp(settings=OpenEphysAppSettings(signal_chain="signal_chain.xml"))
            app.run()
            ```
        """
        self.settings = settings
        self._completed_process: Optional[subprocess.CompletedProcess] = None
        self.ui_helper = ui_helper if ui_helper is not None else DefaultUIHelper()
        if client is None:
            self._client = _OpenEphysGuiClient()
        else:
            self._client = client

    def client(self) -> "_OpenEphysGuiClient":
        """
        Returns the Open Ephys GUI client.

        Returns:
            _OpenEphysGuiClient: The Open Ephys GUI client instance
        """
        return self._client

    def get_result(self, *, allow_stderr: bool = True) -> None:
        """
        Returns the result of the Bonsai process execution.

        Args:
            allow_stderr: Whether to allow stderr in the output. Defaults to True

        Returns:
            None

        Raises:
            RuntimeError: If the app has not been run yet
        """
        if self._completed_process is None:
            raise RuntimeError("The app has not been run yet.")
        return self._process_process_output(allow_stderr=allow_stderr)

    def add_app_settings(self, *args, **kwargs):
        """
        Adds application-specific settings to the additional properties.

        Args:
            *args: Positional arguments (unused)
            **kwargs: Additional keyword arguments to add to settings

        Returns:
            Self: The updated instance of OpenEphysApp
        """
        return self

    def validate(self, *args, **kwargs) -> bool:
        """
        Validates the existence of required files and directories.

        Args:
            *args: Additional positional arguments (unused)
            **kwargs: Additional keyword arguments (unused)

        Returns:
            bool: True if validation is successful

        Raises:
            FileNotFoundError: If any required file or directory is missing
        """
        if not Path(self.settings.executable).exists():
            raise FileNotFoundError(f"Executable not found: {self.settings.executable}")
        if not Path(self.settings.signal_chain).exists():
            raise FileNotFoundError(f"Signal chain file not found: {self.settings.signal_chain}")
        return True

    @override
    def run(self) -> Self:
        """
        Runs the Open Ephys GUI process.

        Returns:
            Self: The updated instance

        Raises:
            FileNotFoundError: If validation fails
            subprocess.CalledProcessError: If the Open Ephys GUI process fails
        """
        self.validate()

        logger.info("Open Ephys process running...")
        try:
            __cmd = f'"{self.settings.executable}" "{self.settings.signal_chain}"'
            logger.debug("Launching Open Ephys with %s", __cmd)
            cwd = self.settings.cwd or os.getcwd()

            proc = subprocess.run(__cmd, cwd=cwd, check=True, timeout=self.settings.timeout, capture_output=True)
        except subprocess.CalledProcessError as e:
            logger.error(
                "Error running Open Ephys GUI process. %s\nProcess stderr: %s",
                e,
                e.stderr if e.stderr else "No stderr output",
            )
            raise
        self._completed_process = proc
        logger.info("Open Ephys GUI process completed.")
        return self

    async def run_async(self) -> Self:
        """
        Runs the Open Ephys GUI process asynchronously without blocking.

        This method executes the Open Ephys GUI in a non-blocking manner,
        allowing other async operations to run concurrently.

        Returns:
            Self: The updated instance

        Raises:
            FileNotFoundError: If validation fails
            subprocess.CalledProcessError: If the Bonsai process fails

        Example:
            ```python
            app = OpenEphysApp(settings=OpenEphysAppSettings(signal_chain="signal_chain.xml"))
            await app.run_async()
            ```
        """
        self.validate()

        logger.info("Open Ephys GUI process running asynchronously...")

        __cmd = f'"{self.settings.executable}" "{self.settings.signal_chain}"'
        logger.debug("Launching Open Ephys GUI asynchronously with %s", __cmd)
        cwd = self.settings.cwd or os.getcwd()

        process = await asyncio.create_subprocess_shell(
            __cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        try:
            if self.settings.timeout is not None:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.settings.timeout,
                )
            else:
                stdout, stderr = await process.communicate()
        except asyncio.TimeoutError as err:
            process.kill()
            await process.wait()
            raise subprocess.TimeoutExpired(
                cmd=__cmd,
                timeout=self.settings.timeout or 0,
                output=None,
                stderr=None,
            ) from err

        # Unfortunately the asyncio implementation does not return a CompletedProcess
        # So, we mock one
        returncode = process.returncode if process.returncode is not None else -1
        self._completed_process = subprocess.CompletedProcess(
            args=__cmd,
            returncode=returncode,
            stdout=stdout.decode() if stdout else "",
            stderr=stderr.decode() if stderr else "",
        )

        if returncode != 0:
            logger.error(
                "Error running Open Ephys GUI process. Return code: %s\nProcess stderr: %s",
                returncode,
                self._completed_process.stderr if self._completed_process.stderr else "No stderr output",
            )
            raise subprocess.CalledProcessError(
                returncode=returncode,
                cmd=__cmd,
                output=self._completed_process.stdout,
                stderr=self._completed_process.stderr,
            )

        logger.info("Open Ephys GUI process completed.")
        return self

    def _process_process_output(self, *, allow_stderr: Optional[bool]) -> None:
        """
        Processes the output from the Open Ephys GUI process result.

        Args:
            allow_stderr: Whether to allow stderr output. If None, prompts user

        Returns:
            None

        Raises:
            RuntimeError: If the app has not been run yet
            subprocess.CalledProcessError: If the process exits with an error
        """
        proc = self._completed_process
        if proc is None:
            raise RuntimeError("The app has not been run yet.")

        try:
            proc.check_returncode()
        except subprocess.CalledProcessError:
            self._log_process_std_output("Open Ephys GUI", proc)
            raise
        else:
            self._log_process_std_output("Open Ephys GUI", proc)
            if len(proc.stderr) > 0:
                logger.error("Open Ephys GUI process finished with errors.")
                if allow_stderr is None:
                    allow_stderr = self.ui_helper.prompt_yes_no_question("Would you like to see the error message?")
                if allow_stderr is False:
                    raise subprocess.CalledProcessError(1, proc.args)
        return

    def _log_process_std_output(self, process_name: str, proc: subprocess.CompletedProcess) -> None:
        """
        Logs the standard output and error of a process.

        Args:
            process_name: Name of the process
            proc: The process result
        """
        if len(proc.stdout) > 0:
            logger.info("%s full stdout dump: \n%s", process_name, proc.stdout)
        if len(proc.stderr) > 0:
            logger.error("%s full stderr dump: \n%s", process_name, proc.stderr)


class Status(str, Enum):
    """GUI acquisition/recording mode."""

    IDLE = "IDLE"
    ACQUIRE = "ACQUIRE"
    RECORD = "RECORD"


class StatusResponse(BaseModel):
    """Response from /api/status endpoint."""

    mode: Status


class StatusRequest(BaseModel):
    """Request to set GUI acquisition/recording mode."""

    mode: Status


class RecordNode(BaseModel):
    """Information about a Record Node."""

    node_id: int
    parent_directory: str
    record_engine: str
    experiment_number: int
    recording_number: int
    is_synchronized: bool


class RecordingResponse(BaseModel):
    """Response from /api/recording endpoint."""

    parent_directory: str
    base_text: str
    prepend_text: str
    append_text: str
    record_nodes: list[RecordNode]


class RecordingRequest(BaseModel):
    """Request to update recording configuration."""

    parent_directory: str | None = None
    base_text: str | None = None
    prepend_text: str | None = None
    append_text: str | None = None


class RecordNodeRequest(BaseModel):
    """Request to update a specific Record Node."""

    parent_directory: str | None = None
    experiment_number: int | None = None
    recording_number: int | None = None


class Stream(BaseModel):
    """Data stream information."""

    channel_count: int
    name: str
    sample_rate: float
    source_id: int
    parameters: list[Any] = Field(default_factory=list)


class Processor(BaseModel):
    """Processor/plugin information."""

    id: int
    name: str
    parameters: list[Any] = Field(default_factory=list)
    predecessor: int | None
    streams: list[Stream] = Field(default_factory=list)


class ProcessorsResponse(BaseModel):
    """Response from /api/processors endpoint."""

    processors: list[Processor]


class ConfigRequest(BaseModel):
    """Request to send configuration message to a processor."""

    text: str


class MessageRequest(BaseModel):
    """Request to broadcast a message to all processors."""

    text: str


class WindowRequest(BaseModel):
    """Request to control GUI window."""

    command: Literal["quit"]


class _OpenEphysGuiClient:
    """Client for interacting with the Open Ephys GUI HTTP Server.

    The Open Ephys HTTP Server runs on port 37497 and provides a RESTful API
    for remote control of the GUI.

    Args:
        host: Hostname or IP address of the machine running the GUI. Defaults to "localhost".
        port: Port number of the HTTP server. Defaults to 37497.
        timeout: Timeout in seconds for HTTP requests. Defaults to 10.
    """

    def __init__(self, host: str = "localhost", port: int = 37497, timeout: float = 10.0):
        """Initialize the client."""
        self._host = host
        self._port = port
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        """Base URL for the API."""
        return f"http://{self._host}:{self._port}/api"

    def _get(self, endpoint: str) -> dict[str, Any]:
        """Send GET request to the API."""
        url = f"{self.base_url}{endpoint}"
        logger.debug("Sending GET request to %s", url)
        response = requests.get(url, timeout=self._timeout)
        response.raise_for_status()
        result = response.json()
        logger.debug("GET response from %s: %s", url, result)
        return result

    def _put(self, endpoint: str, data: BaseModel) -> dict[str, Any]:
        """Send PUT request to the API."""
        url = f"{self.base_url}{endpoint}"
        payload = data.model_dump(exclude_none=True)
        logger.debug("Sending PUT request to %s with payload: %s", url, payload)
        response = requests.put(url, json=payload, timeout=self._timeout)
        response.raise_for_status()
        result = response.json()
        logger.debug("PUT response from %s: %s", url, result)
        return result

    def get_status(self) -> Status:
        """Query the GUI's acquisition/recording status.

        Returns:
            Current status containing the GUI mode (IDLE, ACQUIRE, or RECORD).
        """
        data = self._get("/status")
        return Status(**data)

    def set_status(self, mode: Status) -> Status:
        """Set the GUI's acquisition/recording status.

        Args:
            mode: Desired GUI mode (IDLE, ACQUIRE, or RECORD).

        Returns:
            Updated status response.

        Note:
            The signal chain must contain at least one Record Node for RECORD mode to work.
        """
        request = StatusRequest(mode=mode)
        data = self._put("/status", request)
        return Status(**data)

    def start_acquisition(self) -> Status:
        """Start data acquisition without recording.

        Returns:
            Updated status response.
        """
        return self.set_status(Status.ACQUIRE)

    def start_recording(self) -> Status:
        """Start data acquisition and recording.

        Returns:
            Updated status response.

        Note:
            The signal chain must contain at least one Record Node.
        """
        return self.set_status(Status.RECORD)

    def stop(self) -> Status:
        """Stop acquisition and recording.

        Returns:
            Updated status response.
        """
        return self.set_status(Status.IDLE)

    def get_recording_config(self) -> RecordingResponse:
        """Get recording configuration including all Record Nodes.

        Returns:
            Recording configuration with parent directory and Record Node details.
        """
        data = self._get("/recording")
        return RecordingResponse(**data)

    def set_recording_config(
        self,
        parent_directory: str | None = None,
        base_text: str | None = None,
        prepend_text: str | None = None,
        append_text: str | None = None,
    ) -> RecordingResponse:
        """Update the default recording configuration.

        Args:
            parent_directory: Default location for storing data.
            base_text: Base text for recording names.
            prepend_text: Text to prepend to recording names.
            append_text: Text to append to recording names.

        Returns:
            Updated recording configuration.

        Note:
            Changes only apply to future Record Nodes, not existing ones.
        """
        request = RecordingRequest(
            parent_directory=parent_directory,
            base_text=base_text,
            prepend_text=prepend_text,
            append_text=append_text,
        )
        data = self._put("/recording", request)
        return RecordingResponse(**data)

    def set_record_node_config(
        self,
        node_id: int,
        parent_directory: str | None = None,
        experiment_number: int | None = None,
        recording_number: int | None = None,
    ) -> RecordingResponse:
        """Update configuration for a specific Record Node.

        Args:
            node_id: ID of the Record Node to update.
            parent_directory: Recording directory for this node.
            experiment_number: Experiment number for this node.
            recording_number: Recording number for this node.

        Returns:
            Updated recording configuration.
        """
        request = RecordNodeRequest(
            parent_directory=parent_directory,
            experiment_number=experiment_number,
            recording_number=recording_number,
        )
        data = self._put(f"/recording/{node_id}", request)
        return RecordingResponse(**data)

    def get_processors(self) -> ProcessorsResponse:
        """Get information about all processors in the signal chain.

        Returns:
            List of processors with their parameters and streams.
        """
        data = self._get("/processors")
        return ProcessorsResponse(**data)

    def get_processor(self, processor_id: int) -> Processor:
        """Get information about a specific processor.

        Args:
            processor_id: ID of the processor.

        Returns:
            Processor information including parameters and streams.
        """
        data = self._get(f"/processors/{processor_id}")
        return Processor(**data)

    def send_processor_config(self, processor_id: int, message: str) -> dict[str, Any]:
        """Send a configuration message to a specific processor.

        This can be used to modify processor state prior to starting acquisition.

        Args:
            processor_id: ID of the processor.
            message: Configuration message text (processor-specific format).

        Returns:
            Response from the processor.

        Example:
            To change Neuropixels probe reference:
            >>> client.send_processor_config(100, "NP REFERENCE 3 1 1 TIP")
        """
        request = ConfigRequest(text=message)
        return self._put(f"/processors/{processor_id}/config", request)

    def broadcast_message(self, message: str) -> dict[str, Any]:
        """Broadcast a message to all processors during acquisition.

        Messages are relayed to all processors and saved by all Record Nodes.
        Useful for marking different epochs within a recording.

        Args:
            message: Message text to broadcast.

        Returns:
            Response from the API.

        Example:
            To trigger a pulse on the Acquisition Board:
            >>> client.broadcast_message("ACQBOARD TRIGGER 1 100")

        Note:
            Messages are only processed while acquisition is active and if processors
            have implemented the handleBroadcastMessage() method.
        """
        request = MessageRequest(text=message)
        return self._put("/message", request)

    def quit(self) -> dict[str, Any]:
        """Shut down the GUI remotely.

        Returns:
            Response from the API.
        """
        request = WindowRequest(command="quit")
        return self._put("/window", request)
