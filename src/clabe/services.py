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
    
    This abstract base class defines the interface that all services should inherit from.
    It serves as a marker interface to identify service implementations across the system.
    """


TService = TypeVar("TService", bound=IService)

TLauncher = TypeVar("TLauncher", bound="BaseLauncher")


class ServiceFactory(Generic[TService, TLauncher]):
    """
    A factory class for defer the creation of service instances.
    
    This class allows for lazy instantiation of services, supporting both direct service
    instances and factory functions that create services with launcher context.

    Attributes:
        _service_factory (Optional[Callable]): A callable that creates a service instance
        _service (Optional[TService]): An instance of the service once created
    """

    @overload
    def __init__(self, service_or_factory: TService) -> None:
        """
        Initializes the factory with a service instance.
        
        Args:
            service_or_factory: A pre-instantiated service instance
        """

    @overload
    def __init__(self, service_or_factory: Callable[[TLauncher], TService]) -> None:
        """
        Initializes the factory with a callable that creates a service instance.
        
        Args:
            service_or_factory: A callable that takes a launcher and returns a service
        """

    def __init__(self, service_or_factory: Callable[[TLauncher], TService] | TService) -> None:
        """
        Initializes the factory with either a service instance or a callable.

        Args:
            service_or_factory: A service instance or a callable that creates a service
            
        Raises:
            ValueError: If the argument is neither a service nor a service factory
            
        Example:
            # Using a service instance
            service = MyService()
            factory = ServiceFactory(service)
            
            # Using a factory function
            def create_service(launcher):
                return MyService(launcher.config)
            factory = ServiceFactory(create_service)
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
        
        If the service hasn't been created yet and a factory function is available,
        it calls the factory with the launcher to create the service instance.

        Args:
            launcher: The launcher instance to pass to the service factory
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments

        Returns:
            The service instance
            
        Raises:
            ValueError: If no service factory is set and no service instance exists
            
        Example:
            factory = ServiceFactory(create_service)
            launcher = MyLauncher(...)
            service = factory.build(launcher)  # Creates the service
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
            The service instance or None if not yet created
            
        Example:
            factory = ServiceFactory(create_service)
            print(factory.service)  # None (not built yet)
            service = factory.build(launcher)
            print(factory.service)  # The created service instance
        """
        return self._service


class ServicesFactoryManager(Generic[TLauncher]):
    """
    A manager class for handling multiple service factories.
    
    This class manages a collection of service factories, providing methods to register,
    retrieve, and manage service instances with proper launcher context.

    Attributes:
        _launcher_reference (Optional[TLauncher]): A reference to the launcher instance
        _services (Dict[str, ServiceFactory]): A dictionary of service factories by name
    """

    def __init__(
        self,
        launcher: Optional[TLauncher] = None,
        **kwargs,
    ) -> None:
        """
        Initializes the manager with an optional launcher.

        Args:
            launcher: An optional launcher instance to register
            **kwargs: Additional keyword arguments (unused)
            
        Example:
            # Create without launcher
            manager = ServicesFactoryManager()
            
            # Create with launcher
            launcher = MyLauncher(...)
            manager = ServicesFactoryManager(launcher=launcher)
        """
        self._launcher_reference = launcher
        self._services: Dict[str, ServiceFactory] = {}

    def __getitem__(self, name: str) -> IService:
        """
        Retrieves a service by name using dictionary-style access.

        Args:
            name: The name of the service to retrieve

        Returns:
            The service instance
            
        Raises:
            KeyError: If the service name is not found
            
        Example:
            manager = ServicesFactoryManager(launcher)
            manager.attach_service_factory("my_service", MyService())
            service = manager["my_service"]  # Dictionary-style access
        """
        return self._services[name].build(self.launcher)

    def try_get_service(self, name: str) -> Optional[IService]:
        """
        Tries to retrieve a service by name without raising exceptions.

        Args:
            name: The name of the service to retrieve

        Returns:
            The service instance or None if not found
            
        Example:
            manager = ServicesFactoryManager(launcher)
            service = manager.try_get_service("my_service")
            if service is not None:
                print("Service found")
            else:
                print("Service not found")
        """
        srv = self._services.get(name, None)
        return srv.build(self.launcher) if srv is not None else None

    def attach_service_factory(
        self, name: str, service_factory: ServiceFactory | Callable[[TLauncher], TService] | TService
    ) -> Self:
        """
        Attaches a service factory to the manager.
        
        Registers a service factory, callable, or service instance with the manager
        under the specified name.

        Args:
            name: The name to register the service under
            service_factory: The service factory, callable, or service instance

        Returns:
            The manager instance for method chaining
            
        Raises:
            IndexError: If a service with the same name is already registered
            ValueError: If the service_factory is not a valid type
            
        Example:
            manager = ServicesFactoryManager(launcher)
            
            # Attach a service instance
            manager.attach_service_factory("my_service", MyService())
            
            # Attach a factory function
            manager.attach_service_factory("other_service", lambda l: OtherService(l))
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
        
        Removes a previously registered service factory from the manager.

        Args:
            name: The name of the service to remove

        Returns:
            The manager instance for method chaining
            
        Raises:
            IndexError: If no service with the specified name is registered
        """
        if name in self._services:
            self._services.pop(name)
        else:
            raise IndexError(f"Service with name {name} is not registered")
        return self

    def register_launcher(self, launcher: TLauncher) -> Self:
        """
        Registers a launcher with the manager.
        
        Associates a launcher instance with the manager, which will be passed
        to service factories when creating service instances.

        Args:
            launcher: The launcher instance to register

        Returns:
            The manager instance for method chaining
            
        Raises:
            ValueError: If a launcher is already registered
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
            The launcher instance
            
        Raises:
            ValueError: If no launcher is registered
        """
        if self._launcher_reference is None:
            raise ValueError("Launcher is not registered")
        return self._launcher_reference

    @property
    def services(self) -> Iterable[IService]:
        """
        Returns all services managed by the manager.
        
        Creates and yields all service instances from the registered factories.

        Returns:
            An iterable of service instances
        """
        yield from (service.build(self.launcher) for service in self._services.values())

    def get_services_of_type(self, service_type: Type[TService]) -> Iterable[TService]:
        """
        Retrieves all services of a specific type.
        
        Filters and returns only the services that are instances of the specified type.

        Args:
            service_type: The type of services to retrieve

        Returns:
            An iterable of services of the specified type
            
        Example:
            manager = ServicesFactoryManager(launcher)
            manager.attach_service_factory("db", DatabaseService())
            manager.attach_service_factory("cache", CacheService())
            
            db_services = list(manager.get_services_of_type(DatabaseService))
            print(f"Found {len(db_services)} database services")
        """
        yield from (service for service in self.services if isinstance(service, service_type))

    def map(self, delegate: Callable[[IService], Any]) -> List[Any]:
        """
        Applies a delegate function to all services.
        
        Executes the provided function on each service instance and collects the results.

        Args:
            delegate: A callable to apply to each service

        Returns:
            A list of results from the delegate function
        """
        return [delegate(service) for service in self.services]
