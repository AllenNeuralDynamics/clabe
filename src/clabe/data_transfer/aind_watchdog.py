import importlib.util

if importlib.util.find_spec("aind_watchdog_service") is None:
    raise ImportError(
        "The 'aind_watchdog_service' package is required to use this module. \
            Install the optional dependencies defined in `project.toml' \
                by running `pip install .[aind-services]`"
    )

import datetime
import json
import logging
import os
import subprocess
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Dict, List, Optional, Union, Self

import aind_watchdog_service.models
import pydantic
import requests
import yaml
from aind_data_schema.core.metadata import CORE_FILES
from aind_data_schema.core.session import Session as AdsSession
from aind_watchdog_service.models.manifest_config import BucketType, ManifestConfig, ModalityConfigs, Platform
from aind_watchdog_service.models.watch_config import WatchConfig
from pydantic import BaseModel
from requests.exceptions import HTTPError

from .. import ui
from ..data_mapper.aind_data_schema import AindDataSchemaSessionDataMapper
from ..services import ServiceSettings
from ._base import DataTransfer

if TYPE_CHECKING:
    from ..launcher import BaseLauncher
else:
    BaseLauncher = Any

logger = logging.getLogger(__name__)


_JobConfigs = Union[ModalityConfigs, Callable[["WatchdogDataTransferService"], Union[ModalityConfigs]]]


class WatchdogSettings(ServiceSettings):
    """
    Settings for the WatchdogDataTransferService.

    Attributes:
        destination (PathLike): The destination path for the data transfer.
        schedule_time (Optional[datetime.time]): The time to schedule the data transfer.
        project_name (str): The name of the project.
        platform (Platform): The platform of the project.
        capsule_id (Optional[str]): The capsule ID for the data transfer.
        script (Optional[Dict[str, List[str]]]): A dictionary of scripts to run.
        s3_bucket (BucketType): The S3 bucket to transfer the data to.
        mount (Optional[str]): The mount point for the data transfer.
        force_cloud_sync (bool): Whether to force a cloud sync.
        transfer_endpoint (str): The endpoint for the data transfer service.
        delete_modalities_source_after_success (bool): Whether to delete the source data after a successful transfer.
        extra_identifying_info (Optional[dict]): Extra identifying information for the data transfer.
        upload_job_configs (Optional[Any]): Upload job configurations.
    """
    _yml_section: ClassVar[Optional[str]] = "watchdog"

    destination: PathLike
    schedule_time: Optional[datetime.time] = datetime.time(hour=20)
    project_name: str
    platform: Platform = "behavior"
    capsule_id: Optional[str] = None
    script: Optional[Dict[str, List[str]]] = None
    s3_bucket: BucketType = BucketType.PRIVATE
    mount: Optional[str] = None
    force_cloud_sync: bool = True
    transfer_endpoint: str = "http://aind-data-transfer-service/api/v1/submit_jobs"
    delete_modalities_source_after_success: bool = False
    extra_identifying_info: Optional[dict] = None
    upload_job_configs: Optional[Any] = None


class WatchdogDataTransferService(DataTransfer[WatchdogSettings]):
    """
    A data transfer service that uses the aind-watchdog-service to monitor and transfer
    data based on manifest configurations.

    This service integrates with the AIND data transfer infrastructure to automatically
    monitor directories for new data and transfer it to specified destinations with
    proper metadata handling and validation.

    Attributes:
        _source (PathLike): Source directory to monitor
        _settings (WatchdogSettings): Service settings containing destination and configuration
        _aind_session_data_mapper (Optional[AindDataSchemaSessionDataMapper]): Mapper for session data
        _ui_helper (ui.UiHelper): UI helper for user prompts
        Various configuration attributes accessible via settings

    Example:
        ```python
        # Basic watchdog service setup:
        settings = WatchdogSettings(
            destination="//server/data/session_001",
            project_name="my_project"
        )
        service = WatchdogDataTransferService(
            source="C:/data/session_001",
            settings=settings
        )

        # Full configuration with session mapper:
        settings = WatchdogSettings(
            destination="//server/data/session_001",
            project_name="behavior_study",
            schedule_time=datetime.time(hour=22, minute=30),
            platform=Platform.BEHAVIOR,
            force_cloud_sync=True
        )
        session_mapper = MySessionMapper(session_data)
        service = WatchdogDataTransferService(
            source="C:/data/session_001",
            settings=settings
        )
        service = service.with_aind_session_data_mapper(session_mapper)
        if service.validate():
            service.transfer()
        ```
    """

    def __init__(
        self,
        source: PathLike,
        settings: WatchdogSettings,
        *,
        validate: bool = True,
        session_name: Optional[str] = None,
        ui_helper: Optional[ui.UiHelper] = None,
    ) -> None:
        """
        Initializes the WatchdogDataTransferService.

        Args:
            source: The source directory or file to monitor
            settings: WatchdogSettings containing destination and configuration options
            validate: Whether to validate the project name
            session_name: Name of the session
            ui_helper: UI helper for user prompts

        Example:
            ```python
            # Basic initialization:
            settings = WatchdogSettings(
                destination="//server/archive/session_001",
                project_name="behavior_project"
            )
            service = WatchdogDataTransferService(
                source="C:/data/session_001",
                settings=settings
            )

            # Advanced configuration:
            settings = WatchdogSettings(
                destination="//server/archive/session_001",
                project_name="behavior_project",
                schedule_time=datetime.time(hour=23),
                platform=Platform.BEHAVIOR,
                force_cloud_sync=True,
                delete_modalities_source_after_success=True,
                extra_identifying_info={"experiment_type": "foraging"}
            )
            service = WatchdogDataTransferService(
                source="C:/data/session_001",
                settings=settings
            )
            ```
        """
        self._settings = settings
        self._source = source

        self._aind_session_data_mapper: Optional[AindDataSchemaSessionDataMapper] = None
        self._upload_job_configs = []

        _default_exe = os.environ.get("WATCHDOG_EXE", None)
        _default_config = os.environ.get("WATCHDOG_CONFIG", None)

        if _default_exe is None or _default_config is None:
            raise ValueError("WATCHDOG_EXE and WATCHDOG_CONFIG environment variables must be defined.")

        self.executable_path = Path(_default_exe)
        self.config_path = Path(_default_config)

        self._watch_config: Optional[WatchConfig] = None
        self._manifest_config: Optional[ManifestConfig] = None

        self._validate_project_name = validate
        self._ui_helper = ui_helper or ui.DefaultUIHelper()
        self._session_name = session_name

    @property
    def aind_session_data_mapper(self) -> AindDataSchemaSessionDataMapper:
        """
        Gets the aind-data-schema session data mapper.

        Returns:
            The session data mapper

        Raises:
            ValueError: If the data mapper is not set
        """
        if self._aind_session_data_mapper is None:
            raise ValueError("Data mapper is not set.")
        return self._aind_session_data_mapper

    def with_aind_session_data_mapper(self, value: AindDataSchemaSessionDataMapper) -> "WatchdogDataTransferService":
        """
        Sets the aind-data-schema session data mapper.

        Args:
            value: The data mapper to set

        Raises:
            ValueError: If the provided value is not a valid data mapper
        """
        self._aind_session_data_mapper = value
        return self

    def transfer(self) -> None:
        """
        Executes the data transfer by generating a Watchdog manifest configuration.

        Creates and deploys a manifest configuration file that the watchdog service
        will use to monitor and transfer data according to the specified parameters.
        """
        try:
            if not self.is_running():
                logger.warning("Watchdog service is not running. Attempting to start it.")
                try:
                    self.force_restart(kill_if_running=False)
                except subprocess.CalledProcessError as e:
                    logger.error("Failed to start watchdog service. %s", e)
                    raise RuntimeError("Failed to start watchdog service.") from e
                else:
                    if not self.is_running():
                        logger.error("Failed to start watchdog service.")
                        raise RuntimeError("Failed to start watchdog service.")
                    else:
                        logger.info("Watchdog service restarted successfully.")

            logger.info("Creating watchdog manifest config.")

            if not self.aind_session_data_mapper.is_mapped():
                raise ValueError("Data mapper has not been mapped yet.")

            self._manifest_config = self.create_manifest_config_from_ads_session(
                ads_session=self.aind_session_data_mapper.mapped,
                session_name=self._session_name,
            )

            if self._watch_config is None:
                raise ValueError("Watchdog config is not set.")

            assert self._manifest_config.name is not None, "Manifest config name must be set."
            _manifest_path = self.dump_manifest_config(
                path=Path(self._watch_config.flag_dir) / self._manifest_config.name
            )
            logger.info("Watchdog manifest config created successfully at %s.", _manifest_path)

        except (pydantic.ValidationError, ValueError, IOError) as e:
            logger.error("Failed to create watchdog manifest config. %s", e)
            raise e

    def validate(self, create_config: bool = True) -> bool:
        """
        Validates the Watchdog service and its configuration.

        Checks for required executables, configuration files, service status,
        and project name validity.

        Args:
            create_config: Whether to create a default configuration if missing

        Returns:
            True if the service is valid, False otherwise

        Raises:
            FileNotFoundError: If required files are missing
            HTTPError: If the project name validation fails
        """
        logger.info("Attempting to validate Watchdog service.")
        if not self.executable_path.exists():
            raise FileNotFoundError(f"Executable not found at {self.executable_path}")
        if not self.config_path.exists():
            if not create_config:
                raise FileNotFoundError(f"Config file not found at {self.config_path}")
            else:
                self._watch_config = self.create_watch_config(
                    self.config_path.parent / "Manifests", self.config_path.parent / "Completed"
                )
                self._write_yaml(self._watch_config, self.config_path)
        else:
            self._watch_config = WatchConfig.model_validate(self._read_yaml(self.config_path))

        if not self.is_running():
            logger.warning(
                "Watchdog service is not running. \
                                After the session is over, \
                                the launcher will attempt to forcefully restart it"
            )
            return False

        try:
            _valid_proj = self.is_valid_project_name()
            if not _valid_proj:
                logger.warning("Watchdog project name is not valid.")
        except HTTPError as e:
            logger.error("Failed to fetch project names from endpoint. %s", e)
            raise e
        return _valid_proj

    @staticmethod
    def create_watch_config(
        watched_directory: os.PathLike,
        manifest_complete_directory: os.PathLike,
        create_dir: bool = True,
    ) -> WatchConfig:
        """
        Creates a WatchConfig object for the Watchdog service.

        Configures the directories and settings needed for the watchdog service
        to monitor and process data transfer manifests.

        Args:
            watched_directory: Directory to monitor for changes
            manifest_complete_directory: Directory for completed manifests
            webhook_url: Optional webhook URL for notifications
            create_dir: Whether to create the directories if they don't exist

        Returns:
            A WatchConfig object

        Example:
            ```python
            # Create basic watch configuration:
            config = WatchdogDataTransferService.create_watch_config(
                watched_directory="C:/watchdog/manifests",
                manifest_complete_directory="C:/watchdog/completed"
            )

            # Create configuration with webhook:
            config = WatchdogDataTransferService.create_watch_config(
                watched_directory="C:/watchdog/manifests",
                manifest_complete_directory="C:/watchdog/completed",
                webhook_url="https://my-webhook.com/notify",
                create_dir=True
            )
            ```
        """
        if create_dir:
            if not Path(watched_directory).exists():
                Path(watched_directory).mkdir(parents=True, exist_ok=True)
            if not Path(manifest_complete_directory).exists():
                Path(manifest_complete_directory).mkdir(parents=True, exist_ok=True)

        return WatchConfig(
            flag_dir=str(watched_directory),
            manifest_complete=str(manifest_complete_directory),
        )

    def is_valid_project_name(self) -> bool:
        """
        Checks if the project name is valid by querying the metadata service.

        Validates the project name against the list of known projects from
        the AIND metadata service.

        Returns:
            True if the project name is valid, False otherwise
        """
        project_names = self._get_project_names()
        return self._settings.project_name in project_names

    def create_manifest_config_from_ads_session(
        self,
        ads_session: AdsSession,
        ads_schemas: Optional[List[os.PathLike]] = None,
        session_name: Optional[str] = None,
    ) -> ManifestConfig:
        """
        Creates a ManifestConfig object from an aind-data-schema session.

        Converts session metadata into a manifest configuration that can be
        used by the watchdog service for data transfer operations.

        Args:
            ads_session: The aind-data-schema session data
            ads_schemas: Optional list of schema files
            session_name: Name of the session

        Returns:
            A ManifestConfig object

        Raises:
            ValueError: If the project name is invalid

        Example:
            ```python
            # Create manifest from session data:
            session = Session(...)
            manifest = service.create_manifest_config_from_ads_session(
                ads_session=session,
            )

            # Create with custom schemas:
            schemas = ["C:/data/rig.json", "C:/data/processing.json"]
            manifest = service.create_manifest_config_from_ads_session(
                ads_session=session,
                ads_schemas=schemas,
            )
            ```
        """
        processor_full_name = ",".join(ads_session.experimenter_full_name) or os.environ.get("USERNAME", "unknown")

        destination = Path(self._settings.destination).resolve()
        source = Path(self._source).resolve()

        if self._validate_project_name:
            project_names = self._get_project_names()
            if self._settings.project_name not in project_names:
                raise ValueError(f"Project name {self._settings.project_name} not found in {project_names}")

        ads_schemas = self._find_ads_schemas(source) if ads_schemas is None else ads_schemas

        _manifest_config = ManifestConfig(
            name=session_name,
            modalities={
                str(modality.abbreviation): [str(path.resolve()) for path in [source / str(modality.abbreviation)]]
                for modality in ads_session.data_streams[0].stream_modalities
            },
            subject_id=int(ads_session.subject_id),
            acquisition_datetime=ads_session.session_start_time,
            schemas=[str(value) for value in ads_schemas],
            destination=str(destination.resolve()),
            mount=self._settings.mount,
            processor_full_name=processor_full_name,
            project_name=self._settings.project_name,
            schedule_time=self._settings.schedule_time,
            platform=getattr(self._settings.platform, "abbreviation"),
            capsule_id=self._settings.capsule_id,
            s3_bucket=self._settings.s3_bucket,
            script=self._settings.script if self._settings.script else {},
            force_cloud_sync=self._settings.force_cloud_sync,
            transfer_endpoint=self._settings.transfer_endpoint,
            delete_modalities_source_after_success=self._settings.delete_modalities_source_after_success,
            extra_identifying_info=self._settings.extra_identifying_info,
        )

        # TODO
        _manifest_config = self.add_transfer_service_args(_manifest_config, jobs=self.upload_job_configs)
        return _manifest_config

    def add_transfer_service_args(
        self,
        manifest_config: ManifestConfig,
        jobs=Optional[List[_JobConfigs]],
        submit_job_request_kwargs: Optional[dict] = None,
    ) -> ManifestConfig:
        """
        Adds transfer service arguments to the manifest configuration.

        Configures job-specific parameters for different modalities and
        integrates them into the manifest configuration.

        Args:
            manifest_config: The manifest configuration to update
            jobs: List of job configurations
            submit_job_request_kwargs: Additional arguments for the job request

        Returns:
            The updated ManifestConfig object
        """
        # TODO (bruno-f-cruz)
        # The following code is super hacky and should be refactored once the transfer service
        # has a more composable API. Currently, the idea is to only allow one job per modality

        # we use the aind-watchdog-service library to create the default transfer service args for us
        job_settings = aind_watchdog_service.models.make_standard_transfer_args(manifest_config)
        job_settings = job_settings.model_copy(update=(submit_job_request_kwargs or {}))
        manifest_config.transfer_service_args = job_settings

        if (jobs is None) or (len(jobs) == 0):
            return manifest_config

        def _normalize_callable(job: _JobConfigs) -> ModalityConfigs:
            if callable(job):
                return job(self)
            return job

        modality_configs = [_normalize_callable(job) for job in jobs]

        if len(set([m.modality for m in modality_configs])) < len(modality_configs):
            raise ValueError("Duplicate modality configurations found. Aborting.")

        for modified in modality_configs:
            for overridable in manifest_config.transfer_service_args.upload_jobs[0].modalities:
                if modified.modality == overridable.modality:
                    # We need to let the watchdog api handle this or we are screwed...
                    modified.source = overridable.source
                    manifest_config.transfer_service_args.upload_jobs[0].modalities.remove(overridable)
                    manifest_config.transfer_service_args.upload_jobs[0].modalities.append(modified)
                    break

        return manifest_config

    @staticmethod
    def _find_ads_schemas(source: PathLike) -> List[PathLike]:
        """
        Finds aind-data-schema schema files in the source directory.

        Searches for standard AIND data schema files in the specified directory.

        Args:
            source: The source directory to search

        Returns:
            A list of schema file paths
        """
        json_files = []
        for core_file in CORE_FILES:
            json_file = Path(source) / f"{core_file}.json"
            if json_file.exists():
                json_files.append(json_file)
        return [path for path in json_files]

    @staticmethod
    def _get_project_names(
        end_point: str = "http://aind-metadata-service/project_names", timeout: int = 5
    ) -> list[str]:
        """
        Fetches the list of valid project names from the metadata service.

        Queries the AIND metadata service to retrieve the current list of
        valid project names for validation purposes.

        Args:
            end_point: The endpoint URL for the metadata service
            timeout: Timeout for the request

        Returns:
            A list of valid project names

        Raises:
            HTTPError: If the request fails
        """
        response = requests.get(end_point, timeout=timeout)
        if response.ok:
            return json.loads(response.content)["data"]
        else:
            response.raise_for_status()
            raise HTTPError(f"Failed to fetch project names from endpoint. {response.content.decode('utf-8')}")

    def is_running(self) -> bool:
        """
        Checks if the Watchdog service is currently running.

        Uses system process monitoring to determine if the watchdog executable
        is currently active.

        Returns:
            True if the service is running, False otherwise

        Example:
            ```python
            # Check service status:
            settings = WatchdogSettings(
                destination="//server/data",
                project_name="my_project"
            )
            service = WatchdogDataTransferService(source="C:/data", settings=settings)
            if service.is_running():
                print("Watchdog service is active")
            else:
                print("Watchdog service is not running")
                service.force_restart()
            ```
        """
        output = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {self.executable_path.name}"], shell=True, encoding="utf-8"
        )
        processes = [line.split()[0] for line in output.splitlines()[2:]]
        return len(processes) > 0

    def force_restart(self, kill_if_running: bool = True) -> subprocess.Popen[bytes]:
        """
        Attempts to restart the Watchdog application.

        Terminates the existing service if running and starts a new instance
        with the current configuration.

        Args:
            kill_if_running: Whether to terminate the service if it's already running

        Returns:
            A subprocess.Popen object representing the restarted service
        """
        if kill_if_running is True:
            while self.is_running():
                subprocess.run(["taskkill", "/IM", self.executable_path.name, "/F"], shell=True, check=True)

        cmd_factory = "{exe} -c {config}".format(exe=self.executable_path, config=self.config_path)

        return subprocess.Popen(cmd_factory, start_new_session=True, shell=True)

    def dump_manifest_config(self, path: Optional[os.PathLike] = None, make_dir: bool = True) -> Path:
        """
        Dumps the manifest configuration to a YAML file.

        Saves the current manifest configuration to a file that can be
        processed by the watchdog service.

        Args:
            path: The file path to save the manifest
            make_dir: Whether to create the directory if it doesn't exist

        Returns:
            The path to the saved manifest file

        Raises:
            ValueError: If the manifest or watch configuration is not set
        """
        manifest_config = self._manifest_config
        watch_config = self._watch_config

        if manifest_config is None or watch_config is None:
            raise ValueError("ManifestConfig or WatchConfig config is not set.")

        path = (Path(path) if path else Path(watch_config.flag_dir) / f"manifest_{manifest_config.name}.yaml").resolve()
        if "manifest" not in path.name:
            logger.info("Prefix manifest_ not found in file name. Appending it.")
            path = path.with_name(f"manifest_{path.name}.yaml")

        if make_dir and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)

        manifest_config.destination = str(Path.as_posix(Path(manifest_config.destination)))
        manifest_config.schemas = [str(Path.as_posix(Path(schema))) for schema in manifest_config.schemas]
        for modality in manifest_config.modalities:
            manifest_config.modalities[modality] = [
                str(Path.as_posix(Path(_path))) for _path in manifest_config.modalities[modality]
            ]

        self._write_yaml(manifest_config, path)
        return path

    @staticmethod
    def _yaml_dump(model: BaseModel) -> str:
        """
        Converts a Pydantic model to a YAML string.

        Serializes a Pydantic model to YAML format for file output.

        Args:
            model: The Pydantic model to convert

        Returns:
            A YAML string representation of the model
        """
        native_json = json.loads(model.model_dump_json())
        return yaml.dump(native_json, default_flow_style=False)

    @classmethod
    def _write_yaml(cls, model: BaseModel, path: PathLike) -> None:
        """
        Writes a Pydantic model to a YAML file.

        Saves a Pydantic model as a YAML file at the specified path.

        Args:
            model: The Pydantic model to write
            path: The file path to save the YAML
        """
        with open(path, "w", encoding="utf-8") as f:
            f.write(cls._yaml_dump(model))

    @staticmethod
    def _read_yaml(path: PathLike) -> dict:
        """
        Reads a YAML file and returns its contents as a dictionary.

        Loads and parses a YAML file into a Python dictionary.

        Args:
            path: The file path to read

        Returns:
            A dictionary representation of the YAML file
        """
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def prompt_input(self) -> bool:
        """
        Prompts the user to confirm whether to generate a manifest.

        Provides user interaction to confirm manifest generation for the
        watchdog service.

        Returns:
            True if the user confirms, False otherwise

        Example:
            ```python
            # Interactive manifest generation:
            settings = WatchdogSettings(
                destination="//server/data",
                project_name="my_project"
            )
            service = WatchdogDataTransferService(source="C:/data", settings=settings)
            if service.prompt_input():
                service.transfer()
                print("Manifest generation confirmed")
            else:
                print("Manifest generation cancelled")
            ```
        """
        return self._ui_helper.prompt_yes_no_question("Would you like to generate a watchdog manifest (Y/N)?")

    @classmethod
    def build_runner(
        cls,
        settings: WatchdogSettings,
        aind_session_data_mapper: Callable[[], AindDataSchemaSessionDataMapper] | AindDataSchemaSessionDataMapper,
    ) -> Callable[[BaseLauncher], "WatchdogDataTransferService"]:
        """
        A factory method for creating the watchdog service.

        Args:
            settings: The watchdog settings.
            aind_session_data_mapper: The aind session data mapper.

        Returns:
            A factory for WatchdogDataTransferService.
        """
        def _from_launcher(
            launcher: BaseLauncher,
        ) -> "WatchdogDataTransferService":
            _aind_session_data_mapper = (
                aind_session_data_mapper() if callable(aind_session_data_mapper) else aind_session_data_mapper
            )

            if not _aind_session_data_mapper.is_mapped():
                raise ValueError("Data mapper has not mapped yet. Cannot create watchdog.")

            _settings = settings.model_copy(update={})

            _session = launcher.get_session(strict=True)
            _settings.destination = Path(_settings.destination) / _session.subject
            launcher.copy_logs()
            service = cls(
                source=launcher.session_directory,
                settings=_settings,
                session_name=_session.session_name,
            ).with_aind_session_data_mapper(_aind_session_data_mapper)
            service.transfer()
            return service

        return _from_launcher

