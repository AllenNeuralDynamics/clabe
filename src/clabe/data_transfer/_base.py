from __future__ import annotations

import abc
import logging

from ..services import IService

logger = logging.getLogger(__name__)


class DataTransfer(IService, abc.ABC):
    """
    Abstract base class for data transfer services. All data transfer implementations
    must inherit from this class and implement its abstract methods.

    This class defines the interface that all data transfer services must implement,
    providing a consistent API for different transfer mechanisms such as file copying,
    cloud uploads, or network transfers.

    Examples:
        Implementing a custom data transfer service:

        class MyTransferService(DataTransfer):
            def __init__(self, source, destination):
                self.source = source
                self.destination = destination

            def transfer(self) -> None:
                # Implementation specific transfer logic
                print(f"Transferring from {self.source} to {self.destination}")

            def validate(self) -> bool:
                # Implementation specific validation
                return Path(self.source).exists()

        Using the custom service:

        service = MyTransferService("C:/data", "D:/backup")
        if service.validate():
            service.transfer()
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
