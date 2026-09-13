import pydantic
import pytest
from aind_behavior_services import Rig, Task

from clabe import ui
from clabe.stores import DefaultLayout, Kind, LocalFileStore


class Widget(pydantic.BaseModel):
    value: int = 0


@pytest.fixture
def library(tmp_path):
    return tmp_path / "library"


@pytest.fixture
def store(library, mock_frontend):
    ui.set_current_frontend(mock_frontend)
    return LocalFileStore(library, scope={"computer_name": "RIG-01"})


def write_json(path, model):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")


def a_rig(name="mock_rig"):
    return Rig(rig_name=name, version="0.0.0", data_directory="data", computer_name="RIG-01")


class TestDefaultLayout:
    @pytest.fixture
    def layout(self):
        return DefaultLayout()

    def test_rigs_are_keyed_by_computer_name(self, layout):
        assert layout.read("rig", {"computer_name": "RIG-01"}) == ["Rig/RIG-01/*.json"]
        assert layout.write("rig", {"computer_name": "RIG-01"}) == "Rig/RIG-01/rig.json"

    def test_tasks_read_the_subject_folder_before_the_shared_library(self, layout):
        assert layout.read("task", {"subject": "123"}) == ["Subjects/123/task.json", "Task/*.json"]

    def test_tasks_fall_back_to_the_shared_library_when_unscoped(self, layout):
        assert layout.read("task", {}) == ["Task/*.json"]

    def test_tasks_are_written_to_the_subject_folder(self, layout):
        assert layout.write("task", {"subject": "123"}) == "Subjects/123/task.json"

    def test_other_kinds_are_per_subject(self, layout):
        assert layout.read("trainer_state", {"subject": "123"}) == ["Subjects/123/trainer_state.json"]
        assert layout.write("manipulator_position", {"subject": "123"}) == "Subjects/123/manipulator_position.json"

    def test_other_kinds_sit_at_the_root_when_there_is_no_subject(self, layout):
        assert layout.read("session", {}) == ["session.json"]


class TestLocalFileStore:
    def test_computer_name_defaults_to_this_machine(self, library, monkeypatch):
        monkeypatch.setenv("COMPUTERNAME", "SOME-RIG")
        assert LocalFileStore(library).scope["computer_name"] == "SOME-RIG"

    def test_resolve_reads_the_rig_for_this_computer(self, store, library):
        write_json(library / "Rig" / "RIG-01" / "rig.json", a_rig())
        write_json(library / "Rig" / "OTHER" / "rig.json", a_rig("wrong_rig"))
        assert store.resolve(Kind.from_rig(Rig)).rig_name == "mock_rig"

    def test_resolve_prompts_between_several_rigs(self, store, library, mock_frontend):
        write_json(library / "Rig" / "RIG-01" / "a.json", a_rig("rig_a"))
        write_json(library / "Rig" / "RIG-01" / "b.json", a_rig("rig_b"))
        mock_frontend._ask_pick_mock.return_value = str(library / "Rig" / "RIG-01" / "b.json")
        assert store.resolve(Kind.from_rig(Rig)).rig_name == "rig_b"

    def test_a_missing_record_raises(self, store):
        with pytest.raises(LookupError, match="rig"):
            store.resolve(Kind.from_rig(Rig))

    def test_the_subject_task_is_offered_alongside_the_library(self, store, library):
        write_json(library / "Subjects" / "123" / "task.json", Task(version="0.0.0", task_parameters={}, name="mine"))
        write_json(library / "Task" / "shared.json", Task(version="0.0.0", task_parameters={}, name="shared"))
        names = [t.name for t in store.scoped(subject="123").list(Kind.from_task(Task))]
        assert names == ["mine", "shared"]

    def test_unparseable_files_are_skipped_rather_than_failing_the_listing(self, store, library):
        write_json(library / "Rig" / "RIG-01" / "good.json", a_rig())
        (library / "Rig" / "RIG-01" / "bad.json").write_text("{not json", encoding="utf-8")
        assert len(store.list(Kind.from_rig(Rig))) == 1

    def test_write_round_trips_and_creates_directories(self, store, library):
        store.scoped(subject="123").write(Widget, Widget(value=7))
        assert (library / "Subjects" / "123" / "widget.json").exists()
        assert store.scoped(subject="123").list(Widget) == [Widget(value=7)]

    def test_write_overwrites_without_prompting(self, store, mock_frontend):
        scoped = store.scoped(subject="123")
        scoped.write(Widget, Widget(value=1))
        scoped.write(Widget, Widget(value=2))
        assert scoped.list(Widget) == [Widget(value=2)]
        mock_frontend._ask_confirm_mock.assert_not_called()

    def test_a_store_pointed_at_a_session_directory_reads_a_lone_record(self, tmp_path, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        write_json(tmp_path / "widget.json", Widget(value=42))
        assert LocalFileStore(tmp_path).resolve(Widget) == Widget(value=42)
