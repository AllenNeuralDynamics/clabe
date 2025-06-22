import unittest
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import patch

from pydantic import BaseModel

from clabe.data_mapper.helpers import (
    snapshot_bonsai_environment,
    snapshot_python_environment,
)
from tests import TESTS_ASSETS


class MockModel(BaseModel):
    field1: int
    field2: str
    field3: List[int]
    field4: Dict[str, int]
    field5: Optional[int] = None
    sub_model: Optional["MockModel"] = None


class TestHelpers(unittest.TestCase):
    @patch("importlib.metadata.distributions")
    def test_snapshot_python_environment(self, mock_distributions):
        mock_distributions.return_value = [
            type("Distribution", (object,), {"name": "package1", "version": "1.0.0"}),
            type("Distribution", (object,), {"name": "package2", "version": "2.0.0"}),
        ]
        expected_result = {"package1": "1.0.0", "package2": "2.0.0"}
        result = snapshot_python_environment()
        self.assertEqual(result, expected_result)

    def test_snapshot_bonsai_environment_from_mock(self):
        out = snapshot_bonsai_environment(config_file=Path(TESTS_ASSETS) / "bonsai.config")
        self.assertEqual(
            out,
            {"Bonsai": "2.8.5", "Bonsai.Core": "2.8.5", "Bonsai.Design": "2.8.5", "Bonsai.Design.Visualizers": "2.8.0"},
        )


if __name__ == "__main__":
    unittest.main()
