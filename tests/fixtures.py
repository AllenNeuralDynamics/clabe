from clabe import ui
from clabe.ui.picker import PickerBase


class MockPicker(PickerBase):
    def pick_rig(self):
        raise NotImplementedError("pick_rig method is not implemented")

    def pick_session(self):
        raise NotImplementedError("pick_session method is not implemented")

    def pick_task_logic(self):
        raise NotImplementedError("pick_task_logic method is not implemented")

    def initialize(self) -> None:
        return

    def finalize(self) -> None:
        return


class MockUiHelper(ui.UiHelper):
    def __init__(self):
        self._print_func = lambda x: None
        self._input_func = lambda x: "1"

    def print(self, message: str) -> None:
        return self._print_func(message)

    def input(self, prompt: str) -> str:
        return self._input_func(prompt)

    def prompt_pick_from_list(self, *args, **kwargs):
        return ""

    def prompt_yes_no_question(self, prompt: str) -> bool:
        return True

    def prompt_text(self, prompt: str) -> str:
        return ""

    def prompt_float(self, prompt):
        return 0.0
