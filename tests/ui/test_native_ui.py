from unittest.mock import MagicMock, patch

import pytest

from clabe.ui import NativeUiHelper


@pytest.fixture
def ui_helper():
    return NativeUiHelper(print_func=MagicMock())


class TestNativeUiHelper:
    @patch("builtins.input", side_effect=["Some notes"])
    def test_prompt_get_text(self, mock_input, ui_helper):
        result = ui_helper.prompt_text("")
        assert isinstance(result, str)

    @patch("builtins.input", side_effect=["Y"])
    def test_prompt_yes_no_question(self, mock_input, ui_helper):
        result = ui_helper.prompt_yes_no_question("Continue?")
        assert isinstance(result, bool)

    @patch("builtins.input", side_effect=["1"])
    def test_prompt_pick_from_list(self, mock_input, ui_helper):
        result = ui_helper.prompt_pick_from_list(["item1", "item2"], "Choose an item")
        assert isinstance(result, str)
        assert result == "item1"

    @patch("builtins.input", side_effect=["0"])
    def test_prompt_pick_from_list_none(self, mock_input, ui_helper):
        result = ui_helper.prompt_pick_from_list(["item1", "item2"], "Choose an item")
        assert result is None
