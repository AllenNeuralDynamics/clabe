from pathlib import Path
from typing import Optional

import pydantic
import pytest

from clabe.pickers.default_behavior import ByAnimalModifier


class NestedModel(pydantic.BaseModel):
    foo: str
    bar: int
    nested2: Optional["NestedModel"] = None


class Model(pydantic.BaseModel):
    nested: NestedModel
    something: float


class CustomModifier(ByAnimalModifier[Model]):
    def __init__(self, subject_db_path: Path, model_path="nested", model_name="nested_model", **kwargs):
        super().__init__(subject_db_path=subject_db_path, model_path=model_path, model_name=model_name, **kwargs)

    def _process_before_dump(self):
        return NestedModel(foo="Modified", bar=10, nested2=NestedModel(foo="Modified Nested", bar=20, nested2=None))


class TestByAnimalModifier:
    @pytest.fixture
    def temp_subject_db(self, tmp_path: Path):
        subject_db = tmp_path / "subject_db"
        subject_db.mkdir(parents=True, exist_ok=True)
        return subject_db

    @pytest.fixture
    def sample_model(self):
        return Model(
            nested=NestedModel(foo="Original", bar=5, nested2=NestedModel(foo="Nested", bar=5, nested2=None)),
            something=3.14,
        )

    def test_inject_with_existing_file(self, temp_subject_db: Path, sample_model: Model):
        nested_data = NestedModel(foo="Loaded", bar=99, nested2=None)
        target_file = temp_subject_db / "nested_model.json"
        target_file.write_text(nested_data.model_dump_json(indent=2), encoding="utf-8")

        modifier = CustomModifier(subject_db_path=temp_subject_db)
        modified = modifier.inject(sample_model)

        assert modified.nested.foo == "Loaded"
        assert modified.nested.bar == 99
        assert modified.nested.nested2 is None

    def test_inject_without_existing_file(self, temp_subject_db: Path, sample_model: Model):
        modifier = CustomModifier(subject_db_path=temp_subject_db)
        modified = modifier.inject(sample_model)

        assert modified.nested.foo == "Original"
        assert modified.nested.bar == 5
        assert modified.something == 3.14

    def test_dump_creates_file(self, temp_subject_db: Path, sample_model: Model):
        modifier = CustomModifier(subject_db_path=temp_subject_db)
        modifier.inject(sample_model)
        modifier.dump()

        target_file = temp_subject_db / "nested_model.json"
        assert target_file.exists()

        loaded = NestedModel.model_validate_json(target_file.read_text(encoding="utf-8"))
        assert loaded.foo == "Modified"
        assert loaded.bar == 10
        assert loaded.nested2.foo == "Modified Nested"
        assert loaded.nested2.bar == 20

    def test_dump_without_inject_uses_fallback(self, temp_subject_db: Path):
        modifier = CustomModifier(subject_db_path=temp_subject_db)
        modifier.dump()

        target_file = temp_subject_db / "nested_model.json"
        assert target_file.exists()

    def test_inject_and_dump_roundtrip(self, temp_subject_db: Path, sample_model: Model):
        modifier1 = CustomModifier(subject_db_path=temp_subject_db)
        modifier1.inject(sample_model)
        modifier1.dump()

        modifier2 = CustomModifier(subject_db_path=temp_subject_db)
        modified = modifier2.inject(sample_model)

        assert modified.nested.foo == "Modified"
        assert modified.nested.bar == 10
        assert modified.nested.nested2.foo == "Modified Nested"

    def test_nested_path_access(self, temp_subject_db: Path):
        class DeepModel(pydantic.BaseModel):
            level1: "Level1Model"

        class Level1Model(pydantic.BaseModel):
            level2: "Level2Model"

        class Level2Model(pydantic.BaseModel):
            value: int

        class DeepModifier(ByAnimalModifier[DeepModel]):
            def __init__(self, subject_db_path: Path, **kwargs):
                super().__init__(
                    subject_db_path=subject_db_path, model_path="level1.level2", model_name="deep_value", **kwargs
                )

            def _process_before_dump(self):
                return Level2Model(value=999)

        model = DeepModel(level1=Level1Model(level2=Level2Model(value=1)))

        level2_data = Level2Model(value=42)
        target_file = temp_subject_db / "deep_value.json"
        target_file.write_text(level2_data.model_dump_json(indent=2), encoding="utf-8")

        modifier = DeepModifier(subject_db_path=temp_subject_db)
        modified = modifier.inject(model)

        assert modified.level1.level2.value == 42

    def test_process_before_inject_hook(self, temp_subject_db: Path, sample_model: Model):
        class ModifierWithPreProcess(CustomModifier):
            def _process_before_inject(self, deserialized):
                deserialized.foo = "PreProcessed"
                return deserialized

        nested_data = NestedModel(foo="Loaded", bar=99, nested2=None)
        target_file = temp_subject_db / "nested_model.json"
        target_file.write_text(nested_data.model_dump_json(indent=2), encoding="utf-8")

        modifier = ModifierWithPreProcess(subject_db_path=temp_subject_db)
        modified = modifier.inject(sample_model)

        assert modified.nested.foo == "PreProcessed"
        assert modified.nested.bar == 99

    def test_dump_creates_parent_directories(self, tmp_path: Path, sample_model: Model):
        nested_subject_db = tmp_path / "parent" / "child" / "subject_db"

        modifier = CustomModifier(subject_db_path=nested_subject_db)
        modifier.inject(sample_model)
        modifier.dump()

        target_file = nested_subject_db / "nested_model.json"
        assert target_file.exists()
        assert target_file.parent.exists()
