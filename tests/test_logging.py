import logging
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from clabe._logging_helper import AibsLogServerHandler, add_file_logger


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

    @patch("clabe._logging_helper.AibsLogServerHandler")
    def test_add_log_server_handler(self, mock_log_server_handler):
        mock_log_server_handler_instance = MagicMock()
        mock_log_server_handler.return_value = mock_log_server_handler_instance

        logserver_url = "localhost:12345"
        logger = AibsLogServerHandler.add_handler(self.logger, logserver_url, "0.1.0", "mock_project")

        self.assertEqual(len(logger.handlers), 1)
        self.assertEqual(logger.handlers[0], mock_log_server_handler_instance)
        mock_log_server_handler.assert_called_once_with(
            host="localhost",
            port=12345,
            project_name="mock_project",
            version="0.1.0",
        )


if __name__ == "__main__":
    unittest.main()
