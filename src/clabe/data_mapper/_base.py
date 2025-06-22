from __future__ import annotations

import abc
import logging
from typing import Any, Generic, Optional, TypeVar

from ..services import IService

logger = logging.getLogger(__name__)


T = TypeVar("T")

TMapTo = TypeVar("TMapTo", bound=Any)


class DataMapper(IService, abc.ABC, Generic[TMapTo]):
    """
    Abstract base class for data mappers.

    Attributes:
        _mapped (Optional[TMapTo]): The mapped data object.
    """

    _mapped: Optional[TMapTo]

    @abc.abstractmethod
    def map(self) -> TMapTo:
        """
        Maps data to the target schema or format.

        Returns:
            TMapTo: The mapped data object.
        """
        pass

    @abc.abstractmethod
    def is_mapped(self) -> bool:
        """
        Checks if the data has been successfully mapped.

        Returns:
            bool: True if the data is mapped, False otherwise.
        """
        pass

    @property
    @abc.abstractmethod
    def mapped(self) -> TMapTo:
        """
        Retrieves the mapped data object.

        Returns:
            TMapTo: The mapped data object.
        """
        pass
