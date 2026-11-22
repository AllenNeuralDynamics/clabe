"""A base for command-line commands whose options come from the command line only."""

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource


class CliCommand(BaseSettings):
    """A command whose fields are read from the command line only, never from environment variables or files.

    A plain ``BaseSettings`` also reads each field from the environment variable of the same name, so a field named
    ``path`` would take the value of ``PATH``.
    """

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Only the values the command line passes in."""
        return (init_settings,)
