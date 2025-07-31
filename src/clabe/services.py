from __future__ import annotations

import abc
import logging
import typing as t
from typing import TYPE_CHECKING, Any, Callable, Dict, Type

import pydantic_settings as ps

from .utils import KNOWN_CONFIG_FILES

if TYPE_CHECKING:
    from .launcher import Launcher
else:
    Launcher = Any

logger = logging.getLogger(__name__)


class Service(abc.ABC):
    """
    A base class for all services.

    This abstract base class defines the interface that all services should inherit from.
    It serves as a marker interface to identify service implementations across the system.
    """

    def __init__(self, *args, **kwargs):
        """Initializes the service."""
        pass

    def build_runner(self, *args, **kwargs) -> Callable[[Launcher], Any]:
        """
        Builds a runner function for the service.

        Subclasses must implement this method to return a callable that can be executed by the launcher.
        """
        return lambda launcher: None


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
    def __init_subclass__(cls, *args, **kwargs):
        """Initializes the subclass and sets up the YAML configuration."""
        super().__init_subclass__(*args, **kwargs)
        cls.model_config.update(
            ps.SettingsConfigDict(yaml_file=KNOWN_CONFIG_FILES, yaml_config_section=cls._yml_section, extra="ignore")
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
        """
        Customizes the settings sources to include the safe YAML settings source.

        Args:
            settings_cls: The settings class.
            init_settings: The initial settings source.
            env_settings: The environment settings source.
            dotenv_settings: The dotenv settings source.
            file_secret_settings: The file secret settings source.

        Returns:
            A tuple of settings sources.
        """
        return (
            init_settings,
            _SafeYamlSettingsSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )


class _SafeYamlSettingsSource(ps.YamlConfigSettingsSource):
    """
    A safe YAML settings source that does not raise an error if the YAML configuration section is not found.
    """

    def __init__(
        self,
        settings_cls: type[ps.BaseSettings],
        yaml_file: ps.sources.types.PathType | None = ps.sources.types.DEFAULT_PATH,
        yaml_file_encoding: str | None = None,
        yaml_config_section: str | None = None,
    ):
        """
        Initializes the safe YAML settings source.

        Args:
            settings_cls: The settings class.
            yaml_file: The YAML file path.
            yaml_file_encoding: The YAML file encoding.
            yaml_config_section: The YAML configuration section.
        """
        try:
            # pydantic-settings will raise an error if a yaml_config_section is passed but is not found in the yaml file
            # We override this behavior to allow us to have a behavior as if the file did not exist in the first place
            # We may consider raising a more useful error in the future
            super().__init__(settings_cls, yaml_file, yaml_file_encoding, yaml_config_section)
        except KeyError:
            settings_cls.model_config.update({"yaml_config_section": None})
            super().__init__(settings_cls, yaml_file, yaml_file_encoding, None)

    def __call__(self) -> Dict[str, Any]:
        """
        Calls the settings source and returns the settings dictionary.

        Returns:
            A dictionary of settings.
        """
        try:
            return super().__call__()
        except KeyError:
            return {}
