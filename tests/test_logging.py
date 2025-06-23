import logging
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from clabe._logging_helper import add_file_logger


class TestLoggingHelper(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("test_logger")
        self.logger.handlers = []  # Clear existing handlers

    @patch("logging.FileHandler")
    def test_default_logger_builder_with_output_path(self, mock_file_handler):
        mock_file_handler_instance = MagicMock()
        mock_file_handler.return_value = mock_file_handler_instance

        output_path = Path("/tmp/fake/path/to/logfile.log")
        logger = add_file_logger(self.logger, output_path)

        self.assertEqual(len(logger.handlers), 1)
        self.assertEqual(logger.handlers[0], mock_file_handler_instance)
        mock_file_handler.assert_called_once_with(output_path, encoding="utf-8", mode="w")


if __name__ == "__main__":
    unittest.main()
