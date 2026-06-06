from unittest.mock import MagicMock

import pytest

from clabe.ui import ConfirmRequest, ConsoleFrontend, PickRequest, TextRequest


@pytest.fixture
def frontend():
    return ConsoleFrontend(print_func=MagicMock(), input_func=MagicMock())


class TestConsoleFrontend:
    def test_prompt_text(self, frontend):
        frontend._input = MagicMock(return_value="Some notes")
        result = frontend.prompt_text(TextRequest(label="Notes"))
        assert result == "Some notes"

    def test_prompt_confirm_yes(self, frontend):
        frontend._input = MagicMock(return_value="Y")
        assert frontend.prompt_confirm(ConfirmRequest(label="Continue?")) is True

    def test_prompt_confirm_no(self, frontend):
        frontend._input = MagicMock(return_value="N")
        assert frontend.prompt_confirm(ConfirmRequest(label="Continue?")) is False

    def test_prompt_pick(self, frontend):
        frontend._input = MagicMock(return_value="1")
        result = frontend.prompt_pick(PickRequest(label="Choose", options=["item1", "item2"], allow_none=False))
        assert result == "item1"

    def test_prompt_pick_none(self, frontend):
        frontend._input = MagicMock(return_value="0")
        result = frontend.prompt_pick(PickRequest(label="Choose", options=["item1", "item2"], allow_none=True))
        assert result is None
