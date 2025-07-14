from clabe import ui


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
