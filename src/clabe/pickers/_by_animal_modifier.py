import abc
import functools
import logging
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from pydantic import TypeAdapter

from .._typing import TRig

logger = logging.getLogger(__name__)
T = TypeVar("T")
TInjectable = TypeVar("TInjectable")


@runtime_checkable
class _IByAnimalModifier(Protocol, Generic[TRig]):
    """
    Protocol defining the interface for by-animal modifiers.

    This protocol defines the contract that any by-animal modifier must implement
    to inject and dump subject-specific configurations.
    """

    def inject(self, rig: TRig) -> TRig:
        """Injects subject-specific configuration into the rig model."""
        ...

    def dump(self) -> None:
        """Dumps the configuration to a JSON file."""
        ...


class ByAnimalModifier(abc.ABC, _IByAnimalModifier[TRig]):
    """
    Abstract base class for modifying rig configurations with subject-specific data.

    This class provides a framework for loading and saving subject-specific
    configuration data to/from JSON files. It uses reflection to access nested
    attributes in the rig model and automatically handles serialization.

    Attributes:
        _subject_db_path: Path to the directory containing subject-specific files
        _model_path: Dot-separated path to the attribute in the rig model (e.g., "nested.field")
        _model_name: Base name for the JSON file (without extension)
        _tp: TypeAdapter for the model type, set during inject()

    Example:
        ```python
        from pathlib import Path
        from clabe.pickers.default_behavior import ByAnimalModifier
        import pydantic

        class MyModel(pydantic.BaseModel):
            nested: "NestedConfig"

        class NestedConfig(pydantic.BaseModel):
            value: int

        class MyModifier(ByAnimalModifier[MyModel]):
            def __init__(self, subject_db_path: Path, **kwargs):
                super().__init__(
                    subject_db_path=subject_db_path,
                    model_path="nested",
                    model_name="nested_config",
                    **kwargs
                )

            def _process_before_dump(self):
                return NestedConfig(value=42)

        modifier = MyModifier(Path("./subject_db"))
        model = MyModel(nested=NestedConfig(value=1))
        modified = modifier.inject(model)
        modifier.dump()
        ```
    """

    def __init__(self, subject_db_path: Path, model_path: str, model_name: str, **kwargs) -> None:
        """
        Initializes the ByAnimalModifier.

        Args:
            subject_db_path: Path to the directory containing subject-specific JSON files
            model_path: Dot-separated path to the target attribute in the rig model
            model_name: Base name for the JSON file (without .json extension)
            **kwargs: Additional keyword arguments (reserved for future use)
        """
        self._subject_db_path = Path(subject_db_path)
        self._model_path = model_path
        self._model_name = model_name
        self._tp: TypeAdapter[Any] | None = None

    def _process_before_inject(self, deserialized: T) -> T:
        """
        Hook method called after deserialization but before injection.

        Override this method to modify the deserialized data before it's
        injected into the rig model.

        Args:
            deserialized: The deserialized object from the JSON file

        Returns:
            The processed object to be injected
        """
        return deserialized

    @abc.abstractmethod
    def _process_before_dump(self) -> Any:
        """
        Abstract method to generate the data to be dumped to JSON.

        Subclasses must implement this method to return the object that
        should be serialized and saved to the JSON file.

        Returns:
            The object to be serialized and dumped to JSON
        """
        ...

    def inject(self, rig: TRig) -> TRig:
        """
        Injects subject-specific configuration into the rig model.

        Loads configuration from a JSON file and injects it into the specified
        path in the rig model. If the file doesn't exist, the rig is returned
        unmodified with a warning logged.

        Args:
            rig: The rig model to modify

        Returns:
            The modified rig model
        """
        target_file = self._subject_db_path / f"{self._model_name}.json"
        if not target_file.exists():
            logger.warning(f"File not found: {target_file}. Using default.")
        else:
            target = rgetattr(rig, self._model_path)
            self._tp = TypeAdapter(type(target))
            deserialized = self._tp.validate_json(target_file.read_text(encoding="utf-8"))
            logger.info(f"Loading {self._model_name} from: {target_file}. Deserialized: {deserialized}")
            self._process_before_inject(deserialized)
            rsetattr(rig, self._model_path, deserialized)
        return rig

    def dump(self) -> None:
        """
        Dumps the configuration to a JSON file.

        Calls _process_before_dump() to get the data, then serializes it
        to JSON and writes it to the target file. Creates parent directories
        if they don't exist.

        Raises:
            Exception: If _process_before_dump() fails or serialization fails
        """
        target_folder = self._subject_db_path
        target_file = target_folder / f"{self._model_name}.json"

        if (tp := self._tp) is None:
            logger.warning("TypeAdapter is not set. Using TypeAdapter(Any) as fallback.")
            tp = TypeAdapter(Any)

        try:
            to_inject = self._process_before_dump()
            logger.info(f"Saving {self._model_name} to: {target_file}. Serialized: {to_inject}")
            target_folder.mkdir(parents=True, exist_ok=True)
            target_file.write_text(tp.dump_json(to_inject, indent=2).decode("utf-8"), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to process before dumping modifier: {e}")
            raise


def rsetattr(obj, attr, val):
    """
    Sets an attribute value using a dot-separated path.

    Args:
        obj: The object to modify
        attr: Dot-separated attribute path (e.g., "nested.field.value")
        val: The value to set

    Returns:
        The result of setattr on the final attribute

    Example:
        ```python
        class Inner:
            value = 1

        class Outer:
            inner = Inner()

        obj = Outer()
        rsetattr(obj, "inner.value", 42)
        assert obj.inner.value == 42
        ```
    """
    pre, _, post = attr.rpartition(".")
    return setattr(rgetattr(obj, pre) if pre else obj, post, val)


def rgetattr(obj, attr, *args):
    """
    Gets an attribute value using a dot-separated path.

    Args:
        obj: The object to query
        attr: Dot-separated attribute path (e.g., "nested.field.value")
        *args: Optional default value if attribute doesn't exist

    Returns:
        The attribute value at the specified path

    Example:
        ```python
        class Inner:
            value = 42

        class Outer:
            inner = Inner()

        obj = Outer()
        result = rgetattr(obj, "inner.value")
        assert result == 42

        default = rgetattr(obj, "nonexistent.path", "default")
        assert default == "default"
        ```
    """

    def _getattr(obj, attr):
        """Helper function to get attribute with optional default."""
        return getattr(obj, attr, *args)

    return functools.reduce(_getattr, [obj] + attr.split("."))
