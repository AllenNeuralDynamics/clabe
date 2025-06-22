import os
from pathlib import Path
from typing import Optional, Tuple, Type

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    CliExplicitFlag,
    CliImplicitFlag,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class BaseCliArgs(BaseSettings, cli_prog_name="clabe", cli_kebab_case=True):
    """
    Base class for CLI arguments using Pydantic for validation and configuration.

    Attributes:
        data_dir (os.PathLike): The data directory where to save the data.
        repository_dir (Optional[os.PathLike]): The repository root directory.
        create_directories (CliImplicitFlag[bool]): Whether to create necessary directory structure.
        debug_mode (CliImplicitFlag[bool]): Whether to run in debug mode.
        allow_dirty (CliImplicitFlag[bool]): Whether to allow running with a dirty repository.
        skip_hardware_validation (CliImplicitFlag[bool]): Whether to skip hardware validation.
        subject (Optional[str]): The name of the subject. If None, will be prompted later.
        task_logic_path (Optional[os.PathLike]): Path to the task logic schema. If None, will be prompted later.
        rig_path (Optional[os.PathLike]): Path to the rig schema. If None, will be prompted later.
        validate_init (CliExplicitFlag[bool]): Whether to validate the launcher state during initialization.
        temp_dir (os.PathLike): Directory used for launcher temp files.
        group_by_subject_log (CliExplicitFlag[bool]): Whether to group data logging by subject.
    """

    model_config = SettingsConfigDict(
        env_prefix="CLABE_", yaml_file=["./clabe_default.yml", "./local/clabe_custom.yml"]
    )

    data_dir: os.PathLike = Field(description="The data directory where to save the data")
    repository_dir: Optional[os.PathLike] = Field(default=None, description="The repository root directory")
    create_directories: CliImplicitFlag[bool] = Field(
        default=False, description="Whether to create directory structure necessary for the launcher"
    )
    debug_mode: CliImplicitFlag[bool] = Field(default=False, description="Whether to run in debug mode")
    allow_dirty: CliImplicitFlag[bool] = Field(
        default=False, description="Whether to allow the launcher to run with a dirty repository"
    )
    skip_hardware_validation: CliImplicitFlag[bool] = Field(
        default=False, description="Whether to skip hardware validation"
    )
    subject: Optional[str] = Field(default=None, description="The name of the subject. If None, will be prompted later")
    task_logic_path: Optional[os.PathLike] = Field(
        default=None, description="The path to the task logic schema instance. If None, will be prompted later"
    )
    rig_path: Optional[os.PathLike] = Field(
        default=None, description="The path to the rig schema instance. If None, will be prompted later"
    )
    session_path: Optional[os.PathLike] = Field(
        default=None, description="The path to the session schema instance. If None, will be prompted later"
    )
    validate_init: CliExplicitFlag[bool] = Field(
        default=True, description="Whether to validate the launcher state during initialization"
    )
    temp_dir: os.PathLike = Field(
        default=Path("local/.temp"), description="The directory used for the launcher temp files"
    )
    group_by_subject_log: CliExplicitFlag[bool] = Field(
        default=True, description="Whether to group data logging by subject"
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        """
        Customizes the order of settings sources for the CLI.

        Args:
            settings_cls (Type[BaseSettings]): The settings class.
            init_settings (PydanticBaseSettingsSource): Initial settings source.
            env_settings (PydanticBaseSettingsSource): Environment variable settings source.
            dotenv_settings (PydanticBaseSettingsSource): Dotenv settings source.
            file_secret_settings (PydanticBaseSettingsSource): File secret settings source.

        Returns:
            Tuple[PydanticBaseSettingsSource, ...]: Ordered tuple of settings sources.
        """
        return (
            init_settings,
            YamlConfigSettingsSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )
