import io
from enum import Enum
from pathlib import Path

import pytest
from pydantic import BaseModel, Field
from rich.console import Console

from clabe import ui
from clabe.ui import (
    AnswerRejected,
    ClientSession,
    ConfirmRequest,
    ConsoleClient,
    FieldRequest,
    FormRequest,
    GroupRequest,
    NumberRequest,
    PathRequest,
    PickRequest,
    TextRequest,
)
from clabe.workflow import Run, Workflow
from clabe.workflow.events import PromptClosed, PromptOpened


class TestBoundedNumbers:
    def test_bounds_and_step_are_enforced(self):
        request = NumberRequest(label="Gain", minimum=0, maximum=1, step=0.25)
        assert request.validate_answer("0.75") == 0.75
        with pytest.raises(AnswerRejected, match="≥ 0"):
            request.validate_answer(-1)
        with pytest.raises(AnswerRejected, match="≤ 1"):
            request.validate_answer(2)
        with pytest.raises(AnswerRejected, match="multiple of 0.25"):
            request.validate_answer(0.3)

    def test_schema_advertises_bounds_and_the_widget(self):
        schema = NumberRequest(label="Gain", minimum=0, maximum=1, step=0.1, widget="slider").answer_schema()
        assert schema == {"type": "number", "minimum": 0, "maximum": 1, "multipleOf": 0.1, "x-widget": "slider"}
        assert NumberRequest(label="n").answer_schema() == {"type": "number"}

    def test_floating_point_steps_are_tolerated(self):
        assert NumberRequest(label="g", step=0.1).validate_answer(0.3) == 0.3


class Mode(Enum):
    FAST = "fast"
    SLOW = "slow"


class Settings(BaseModel):
    trials: int = Field(default=10, gt=0)
    mode: Mode = Mode.FAST


def _group() -> GroupRequest:
    return GroupRequest(
        title="Session",
        items={
            "notes": TextRequest(label="Notes"),
            "gain": NumberRequest(label="Gain", minimum=0, maximum=1, widget="slider"),
            "ok": ConfirmRequest(label="Ready?", default=True),
            "rig": PickRequest(label="Rig", options=["a", "b"], allow_none=False),
            "trials": FieldRequest(model=Settings, field_name="trials"),
        },
    )


class TestGroupRequest:
    def test_answers_are_validated_and_typed(self):
        answer = _group().validate_answer({"notes": "hi", "gain": "0.4", "rig": "b", "trials": "3"})
        assert answer == {"notes": "hi", "gain": 0.4, "ok": True, "rig": "b", "trials": 3}

    def test_a_rejection_names_the_item(self):
        with pytest.raises(AnswerRejected, match="gain"):
            _group().validate_answer({"gain": 2, "rig": "a"})
        with pytest.raises(AnswerRejected, match="trials"):
            _group().validate_answer({"gain": 0.1, "rig": "a", "trials": 0})

    def test_schema_and_spec(self):
        group = _group()
        gain = group.answer_schema()["properties"]["gain"]
        assert (gain["minimum"], gain["maximum"], gain["x-widget"]) == (0, 1, "slider")
        spec = group.spec()
        assert spec["title"] == "Session"
        assert spec["items"]["gain"]["prompt_kind"] == "number" and spec["items"]["gain"]["widget"] == "slider"
        assert spec["items"]["trials"]["prompt_kind"] == "field"

    def test_model_instances_are_accepted(self):
        group = _group()
        instance = group.model()(notes="", gain=0.5, rig="a")
        assert group.validate_answer(instance)["gain"] == 0.5

    def test_path_items_keep_their_rules(self, tmp_path):
        rig = tmp_path / "rig.json"
        rig.write_text("{}")
        group = GroupRequest(items={"rig": PathRequest(label="Rig", extensions=[".json"])})
        assert group.validate_answer({"rig": str(rig)}) == {"rig": rig}
        with pytest.raises(AnswerRejected, match="does not exist"):
            group.validate_answer({"rig": str(tmp_path / "nope.json")})

    @pytest.mark.parametrize("bad", ["not an identifier", "class-name"])
    def test_item_names_must_be_identifiers(self, bad):
        with pytest.raises(ValueError, match="identifier"):
            GroupRequest(items={bad: TextRequest(label="x")})

    def test_whole_interactions_cannot_be_items(self):
        with pytest.raises(ValueError, match="cannot be part of a group"):
            GroupRequest(items={"inner": GroupRequest(items={})})
        with pytest.raises(ValueError, match="cannot be part of a group"):
            GroupRequest(items={"form": FormRequest(model=Settings)})


class TestGroupThroughARun:
    def test_one_prompt_one_dict_answer(self):
        wf = Workflow("demo")

        @wf.step
        def ask():
            return ui.group(
                "Session",
                notes=TextRequest(label="Notes"),
                gain=NumberRequest(label="Gain", minimum=0, maximum=1, widget="slider"),
            )

        run = Run(wf)
        opened: list = []

        def answer(event):
            if isinstance(event, PromptOpened):
                opened.append(event)
                import threading

                threading.Thread(
                    target=lambda: (
                        run.answer(event.prompt_id, {"notes": "x", "gain": 5}),  # rejected: out of range
                        run.answer(event.prompt_id, {"notes": "x", "gain": 0.5}),
                    )
                ).start()

        run.events.subscribe(answer)
        run.start()
        assert run.wait(5)
        assert run.output("ask") == {"notes": "x", "gain": 0.5}
        assert len(opened) == 1 and opened[0].kind == "group"
        assert opened[0].answer_schema["properties"]["gain"]["x-widget"] == "slider"
        closed = [e for e in run.events.since(0) if isinstance(e, PromptClosed)]
        assert closed[0].answer == {"notes": "x", "gain": 0.5}


class TestGroupOnTheConsole:
    def test_the_console_asks_a_group_as_a_form(self, monkeypatch):
        client = ConsoleClient(console=Console(file=io.StringIO(), force_terminal=False))
        # notes, gain (2 rejected, then 0.5), ok, rig (asked as a strict autocomplete on the option values)
        answers = iter(["hello", "2", "0.5", "y", "b"])
        monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
        group = GroupRequest(
            items={
                "notes": TextRequest(label="Notes"),
                "gain": NumberRequest(label="Gain", minimum=0, maximum=1),
                "ok": ConfirmRequest(label="Ready?"),
                "rig": PickRequest(label="Rig", options=["a", "b"], allow_none=False),
            }
        )
        assert ClientSession(client).ask(group) == {"notes": "hello", "gain": 0.5, "ok": True, "rig": "b"}

    def test_the_slider_range_is_shown(self, monkeypatch):
        client = ConsoleClient(console=Console(file=io.StringIO(), force_terminal=False))
        monkeypatch.setattr("builtins.input", lambda *a, **k: "0.2")
        group = GroupRequest(items={"gain": NumberRequest(label="Gain", minimum=0, maximum=1, widget="slider")})
        assert ClientSession(client).ask(group) == {"gain": 0.2}


def test_path_defaults_survive(tmp_path):
    group = GroupRequest(items={"out": PathRequest(label="Out", default=str(tmp_path), kind="dir")})
    assert group.validate_answer({}) == {"out": Path(str(tmp_path))}
