import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clabe.logging_helper import add_file_handler, aibs


@pytest.fixture
def logger():
    test_logger = logging.getLogger("test_logger")
    test_logger.handlers = []  # Clear existing handlers
    return test_logger


@pytest.fixture
def settings():
    return aibs.AibsLogServerHandlerSettings(
        project_name="test_project",
        version="0.1.0",
        host="localhost",
        port=12345,
        rig_id="test_rig",
        comp_id="test_comp",
    )


class TestLoggingHelper:
    @patch("logging.FileHandler")
    def test_default_logger_builder_with_output_path(self, mock_file_handler, logger):
        mock_file_handler_instance = MagicMock()
        mock_file_handler.return_value = mock_file_handler_instance

        output_path = Path("/tmp/fake/path/to/logfile.log")
        result_logger = add_file_handler(logger, output_path)

        assert len(result_logger.handlers) == 1
        assert result_logger.handlers[0] == mock_file_handler_instance
        mock_file_handler.assert_called_once_with(output_path, encoding="utf-8", mode="w")

    @patch("clabe.logging_helper.aibs.AibsLogServerHandler")
    def test_add_log_server_handler(self, mock_log_server_handler, logger, settings):
        mock_log_server_handler_instance = MagicMock()
        mock_log_server_handler.return_value = mock_log_server_handler_instance

        result_logger = aibs.add_handler(logger, settings)

        assert len(result_logger.handlers) == 1
        assert result_logger.handlers[0] == mock_log_server_handler_instance
        mock_log_server_handler.assert_called_once_with(settings=settings)
