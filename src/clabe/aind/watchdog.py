import dataclasses
import datetime
import json
import logging
import os
import subprocess
import warnings
from collections.abc import Callable
from os import PathLike
from pathlib import Path, PurePosixPath
from typing import ClassVar, Literal, Self

import aind_data_transfer_service.models.core
import pydantic
import requests
import yaml
from aind_behavior_services import Session
from aind_watchdog_service.models import (
    ManifestConfig,
)
from aind_watchdog_service.models.watch_config import WatchConfig
from pydantic import BaseModel, SerializeAsAny, TypeAdapter
from requests.exceptions import HTTPError

from ..data_transfer import DataTransfer
from ..runnable import runnable
from ..settings import ServiceSettings

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_TRANSFER_ENDPOINT",
    "ManifestConfig",
    "PlannedManifest",
    "UploadGroup",
    "WatchConfig",
    "WatchdogDataTransferService",
    "WatchdogSettings",
]

DEFAULT_TRANSFER_ENDPOINT: str = "http://aind-data-transfer-service-dev/api/v2/submit_jobs"

DEFAULT_UPLOAD_GROUP_NAME = "primary"


def _default_email_from_experimenter(user_name: str) -> str:
    """Looks up the experimenter's email via Active Directory.

    Args:
        user_name: The username of the experimenter.

    Returns:
        The email address of the experimenter.

    Raises:
        requests.RequestException: If the Active Directory lookup fails.
        pydantic.ValidationError: If the Active Directory response is malformed.
        ValueError: If the user has no email on record.
    """
    from .validators import get_active_directory_user

    user = get_active_directory_user(user_name)
    if not user.email:
        raise ValueError(f"Active Directory record for user '{user_name}' has no email on file.")
    return user.email


TransferServiceTask = dict[
    str, aind_data_transfer_service.models.core.Task | dict[str, aind_data_transfer_service.models.core.Task]
]


class UploadGroup(BaseModel):
    """One upload job of a session, written as its own watchdog manifest.

    A session's modalities can be split into groups that are uploaded independently, e.g. to make the behavior data
    available hours before its videos are compressed. All groups write to the same folder and asset: the group with
    ``creates_record`` creates the asset's metadata record and the others append to it, so the job types must be set
    up for that in the data transfer service.

    The ``job_type``, ``schedule_time``, ``delete_modalities_source_after_success`` and ``upload_tasks`` fields of the
    ``watchdog`` section are the shorthand for a single group with every modality.
    """

    model_config = pydantic.ConfigDict(extra="forbid")

    name: str = pydantic.Field(
        pattern=r"^[A-Za-z0-9_-]+$", description="Names the group, and the manifest file of groups that append."
    )
    job_type: str = pydantic.Field(description="Transfer service job type.")
    modalities: list[str] | Literal["*"] = pydantic.Field(
        default="*", description="Modality abbreviations of this group, or '*' for every modality no group claims."
    )
    creates_record: bool = pydantic.Field(
        default=False,
        description="Whether this group creates the metadata record. It alone gets the session's *.json "
        "metadata files, the gather_preliminary_metadata task and the data description tags.",
    )
    schedule_time: datetime.time | None = pydantic.Field(
        default=datetime.time(hour=20), description="When the watchdog submits the group; null means now."
    )
    delete_modalities_source_after_success: bool = pydantic.Field(
        default=False, description="Deletes this group's modality folders once submitted."
    )
    upload_tasks: SerializeAsAny[TransferServiceTask] | None = pydantic.Field(
        default=None, description="Extra transfer service tasks."
    )

    @pydantic.field_validator("modalities")
    @classmethod
    def _validate_modalities(cls, value: list[str] | Literal["*"]) -> list[str] | Literal["*"]:
        """Refuses empty, unknown or duplicate modalities."""
        if value == "*":
            return value
        if not value:
            raise ValueError("modalities must not be empty; use '*' for every unclaimed modality.")
        known = aind_data_transfer_service.models.core.Modality.abbreviation_map.keys()
        unknown = [m for m in value if m not in known]
        if unknown:
            raise ValueError(f"Unknown modalities {unknown}.")
        if len(set(value)) != len(value):
            raise ValueError(f"Duplicate modalities in {value}.")
        return value


class WatchdogSettings(ServiceSettings):
    """
    Settings for the WatchdogDataTransferService.

    Configures data transfer operations including destination paths, scheduling,
    and integration with the AIND watchdog service.
    """

    __yml_section__: ClassVar[str | None] = "watchdog"

    destination: Path
    schedule_time: datetime.time | None = datetime.time(hour=20)
    project_name: str
    transfer_endpoint: str = DEFAULT_TRANSFER_ENDPOINT
    delete_modalities_source_after_success: bool = False
    extra_identifying_info: dict | None = None
    upload_tasks: SerializeAsAny[TransferServiceTask] | None = None
    job_type: str = "default"
    data_description_tags: list[str] | None = pydantic.Field(
        default=None, description="Tags to add to the data description via the gather_preliminary_metadata task"
    )
    extra_modality_data: dict[str, list[Path]] | None = pydantic.Field(
        default=None, description="Additional modality data to include in the transfer"
    )
    upload_groups: list[UploadGroup] | None = pydantic.Field(
        default=None,
        description="Splits the session into independent upload jobs, one manifest each. Without it, the session is "
        "a single job with every modality, configured by the deprecated job_type, schedule_time, "
        "delete_modalities_source_after_success and upload_tasks, which are ignored when this is set.",
    )

    @pydantic.model_validator(mode="after")
    def _warn_deprecated_single_job_fields(self) -> Self:
        """Warns that the single-job fields are deprecated in favor of upload_groups."""
        shorthand = sorted(_SINGLE_JOB_FIELDS & self.model_fields_set)
        if shorthand and self.upload_groups is None:
            warnings.warn(
                f"WatchdogSettings {shorthand} are deprecated: configure a single upload group in upload_groups "
                "instead (name, job_type, creates_record: true and the same fields).",
                FutureWarning,
                stacklevel=2,
            )
        return self

    @pydantic.field_validator("upload_groups")
    @classmethod
    def _validate_upload_groups(cls, value: list[UploadGroup] | None) -> list[UploadGroup] | None:
        """Requires unique names, exactly one group that creates the record, at most one '*' and disjoint groups."""
        if value is None:
            return value
        if not value:
            raise ValueError("upload_groups must not be empty; leave it out for a single job.")
        names = [g.name for g in value]
        if len(set(names)) != len(names):
            raise ValueError(f"Upload group names must be unique, got {names}.")
        record_groups = [g.name for g in value if g.creates_record]
        if len(record_groups) != 1:
            raise ValueError(f"Exactly one upload group must set creates_record, got {record_groups or 'none'}.")
        wildcard_groups = [g.name for g in value if g.modalities == "*"]
        if len(wildcard_groups) > 1:
            raise ValueError(f"At most one upload group can claim '*', got {wildcard_groups}.")
        claimed: dict[str, str] = {}
        for group in value:
            if group.modalities == "*":
                continue
            for modality in group.modalities:
                if modality in claimed:
                    raise ValueError(
                        f"Modality '{modality}' is claimed by both '{claimed[modality]}' and '{group.name}'."
                    )
                claimed[modality] = group.name
        return value

    def resolved_upload_groups(self) -> list[UploadGroup]:
        """The session's upload groups, the group that creates the record first.

        Without ``upload_groups``, a single group with every modality, from the deprecated single-job fields. It is
        built on each call, so changes made to those fields after the settings are created apply. With
        ``upload_groups``, those fields are ignored, with a warning if any of them was set.
        """
        if self.upload_groups is not None:
            ignored = sorted(_SINGLE_JOB_FIELDS & self.model_fields_set)
            if ignored:
                logger.warning(
                    "WatchdogSettings %s are ignored because upload_groups is set: set them on the groups instead.",
                    ignored,
                )
        groups = self.upload_groups or [
            UploadGroup(
                name=DEFAULT_UPLOAD_GROUP_NAME,
                job_type=self.job_type,
                modalities="*",
                creates_record=True,
                schedule_time=self.schedule_time,
                delete_modalities_source_after_success=self.delete_modalities_source_after_success,
                upload_tasks=self.upload_tasks,
            )
        ]
        return sorted(groups, key=lambda g: not g.creates_record)


# The WatchdogSettings fields that configure the single job a session is without upload_groups
_SINGLE_JOB_FIELDS = frozenset({"job_type", "schedule_time", "delete_modalities_source_after_success", "upload_tasks"})


@dataclasses.dataclass
class PlannedManifest:
    """A manifest to write to the watchdog's flag directory.

    Attributes:
        group: Name of the upload group.
        creates_record: Whether the group creates the metadata record.
        filename: The manifest's file name in the watchdog's flag directory.
        manifest: The manifest.
    """

    group: str
    creates_record: bool
    filename: str
    manifest: ManifestConfig


class WatchdogDataTransferService(DataTransfer[WatchdogSettings]):
    """
    A data transfer service that uses the aind-watchdog-service to monitor and transfer data.

    Integrates with the AIND data transfer infrastructure to automatically monitor
    directories for new data and transfer it to specified destinations with proper
    metadata handling and validation.

    A session is written as one manifest per upload group (see ``WatchdogSettings.upload_groups``).

    Methods:
        transfer: Writes the session's manifests and starts the watchdog service
        plan: Builds the session's manifests without writing them
        validate: Validates the Watchdog service and its configuration
        is_valid_project_name: Checks if the project name is valid
        is_running: Checks if the Watchdog service is currently running
        force_restart: Attempts to restart the Watchdog application
        dump_manifest_config: Dumps the manifest configuration to a YAML file
    """

    def __init__(
        self,
        source: PathLike | list[PathLike],
        settings: WatchdogSettings,
        session: Session,
        *,
        validate: bool = False,
        email_from_experimenter_builder: Callable[[str], str] | None = _default_email_from_experimenter,
    ) -> None:
        """
        Initializes the WatchdogDataTransferService.

        Args:
            source: The session folder(s) to transfer. Folders on other machines are given through their share.
            settings: Configuration for the watchdog service
            session: The session data from aind-behavior-services
            validate: Whether to validate the project name
            email_from_experimenter_builder: Function to build email from experimenter name. Defaults to
                looking up the email via Active Directory, raising ``ValueError`` if it cannot be
                determined.
        """
        self._settings = settings
        self._sources = source if isinstance(source, list) else [source]

        self._session = session

        _default_exe = os.environ.get("WATCHDOG_EXE", None)
        _default_config = os.environ.get("WATCHDOG_CONFIG", None)

        if _default_exe is None or _default_config is None:
            raise ValueError("WATCHDOG_EXE and WATCHDOG_CONFIG environment variables must be defined.")

        self.executable_path = Path(_default_exe)
        self.config_path = Path(_default_config)

        self._watch_config: WatchConfig | None = None
        self._manifest_config: ManifestConfig | None = None

        self._validate_project_name = validate

        if validate:
            self.validate()

        self._watch_config = WatchConfig.model_validate(self._read_yaml(self.config_path))

        self._email_from_experimenter_builder = email_from_experimenter_builder

    @runnable(name="Transfer (watchdog)", notify="Transferring data (watchdog)…")
    def transfer(self) -> None:
        """
        Writes the session's watchdog manifests, one per upload group, then starts the watchdog if needed.

        The manifest of the group that creates the metadata record is written first, and nothing else is written if
        it fails. The manifests are written even if the service is not running, since it processes existing
        manifests when it starts; the service is then started if needed. Failing to start it is logged, never
        raised, since the manifests are already queued.
        """
        try:
            logger.debug("Creating watchdog manifest config(s).")

            if self._watch_config is None:
                raise ValueError("Watchdog config is not set.")

            planned = self.plan()
            self._manifest_config = planned[0].manifest
            written: list[Path] = []
            for item in planned:
                written.append(
                    self.dump_manifest_config(
                        path=Path(self._watch_config.flag_dir) / item.filename, manifest=item.manifest
                    )
                )
                logger.info("Watchdog manifest for '%s' created successfully at %s.", item.group, written[-1])

        except (OSError, pydantic.ValidationError, ValueError) as e:
            logger.error("Failed to create watchdog manifest config. %s", e)
            raise

        try:
            if self.is_running():
                return
            logger.warning("Watchdog service is not running. Attempting to start it.")
            self.force_restart(kill_if_running=False)
            started = self.is_running()
        except Exception as e:  # noqa: BLE001 -- the manifest is queued, so a failed start must not fail the transfer
            logger.error("Failed to start watchdog service: %s", e)
            started = False
        if started:
            logger.info("Watchdog service restarted successfully.")
        else:
            logger.error(
                "Watchdog service is not running. The manifest(s) %s are written and will be transferred "
                "once the watchdog service runs.",
                ", ".join(str(p) for p in written),
            )

    def plan(self, session: Session | None = None) -> list[PlannedManifest]:
        """
        Builds the session's manifests, one per upload group, without writing them.

        Each source is searched for folders named after a modality and for ``*.json`` metadata files. Each modality
        goes to the group that claims it, or to the ``'*'`` group. Groups that find none of their modalities are
        skipped.

        Args:
            session: The session. Defaults to the service's.

        Returns:
            The manifests, the one of the group that creates the record first.

        Raises:
            FileNotFoundError: If a source cannot be reached.
            ValueError: If a modality is claimed by no group, the group that creates the record finds none of its
                modalities, or a group that appends is submitted before it.
        """
        session = session or self._session

        if (len(session.experimenter) > 0) and self._email_from_experimenter_builder is not None:
            user_email = self._email_from_experimenter_builder(session.experimenter[-1])
        else:
            user_email = None

        destination = Path(self._settings.destination).resolve()

        sources = {Path(s).resolve() for s in self._sources}
        # Sources may be network shares on other machines, so report every unreachable one up front
        missing = sorted(str(s) for s in sources if not s.is_dir())
        if missing:
            raise FileNotFoundError(f"Watchdog source directories not found or not reachable: {', '.join(missing)}")

        if self._validate_project_name:
            project_names = self._get_project_names()
            if self._settings.project_name not in project_names:
                raise ValueError(f"Project name {self._settings.project_name} not found in {project_names}")

        found, schemas = self._discover(sources)

        groups = self._settings.resolved_upload_groups()
        claimed = {m for g in groups if g.modalities != "*" for m in g.modalities}
        unclaimed = [m for m in found if m not in claimed]  # in the order found, as in the manifest
        if unclaimed and not any(g.modalities == "*" for g in groups):
            raise ValueError(f"Modalities {unclaimed} are claimed by no upload group. Add them to one, or add '*'.")
        self._check_schedule_order(groups)

        planned: list[PlannedManifest] = []
        for group in groups:
            names = unclaimed if group.modalities == "*" else group.modalities
            modalities = {m: found[m] for m in names if m in found}
            if not modalities:
                if group.creates_record:
                    raise ValueError(
                        f"Upload group '{group.name}' creates the record but found none of its modalities."
                    )
                logger.warning("Upload group '%s' found none of its modalities %s. Skipping it.", group.name, names)
                continue
            not_found = [m for m in names if m not in found]
            if not_found:
                logger.info("Upload group '%s' did not find modalities %s.", group.name, not_found)
            planned.append(
                self._plan_group(
                    session,
                    group,
                    modalities,
                    schemas if group.creates_record else [],
                    destination=destination,
                    user_email=user_email,
                )
            )
        return planned

    def _discover(self, sources: set[Path]) -> tuple[dict[str, list[Path]], list[Path]]:
        """Finds the modality folders (including ``extra_modality_data``) and the metadata files of the sources."""
        # Invert so that unique modality names map to lists of paths
        found: dict[str, list[Path]] = {}
        for p in sources:
            for modality, paths in self._find_modality_candidates(p).items():
                found.setdefault(modality, []).extend(paths)
        for modality, paths in (self._settings.extra_modality_data or {}).items():
            found.setdefault(modality, []).extend(paths)

        # Collect unique schema candidates from all sources
        schemas: list[Path] = []
        for p in sources:
            schemas.extend(self._find_schema_candidates(p))
        return found, list({s.resolve() for s in schemas})

    @staticmethod
    def _check_schedule_order(groups: list[UploadGroup]) -> None:
        """Refuses groups that append submitted now while the record is created later, and warns about earlier times.

        A time of day earlier than the record's may still run after it, past midnight, so it is only a warning.
        """
        record_group = groups[0]
        if record_group.schedule_time is None:
            return
        for group in groups[1:]:
            if group.schedule_time is None:
                raise ValueError(
                    f"Upload group '{group.name}' is submitted now, before '{record_group.name}', which creates the "
                    f"record it appends to, at {record_group.schedule_time}."
                )
            if group.schedule_time < record_group.schedule_time:
                logger.warning(
                    "Upload group '%s' is scheduled at %s, before '%s' (%s), unless it runs after midnight.",
                    group.name,
                    group.schedule_time,
                    record_group.name,
                    record_group.schedule_time,
                )

    def _plan_group(
        self,
        session: Session,
        group: UploadGroup,
        modalities: dict[str, list[Path]],
        schemas: list[Path],
        *,
        destination: Path,
        user_email: str | None,
    ) -> PlannedManifest:
        """Builds the manifest of one upload group."""
        manifest = ManifestConfig(
            name=session.session_name,
            modalities={m: [str(Path(p)) for p in paths] for m, paths in modalities.items()},
            subject_id=session.subject,
            acquisition_datetime=session.date,
            schemas=[str(Path(value)) for value in schemas],
            destination=str(Path(destination)),
            project_name=self._settings.project_name,
            schedule_time=group.schedule_time,
            transfer_endpoint=self._settings.transfer_endpoint,
            delete_modalities_source_after_success=group.delete_modalities_source_after_success,
            extra_identifying_info=self._settings.extra_identifying_info,
            transfer_service_job_type=group.job_type,
            checksum_mode_override=None,
        )
        manifest = self._make_transfer_args(
            manifest,
            add_default_tasks=True,
            gather_metadata=group.creates_record,
            extra_tasks=group.upload_tasks or {},
            job_type=group.job_type,
            user_email=user_email,
        )
        if group.creates_record and self._settings.data_description_tags:
            manifest = self._apply_data_description_tags(manifest)

        assert manifest.name is not None, "Manifest config name must be set."
        suffix = "" if group.creates_record else f"__{group.name}"
        return PlannedManifest(
            group=group.name,
            creates_record=group.creates_record,
            filename=f"manifest_{manifest.name}{suffix}.yaml",
            manifest=manifest,
        )

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

    def _create_manifest_from_session(self, session: Session) -> ManifestConfig:
        """
        Creates the manifest of the upload group that creates the metadata record.

        Args:
            session: The aind-behavior-services session data

        Returns:
            A ManifestConfig object

        Raises:
            ValueError: If the project name is invalid
        """
        return self.plan(session)[0].manifest

    def add_data_description_tags(self, *tags: str) -> None:
        """
        Registers tags to be added to the data description.

        The tags are stored in the settings and written to the manifest of the group that creates the record when
        it is built (i.e. on `transfer`). Duplicates are dropped when written.

        Args:
            *tags: The tags to add
        """
        self._settings.data_description_tags = [*(self._settings.data_description_tags or []), *tags]

    def _apply_data_description_tags(self, manifest: ManifestConfig) -> ManifestConfig:
        """
        Mutates a manifest so that the registered tags are added to the data description.

        The tags are written to the `data_description_settings.tags` field of the
        `gather_preliminary_metadata` task job settings (see aind-metadata-mapper's
        `JobSettings`). The task is created if missing. Existing tags are preserved
        and duplicates are dropped.

        Args:
            manifest: The manifest configuration to mutate

        Returns:
            The same manifest, with the tags added

        Raises:
            ValueError: If the manifest has no transfer service args
        """
        tags = set(self._settings.data_description_tags or [])
        if manifest.transfer_service_args is None:
            raise ValueError("Manifest has no transfer service args. Cannot add tags.")

        for job in manifest.transfer_service_args.upload_jobs:
            task = job.tasks.get("gather_preliminary_metadata")
            if not isinstance(task, aind_data_transfer_service.models.core.Task):
                task = aind_data_transfer_service.models.core.Task()
                job.tasks["gather_preliminary_metadata"] = task
            job_settings = dict(task.job_settings or {})
            data_description = dict(job_settings.get("data_description_settings") or {})
            data_description["tags"] = list(set(data_description.get("tags") or []) | tags)
            job_settings["data_description_settings"] = data_description
            task.job_settings = job_settings
        return manifest

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
        gather_metadata: bool = True,
        extra_tasks: TransferServiceTask,
        user_email: str | None = None,
    ) -> ManifestConfig:
        """
        Appends tasks to a manifest configuration.

        Adds metadata and modality transformation tasks to the manifest, along with
        any extra tasks specified.

        Args:
            manifest: The manifest configuration to update
            job_type: The job type identifier
            add_default_tasks: Whether to add default metadata and transformation tasks
            gather_metadata: Whether the default tasks include gather_preliminary_metadata, which creates the
                metadata record. Jobs that append to an existing record leave it out.
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
                for modality in manifest.modalities
            }

            if gather_metadata:
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
            modalities=[
                aind_data_transfer_service.models.core.Modality.from_abbreviation(m) for m in manifest.modalities
            ],
            subject_id=str(manifest.subject_id),
            acq_datetime=manifest.acquisition_datetime.strftime("%Y-%m-%d %H:%M:%S"),
            tasks=tasks,
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
    def _find_schema_candidates(source: PathLike) -> list[Path]:
        """
        Finds json files in the source directory

        Args:
            source: The source directory to search

        Returns:
            A list of schema file paths
        """
        return list(Path(source).glob("*.json"))

    @staticmethod
    def _find_modality_candidates(source: PathLike) -> dict[str, list[Path]]:
        """
        Finds modality files in the source directory.

        Args:
            source: The source directory to search
        Returns:
            A list of modality directory paths
        """
        # The assumption is that the modality directories are named after the modality abbreviations
        _candidates = aind_data_transfer_service.models.core.Modality.abbreviation_map.keys()
        modality_dirs = {}
        for _dir in Path(source).iterdir():
            if _dir.is_dir() and _dir.name in _candidates:
                modality_dirs[_dir.name] = [_dir.resolve()]
                continue
            if _dir.is_dir():
                logger.warning("Directory %s is not a recognized modality directory. Will not be appended", _dir.name)
        return modality_dirs

    @staticmethod
    def _get_project_names(
        end_point: str = "http://aind-metadata-service/api/v2/project_names", timeout: int = 5
    ) -> list[str]:
        """
        Fetches the list of valid project names from the metadata service.

        Args:
            end_point: The endpoint URL for the metadata service.
            timeout: Timeout for the request in seconds.

        Returns:
            A list of valid project names.

        Raises:
            HTTPError: If the request fails.
        """
        response = requests.get(end_point, timeout=timeout)
        if response.ok:
            return response.json()
        else:
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

        cmd_factory = f"{self.executable_path} -c {self.config_path}"

        return subprocess.Popen(cmd_factory, start_new_session=True, shell=True)

    def dump_manifest_config(
        self, path: os.PathLike | None = None, make_dir: bool = True, manifest: ManifestConfig | None = None
    ) -> Path:
        """
        Dumps a manifest configuration to a YAML file.

        Args:
            path: The file path to save the manifest
            make_dir: Whether to create the directory if it doesn't exist
            manifest: The manifest to dump. Defaults to the last one built by `transfer`.

        Returns:
            The path to the saved manifest file

        Raises:
            ValueError: If the manifest or watch configuration is not set
        """
        manifest_config = manifest or self._manifest_config
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

        manifest_config.destination = str(Path(manifest_config.destination))
        manifest_config.schemas = [str(Path(schema)) for schema in manifest_config.schemas]
        for modality in manifest_config.modalities:
            manifest_config.modalities[modality] = [str(Path(_path)) for _path in manifest_config.modalities[modality]]

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
