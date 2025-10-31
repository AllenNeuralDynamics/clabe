import logging
import os
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

import requests
from pydantic import BaseModel, Field

from ._base import Command, CommandResult, ExecutableApp, identity_parser
from ._executors import _DefaultExecutorMixin

logger = logging.getLogger(__name__)


class OpenEphysApp(ExecutableApp, _DefaultExecutorMixin):
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
        signal_chain: os.PathLike,
        *,
        executable: os.PathLike = Path("./.open_ephys/open_ephys.exe"),
        client: Optional["_OpenEphysGuiClient"] = None,
        skip_validation: bool = False,
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
        self.signal_chain = Path(signal_chain).resolve()
        self.executable = Path(executable).resolve()
        self._client = client or _OpenEphysGuiClient()

        if not skip_validation:
            self.validate()

        self._command = Command[CommandResult](
            cmd=f'"{self.executable}" "{self.signal_chain}"', output_parser=identity_parser
        )

    def validate(self):
        """
        Validates the existence of required files and directories.

        Raises:
            FileNotFoundError: If any required file or directory is missing
        """
        if not Path(self.executable).exists():
            raise FileNotFoundError(f"Executable not found: {self.executable}")
        if not Path(self.signal_chain).exists():
            raise FileNotFoundError(f"Signal chain file not found: {self.signal_chain}")


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
