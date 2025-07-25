from __future__ import annotations

import abc
import logging
from typing import Generic, TypeVar

from ..services import IService, ServiceSettings

logger = logging.getLogger(__name__)

TSettings = TypeVar("TSettings", bound=ServiceSettings)


class DataTransfer(IService, abc.ABC, Generic[TSettings]):
    """
    Abstract base class for data transfer services. All data transfer implementations
    must inherit from this class and implement its abstract methods.

    This class defines the interface that all data transfer services must implement,
    providing a consistent API for different transfer mechanisms such as file copying,
    cloud uploads, or network transfers.

    Example:
        ```python
        # Implementing a custom data transfer service with settings:
        class MyTransferSettings(ServiceSettings):
            destination: str

        class MyTransferService(DataTransfer[MyTransferSettings]):
            def __init__(self, source: str, settings: MyTransferSettings):
                self.source = source
                self._settings = settings

            def transfer(self) -> None:
                # Implementation specific transfer logic
                print(f"Transferring from {self.source} to {self._settings.destination}")

            def validate(self) -> bool:
                # Implementation specific validation
                return Path(self.source).exists()

        # Using the custom service:
        settings = MyTransferSettings(destination="D:/backup")
        service = MyTransferService("C:/data", settings)
        if service.validate():
            service.transfer()
        ```
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
        return self._settings
