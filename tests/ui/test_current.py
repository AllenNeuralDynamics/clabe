import pytest

from clabe import ui


class TestOutputPassthroughs:
    def test_they_reach_the_registered_frontend(self, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        ui.notify("hello")
        mock_frontend._render_mock.assert_called_once()

    def test_they_no_op_without_a_frontend(self):
        ui.notify("hello")
        ui.header("hello")
        with ui.activity("working"):
            pass


class TestPromptPassthroughs:
    def test_they_reach_the_registered_frontend(self, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        mock_frontend._ask_confirm_mock.return_value = False
        assert ui.prompt_confirm(ui.ConfirmRequest(label="sure?")) is False

    @pytest.mark.parametrize(
        ("prompt", "request_"),
        [
            (ui.prompt_pick, ui.PickRequest(label="pick", options=["a"])),
            (ui.prompt_confirm, ui.ConfirmRequest(label="sure?")),
            (ui.prompt_text, ui.TextRequest(label="text")),
            (ui.prompt_autocomplete, ui.AutoCompleteRequest(label="auto", options=[])),
            (ui.prompt_acknowledge, ui.AcknowledgeRequest(message="ok")),
        ],
    )
    def test_they_raise_without_a_frontend(self, prompt, request_):
        with pytest.raises(ui.NoFrontendError):
            prompt(request_)


class TestUseFrontend:
    def test_it_restores_the_previous_frontend(self, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        with ui.use_frontend(None):
            assert ui.current_frontend() is None
        assert ui.current_frontend() is mock_frontend

    def test_it_restores_on_error(self, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        with pytest.raises(RuntimeError), ui.use_frontend(None):
            raise RuntimeError("boom")
        assert ui.current_frontend() is mock_frontend


class TestRequireFrontend:
    def test_it_returns_the_registered_frontend(self, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        assert ui.require_frontend() is mock_frontend

    def test_it_raises_when_none_is_registered(self):
        with pytest.raises(ui.NoFrontendError):
            ui.require_frontend()
