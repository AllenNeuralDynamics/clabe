"""Settings: ``ServiceSettings`` (read from the environment, ``clabe.yml`` files and an in-memory document), the
``clabe.yml`` files the launcher knows, and where it keeps temporary files."""

import abc
import contextlib
import contextvars
import logging
import os
import typing as t
from pathlib import Path

import pydantic_settings as ps

logger = logging.getLogger(__name__)

#: Where the launcher keeps temporary files, relative to the working directory.
TMP_DIR = ".cache"

PROGRAMDATA_DIR = os.environ.get("PROGRAMDATA", "C:/ProgramData")

#: The clabe.yml files, highest priority first.
KNOWN_CONFIG_FILES: list[str] = [
    "./local/clabe.yml",
    str(Path(PROGRAMDATA_DIR) / "clabe.yml"),
]

for _rank, _file in enumerate(KNOWN_CONFIG_FILES):
    if Path(_file).exists():
        logger.debug("Found config file: %s with rank priority %s", _file, _rank)

# Sentinel set by `override_clabe_yml(None)` to disable the in-memory document inside a block
_NO_CONFIG: t.Mapping[str, t.Any] = {}

_global_config: t.Mapping[str, t.Any] | None = None
_context_config: contextvars.ContextVar[t.Mapping[str, t.Any] | None] = contextvars.ContextVar(
    "_context_config", default=None
)


def set_clabe_yml(config: t.Mapping[str, t.Any] | None) -> None:
    """
    Installs a process-wide, in-memory configuration document.

    The document has the same shape as a clabe.yml file (top-level keys are ``__yml_section__`` names) and is
    consulted by every `ServiceSettings` subclass constructed afterwards. It takes priority over all
    known config files except ``./local/clabe.yml``. Settings objects created before this call are not affected.

    Args:
        config: The configuration document, e.g. fetched from a database. ``None`` clears it.

    Raises:
        TypeError: If ``config`` is not a mapping, e.g. a path or raw YAML text.

    Example:
        ```python
        set_clabe_yml(fetch_clabe_config_from_db())
        settings = MyServiceSettings()  # reads the "my_service" section of the in-memory document
        ```
    """
    global _global_config
    _global_config = _as_document(config)


@contextlib.contextmanager
def override_clabe_yml(config: t.Mapping[str, t.Any] | None) -> t.Iterator[None]:
    """
    Temporarily overrides the in-memory configuration document for the current context.

    While active, the given document replaces the one installed by `set_clabe_yml`. Passing ``None``
    disables the in-memory document inside the block. The override is scoped with a ``ContextVar``, so it
    does not leak across threads or asyncio tasks.

    Args:
        config: The configuration document to use inside the block.

    Example:
        ```python
        with override_clabe_yml({"my_service": {"port": 9090}}):
            settings = MyServiceSettings()
        ```
    """
    document = _as_document(config)
    token = _context_config.set(document if document is not None else _NO_CONFIG)
    try:
        yield
    finally:
        _context_config.reset(token)


def get_clabe_yml() -> t.Mapping[str, t.Any] | None:
    """
    Returns the active in-memory configuration document, if any.

    A document set via `override_clabe_yml` takes precedence over the one set via `set_clabe_yml`.

    Returns:
        The active configuration document, or ``None`` if none is installed.
    """
    ctx = _context_config.get()
    if ctx is _NO_CONFIG:
        return None
    return ctx if ctx is not None else _global_config


def _as_document(config: t.Mapping[str, t.Any] | None) -> dict[str, t.Any] | None:
    """Validates and copies a clabe.yml document, rejecting paths and raw YAML text."""
    if config is None:
        return None
    if isinstance(config, (str, bytes, os.PathLike)):
        raise TypeError(
            "Expected the parsed clabe.yml document as a mapping, not a path or YAML text. "
            "Parse it first, e.g. set_clabe_yml(yaml.safe_load(text))."
        )
    if not isinstance(config, t.Mapping):
        raise TypeError(f"Expected the clabe.yml document as a mapping, got {type(config).__name__}.")
    return dict(config)


def _read_clabe_yml(path: os.PathLike[str] | str) -> dict[str, t.Any]:
    """
    Reads a clabe.yml file from an arbitrary path.

    Args:
        path: Path to the YAML file.

    Returns:
        The parsed document. An empty file yields an empty document.

    Raises:
        ImportError: If PyYAML is not installed.
        ValueError: If the file does not contain a mapping at the top level.
    """
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("Reading a clabe.yml file requires PyYAML: pip install pyyaml") from exc

    with open(path, "r", encoding="utf-8") as f:
        document = yaml.safe_load(f)
    if document is None:
        return {}
    if not isinstance(document, dict):
        # ValueError (not TypeError) so pydantic reports it as a validation error of the --clabe-yml field
        raise ValueError(  # noqa: TRY004
            f"{path} must contain a mapping at the top level, got {type(document).__name__}."
        )
    return document


class Service(abc.ABC):
    """
    Abstract base class for all services in the application.

    This may be needed in the future to ensure a common interface.
    """


class ServiceSettings(ps.BaseSettings, abc.ABC):
    """
    Base class for service settings with YAML configuration support.

    This class provides automatic YAML configuration loading using pydantic-settings. Highest priority first: keyword
    arguments, the in-memory document (``--clabe-yml``, `set_clabe_yml` or `override_clabe_yml`),
    ``./local/clabe.yml``, ``%PROGRAMDATA%/clabe.yml``, then environment variables named
    ``CLABE_<SECTION>_<FIELD>`` (e.g. ``CLABE_WATCHDOG_JOB_TYPE``; ``CLABE_<FIELD>`` without a section).

    Attributes:
        __yml_section__: Optional class variable to override the config section name

    Example:
        ```python
        # Define a settings class
        class MyServiceSettings(ServiceSettings):
            __yml_section__: ClassVar[str] = "my_service"

            host: str = "localhost"
            port: int = 8080
            enabled: bool = True

        # Usage will automatically load from YAML files
        settings = MyServiceSettings()
        ```
    """

    __yml_section__: t.ClassVar[str | None] = None

    @classmethod
    def __init_subclass__(cls, *args, **kwargs):
        """
        Initializes the subclass and sets up the YAML configuration.

        Args:
            *args: Positional arguments
            **kwargs: Keyword arguments
        """
        super().__init_subclass__(*args, **kwargs)
        section = cls.__dict__.get("__yml_section__", cls.__yml_section__)
        prefix = getattr(cls, "__env_prefix__", None)
        if prefix is None:
            prefix = f"CLABE_{section.upper()}_" if section else "CLABE_"
        # An empty variable (TOKEN=) is as if it were not set
        cls.model_config.update(ps.SettingsConfigDict(extra="ignore", env_prefix=prefix, env_ignore_empty=True))

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[ps.BaseSettings],
        init_settings: ps.PydanticBaseSettingsSource,
        env_settings: ps.PydanticBaseSettingsSource,
        dotenv_settings: ps.PydanticBaseSettingsSource,
        file_secret_settings: ps.PydanticBaseSettingsSource,
    ) -> tuple[ps.PydanticBaseSettingsSource, ...]:
        """
        Customizes the settings sources to include the safe YAML settings source.

        Args:
            settings_cls: The settings class
            init_settings: The initial settings source
            env_settings: The environment settings source
            dotenv_settings: The dotenv settings source
            file_secret_settings: The file secret settings source

        Returns:
            Tuple[PydanticBaseSettingsSource, ...]: A tuple of settings sources
        """
        yaml_sources = [
            _SafeYamlSettingsSource(settings_cls, yaml_file=p, yaml_config_section=cls.__yml_section__)
            for p in KNOWN_CONFIG_FILES
        ]
        # The in-memory document (--clabe-yml) ranks above every file
        return (
            init_settings,
            _InMemorySettingsSource(settings_cls, config_section=cls.__yml_section__),
            *yaml_sources,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )


class EnvSettings(ServiceSettings):
    """Settings read only from keyword arguments and environment variables (``<__env_prefix__><FIELD>``), never
    from clabe.yml: the daemon's (``DAEMON_CLABE_``) and the hub's (no prefix: they live in its container's ``.env``)."""

    __env_prefix__: t.ClassVar[str | None] = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[ps.BaseSettings],
        init_settings: ps.PydanticBaseSettingsSource,
        env_settings: ps.PydanticBaseSettingsSource,
        dotenv_settings: ps.PydanticBaseSettingsSource,
        file_secret_settings: ps.PydanticBaseSettingsSource,
    ) -> tuple[ps.PydanticBaseSettingsSource, ...]:
        """Keyword arguments, then the environment, then a ``.env`` file when the class names one."""
        return (init_settings, env_settings, dotenv_settings)


def split_list(value: t.Any) -> t.Any:
    """A list from an environment variable: a JSON list, or items separated by ``;``; anything else unchanged."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.startswith("["):
        import json

        return json.loads(text)
    return [item.strip() for item in text.split(";") if item.strip()]


class _SafeYamlSettingsSource(ps.YamlConfigSettingsSource):
    """
    A safe YAML settings source that does not raise an error if the YAML configuration section is not found.

    This class extends YamlConfigSettingsSource to gracefully handle missing configuration sections,
    allowing the settings to continue loading from other sources when a specific YAML section is absent.
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
            settings_cls: The settings class
            yaml_file: The YAML file path. Defaults to DEFAULT_PATH
            yaml_file_encoding: The YAML file encoding. Defaults to None
            yaml_config_section: The YAML configuration section. Defaults to None
        """
        try:
            # pydantic-settings will raise an error if a yaml_config_section is passed but is not found in the yaml file
            # We override this behavior to allow us to have a behavior as if the file did not exist in the first place
            # We may consider raising a more useful error in the future
            super().__init__(settings_cls, yaml_file, yaml_file_encoding, yaml_config_section)
        except KeyError:
            settings_cls.model_config.update({"yaml_config_section": None})
            super().__init__(settings_cls, yaml_file, yaml_file_encoding, None)

    def __call__(self) -> dict[str, t.Any]:
        """
        Calls the settings source and returns the settings dictionary.

        Returns:
            Dict[str, Any]: A dictionary of settings
        """
        try:
            return super().__call__()
        except KeyError:
            return {}


class _InMemorySettingsSource(ps.PydanticBaseSettingsSource):
    """
    A settings source that reads from the active in-memory configuration document.

    Mirrors `_SafeYamlSettingsSource`: a missing document or section yields no settings.
    """

    def __init__(self, settings_cls: type[ps.BaseSettings], config_section: str | None = None):
        """
        Initializes the in-memory settings source.

        Args:
            settings_cls: The settings class
            config_section: The configuration section to read. Defaults to None (the whole document)
        """
        super().__init__(settings_cls)
        self._config_section = config_section

    def get_field_value(self, field: t.Any, field_name: str) -> tuple[t.Any, str, bool]:
        """Unused; values are resolved in bulk by `__call__`."""
        return None, field_name, False

    def __call__(self) -> dict[str, t.Any]:
        """
        Calls the settings source and returns the settings dictionary.

        Returns:
            Dict[str, Any]: A dictionary of settings
        """
        config = get_clabe_yml()
        if config is None:
            return {}
        if self._config_section is None:
            return dict(config)
        section = config.get(self._config_section)
        return dict(section) if isinstance(section, t.Mapping) else {}
