from typing import Optional

import pydantic
import pytest

from clabe.modifiers import ByAnimalModifier
from clabe.stores import Kind, MemoryStore


class NestedModel(pydantic.BaseModel):
    foo: str
    bar: int
    nested2: Optional["NestedModel"] = None


class Model(pydantic.BaseModel):
    nested: NestedModel
    something: float


NESTED = Kind(NestedModel, "nested_model")


class CustomModifier(ByAnimalModifier[Model]):
    def __init__(self, store, model_path="nested"):
        super().__init__(store, NESTED, model_path)

    def _process_before_dump(self):
        return NestedModel(foo="Modified", bar=10, nested2=NestedModel(foo="Modified Nested", bar=20))


@pytest.fixture
def store():
    return MemoryStore().scoped(subject="123")


@pytest.fixture
def sample_model():
    return Model(
        nested=NestedModel(foo="Original", bar=5, nested2=NestedModel(foo="Nested", bar=5)),
        something=3.14,
    )


class TestByAnimalModifier:
    def test_inject_uses_the_stored_record(self, store, sample_model):
        store.write(NESTED, NestedModel(foo="Loaded", bar=99))
        modified = CustomModifier(store).inject(sample_model)
        assert (modified.nested.foo, modified.nested.bar, modified.nested.nested2) == ("Loaded", 99, None)

    def test_inject_leaves_the_rig_alone_when_nothing_is_stored(self, store, sample_model):
        modified = CustomModifier(store).inject(sample_model)
        assert (modified.nested.foo, modified.nested.bar, modified.something) == ("Original", 5, 3.14)

    def test_dump_writes_through_the_store(self, store, sample_model):
        CustomModifier(store).dump()
        assert store.list(NESTED) == [
            NestedModel(foo="Modified", bar=10, nested2=NestedModel(foo="Modified Nested", bar=20))
        ]

    def test_dump_is_scoped_to_the_animal(self, store):
        CustomModifier(store).dump()
        assert store.scoped(subject="456").list(NESTED) == []

    def test_inject_and_dump_round_trip(self, store, sample_model):
        CustomModifier(store).dump()
        modified = CustomModifier(store).inject(sample_model)
        assert (modified.nested.foo, modified.nested.nested2.foo) == ("Modified", "Modified Nested")

    def test_a_nested_model_path_is_followed(self, store):
        class Level2Model(pydantic.BaseModel):
            value: int

        class Level1Model(pydantic.BaseModel):
            level2: Level2Model

        class DeepModel(pydantic.BaseModel):
            level1: Level1Model

        class DeepModifier(ByAnimalModifier[DeepModel]):
            def __init__(self, store):
                super().__init__(store, Kind(Level2Model, "deep_value"), "level1.level2")

            def _process_before_dump(self):
                return Level2Model(value=999)

        store.write(Kind(Level2Model, "deep_value"), Level2Model(value=42))
        model = DeepModel(level1=Level1Model(level2=Level2Model(value=1)))
        assert DeepModifier(store).inject(model).level1.level2.value == 42

    def test_the_pre_inject_hook_can_rewrite_the_record(self, store, sample_model):
        class WithPreProcess(CustomModifier):
            def _process_before_inject(self, deserialized):
                return deserialized.model_copy(update={"foo": "PreProcessed"})

        store.write(NESTED, NestedModel(foo="Loaded", bar=99))
        modified = WithPreProcess(store).inject(sample_model)
        assert (modified.nested.foo, modified.nested.bar) == ("PreProcessed", 99)
