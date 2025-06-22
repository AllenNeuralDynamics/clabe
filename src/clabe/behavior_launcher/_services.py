from __future__ import annotations

import datetime
import logging
import os
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Type, TypeAlias, TypeVar, Union

from ..apps import App
from ..data_mapper import DataMapper
from ..data_mapper.aind_data_schema import AindDataSchemaSessionDataMapper
from ..data_transfer import DataTransfer
from ..data_transfer.aind_watchdog import WatchdogDataTransferService
from ..data_transfer.robocopy import RobocopyService
from ..resource_monitor import ResourceMonitor
from ..services import IService, ServiceFactory, ServicesFactoryManager

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..behavior_launcher import BehaviorLauncher
else:
    BehaviorLauncher = object


TService = TypeVar("TService", bound=IService)

_ServiceFactoryIsh: TypeAlias = Union[
    ServiceFactory[TService, BehaviorLauncher], Callable[[BehaviorLauncher], TService], TService
]


class BehaviorServicesFactoryManager(ServicesFactoryManager[BehaviorLauncher]):
    """
    Manages the creation and attachment of services for the BehaviorLauncher.

    This class provides methods to attach and retrieve various services such as
    app, data transfer, resource monitor, and data mapper. It ensures that the
    services are of the correct type and properly initialized.

    Attributes:
        Services are managed internally through the parent class and accessed via properties.

    Examples:
        # Create a manager and attach an app service
        mgr = BehaviorServicesFactoryManager()
        mgr.attach_app(App())
        # Attach a data transfer service
        mgr.attach_data_transfer(DataTransfer())
        # Retrieve the app service
        app = mgr.app
    """

    def __init__(self, launcher: Optional[BehaviorLauncher] = None, **kwargs) -> None:
        """
        Initializes the BehaviorServicesFactoryManager.

        Args:
            launcher: The launcher instance to associate with the services
            **kwargs: Additional keyword arguments for service initialization
        """
        super().__init__(launcher, **kwargs)
        self._add_to_services("app", kwargs)
        self._add_to_services("data_transfer", kwargs)
        self._add_to_services("resource_monitor", kwargs)
        self._add_to_services("data_mapper", kwargs)

    def _add_to_services(self, name: str, input_kwargs: Dict[str, Any]) -> Optional[ServiceFactory]:
        """
        Adds a service to the manager by attaching it to the appropriate factory.

        Args:
            name: The name of the service to add
            input_kwargs: The keyword arguments containing the service instance or factory

        Returns:
            Optional[ServiceFactory]: The attached service factory, if any
        """
        srv = input_kwargs.pop(name, None)
        if srv is not None:
            self.attach_service_factory(name, srv)
        return srv

    @property
    def app(self) -> App:
        """
        Retrieves the app service.

        Returns:
            App: The app service instance

        Raises:
            ValueError: If the app service is not set
        """
        srv = self.try_get_service("app")
        srv = self._validate_service_type(srv, App)
        if srv is None:
            raise ValueError("App is not set.")
        return srv

    def attach_app(self, value: _ServiceFactoryIsh[App, BehaviorLauncher]) -> None:
        """
        Attaches an app service factory.

        Args:
            value: The app service factory or instance
        """
        self.attach_service_factory("app", value)

    @property
    def data_mapper(self) -> Optional[DataMapper]:
        """
        Retrieves the data mapper service.

        Returns:
            Optional[DataMapper]: The data mapper service instance
        """
        srv = self.try_get_service("data_mapper")
        return self._validate_service_type(srv, DataMapper)

    def attach_data_mapper(self, value: _ServiceFactoryIsh[DataMapper, BehaviorLauncher]) -> None:
        """
        Attaches a data mapper service factory.

        Args:
            value: The data mapper service factory or instance
        """
        self.attach_service_factory("data_mapper", value)

    @property
    def resource_monitor(self) -> Optional[ResourceMonitor]:
        """
        Retrieves the resource monitor service.

        Returns:
            Optional[ResourceMonitor]: The resource monitor service instance
        """
        srv = self.try_get_service("resource_monitor")
        return self._validate_service_type(srv, ResourceMonitor)

    def attach_resource_monitor(self, value: _ServiceFactoryIsh[ResourceMonitor, BehaviorLauncher]) -> None:
        """
        Attaches a resource monitor service factory.

        Args:
            value: The resource monitor service factory or instance
        """
        self.attach_service_factory("resource_monitor", value)

    @property
    def data_transfer(self) -> Optional[DataTransfer]:
        """
        Retrieves the data transfer service.

        Returns:
            Optional[DataTransfer]: The data transfer service instance
        """
        srv = self.try_get_service("data_transfer")
        return self._validate_service_type(srv, DataTransfer)

    def attach_data_transfer(self, value: _ServiceFactoryIsh[DataTransfer, BehaviorLauncher]) -> None:
        """
        Attaches a data transfer service factory.

        Args:
            value: The data transfer service factory or instance
        """
        self.attach_service_factory("data_transfer", value)

    @staticmethod
    def _validate_service_type(value: Any, type_of: Type) -> Optional[TService]:
        """
        Validates the type of a service.

        Args:
            value: The service instance to validate
            type_of: The expected type of the service

        Returns:
            Optional[TService]: The validated service instance

        Raises:
            ValueError: If the service is not of the expected type
        """
        if value is None:
            return None
        if not isinstance(value, type_of):
            raise ValueError(f"{type(value).__name__} is not of the correct type. Expected {type_of.__name__}.")
        return value


def watchdog_data_transfer_factory(
    *,
    destination: os.PathLike,
    schedule_time: Optional[datetime.time] = datetime.time(hour=20),
    project_name: Optional[str] = None,
    **watchdog_kwargs,
) -> Callable[[BehaviorLauncher], WatchdogDataTransferService]:
    """
    Creates a factory for the WatchdogDataTransferService.

    This factory creates a partial function that can be used to instantiate
    a watchdog data transfer service with pre-configured parameters.

    Args:
        destination: The destination path for data transfer
        schedule_time: The scheduled time for data transfer
        project_name: The project name for organization
        **watchdog_kwargs: Additional keyword arguments for the watchdog service

    Returns:
        Callable[[BehaviorLauncher], WatchdogDataTransferService]: A callable factory for the watchdog service

    Examples:
        # Create a watchdog data transfer factory
        factory = watchdog_data_transfer_factory(destination="/mnt/data")
        # Use the factory with a launcher
        service = factory(launcher)
    """
    return partial(
        _watchdog_data_transfer_factory,
        destination=destination,
        schedule_time=schedule_time,
        project_name=project_name,
        **watchdog_kwargs,
    )


def _watchdog_data_transfer_factory(launcher: BehaviorLauncher, **watchdog_kwargs) -> WatchdogDataTransferService:
    """
    Internal factory function for creating a WatchdogDataTransferService.

    This function is used internally by the watchdog_data_transfer_factory to create
    the actual service instance with the launcher context.

    Args:
        launcher: The launcher instance providing context and configuration
        **watchdog_kwargs: Additional keyword arguments for the watchdog service

    Returns:
        WatchdogDataTransferService: The created watchdog service

    Raises:
        ValueError: If the data mapper service is not set or is of the wrong type
    """
    if launcher.services_factory_manager.data_mapper is None:
        raise ValueError("Data mapper service is not set. Cannot create watchdog.")
    if not isinstance(launcher.services_factory_manager.data_mapper, AindDataSchemaSessionDataMapper):
        raise ValueError(
            "Data mapper service is not of the correct type (AindDataSchemaSessionDataMapper). Cannot create watchdog."
        )

    watchdog = WatchdogDataTransferService(
        source=launcher.session_directory,
        aind_session_data_mapper=launcher.services_factory_manager.data_mapper,
        session_name=launcher.session_schema.session_name,
        **watchdog_kwargs,
    )
    return watchdog


def robocopy_data_transfer_factory(
    destination: os.PathLike,
    **robocopy_kwargs,
) -> Callable[[BehaviorLauncher], RobocopyService]:
    """
    Creates a factory for the RobocopyService.

    This factory creates a partial function that can be used to instantiate
    a robocopy data transfer service with pre-configured parameters.

    Args:
        destination: The destination path for data transfer
        **robocopy_kwargs: Additional keyword arguments for the robocopy service

    Returns:
        Callable[[BehaviorLauncher], RobocopyService]: A callable factory for the robocopy service

    Examples:
        # Create a robocopy data transfer factory
        factory = robocopy_data_transfer_factory(destination="/mnt/data")
        # Use the factory with a launcher
        service = factory(launcher)
    """
    return partial(_robocopy_data_transfer_factory, destination=destination, **robocopy_kwargs)


def _robocopy_data_transfer_factory(
    launcher: BehaviorLauncher, destination: os.PathLike, **robocopy_kwargs
) -> RobocopyService:
    """
    Internal factory function for creating a RobocopyService.

    This function is used internally by the robocopy_data_transfer_factory to create
    the actual service instance with the launcher context and proper destination paths.

    Args:
        launcher: The launcher instance providing context and configuration
        destination: The destination path for data transfer
        **robocopy_kwargs: Additional keyword arguments for the robocopy service

    Returns:
        RobocopyService: The created robocopy service
    """
    if launcher.group_by_subject_log:
        dst = Path(destination) / launcher.session_schema.subject / launcher.session_schema.session_name
    else:
        dst = Path(destination) / launcher.session_schema.session_name
    return RobocopyService(source=launcher.session_directory, destination=dst, **robocopy_kwargs)
