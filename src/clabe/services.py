from __future__ import annotations

import abc
import logging
import typing as t
from typing import TYPE_CHECKING, Any, Dict, Type

import pydantic_settings as ps

from .utils import KNOWN_CONFIG_FILES

if TYPE_CHECKING:
    from .launcher import BaseLauncher
else:
    BaseLauncher = Any

logger = logging.getLogger(__name__)


class IService(abc.ABC):
    """
    A base class for all services.

    This abstract base class defines the interface that all services should inherit from.
    It serves as a marker interface to identify service implementations across the system.
    """


class ServiceSettings(ps.BaseSettings, abc.ABC):
    """
    Base class for service settings with YAML configuration support.

    This class provides automatic YAML configuration loading using pydantic-settings. The configuration is loaded from
    files defined in KNOWN_CONFIG_FILES.

    Attributes:
        _yml_section: Optional class variable to override the config section name

    Example:
        ```python
        # Define a settings class
        class MyServiceSettings(ServiceSettings):
            _yml_section: ClassVar[str] = "my_service"

            host: str = "localhost"
            port: int = 8080
            enabled: bool = True

        # Usage will automatically load from YAML files
        settings = MyServiceSettings()
        ```
    """

    _yml_section: t.ClassVar[t.Optional[str]] = None

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.model_config = ps.SettingsConfigDict(
            yaml_file=KNOWN_CONFIG_FILES, yaml_config_section=cls._yml_section, extra="ignore"
        )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[ps.BaseSettings],
        init_settings: ps.PydanticBaseSettingsSource,
        env_settings: ps.PydanticBaseSettingsSource,
        dotenv_settings: ps.PydanticBaseSettingsSource,
        file_secret_settings: ps.PydanticBaseSettingsSource,
    ) -> t.Tuple[ps.PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            _SafeYamlSettingsSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )


class _SafeYamlSettingsSource(ps.YamlConfigSettingsSource):
    def __init__(
        self,
        settings_cls: type[ps.BaseSettings],
        yaml_file: ps.sources.types.PathType | None = ps.sources.types.DEFAULT_PATH,
        yaml_file_encoding: str | None = None,
        yaml_config_section: str | None = None,
    ):
        try:
            # pydantic-settings will raise an error if a yaml_config_section is passed but is not found in the yaml file
            # We override this behavior to allow us to have a behavior as if the file did not exist in the first place
            # We may consider raising a more useful error in the future
            super().__init__(settings_cls, yaml_file, yaml_file_encoding, yaml_config_section)
        except KeyError:
            settings_cls.model_config.update({"yaml_config_section": None})
            super().__init__(settings_cls, yaml_file, yaml_file_encoding, None)

    def __call__(self) -> Dict[str, Any]:
        try:
            return super().__call__()
        except KeyError:
            return {}
