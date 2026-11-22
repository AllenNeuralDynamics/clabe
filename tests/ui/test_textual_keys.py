"""Terminal UI keys: Alt+Left/Right navigate, 1-9 pick, y/n confirm, r retries; options from a function."""

import queue

import pytest
from textual.widgets import OptionList

from clabe.ui import AutoCompleteRequest, Choice, ConfirmRequest, NavigationRequested, PickRequest
from clabe.ui._textual import _LauncherApp, _unwrap


def test_navigation_keys_are_bound():
    actions = {binding.key: binding.action for binding in _LauncherApp.BINDINGS}
    assert actions["alt+left"] == "back" and actions["alt+right"] == "forward"
    assert actions["ctrl+c"] == "cancel" and actions["f2"] == "toggle_logs"


@pytest.mark.asyncio
@pytest.mark.parametrize(("key", "action"), [("alt+left", "back"), ("alt+right", "forward")])
async def test_alt_arrows_release_the_prompt_as_navigation(key, action):
    app = _LauncherApp(show_logs=False)
    async with app.run_test() as pilot:
        reply: queue.Queue = queue.Queue()
        await app.ask_confirm(ConfirmRequest(label="ok?"), reply)
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()
        with pytest.raises(NavigationRequested) as raised:
            _unwrap(reply.get_nowait())
        assert raised.value.action == action


@pytest.mark.asyncio
async def test_number_keys_pick_an_option():
    app = _LauncherApp(show_logs=False)
    async with app.run_test() as pilot:
        reply: queue.Queue = queue.Queue()
        await app.ask_pick(PickRequest(label="Rig", options=["a", "b", "c"], allow_none=True), reply)
        await pilot.pause()
        labels = [str(option.prompt) for option in app.query_one("#clabe-options", OptionList).options]
        assert labels == ["1. None", "2. a", "3. b", "4. c"]
        await pilot.press("3")
        await pilot.pause()
        assert reply.get_nowait() == "b"


@pytest.mark.asyncio
@pytest.mark.parametrize(("key", "answer"), [("y", True), ("n", False)])
async def test_y_and_n_answer_a_confirm(key, answer):
    app = _LauncherApp(show_logs=False)
    async with app.run_test() as pilot:
        reply: queue.Queue = queue.Queue()
        await app.ask_confirm(ConfirmRequest(label="ok?", default=not answer), reply)
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()
        assert reply.get_nowait() is answer


@pytest.mark.asyncio
async def test_r_retries_a_failed_step_only():
    failure = PickRequest(
        label="a failed",
        options=[Choice("retry", "Retry"), Choice("abort", "Abort")],
        allow_none=False,
        field="a.failed",
    )
    app = _LauncherApp(show_logs=False)
    async with app.run_test() as pilot:
        reply: queue.Queue = queue.Queue()
        await app.ask_pick(failure, reply)
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert reply.get_nowait() == "retry"

        other = PickRequest(label="p", options=["retry", "x"], allow_none=False)
        await app.ask_pick(other, reply)
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert reply.empty()  # not a failure prompt: r does nothing


@pytest.mark.asyncio
async def test_letters_in_a_text_input_are_typed_not_shortcuts():
    app = _LauncherApp(show_logs=False)
    async with app.run_test() as pilot:
        reply: queue.Queue = queue.Queue()
        await app.ask_autocomplete(AutoCompleteRequest(label="Subject", options=["y1", "n1"]), reply)
        await pilot.pause()
        await pilot.press("y", "1", "enter")
        await pilot.pause()
        assert reply.get_nowait() == "y1"


@pytest.mark.asyncio
async def test_options_come_from_a_function_as_the_user_types():
    calls: list[str] = []

    def search(typed: str) -> list[str]:
        calls.append(typed)
        return [name for name in ("mouse-7", "mouse-8", "rat-1") if typed in name]

    app = _LauncherApp(show_logs=False)
    async with app.run_test() as pilot:
        reply: queue.Queue = queue.Queue()
        await app.ask_autocomplete(AutoCompleteRequest(label="Subject", options=search), reply)
        await pilot.pause()
        options = app.query_one("#clabe-options", OptionList)
        assert options.option_count == 3 and calls == [""]
        await pilot.press("r", "a")
        await pilot.pause(0.5)
        assert calls[-1] == "ra"
        assert [str(option.prompt) for option in options.options] == ["rat-1"]
