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

    This class defines the interface for mapping data from various sources to specific
    target formats or schemas. It provides a generic framework for data transformation
    and validation operations.

    Attributes:
        _mapped (Optional[TMapTo]): The mapped data object, set after successful mapping

    Examples:
        ```python
        # Creating a custom data mapper
        class MyDataMapper(DataMapper[MyTargetType]):
            def map(self) -> MyTargetType:
                # Implementation specific mapping logic
                self._mapped = MyTargetType(...)
                return self._mapped

            def is_mapped(self) -> bool:
                return self._mapped is not None

            @property
            def mapped(self) -> MyTargetType:
                if not self.is_mapped():
                    raise ValueError("Data not yet mapped")
                return self._mapped

        # Using the mapper
        mapper = MyDataMapper()
        result = mapper.map()
        if mapper.is_mapped():
            data = mapper.mapped
        ```
    """

    _mapped: Optional[TMapTo]

    @abc.abstractmethod
    def map(self) -> TMapTo:
        """
        Maps data to the target schema or format.

        This method should contain the core logic for transforming input data
        into the target format specified by the TMapTo type parameter.

        Returns:
            TMapTo: The mapped data object
        """
        pass

    @abc.abstractmethod
    def is_mapped(self) -> bool:
        """
        Checks if the data has been successfully mapped.

        This method should verify whether the mapping operation has been completed
        and the data is available in the target format.

        Returns:
            bool: True if the data is mapped, False otherwise
        """
        pass

    @property
    @abc.abstractmethod
    def mapped(self) -> TMapTo:
        """
        Retrieves the mapped data object.

        This property should return the successfully mapped data object.
        Implementations should ensure that mapping has been completed before
        returning the data.

        Returns:
            TMapTo: The mapped data object
        """
        pass
