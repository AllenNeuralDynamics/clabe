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

    This class provides the foundation for mapping experimental data to AIND data schema
    formats, ensuring consistent structure and metadata handling across different data types.

    Attributes:
        session_name (str): The name of the session associated with the data

    Example:
        ```python
        # Example subclass implementing session_name
        class MySessionMapper(AindDataSchemaDataMapper):
            @property
            def session_name(self) -> str:
                return "session_001"
        ```
    """

    @property
    @abc.abstractmethod
    def session_name(self) -> str:
        """
        Abstract property that must be implemented to return the session name.

        Subclasses must implement this property to provide the session name
        associated with the data being mapped.

        Returns:
            str: The name of the session
        """


class AindDataSchemaSessionDataMapper(AindDataSchemaDataMapper[Session], abc.ABC):
    """
    Abstract base class for mapping session data to aind-data-schema Session objects.

    This class specializes the generic data mapper for session-specific data,
    providing the interface for converting experimental session data to the
    AIND data schema Session format.
    """

    def session_schema(self) -> Session:
        """
        Returns the session schema for the mapped session data.

        This method should be implemented by subclasses to return the specific
        session schema that corresponds to the data being mapped.

        Returns:
            ads_session.Session: The session schema object
        """
        raise NotImplementedError("Subclasses must implement this method to return the session schema.")


class AindDataSchemaRigDataMapper(AindDataSchemaDataMapper[Rig], abc.ABC):
    """
    Abstract base class for mapping rig data to aind-data-schema Rig objects.

    This class specializes the generic data mapper for rig-specific data,
    providing the interface for converting experimental rig configurations
    to the AIND data schema Rig format.
    """

    def rig_schema(self) -> Rig:
        """
        Returns the rig schema for the mapped rig data.

        This method should be implemented by subclasses to return the specific
        rig schema that corresponds to the data being mapped.

        Returns:
            ads_rig.Rig: The rig schema object
        """
        raise NotImplementedError("Subclasses must implement this method to return the rig schema.")
