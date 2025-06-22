from __future__ import annotations

import abc
import logging

from ..services import IService

logger = logging.getLogger(__name__)


class DataTransfer(IService, abc.ABC):
    """
    Abstract base class for data transfer services. All data transfer implementations
    must inherit from this class and implement its abstract methods.
    """

    @abc.abstractmethod
    def transfer(self) -> None:
        """
        Executes the data transfer process. Must be implemented by subclasses.
        """

    @abc.abstractmethod
    def validate(self) -> bool:
        """
        Validates the data transfer service. Must be implemented by subclasses.

        Returns:
            True if the service is valid, False otherwise.
        """
