import logging
import warnings
from pathlib import Path
from shutil import _ntuple_diskusage
from unittest.mock import MagicMock, patch

import pytest

from clabe.resource_monitor import (
    Constraint,
    ResourceMonitor,
    available_storage_constraint_factory,
    remote_dir_exists_constraint_factory,
)


@pytest.fixture
def monitor():
    warnings.simplefilter("ignore")
    logging.disable(logging.CRITICAL)
    return ResourceMonitor()


class TestResourceMonitor:
    def test_add_constraint(self, monitor):
        constraint = MagicMock(spec=Constraint)
        monitor.add_constraint(constraint)
        assert constraint in monitor.constraints

    def test_remove_constraint(self, monitor):
        constraint = MagicMock(spec=Constraint)
        monitor.add_constraint(constraint)
        monitor.remove_constraint(constraint)
        assert constraint not in monitor.constraints

    def test_evaluate_constraints_all_pass(self, monitor):
        constraint = MagicMock(spec=Constraint)
        constraint.return_value = True
        monitor.add_constraint(constraint)
        assert monitor.evaluate_constraints()

    def test_evaluate_constraints_one_fails(self, monitor):
        constraint1 = MagicMock(spec=Constraint)
        constraint1.return_value = True
        constraint2 = MagicMock(spec=Constraint)
        constraint2.return_value = False
        constraint2.on_fail.return_value = "Constraint failed"
        monitor.add_constraint(constraint1)
        monitor.add_constraint(constraint2)
        assert not monitor.evaluate_constraints()

    @patch("shutil.disk_usage")
    def test_available_storage_constraint_factory(self, mock_disk_usage):
        mock_disk_usage.return_value = _ntuple_diskusage(total=int(500e9), used=int(100e9), free=int(400e9))
        constraint = available_storage_constraint_factory(drive=Path("C:\\"), min_bytes=2e11)
        assert constraint()
        constraint = available_storage_constraint_factory(drive=Path("C:\\"), min_bytes=2e13)
        assert not constraint()

    @patch("os.path.exists")
    def test_remote_dir_exists_constraint_factory(self, mock_exists):
        mock_exists.return_value = True
        constraint = remote_dir_exists_constraint_factory(dir_path=Path("/some/remote/dir"))
        assert constraint()

    def test_resource_monitor_service(self):
        resource_monitor = ResourceMonitor()

        resource_monitor.add_constraint(
            Constraint(name="test_constraint", constraint=lambda: True, fail_msg_handler=lambda: "Constraint failed.")
        )

        assert resource_monitor.evaluate_constraints()

        resource_monitor.add_constraint(
            Constraint(name="test_constraint", constraint=lambda: False, fail_msg_handler=lambda: "Constraint failed.")
        )

        assert not resource_monitor.evaluate_constraints()

    def test_resource_monitor_service_constraint(self):
        constraint = Constraint(
            name="test_constraint", constraint=lambda x: x, fail_msg_handler=lambda: "Constraint failed.", args=[True]
        )

        assert constraint()

        constraint = Constraint(
            name="test_constraint", constraint=lambda x: x, fail_msg_handler=lambda: "Constraint failed.", args=[False]
        )
        assert not constraint()
