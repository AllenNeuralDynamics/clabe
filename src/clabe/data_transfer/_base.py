import abc
import logging
from typing import Generic, TypeVar

from ..services import Service, ServiceSettings

logger = logging.getLogger(__name__)

TSettings = TypeVar("TSettings", bound=ServiceSettings)


class DataTransfer(Service, abc.ABC, Generic[TSettings]):
    """
    Abstract base class for data transfer services.

    Defines the interface that all data transfer services must implement, providing
    a consistent API for different transfer mechanisms such as file copying, cloud
    uploads, or network transfers.

    Type Parameters:
        TSettings: Type of the service settings

    Properties:
        settings: The service settings

    Methods:
        transfer: Executes the data transfer process
        validate: Validates the data transfer service
    """

    @abc.abstractmethod
    def transfer(self) -> None:
        """
        Executes the data transfer process. Must be implemented by subclasses.

        This method should contain the core logic for transferring data from
        source to destination according to the service's specific implementation.
        """

    @abc.abstractmethod
    def validate(self) -> bool:
        """
        Validates the data transfer service. Must be implemented by subclasses.

        This method should verify that the service is properly configured and
        ready to perform data transfers, checking for required dependencies,
        connectivity, permissions, etc.

        Returns:
            True if the service is valid and ready for use, False otherwise
        """

    _settings: TSettings

    @property
    def settings(self) -> TSettings:
        """
        Returns the settings for the data transfer service.

        Returns:
            TSettings: The service settings
        """
        return self._settings
