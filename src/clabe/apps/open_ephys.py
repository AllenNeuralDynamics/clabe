import asyncio
import json
import logging
import os
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import requests
from pydantic import BaseModel, Field

from ..services import ServiceSettings
from ._base import AsyncExecutor, Command, CommandResult, ExecutableApp, identity_parser
from ._executors import _DefaultExecutorMixin

logger = logging.getLogger(__name__)

_LOOPBACK_ADDRESSES = {"localhost", "127.0.0.1", "::1"}


class OpenEphysAppSettings(ServiceSettings):
    """Settings for Open Ephys App."""

    __yml_section__ = "open_ephys"

    signal_chain: os.PathLike
    executable: os.PathLike = Path("./.open-ephys/open-ephys.exe")
    address: str = "localhost"
    port: int = 37497
    headless: bool = Field(default=False, description="Run the GUI without a window (--headless)")


class OpenEphysApp(ExecutableApp, _DefaultExecutorMixin):
    """
    A class to manage the execution of Open Ephys GUI.

    Handles Open Ephys GUI execution, configuration management, and process
    monitoring for ephys experiments.

    Methods:
        run: Executes the Open Ephys GUI
        launch: Starts the GUI in the background and waits until its API responds
        start: Starts recording
        stop: Stops recording
        close: Quits the GUI and waits for it to exit
        get_result: Retrieves the result of the Open Ephys execution
        add_app_settings: Adds or updates application settings
        validate: Validates the Open Ephys application configuration
    """

    def __init__(
        self,
        settings: OpenEphysAppSettings,
        skip_validation: bool = False,
    ) -> None:
        """
        Initializes the OpenEphysApp instance.

        Args:
            settings: Settings for the Open Ephys App
            skip_validation: Whether to skip validation of required files

        Example:
            ```python
            # Create and run a Open Ephys app
            app = OpenEphysApp(settings=OpenEphysAppSettings(signal_chain="signal_chain.xml"))
            app.run()
            ```
        """
        self.settings = settings
        self.signal_chain = Path(self.settings.signal_chain).resolve()
        self.executable = Path(self.settings.executable).resolve()
        self._client = _OpenEphysGuiClient(host=self.settings.address, port=self.settings.port)
        self._gui: asyncio.Task[CommandResult] | None = None

        if not skip_validation:
            self.validate()

        self._command = Command[CommandResult](
            cmd=[str(self.executable), str(self.signal_chain), *(["--headless"] if self.settings.headless else [])],
            output_parser=identity_parser,
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

    @property
    def command(self) -> Command[CommandResult]:
        """Get the command to execute."""
        return self._command

    @property
    def client(self) -> "_OpenEphysGuiClient":
        """Get the Open Ephys GUI client."""
        return self._client

    async def launch(
        self,
        executor: AsyncExecutor | None = None,
        *,
        timeout: float = 180.0,
        poll_interval: float = 0.5,
    ) -> "_OpenEphysGuiClient":
        """Starts the GUI in the background and waits until its HTTP API responds.

        Call :meth:`close` when done, e.g. in a ``finally`` block, to quit the GUI. If launching
        fails, the GUI is closed before the error is raised.

        When ``executor`` runs commands on another machine (it has a ``host``, like
        ``clabe.xml_rpc.RemoteRepository``) and ``settings.address`` is a loopback address, the client
        is pointed at that machine instead. The GUI's port must then be reachable from this machine.

        Args:
            executor: Executor to run the GUI with. Defaults to a local executor.
            timeout: Seconds to wait for the API to respond after starting the GUI. Startup can take
                minutes while hardware processors (e.g. Neuropix-PXI, NI-DAQmx) search for devices.
            poll_interval: Seconds between attempts to reach the API.

        Returns:
            The client connected to the running GUI.

        Raises:
            RuntimeError: If the GUI is already launched, or exits before its API responds.
            TimeoutError: If the API does not respond within ``timeout``.
            CommandError: If the GUI exits with a non-zero code before its API responds.

        Example:
            ```python
            await app.launch(executor=remote)
            try:
                app.start()
                await behavior_app.run_async()
                app.stop()
            finally:
                await app.close()
            ```
        """
        if self._gui is not None:
            raise RuntimeError("Open Ephys GUI is already launched; call close() first.")
        host = getattr(executor, "host", None)
        if host is not None and self.settings.address in _LOOPBACK_ADDRESSES:
            self._client = _OpenEphysGuiClient(host=host, port=self.settings.port)

        self._gui = asyncio.create_task(self.run_async(executor=executor))
        try:
            await self._wait_until_ready(self._gui, timeout, poll_interval)
        except BaseException:
            # The launch error is the one to report; how the GUI then exits is secondary
            try:
                await self.close()
            except Exception as e:  # noqa: BLE001 -- must not mask the launch error
                logger.debug("Open Ephys GUI failed while closing after a failed launch: %s", e)
            raise
        return self._client

    def start(self, record: bool = True) -> "Status":
        """Starts recording, or only acquisition when ``record`` is False.

        Returns:
            The GUI's mode after the request.
        """
        return self._client.start_recording() if record else self._client.start_acquisition()

    def stop(self) -> "Status":
        """Stops recording and acquisition.

        Returns:
            The GUI's mode after the request.
        """
        return self._client.stop()

    async def close(self, timeout: float = 30.0) -> CommandResult | None:
        """Asks the GUI to quit and waits for it to exit. Does nothing if it is not launched.

        Args:
            timeout: Seconds to wait for the GUI to exit after asking it to quit.

        Returns:
            The GUI's result, or None if it was not launched or did not exit within ``timeout``.

        Raises:
            CommandError: If the GUI exited with a non-zero code.
        """
        gui, self._gui = self._gui, None
        if gui is None:
            return None
        if not gui.done():
            try:
                await asyncio.to_thread(self._client.quit)
            except requests.RequestException as e:
                # The GUI may close the connection as it quits, or not be reachable at all
                logger.debug("Quit request to Open Ephys GUI failed: %s", e)
            done, _ = await asyncio.wait({gui}, timeout=timeout)
            if not done:
                gui.cancel()
                # Cancelling stops waiting for the process but does not end it
                logger.warning("Open Ephys GUI did not exit within %ss of quitting; it may still be running", timeout)
                return None
        if gui.cancelled():
            return None
        return gui.result()

    async def _wait_until_ready(self, gui: asyncio.Task, timeout: float, poll_interval: float) -> None:
        """Polls the GUI's API until it responds, failing if the GUI exits first or ``timeout`` passes."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        await asyncio.sleep(0)  # let the GUI task start, so an immediate failure is seen before polling
        while True:
            if gui.done():
                gui.result()  # raises the GUI's own error if it failed
                raise RuntimeError("Open Ephys GUI exited before its HTTP API became available.")
            try:
                await asyncio.to_thread(self._client.get_status)
                logger.info("Open Ephys GUI is ready at %s", self._client.base_url)
                return
            except requests.RequestException:
                pass
            if loop.time() >= deadline:
                raise TimeoutError(f"Open Ephys GUI API at {self._client.base_url} did not respond within {timeout}s")
            await asyncio.sleep(poll_interval)


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
    """Request to control GUI window (GUI versions before v1.1)."""

    command: Literal["quit"]


class QuitRequest(BaseModel):
    """Request to shut down the GUI (``/api/quit``, GUI v1.1+)."""


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

    def get_config(self) -> str:
        """Retrieve the running GUI's current XML configuration.

        Returns:
            The XML text from the ``info`` field of GET /api/config, unchanged.

        Raises:
            ValueError: The response does not contain a string in ``info``.
            requests.RequestException: The HTTP request fails.
        """
        response = self._get("/config")
        xml_text = response.get("info") if isinstance(response, dict) else None
        if not isinstance(xml_text, str):
            # This is invalid server data, not an invalid caller argument type.
            raise ValueError("Configuration response must contain XML text in 'info'.")  # noqa: TRY004
        return xml_text

    def get_status(self) -> Status:
        """Query the GUI's acquisition/recording status.

        Returns:
            Current status containing the GUI mode (IDLE, ACQUIRE, or RECORD).
        """
        data = self._get("/status")
        return StatusResponse(**data).mode

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
        return StatusResponse(**data).mode

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

    def get_processor_metadata(self, processor_id: int) -> list[dict[str, Any]]:
        """Read metadata history from an Ephys Metadata Generator processor.

        The GUI wraps the processor's JSON string in the response's ``info``
        field. This method decodes that string without adding an AIND schema
        dependency. It does not clear history or change recording state.

        Args:
            processor_id: ID of the metadata generator, not a Record Node.

        Returns:
            Metadata objects in the order returned by the processor.

        Raises:
            ValueError: The response does not contain a JSON array of objects.
            requests.RequestException: The HTTP request fails.
        """
        response = self.send_processor_config(processor_id, "get metadata")
        info = response.get("info") if isinstance(response, dict) else None
        if not isinstance(info, str):
            # This is invalid server data, not an invalid caller argument type.
            raise ValueError("Metadata response must contain a JSON string in 'info'.")  # noqa: TRY004
        try:
            metadata = json.loads(info)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Processor {processor_id} did not return valid metadata JSON.") from exc
        if not isinstance(metadata, list) or any(not isinstance(item, dict) for item in metadata):
            raise ValueError("Processor metadata must be a JSON array of objects.")
        return metadata

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

        Uses ``/api/quit`` (GUI v1.1+, which also works headless), falling back to the
        ``/api/window`` command of older GUI versions.

        Returns:
            Response from the API.
        """
        try:
            return self._put("/quit", QuitRequest())
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 404:
                raise
        return self._put("/window", WindowRequest(command="quit"))
