import importlib.util

if importlib.util.find_spec("aind_data_transfer_service") is None:
    raise ImportError(
        "The 'aind_data_transfer_service' package is required to use this module. \
            Install the optional dependencies defined in `project.toml' \
                by running `pip install .[aind-services]`"
    )

import datetime
import importlib.metadata
import json
import logging
import os
import subprocess
from os import PathLike
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Dict, List, Optional, Union

import aind_data_transfer_service.models.core
import pydantic
import requests
import semver
import yaml
from aind_data_schema.core.metadata import CORE_FILES
from pydantic import BaseModel, SerializeAsAny, TypeAdapter
from requests.exceptions import HTTPError
from typing_extensions import deprecated

from .. import ui
from ..services import ServiceSettings
from ._aind_watchdog_models import (
    DEFAULT_TRANSFER_ENDPOINT,
    BucketType,
    ManifestConfig,
    Modality,
    Platform,
    WatchConfig,
)
from ._base import DataTransfer

if TYPE_CHECKING:
    from ..data_mapper.aind_data_schema import Session
else:
    Session = Any


logger = logging.getLogger(__name__)

TransferServiceTask = Dict[
    str, Union[aind_data_transfer_service.models.core.Task, Dict[str, aind_data_transfer_service.models.core.Task]]
]

_AIND_DATA_SCHEMA_PKG_VERSION = semver.Version.parse(importlib.metadata.version("aind_data_schema"))


class WatchdogSettings(ServiceSettings):
    """
    Settings for the WatchdogDataTransferService.

    Configures data transfer operations including destination paths, scheduling,
    and integration with the AIND watchdog service.
    """

    __yml_section__: ClassVar[Optional[str]] = "watchdog"

    destination: Path
    schedule_time: Optional[datetime.time] = datetime.time(hour=20)
    project_name: str
    platform: Platform = "behavior"
    capsule_id: Optional[str] = None
    script: Optional[Dict[str, List[str]]] = None
    s3_bucket: BucketType = "private"
    mount: Optional[str] = None
    force_cloud_sync: bool = True
    transfer_endpoint: str = DEFAULT_TRANSFER_ENDPOINT
    delete_modalities_source_after_success: bool = False
    extra_identifying_info: Optional[dict] = None
    upload_tasks: Optional[SerializeAsAny[TransferServiceTask]] = None
    job_type: str = "default"
    extra_modalities: Optional[List[Modality]] = None


class WatchdogDataTransferService(DataTransfer[WatchdogSettings]):
    """
    A data transfer service that uses the aind-watchdog-service to monitor and transfer data.

    Integrates with the AIND data transfer infrastructure to automatically monitor
    directories for new data and transfer it to specified destinations with proper
    metadata handling and validation.
    """

    def __init__(
        self,
        source: PathLike,
        settings: WatchdogSettings,
        aind_data_schema_session: Session,
        *,
        validate: bool = True,
        session_name: Optional[str] = None,
        ui_helper: Optional[ui.UiHelper] = None,
        email_from_experimenter_builder: Optional[
            Callable[[str], str]
        ] = lambda user_name: f"{user_name}@alleninstitute.org",
    ) -> None:
        """
        Initializes the WatchdogDataTransferService.

        Args:
            source: The source directory or file to monitor
            settings: Configuration for the watchdog service
            aind_data_schema_session: The session data schema
            validate: Whether to validate the project name
            session_name: Name of the session
            ui_helper: UI helper for user prompts
            email_from_experimenter_builder: Function to build email from experimenter name
        """
        self._settings = settings
        self._source = source

        self._aind_data_schema_session: Session = aind_data_schema_session

        _default_exe = os.environ.get("WATCHDOG_EXE", None)
        _default_config = os.environ.get("WATCHDOG_CONFIG", None)

        if _default_exe is None or _default_config is None:
            raise ValueError("WATCHDOG_EXE and WATCHDOG_CONFIG environment variables must be defined.")

        self.executable_path = Path(_default_exe)
        self.config_path = Path(_default_config)

        self._watch_config: Optional[WatchConfig] = None
        self._manifest_config: Optional[ManifestConfig] = None

        self._validate_project_name = validate

        if validate:
            self.validate()

        self._watch_config = WatchConfig.model_validate(self._read_yaml(self.config_path))

        self._ui_helper = ui_helper or ui.DefaultUIHelper()
        self._session_name = session_name
        self._email_from_experimenter_builder = email_from_experimenter_builder

    def transfer(self) -> None:
        """
        Executes the data transfer by generating a Watchdog manifest configuration.

        Creates and deploys a manifest configuration file that the watchdog service
        will use to monitor and transfer data.
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

            logger.debug("Creating watchdog manifest config.")

            if _AIND_DATA_SCHEMA_PKG_VERSION.major < 2:
                logger.warning(
                    "Using deprecated AIND data schema version %s. Consider upgrading.", _AIND_DATA_SCHEMA_PKG_VERSION
                )
                self._manifest_config = self._create_manifest_config_from_ads_session(
                    ads_session=self._aind_data_schema_session,
                    session_name=self._session_name,
                )
            else:
                self._manifest_config = self._create_manifest_config_from_ads_acquisition(
                    ads_session=self._aind_data_schema_session,
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
            raise

    def validate(self) -> bool:
        """
        Validates the Watchdog service and its configuration.

        Checks for required executables, configuration files, service status,
        and project name validity.

        Returns:
            True if the service is valid, False otherwise

        Raises:
            FileNotFoundError: If required files are missing
            HTTPError: If the project name validation fails
        """
        logger.debug("Attempting to validate Watchdog service.")
        if not self.executable_path.exists():
            raise FileNotFoundError(f"Executable not found at {self.executable_path}")
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found at {self.config_path}")

        if not self.is_running():
            logger.warning(
                "Watchdog service is not running. \
                                After the session is over, \
                                the launcher will attempt to forcefully restart it"
            )
            return False

        if self.settings.project_name is None:
            logger.warning("Watchdog project name is not set. Skipping validation.")
        else:
            try:
                _valid_proj = self.is_valid_project_name()
                if not _valid_proj:
                    logger.warning("Watchdog project name is not valid.")
            except HTTPError as e:
                logger.error("Failed to fetch project names from endpoint. %s", e)
                raise
            return _valid_proj

        return True

    def is_valid_project_name(self) -> bool:
        """
        Checks if the project name is valid by querying the metadata service.

        Returns:
            True if the project name is valid, False otherwise
        """
        project_names = self._get_project_names()
        return self._settings.project_name in project_names

    def _create_manifest_config_from_ads_acquisition(
        self,
        ads_session: Session,
        ads_schemas: Optional[List[os.PathLike]] = None,
        session_name: Optional[str] = None,
    ) -> ManifestConfig:
        """
        Creates a ManifestConfig from an aind-data-schema acquisition.

        For aind-data-schema versions >= 2.0. Converts acquisition metadata into
        a manifest configuration for the watchdog service.

        Args:
            ads_session: The aind-data-schema acquisition data
            ads_schemas: Optional list of schema files
            session_name: Name of the session

        Returns:
            A ManifestConfig object

        Raises:
            ValueError: If the project name is invalid
        """
        processor_full_name = ",".join(ads_session.experimenters) or os.environ.get("USERNAME", "unknown")

        if (len(ads_session.experimenters) > 0) and self._email_from_experimenter_builder is not None:
            user_email = self._email_from_experimenter_builder(ads_session.experimenters[0])
        else:
            user_email = None

        destination = Path(self._settings.destination).resolve()
        source = Path(self._source).resolve()

        if self._validate_project_name:
            project_names = self._get_project_names()
            if self._settings.project_name not in project_names:
                raise ValueError(f"Project name {self._settings.project_name} not found in {project_names}")

        ads_schemas = self._find_ads_schemas(source) if ads_schemas is None else ads_schemas
        modalities = {
            str(modality.abbreviation): [Path(path.resolve()) for path in [source / str(modality.abbreviation)]]
            for modality in ads_session.data_streams[0].modalities
        }
        if self._settings.extra_modalities is not None:
            for modality in self._settings.extra_modalities:
                modalities[str(modality)] = [Path(path.resolve()) for path in [source / str(modality)]]

        _manifest_config = ManifestConfig(
            name=session_name,
            modalities=modalities,
            subject_id=int(ads_session.subject_id),
            acquisition_datetime=ads_session.acquisition_start_time,
            schemas=[Path(value) for value in ads_schemas],
            destination=Path(destination),
            mount=self._settings.mount,
            processor_full_name=processor_full_name,
            project_name=self._settings.project_name,
            schedule_time=self._settings.schedule_time,
            platform=self._settings.platform,
            capsule_id=self._settings.capsule_id,
            s3_bucket=self._settings.s3_bucket,
            script=self._settings.script if self._settings.script else {},
            force_cloud_sync=self._settings.force_cloud_sync,
            transfer_endpoint=self._settings.transfer_endpoint,
            delete_modalities_source_after_success=self._settings.delete_modalities_source_after_success,
            extra_identifying_info=self._settings.extra_identifying_info,
        )

        _manifest_config = self._make_transfer_args(
            _manifest_config,
            add_default_tasks=True,
            extra_tasks=self._settings.upload_tasks or {},
            job_type=self._settings.job_type,
            user_email=user_email,
        )
        return _manifest_config

    @deprecated("Use _create_manifest_config_from_ads_acquisition instead. This will be removed in a future release.")
    def _create_manifest_config_from_ads_session(
        self,
        ads_session: Session,
        ads_schemas: Optional[List[os.PathLike]] = None,
        session_name: Optional[str] = None,
    ) -> ManifestConfig:
        """
        Creates a ManifestConfig from an aind-data-schema session.

        Deprecated: Use _create_manifest_config_from_ads_acquisition instead.

        Args:
            ads_session: The aind-data-schema session data
            ads_schemas: Optional list of schema files
            session_name: Name of the session

        Returns:
            A ManifestConfig object

        Raises:
            ValueError: If the project name is invalid
        """
        processor_full_name = ",".join(ads_session.experimenter_full_name) or os.environ.get("USERNAME", "unknown")

        if (len(ads_session.experimenter_full_name) > 0) and self._email_from_experimenter_builder is not None:
            user_email = self._email_from_experimenter_builder(ads_session.experimenter_full_name[0])
        else:
            user_email = None

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
                str(modality.abbreviation): [Path(path.resolve()) for path in [source / str(modality.abbreviation)]]
                for modality in ads_session.data_streams[0].stream_modalities
            },
            subject_id=int(ads_session.subject_id),
            acquisition_datetime=ads_session.session_start_time,
            schemas=[Path(value) for value in ads_schemas],
            destination=Path(destination),
            mount=self._settings.mount,
            processor_full_name=processor_full_name,
            project_name=self._settings.project_name,
            schedule_time=self._settings.schedule_time,
            platform=self._settings.platform,
            capsule_id=self._settings.capsule_id,
            s3_bucket=self._settings.s3_bucket,
            script=self._settings.script if self._settings.script else {},
            force_cloud_sync=self._settings.force_cloud_sync,
            transfer_endpoint=self._settings.transfer_endpoint,
            delete_modalities_source_after_success=self._settings.delete_modalities_source_after_success,
            extra_identifying_info=self._settings.extra_identifying_info,
        )

        _manifest_config = self._make_transfer_args(
            _manifest_config,
            add_default_tasks=True,
            extra_tasks=self._settings.upload_tasks or {},
            job_type=self._settings.job_type,
            user_email=user_email,
        )
        return _manifest_config

    @staticmethod
    def _remote_destination_root(manifest: ManifestConfig) -> Path:
        """
        Determines the remote destination root path for the manifest.

        Args:
            manifest: The manifest configuration

        Returns:
            The remote destination root path
        """
        assert manifest.destination is not None, "Manifest destination must be set."
        assert manifest.name is not None, "Manifest name must be set."
        return Path(manifest.destination) / manifest.name

    @classmethod
    def _make_transfer_args(
        cls,
        manifest: ManifestConfig,
        *,
        job_type: str = "default",
        add_default_tasks: bool = True,
        extra_tasks: TransferServiceTask,
        user_email: Optional[str] = None,
    ) -> ManifestConfig:
        """
        Appends tasks to a manifest configuration.

        Adds metadata and modality transformation tasks to the manifest, along with
        any extra tasks specified.

        Args:
            manifest: The manifest configuration to update
            job_type: The job type identifier
            add_default_tasks: Whether to add default metadata and transformation tasks
            extra_tasks: Additional tasks to include
            user_email: Optional user email for the job submission

        Returns:
            The updated manifest configuration
        """
        tasks = {}

        if add_default_tasks:
            tasks["modality_transformation_settings"] = {
                modality: aind_data_transfer_service.models.core.Task(
                    job_settings={"input_source": str(PurePosixPath(cls._remote_destination_root(manifest) / modality))}
                )
                for modality in manifest.modalities.keys()
            }

            tasks["gather_preliminary_metadata"] = aind_data_transfer_service.models.core.Task(
                job_settings={"metadata_dir": str(PurePosixPath(cls._remote_destination_root(manifest)))}
            )

        extra_tasks = cls._interpolate_from_manifest(
            extra_tasks,
            str(PurePosixPath(cls._remote_destination_root(manifest))),
            "{{ destination }}",
        )

        tasks.update(extra_tasks)

        upload_job_configs_v2 = aind_data_transfer_service.models.core.UploadJobConfigsV2(
            job_type=job_type,
            project_name=manifest.project_name,
            platform=aind_data_transfer_service.models.core.Platform.from_abbreviation(manifest.platform),
            modalities=[
                aind_data_transfer_service.models.core.Modality.from_abbreviation(m) for m in manifest.modalities.keys()
            ],
            subject_id=str(manifest.subject_id),
            acq_datetime=manifest.acquisition_datetime.strftime("%Y-%m-%d %H:%M:%S"),
            tasks=tasks,
            s3_bucket=manifest.s3_bucket,
            acquisition_datetime=manifest.acquisition_datetime,
        )

        submit_request_v2 = aind_data_transfer_service.models.core.SubmitJobRequestV2(
            upload_jobs=[upload_job_configs_v2], user_email=user_email
        )
        manifest.transfer_service_args = submit_request_v2
        return manifest

    @staticmethod
    def _interpolate_from_manifest(
        tasks: TransferServiceTask | dict, value: str, placeholder: str
    ) -> TransferServiceTask:
        """
        Interpolates values from the manifest into upload job configurations.

        Args:
            tasks: The upload job configuration to update
            value: The value to use for interpolation
            placeholder: The placeholder string to replace

        Returns:
            The updated upload job configuration
        """
        _adapter = TypeAdapter(TransferServiceTask)
        literal = _adapter.dump_json(tasks, serialize_as_any=True)
        updated_literal = literal.decode("utf-8").replace(placeholder, value)
        return _adapter.validate_json(updated_literal)

    @staticmethod
    def _find_ads_schemas(source: PathLike) -> List[PathLike]:
        """
        Finds aind-data-schema schema files in the source directory.

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

        Args:
            end_point: The endpoint URL for the metadata service
            timeout: Timeout for the request in seconds

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

        Returns:
            True if the service is running, False otherwise
        """
        output = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {self.executable_path.name}"], shell=True, encoding="utf-8"
        )
        processes = [line.split()[0] for line in output.splitlines()[2:]]
        return len(processes) > 0

    def force_restart(self, kill_if_running: bool = True) -> subprocess.Popen[bytes]:
        """
        Attempts to restart the Watchdog application.

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

        if path.suffix not in [".yml", ".yaml"]:
            path = path.with_suffix(".yaml")

        if not path.name.startswith("manifest_"):
            logger.debug("Prefix manifest_ not found in file name. Appending it.")
            path = path.with_name(f"manifest_{path.stem}{path.suffix}")

        if make_dir and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)

        manifest_config.destination = Path(manifest_config.destination)
        manifest_config.schemas = [Path(schema) for schema in manifest_config.schemas]
        for modality in manifest_config.modalities:
            manifest_config.modalities[modality] = [_path for _path in manifest_config.modalities[modality]]

        self._write_yaml(manifest_config, path)
        return path

    @staticmethod
    def _yaml_dump(model: BaseModel) -> str:
        """
        Converts a Pydantic model to a YAML string.

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

        Returns:
            True if the user confirms, False otherwise
        """
        return self._ui_helper.prompt_yes_no_question("Would you like to generate a watchdog manifest (Y/N)?")
