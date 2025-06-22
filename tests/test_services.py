import unittest
from unittest.mock import create_autospec

from src.clabe.launcher import BaseLauncher
from src.clabe.services import IService, ServiceFactory, ServicesFactoryManager


class TestServicesFactoryManager(unittest.TestCase):
    def setUp(self):
        self.launcher = create_autospec(BaseLauncher)
        self.manager = ServicesFactoryManager(self.launcher)

    def test_attach_service_factory(self):
        service = create_autospec(IService)
        self.manager.attach_service_factory("test_service", service)
        self.assertIn("test_service", self.manager._services)

    def test_detach_service_factory(self):
        service = create_autospec(IService)
        self.manager.attach_service_factory("test_service", service)
        self.manager.detach_service_factory("test_service")
        self.assertNotIn("test_service", self.manager._services)

    def test_register_launcher(self):
        new_launcher = create_autospec(BaseLauncher)
        manager = ServicesFactoryManager()
        manager.register_launcher(new_launcher)
        self.assertEqual(manager.launcher, new_launcher)

    def test_getitem(self):
        service = create_autospec(IService)
        factory = ServiceFactory(service)
        self.manager.attach_service_factory("test_service", factory)
        self.assertIsInstance(self.manager["test_service"], IService)

    def test_try_get_service(self):
        service = create_autospec(IService)
        factory = ServiceFactory(service)
        self.manager.attach_service_factory("test_service", factory)
        self.assertIsInstance(self.manager["test_service"], IService)
        self.assertIsNone(self.manager.try_get_service("non_existent_service"))

    def test_get_multiple(self):
        service1 = create_autospec(IService)
        service2 = create_autospec(IService)
        self.manager.attach_service_factory("service1", service1)
        self.manager.attach_service_factory("service2", service2)
        self.assertIsInstance(self.manager["service1"], IService)
        self.assertIsInstance(self.manager["service2"], IService)

    def test_get_services_of_type(self):
        class TestService(IService):
            pass

        service1 = TestService()
        service2 = create_autospec(IService)
        self.manager.attach_service_factory("service1", service1)
        self.manager.attach_service_factory("service2", service2)
        services = list(self.manager.get_services_of_type(TestService))
        self.assertIn(service1, services)
        self.assertNotIn(service2, services)


if __name__ == "__main__":
    unittest.main()
