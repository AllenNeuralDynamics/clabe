import abc
import logging
from typing import Any, Generic, Optional, TypeVar, final

from clabe.runnable._core import runnable

from ..services import Service

logger = logging.getLogger(__name__)


T = TypeVar("T")

TMapTo = TypeVar("TMapTo", bound=Any)


class DataMapper(Service, abc.ABC, Generic[TMapTo]):
    """
    Abstract base class for data mappers.

    Defines the interface for mapping data from various sources to specific
    target formats or schemas.
    """

    _mapped: Optional[TMapTo]

    @runnable
    @final
    def run(self) -> TMapTo:
        """
        Executes the data mapping process.

        Returns:
            The mapped data object
        """
        self._mapped = self.map()
        return self._mapped

    @abc.abstractmethod
    def map(self) -> TMapTo:
        """
        Maps data to the target schema or format.

        Returns:
            The mapped data object
        """
        pass

    def is_mapped(self) -> bool:
        """
        Checks if the data has been successfully mapped.

        Returns:
            True if the data is mapped, False otherwise
        """
        return self._mapped is not None

    @property
    def mapped(self) -> TMapTo:
        """
        Retrieves the mapped data object.

        Returns:
            The mapped data object

        Raises:
            ValueError: If the data has not been mapped yet
        """
        if not self.is_mapped():
            raise ValueError("Data not yet mapped")
        assert self._mapped is not None, "Mapped data should not be None"
        return self._mapped
