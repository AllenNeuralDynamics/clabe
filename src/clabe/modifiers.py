import abc
import functools
import logging
from typing import Any, Generic, TypeVar

from ._typing import TRig
from .stores import Kind, KindLike, Store, as_kind

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ByAnimalModifier(abc.ABC, Generic[TRig]):
    """
    Injects per-animal state into a rig, and saves it back, through a :class:`~clabe.stores.Store`.

    The store decides where per-animal state lives, so the same modifier works
    against the config library, Dataverse or an in-memory fake. Narrow the store
    to the animal before handing it over, e.g. ``store.scoped(subject=...)``.

    Example:
        ```python
        class ManipulatorModifier(ByAnimalModifier[MyRig]):
            def __init__(self, store: Store):
                super().__init__(store, Kind(ManipulatorPosition), "manipulator.position")

            def _process_before_dump(self) -> ManipulatorPosition:
                return read_position_from_hardware()

        modifier = ManipulatorModifier(store.scoped(subject=session.subject))
        rig = modifier.inject(rig)
        ...
        modifier.dump()
        ```
    """

    def __init__(self, store: Store, kind: KindLike[Any], model_path: str) -> None:
        """
        Args:
            store: The store holding this record, already scoped to the animal.
            kind: The record kind to read and write.
            model_path: Dot-separated path to the target attribute in the rig model.
        """
        self._store = store
        self._kind: Kind[Any] = as_kind(kind)
        self._model_path = model_path

    def _process_before_inject(self, deserialized: T) -> T:
        """
        Hook called after reading but before injection.

        Args:
            deserialized: The record read from the store.

        Returns:
            The object to inject into the rig.
        """
        return deserialized

    @abc.abstractmethod
    def _process_before_dump(self) -> Any:
        """Returns the object to persist. Subclasses must implement this."""

    def inject(self, rig: TRig) -> TRig:
        """
        Injects the stored record into the rig, leaving the rig untouched if there is none.

        Args:
            rig: The rig model to modify.

        Returns:
            The rig, modified in place.
        """
        records = self._store.list(self._kind)
        if not records:
            logger.warning("No %s found in %s. Using default.", self._kind.name, self._store)
            return rig
        logger.info("Loading %s. Deserialized: %s", self._kind.name, records[0])
        recursive_setattr(rig, self._model_path, self._process_before_inject(records[0]))
        return rig

    def dump(self) -> None:
        """
        Persists the record produced by :meth:`_process_before_dump`.

        Raises:
            Exception: Whatever :meth:`_process_before_dump` or the store raises.
        """
        try:
            to_dump = self._process_before_dump()
            logger.info("Saving %s. Serialized: %s", self._kind.name, to_dump)
            self._store.write(self._kind, to_dump)
        except Exception as e:
            logger.error("Failed to dump modifier: %s", e)
            raise


def recursive_setattr(obj, attr, val):
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
        recursive_setattr(obj, "inner.value", 42)
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
