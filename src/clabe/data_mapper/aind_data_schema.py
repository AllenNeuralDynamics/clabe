from __future__ import annotations

import importlib.util

if importlib.util.find_spec("aind_data_schema") is None:
    raise ImportError(
        "The 'aind-data-schema' package is required to use this module. "
        "Install the optional dependencies defined in `project.toml` "
        "by running `pip install .[aind-services]`"
    )

import abc
import logging
from typing import TypeVar, Union

from aind_data_schema.core import rig as ads_rig
from aind_data_schema.core import session as ads_session

from ..data_mapper import _base

logger = logging.getLogger(__name__)

_TAdsObject = TypeVar("_TAdsObject", bound=Union[ads_session.Session, ads_rig.Rig])


class AindDataSchemaDataMapper(_base.DataMapper[_TAdsObject], abc.ABC):
    """
    Abstract base class for mapping data to aind-data-schema objects.
    
    This class provides the foundation for mapping experimental data to AIND data schema
    formats, ensuring consistent structure and metadata handling across different data types.

    Attributes:
        session_name (str): The name of the session associated with the data
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


class AindDataSchemaSessionDataMapper(AindDataSchemaDataMapper[ads_session.Session], abc.ABC):
    """
    Abstract base class for mapping session data to aind-data-schema Session objects.
    
    This class specializes the generic data mapper for session-specific data,
    providing the interface for converting experimental session data to the
    AIND data schema Session format.
    """


class AindDataSchemaRigDataMapper(AindDataSchemaDataMapper[ads_rig.Rig], abc.ABC):
    """
    Abstract base class for mapping rig data to aind-data-schema Rig objects.
    
    This class specializes the generic data mapper for rig-specific data,
    providing the interface for converting experimental rig configurations
    to the AIND data schema Rig format.
    """
