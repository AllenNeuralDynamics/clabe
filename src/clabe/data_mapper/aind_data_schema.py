import importlib.metadata
import importlib.util

if importlib.util.find_spec("aind_data_schema") is None:
    raise ImportError(
        "The 'aind-data-schema' package is required to use this module. "
        "Install the optional dependencies defined in `project.toml` "
        "by running `pip install .[aind-services]`"
    )
else:
    import importlib.metadata

    import semver

    ads_version = semver.Version.parse(importlib.metadata.version("aind-data-schema"))

import abc
import logging
from typing import TypeAlias, TypeVar, Union

from ..data_mapper import _base

logger = logging.getLogger(__name__)

# This ensures that clabe works across aind-data-schema versions
if ads_version.major < 2:
    from aind_data_schema.core.rig import Rig
    from aind_data_schema.core.session import Session

    Acquisition: TypeAlias = Session
    Instrument: TypeAlias = Rig
    logger.warning("Using deprecated AIND data schema version %s. Consider upgrading.", ads_version)

else:
    from aind_data_schema.core.acquisition import Acquisition
    from aind_data_schema.core.instrument import Instrument

    Session: TypeAlias = Acquisition
    Rig: TypeAlias = Instrument


_TAdsObject = TypeVar("_TAdsObject", bound=Union[Session, Rig, Acquisition, Instrument])


class AindDataSchemaDataMapper(_base.DataMapper[_TAdsObject], abc.ABC):
    """
    Abstract base class for mapping data to aind-data-schema objects.

    Provides the foundation for mapping experimental data to AIND data schema
    formats, ensuring consistent structure and metadata handling.
    """

    @property
    @abc.abstractmethod
    def session_name(self) -> str:
        """
        Returns the session name associated with the data.

        Returns:
            The name of the session
        """


class AindDataSchemaSessionDataMapper(AindDataSchemaDataMapper[Session], abc.ABC):
    """
    Abstract base class for mapping session data to aind-data-schema Session objects.

    Specializes the generic data mapper for session-specific data, providing the
    interface for converting experimental session data to the AIND data schema format.
    """

    def session_schema(self) -> Session:
        """
        Returns the session schema for the mapped session data.

        Returns:
            The session schema object
        """
        raise NotImplementedError("Subclasses must implement this method to return the session schema.")


class AindDataSchemaRigDataMapper(AindDataSchemaDataMapper[Rig], abc.ABC):
    """
    Abstract base class for mapping rig data to aind-data-schema Rig objects.

    Specializes the generic data mapper for rig-specific data, providing the
    interface for converting experimental rig configurations to the AIND data schema format.
    """

    def rig_schema(self) -> Rig:
        """
        Returns the rig schema for the mapped rig data.

        Returns:
            The rig schema object
        """
        raise NotImplementedError("Subclasses must implement this method to return the rig schema.")
