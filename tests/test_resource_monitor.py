import logging
import unittest
import warnings
from shutil import _ntuple_diskusage
from unittest.mock import MagicMock, patch

from clabe.resource_monitor import (
    Constraint,
    ResourceMonitor,
    available_storage_constraint_factory,
    remote_dir_exists_constraint_factory,
)


class TestResourceMonitor(unittest.TestCase):
    def setUp(self):
        self.monitor = ResourceMonitor()
        warnings.simplefilter("ignore")
        logging.disable(logging.CRITICAL)

    def test_add_constraint(self):
        constraint = MagicMock(spec=Constraint)
        self.monitor.add_constraint(constraint)
        self.assertIn(constraint, self.monitor.constraints)

    def test_remove_constraint(self):
        constraint = MagicMock(spec=Constraint)
        self.monitor.add_constraint(constraint)
        self.monitor.remove_constraint(constraint)
        self.assertNotIn(constraint, self.monitor.constraints)

    def test_evaluate_constraints_all_pass(self):
        constraint = MagicMock(spec=Constraint)
        constraint.return_value = True
        self.monitor.add_constraint(constraint)
        self.assertTrue(self.monitor.evaluate_constraints())

    def test_evaluate_constraints_one_fails(self):
        constraint1 = MagicMock(spec=Constraint)
        constraint1.return_value = True
        constraint2 = MagicMock(spec=Constraint)
        constraint2.return_value = False
        constraint2.on_fail.return_value = "Constraint failed"
        self.monitor.add_constraint(constraint1)
        self.monitor.add_constraint(constraint2)
        self.assertFalse(self.monitor.evaluate_constraints())

    @patch("shutil.disk_usage")
    def test_available_storage_constraint_factory(self, mock_disk_usage):
        mock_disk_usage.return_value = _ntuple_diskusage(total=500e9, used=100e9, free=400e9)
        constraint = available_storage_constraint_factory(drive="C:\\", min_bytes=2e11)
        self.assertTrue(constraint())
        constraint = available_storage_constraint_factory(drive="C:\\", min_bytes=2e13)
        self.assertFalse(constraint())

    @patch("os.path.exists")
    def test_remote_dir_exists_constraint_factory(self, mock_exists):
        mock_exists.return_value = True
        constraint = remote_dir_exists_constraint_factory(dir_path="/some/remote/dir")
        self.assertTrue(constraint())

    def test_resource_monitor_service(self):
        resource_monitor = ResourceMonitor()

        resource_monitor.add_constraint(
            Constraint(name="test_constraint", constraint=lambda: True, fail_msg_handler=lambda: "Constraint failed.")
        )

        self.assertTrue(resource_monitor.evaluate_constraints())

        resource_monitor.add_constraint(
            Constraint(name="test_constraint", constraint=lambda: False, fail_msg_handler=lambda: "Constraint failed.")
        )

        resource_monitor.add_constraint(resource_monitor)
        self.assertFalse(resource_monitor.evaluate_constraints())

    def test_resource_monitor_service_constraint(self):
        constraint = Constraint(
            name="test_constraint", constraint=lambda x: x, fail_msg_handler=lambda: "Constraint failed.", args=[True]
        )

        self.assertTrue(constraint(), True)

        constraint = Constraint(
            name="test_constraint", constraint=lambda x: x, fail_msg_handler=lambda: "Constraint failed.", args=[False]
        )
        self.assertFalse(constraint(), False)


if __name__ == "__main__":
    unittest.main()
