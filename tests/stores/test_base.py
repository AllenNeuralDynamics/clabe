import pydantic
import pytest
from aind_behavior_curriculum import TrainerState
from aind_behavior_services import Rig, Task

from clabe import ui
from clabe.cache_manager import CacheManager
from clabe.stores import CompositeStore, Kind, MemoryStore, as_kind


class Widget(pydantic.BaseModel):
    value: int = 0


class SomeNestedThing(pydantic.BaseModel):
    value: int = 0


class TestKind:
    def test_bare_constructor_snake_cases_the_model_name(self):
        assert Kind(Widget).name == "widget"
        assert Kind(SomeNestedThing).name == "some_nested_thing"

    def test_explicit_name_wins(self):
        assert Kind(Widget, "gadget").name == "gadget"

    def test_canonical_kinds_fix_the_framework_names(self):
        assert Kind.from_rig(Rig).name == "rig"
        assert Kind.from_task(Task).name == "task"
        assert Kind.from_session().name == "session"
        assert Kind.from_trainer_state().name == "trainer_state"

    def test_rig_models_of_different_shapes_share_the_rig_name(self):
        class OtherRig(Rig):
            pass

        assert Kind.from_rig(Rig).name == Kind.from_rig(OtherRig).name == "rig"

    def test_as_kind_wraps_a_bare_model(self):
        kind = as_kind(Widget)
        assert kind.name == "widget"
        assert kind.model is Widget

    def test_as_kind_passes_a_kind_through(self):
        kind = Kind(Widget, "gadget")
        assert as_kind(kind) is kind

    def test_trainer_state_validator_rejects_a_stageless_state(self, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        store = MemoryStore()
        store.write(Kind.from_trainer_state(), TrainerState(curriculum=None, stage=None, is_on_curriculum=False))
        with pytest.raises(ValueError, match="no stage"):
            store.resolve(Kind.from_trainer_state())


class TestResolve:
    @pytest.fixture
    def store(self):
        return MemoryStore()

    def test_no_candidates_raises(self, store):
        with pytest.raises(LookupError, match="widget"):
            store.resolve(Widget)

    def test_a_lone_candidate_is_selected_without_prompting(self, store, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        store.write(Widget, Widget(value=1))
        assert store.resolve(Widget) == Widget(value=1)
        mock_frontend._ask_pick_mock.assert_not_called()

    def test_many_candidates_prompt(self, store, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        store.write(Widget, Widget(value=1))
        store.write(Widget, Widget(value=2))
        mock_frontend._ask_pick_mock.return_value = "widget[1]"

        assert store.resolve(Widget) == Widget(value=2)

    def test_prompting_without_a_frontend_raises(self, store):
        store.write(Widget, Widget(value=1))
        store.write(Widget, Widget(value=2))
        with pytest.raises(ui.NoFrontendError):
            store.resolve(Widget)

    def test_declining_the_prompt_raises(self, store, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        store.write(Widget, Widget(value=1))
        store.write(Widget, Widget(value=2))
        mock_frontend._ask_pick_mock.return_value = None
        with pytest.raises(LookupError, match="selected"):
            store.resolve(Widget)

    def test_the_kind_validator_is_applied(self, store, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        store.write(Widget, Widget(value=1))
        doubled = Kind(Widget, validators=lambda w: Widget(value=w.value * 2))
        assert store.resolve(doubled) == Widget(value=2)

    def test_recently_chosen_options_are_offered_first(self, store, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        for value in range(3):
            store.write(Widget, Widget(value=value))
        mock_frontend._ask_pick_mock.return_value = "widget[2]"
        store.resolve(Widget)

        mock_frontend._ask_pick_mock.return_value = "widget[0]"
        store.resolve(Widget)

        assert mock_frontend._ask_pick_mock.call_args.args[0].options[0] == "widget[2]"
        assert CacheManager.get_instance().get_cache("widget")[0] == "widget[0]"


class TestScope:
    def test_scoped_narrows_reads_without_mutating_the_original(self):
        store = MemoryStore()
        store.write(Widget, Widget(value=1), scope={"subject": "123"})
        assert store.list(Widget) == []
        assert store.scoped(subject="123").list(Widget) == [Widget(value=1)]
        assert store.scope == {}

    def test_call_scope_layers_over_store_scope(self):
        store = MemoryStore().scoped(subject="123")
        store.write(Widget, Widget(value=1), scope={"slot": "a"})
        assert store.list(Widget, scope={"slot": "a"}) == [Widget(value=1)]
        assert store.list(Widget, scope={"slot": "b"}) == []

    def test_a_scoped_view_shares_the_underlying_records(self):
        store = MemoryStore()
        store.scoped(subject="123").write(Widget, Widget(value=1))
        assert store.scoped(subject="123").list(Widget) == [Widget(value=1)]

    def test_write_appends_a_candidate(self):
        store = MemoryStore().scoped(subject="123")
        store.write(Widget, Widget(value=1))
        store.write(Widget, Widget(value=2))
        assert store.list(Widget) == [Widget(value=1), Widget(value=2)]


class TestCompositeStore:
    @pytest.fixture
    def stores(self):
        return MemoryStore(), MemoryStore()

    def test_routes_by_kind_name(self, stores, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        default, routed = stores
        composite = CompositeStore(default=default, routes={"gadget": routed})

        composite.write(Widget, Widget(value=1))
        composite.write(Kind(Widget, "gadget"), Widget(value=2))

        assert default.list(Widget) == [Widget(value=1)]
        assert routed.list(Kind(Widget, "gadget")) == [Widget(value=2)]
        assert composite.resolve(Kind(Widget, "gadget")) == Widget(value=2)

    def test_an_unrouted_kind_raises_when_there_is_no_default(self, stores):
        composite = CompositeStore(routes={"gadget": stores[0]})
        with pytest.raises(LookupError, match="widget"):
            composite.list(Widget)

    def test_scoped_narrows_every_backend(self, stores):
        default, routed = stores
        composite = CompositeStore(default=default, routes={"gadget": routed}).scoped(subject="123")

        composite.write(Widget, Widget(value=1))
        composite.write(Kind(Widget, "gadget"), Widget(value=2))

        assert default.list(Widget) == []
        assert default.scoped(subject="123").list(Widget) == [Widget(value=1)]
        assert routed.scoped(subject="123").list(Kind(Widget, "gadget")) == [Widget(value=2)]
