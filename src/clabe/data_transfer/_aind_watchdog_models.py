import importlib.util

if importlib.util.find_spec("aind_data_transfer_service") is None:
    raise ImportError(
        "The 'aind_data_transfer_service' package is required to use this module. \
            Install the optional dependencies defined in `project.toml' \
                by running `pip install .[aind-services]`"
    )
import datetime
import logging
import pathlib
from typing import Annotated, Dict, List, Literal, Optional, Self, TypeAlias, Union

from aind_data_schema_models.data_name_patterns import build_data_name
from aind_data_transfer_service.models.core import SubmitJobRequestV2
from pydantic import AfterValidator, BaseModel, Field, SerializeAsAny, model_validator

# From https://github.com/AllenNeuralDynamics/aind-watchdog-service/blob/0.1.5/

Path: TypeAlias = Annotated[Union[pathlib.Path, str], AfterValidator(lambda v: pathlib.Path(v).as_posix())]

Modality: TypeAlias = str
BucketType: TypeAlias = Literal["private", "open", "default"]
DEFAULT_TRANSFER_ENDPOINT: str = "http://aind-data-transfer-service-dev/api/v2/submit_jobs"

logger = logging.getLogger(__name__)


class ManifestConfig(BaseModel, extra="ignore"):
    """Job configs for data transfer to VAST"""

    name: Optional[str] = Field(
        None,
        description="If not provided, gets generated to match CO asset. Leave as None to generate automatically server side.",
        title="Manifest name",
    )
    processor_full_name: str = Field(description="User who processed the data", title="Processor name")
    subject_id: int = Field(description="Subject ID", title="Subject ID")
    acquisition_datetime: datetime.datetime = Field(
        description="Acquisition datetime",
        title="Acquisition datetime",
    )
    schedule_time: Optional[datetime.time] = Field(
        default=None,
        description="Transfer time to schedule copy and upload. If None defaults to trigger the transfer immediately",
        title="APScheduler transfer time",
    )
    force_cloud_sync: bool = Field(
        default=False,
        description="Overwrite data in AWS",
        title="Force cloud sync",
    )
    transfer_endpoint: str = Field(
        default=DEFAULT_TRANSFER_ENDPOINT,
        description="Transfer endpoint for data transfer",
        title="Transfer endpoint",
    )
    capsule_id: Optional[str] = Field(default=None, description="Capsule ID of pipeline to run", title="Capsule")
    mount: Optional[str] = Field(default=None, description="Mount point for pipeline run", title="Mount point")
    s3_bucket: BucketType = Field(default="private", description="s3 endpoint", title="S3 endpoint")
    project_name: str = Field(description="Project name", title="Project name")
    destination: Path = Field(
        description="Remote directory on VAST where to copy the data to.",
        title="Destination directory",
        examples=[r"\\allen\aind\scratch\test"],
    )
    modalities: Dict[Modality, List[Path]] = Field(
        default={},
        description="list of ModalityFile objects containing modality names and associated files or directories",
        title="modality files",
    )
    schemas: List[Path] = Field(
        default=[],
        description="Where schema files to be uploaded are saved",
        title="Schema directory",
    )
    script: Dict[str, List[str]] = Field(
        default={},
        description="Set of commands to run in subprocess. - DEPRECATED - NONFUNCTIONAL",
        title="Commands",
    )
    transfer_service_args: Optional[SerializeAsAny[SubmitJobRequestV2]] = Field(
        default=None,
        description="Arguments to pass to data-transfer-service",
        title="Transfer service args",
    )

    delete_modalities_source_after_success: bool = False

    extra_identifying_info: Optional[dict] = None

    @model_validator(mode="after")
    def validate_capsule(self) -> Self:
        """Validate capsule and mount"""
        if (self.capsule_id is None) ^ (self.mount is None):
            raise ValueError("Both capsule and mount must be provided, or must both be None")
        return self

    @model_validator(mode="after")
    def set_name(self) -> Self:
        """Construct name"""
        if self.name is None:
            label = self.subject_id
            self.name = build_data_name(
                label=str(label),
                creation_datetime=self.acquisition_datetime,
            )
        return self


class ChecksumConfig(BaseModel, extra="ignore"):
    """Configuration for checksum generation"""

    max_retries: int = 3
    chunk_size: int = 1024 * 1024
    file_size_threshold: int = 10 * 1024 * 1024


class WatchConfig(BaseModel, extra="ignore"):
    """Configuration for rig"""

    flag_dir: str = Field(description="Directory for watchdog to poll", title="Poll directory")
    manifest_complete: str = Field(
        description="Manifest directory for triggered data",
        title="Manifest complete directory",
    )
    misfire_grace_time_s: Union[int, None] = Field(
        default=3 * 3600,
        description="If the job scheduler is busy, wait this long before skipping a job."
        + " If None, allow the job to run no matter how late it is",
        title="Scheduler grace time",
    )

    robocopy_args: list[str] = [
        "/e",
        "/z",
        "/j",
        "/r:5",
        "/np",
        "/log+:C:\\ProgramData\\AIBS_MPE\\aind_watchdog_service\\logs\\robocopy.log",
    ]

    windows_copy_utility: Literal["shutil", "robocopy"] = "robocopy"

    checksum_parameters: ChecksumConfig = ChecksumConfig()
