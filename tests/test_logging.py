import logging
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from clabe.logging_helper import add_file_handler, aibs


class TestLoggingHelper(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("test_logger")
        self.settings = aibs.AibsLogServerHandlerSettings(
            project_name="test_project",
            version="0.1.0",
            host="localhost",
            port=12345,
            rig_id="test_rig",
            comp_id="test_comp",
        )
        self.logger.handlers = []  # Clear existing handlers

    @patch("logging.FileHandler")
    def test_default_logger_builder_with_output_path(self, mock_file_handler):
        mock_file_handler_instance = MagicMock()
        mock_file_handler.return_value = mock_file_handler_instance

        output_path = Path("/tmp/fake/path/to/logfile.log")
        logger = add_file_handler(self.logger, output_path)

        self.assertEqual(len(logger.handlers), 1)
        self.assertEqual(logger.handlers[0], mock_file_handler_instance)
        mock_file_handler.assert_called_once_with(output_path, encoding="utf-8", mode="w")

    @patch("clabe.logging_helper.aibs.AibsLogServerHandler")
    def test_add_log_server_handler(self, mock_log_server_handler):
        mock_log_server_handler_instance = MagicMock()
        mock_log_server_handler.return_value = mock_log_server_handler_instance

        logger = aibs.add_handler(self.logger, self.settings)

        self.assertEqual(len(logger.handlers), 1)
        self.assertEqual(logger.handlers[0], mock_log_server_handler_instance)
        mock_log_server_handler.assert_called_once_with(settings=self.settings)


if __name__ == "__main__":
    unittest.main()
