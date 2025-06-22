from __future__ import annotations

import abc
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, Generic, Iterable, List, Optional, Self, Type, TypeVar, overload

if TYPE_CHECKING:
    from .launcher import BaseLauncher
else:
    BaseLauncher = Any

logger = logging.getLogger(__name__)


class IService(abc.ABC):
    """
    A base class for all services.
    All services should inherit from this class.
    """


TService = TypeVar("TService", bound=IService)

TLauncher = TypeVar("TLauncher", bound="BaseLauncher")


class ServiceFactory(Generic[TService, TLauncher]):
    """
    A factory class for defer the creation of service instances.

    Attributes:
        _service_factory: A callable that creates a service instance.
        _service: An instance of the service.
    """

    @overload
    def __init__(self, service_or_factory: TService) -> None:
        """
        Initializes the factory with a service type.
        """

    @overload
    def __init__(self, service_or_factory: Callable[[TLauncher], TService]) -> None:
        """
        Initializes the factory with a callable that creates a service instance.
        """

    def __init__(self, service_or_factory: Callable[[TLauncher], TService] | TService) -> None:
        """
        Initializes the factory with either a service instance or a callable.

        Args:
            service_or_factory: A service instance or a callable that creates a service.
        """
        self._service_factory: Optional[Callable[[TLauncher], TService]] = None
        self._service: Optional[TService] = None
        if callable(service_or_factory):
            self._service_factory = service_or_factory
            self._service = None
        elif isinstance(service_or_factory, IService):
            self._service = service_or_factory
            self._service_factory = None
        else:
            raise ValueError("service_or_factory must be either a service or a service factory")

    def build(self, launcher: TLauncher, *args, **kwargs) -> TService:
        """
        Builds/instantiates the service instance.

        Args:
            launcher: The launcher instance to pass to the service factory.
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.

        Returns:
            The service instance.
        """
        if self._service is None:
            if self._service_factory is None:
                raise ValueError("Service factory is not set")
            else:
                self._service = self._service_factory(launcher, *args, **kwargs)
        return self._service

    @property
    def service(self) -> Optional[TService]:
        """
        Returns the service instance if it has been created.

        Returns:
            The service instance or None.
        """
        return self._service


class ServicesFactoryManager(Generic[TLauncher]):
    """
    A manager class for handling multiple service factories.

    Attributes:
        _launcher_reference: A reference to the launcher instance.
        _services: A dictionary of service factories.
    """

    def __init__(
        self,
        launcher: Optional[TLauncher] = None,
        **kwargs,
    ) -> None:
        """
        Initializes the manager with an optional launcher.

        Args:
            launcher: An optional launcher instance.
            **kwargs: Additional keyword arguments.
        """
        self._launcher_reference = launcher
        self._services: Dict[str, ServiceFactory] = {}

    def __getitem__(self, name: str) -> IService:
        """
        Retrieves a service by name.

        Args:
            name: The name of the service.

        Returns:
            The service instance.
        """
        return self._services[name].build(self.launcher)

    def try_get_service(self, name: str) -> Optional[IService]:
        """
        Tries to retrieve a service by name.

        Args:
            name: The name of the service.

        Returns:
            The service instance or None if not found.
        """
        srv = self._services.get(name, None)
        return srv.build(self.launcher) if srv is not None else None

    def attach_service_factory(
        self, name: str, service_factory: ServiceFactory | Callable[[TLauncher], TService] | TService
    ) -> Self:
        """
        Attaches a service factory to the manager.

        Args:
            name: The name of the service.
            service_factory: The service factory or callable.

        Returns:
            The manager instance.
        """
        if name in self._services:
            raise IndexError(f"Service with name {name} is already registered")
        _service_factory: ServiceFactory
        if isinstance(service_factory, ServiceFactory):
            _service_factory = service_factory
        elif callable(service_factory) | isinstance(service_factory, IService):
            _service_factory = ServiceFactory(service_factory)
        else:
            raise ValueError("service_factory must be either a service or a service factory")
        self._services[name] = _service_factory
        return self

    def detach_service_factory(self, name: str) -> Self:
        """
        Detaches a service factory from the manager.

        Args:
            name: The name of the service.

        Returns:
            The manager instance.
        """
        if name in self._services:
            self._services.pop(name)
        else:
            raise IndexError(f"Service with name {name} is not registered")
        return self

    def register_launcher(self, launcher: TLauncher) -> Self:
        """
        Registers a launcher with the manager.

        Args:
            launcher: The launcher instance.

        Returns:
            The manager instance.
        """
        if self._launcher_reference is None:
            self._launcher_reference = launcher
        else:
            raise ValueError("Launcher is already registered")
        return self

    @property
    def launcher(self) -> TLauncher:
        """
        Returns the registered launcher.

        Returns:
            The launcher instance.

        Raises:
            ValueError: If no launcher is registered.
        """
        if self._launcher_reference is None:
            raise ValueError("Launcher is not registered")
        return self._launcher_reference

    @property
    def services(self) -> Iterable[IService]:
        """
        Returns all services managed by the manager.

        Returns:
            An iterable of service instances.
        """
        yield from (service.build(self.launcher) for service in self._services.values())

    def get_services_of_type(self, service_type: Type[TService]) -> Iterable[TService]:
        """
        Retrieves all services of a specific type.

        Args:
            service_type: The type of services to retrieve.

        Returns:
            An iterable of services of the specified type.
        """
        yield from (service for service in self.services if isinstance(service, service_type))

    def map(self, delegate: Callable[[IService], Any]) -> List[Any]:
        """
        Applies a delegate function to all services.

        Args:
            delegate: A callable to apply to each service.

        Returns:
            A list of results from the delegate function.
        """
        return [delegate(service) for service in self.services]
