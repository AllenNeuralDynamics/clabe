import abc
import dataclasses
import functools
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Generic, Protocol, TypedDict, TypeVar, runtime_checkable

import pydantic
from aind_behavior_curriculum import TrainerState
from aind_behavior_services import Rig, Session, Task
from pydantic.alias_generators import to_snake

from .. import ui
from .._typing import TRig, TTask
from ..cache_manager import CacheManager
from ..utils.aind_validators import validate_rig_computer_name

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: A set of key/value narrowings selecting which records a call addresses,
#: e.g. ``{"subject": "789012", "computer_name": "RIG-01"}``.
Scope = Mapping[str, str]


def _recent_first(cache_name: str, options: Sequence[str]) -> list[str]:
    """Floats the most recently chosen options to the top of a pick list, newest first."""
    recent = CacheManager.get_instance().try_get_cache(cache_name) or []
    ranked = [o for o in recent if o in options]
    return ranked + [o for o in options if o not in ranked]


def _validate_trainer_state(trainer_state: TrainerState) -> TrainerState:
    """Rejects a stage-less trainer state, and warns when it has fallen off the curriculum."""
    if trainer_state.stage is None:
        raise ValueError("Trainer state has no stage.")
    if not trainer_state.is_on_curriculum:
        ui.notify("Deserialized TrainerState is NOT on curriculum.", ui.MessageLevel.WARNING)
    return trainer_state


_Validator = Callable[[Any], Any]
#: Accepted forms for ``validators``: a single callable, any iterable of callables, or ``None``.
_ValidatorsArg = _Validator | Iterable[_Validator] | None


class PickRequestKwargs(TypedDict, total=False):
    """
    Optional overrides forwarded to :class:`ui.PickRequest` by :meth:`StoreBase.resolve`.

    All fields are optional; omitted fields fall back to ``resolve``'s own defaults.
    ``options`` is always computed from the store's candidates and cannot be overridden here.
    """

    label: str
    default: str | None
    allow_none: bool
    none_label: str
    field: str | None
    help: str | None


@dataclasses.dataclass(frozen=True)
class _CanonicalSpec:
    """Pairs a canonical record name with its default validators for a base model type."""

    name: str
    validators: _ValidatorsArg = None


_CANONICAL_KINDS: dict[type, _CanonicalSpec] = {
    Rig: _CanonicalSpec("rig", validate_rig_computer_name),
    Task: _CanonicalSpec("task"),
    Session: _CanonicalSpec("session"),
    TrainerState: _CanonicalSpec("trainer_state", _validate_trainer_state),
}


class Kind(Generic[T]):
    """
    A record name bound to the pydantic model that parses it.

    The name routes the record to a backend and determines its storage layout;
    the model types the return value of every store call made with this kind.

    Canonical kinds should be built through :meth:`from_rig`, :meth:`from_task`,
    :meth:`from_session` or :meth:`from_trainer_state`, which fix the framework
    name. The bare constructor derives the name from the model class instead,
    so ``Kind(AindVrForagingRig)`` is named ``"aind_vr_foraging_rig"``, *not*
    ``"rig"`` -- it is for custom records only.
    """

    def __init__(
        self,
        model: type[T],
        name: str | None = None,
        *,
        validators: Callable[[T], T] | Iterable[Callable[[T], T]] | None = None,
    ) -> None:
        """
        Args:
            model: The pydantic model records of this kind deserialize to.
            name: The record name. Defaults to the snake-cased model class name.
            validators: One callable, an iterable of callables, or ``None``.
                Each is applied in order to every record read through a store.
        """
        self.model = model
        self.name = name if name is not None else to_snake(model.__name__)
        self.validators: list[Callable[[T], T]] = (
            []
            if validators is None
            else [validators]  # type: ignore[list-item]
            if callable(validators)
            else list(validators)  # type: ignore[arg-type]
        )

    @staticmethod
    def from_rig(
        model: type[TRig],
        *,
        validators: Callable[[TRig], TRig] | Iterable[Callable[[TRig], TRig]] | None = _CANONICAL_KINDS[Rig].validators,
    ) -> "Kind[TRig]":
        """Builds the canonical ``"rig"`` kind for a rig model."""
        return Kind(model, _CANONICAL_KINDS[Rig].name, validators=validators)

    @staticmethod
    def from_task(model: type[TTask]) -> "Kind[TTask]":
        """Builds the canonical ``"task"`` kind for a task model."""
        return Kind(model, _CANONICAL_KINDS[Task].name)

    @staticmethod
    def from_session() -> "Kind[Session]":
        """Builds the canonical ``"session"`` kind for a session model."""
        return Kind(Session, _CANONICAL_KINDS[Session].name)

    @staticmethod
    def from_trainer_state() -> "Kind[TrainerState]":
        """Builds the canonical ``"trainer_state"`` kind."""
        spec = _CANONICAL_KINDS[TrainerState]
        return Kind(TrainerState, spec.name, validators=spec.validators)

    @functools.cached_property
    def adapter(self) -> pydantic.TypeAdapter[T]:
        """Serializes and parses records of this kind. Also covers models that are not ``BaseModel``."""
        return pydantic.TypeAdapter(self.model)

    def __repr__(self) -> str:
        return f"Kind({self.name!r}, {self.model.__name__})"


#: Either a declared kind or a bare model, which is wrapped in ``Kind(model)``.
KindLike = Kind[T] | type[T]


def as_kind(kind: KindLike[T]) -> Kind[T]:
    """Normalizes a kind-or-model argument into a :class:`Kind`."""
    return kind if isinstance(kind, Kind) else Kind(kind)


@runtime_checkable
class Store(Protocol):
    """
    Reads, resolves and writes records of a given :class:`Kind`.

    Which kinds a store actually serves is a deployment fact, not a type-level
    one: an unsupported kind raises when the call is made.
    """

    def resolve(
        self,
        kind: KindLike[T],
        *,
        scope: Scope | None = None,
        pick_kwargs: "PickRequestKwargs | None" = None,
    ) -> T:
        """Selects a single record, prompting the user when the choice is ambiguous."""
        ...

    def list(self, kind: KindLike[T], *, scope: Scope | None = None) -> Sequence[T]:
        """Returns every record of this kind in scope, without prompting."""
        ...

    def write(self, kind: KindLike[T], value: T, *, scope: Scope | None = None) -> None:
        """Persists a record. Whether that overwrites, versions or appends is the store's contract."""
        ...

    def scoped(self, **scope: str) -> "Store":
        """Returns a view of this store narrowed by the given scope."""
        ...


@dataclasses.dataclass
class Candidate(Generic[T]):
    """A record together with the label a store shows when asking the user to pick it."""

    label: str
    value: T


class StoreBase(abc.ABC):
    """
    Base for stores, supplying scope narrowing and the default resolution policy.

    Subclasses implement :meth:`_candidates` and :meth:`write`, and may override
    :meth:`resolve` when their backend affords a better presentation than a flat
    pick list.
    """

    def __init__(self, *, scope: Scope | None = None) -> None:
        self._scope: dict[str, str] = dict(scope or {})

    @property
    def scope(self) -> Scope:
        """The scope every call on this store is narrowed by."""
        return dict(self._scope)

    def scoped(self, **scope: str) -> "StoreBase":
        """Returns a view sharing this store's data, narrowed by the given scope."""
        clone = object.__new__(type(self))
        clone.__dict__.update(self.__dict__)
        clone._scope = {**self._scope, **scope}
        return clone

    def _merge_scope(self, scope: Scope | None) -> dict[str, str]:
        """Layers a call's scope over the store's own."""
        return {**self._scope, **(scope or {})}

    @abc.abstractmethod
    def _candidates(self, kind: Kind[T], scope: Scope) -> Sequence[Candidate[T]]:
        """Returns every record of this kind in scope, each labelled for display."""

    @abc.abstractmethod
    def write(self, kind: KindLike[T], value: T, *, scope: Scope | None = None) -> None:
        """Persists a record. See :meth:`Store.write`."""

    def list(self, kind: KindLike[T], *, scope: Scope | None = None) -> Sequence[T]:
        """Returns every record of this kind in scope, validated but unprompted."""
        _kind = as_kind(kind)
        return [self._validated(_kind, c.value) for c in self._candidates(_kind, self._merge_scope(scope))]

    def resolve(
        self,
        kind: KindLike[T],
        *,
        scope: Scope | None = None,
        pick_kwargs: PickRequestKwargs | None = None,
    ) -> T:
        """
        Selects a single record: raises on none, auto-selects a lone candidate, prompts otherwise.

        Args:
            kind: The kind of record to resolve.
            scope: Narrows which records are considered. Layered over the store's own scope.
            pick_kwargs: Optional overrides for the :class:`ui.PickRequest` shown to the user.
                Any key present here takes precedence over ``resolve``'s own defaults.
                ``options`` is always derived from the store's candidates and is not overridable.

        Raises:
            LookupError: If no record of this kind exists in scope, or the user declined to pick one.
            ui.NoFrontendError: If a choice must be made and no frontend is registered.
        """
        _kind = as_kind(kind)
        candidates = self._candidates(_kind, self._merge_scope(scope))
        if not candidates:
            raise LookupError(f"No {_kind.name!r} records found in {self}.")
        if len(candidates) == 1:
            ui.notify(f"Found a single {_kind.name}. Using {candidates[0].label}.")
            return self._validated(_kind, candidates[0].value)
        by_label = {c.label: c.value for c in candidates}
        _request_kwargs: dict[str, Any] = {
            "label": f"Choose a {_kind.name} for {_kind.model.__name__}:",
            "allow_none": False,
            "field": _kind.name,
        }
        _request_kwargs.update(pick_kwargs or {})
        picked = ui.prompt_pick(
            ui.PickRequest(
                options=_recent_first(_kind.name, sorted(by_label)),
                **_request_kwargs,
            )
        )
        if picked is None:
            raise LookupError(f"No {_kind.name} was selected.")
        CacheManager.get_instance().add_to_cache(_kind.name, picked)
        return self._validated(_kind, by_label[picked])

    @staticmethod
    def _validated(kind: Kind[T], value: T) -> T:
        """Applies the kind's validators in order."""
        for fn in kind.validators:
            value = fn(value)
        return value


class CompositeStore(StoreBase):
    """
    Routes each kind to a backend by record name, falling back to a default store.

    This is what composition buys over subclassing: a deployment keeping rigs and
    tasks on a network share but trainer state in Dataverse is a routing table,
    not a class.

    Example:
        ```python
        from clabe.stores.dataverse import DataverseStore

        store = CompositeStore(
            default=LocalFileStore(root=VR_LIB),
            routes={"trainer_state": DataverseStore()},
        )
        ```
    """

    def __init__(self, *, default: Store | None = None, routes: Mapping[str, Store] | None = None) -> None:
        """
        Args:
            default: Serves any kind not named in ``routes``. If ``None``,
                unrouted kinds raise.
            routes: Maps a :attr:`Kind.name` to the store serving it.
        """
        super().__init__()
        self._default = default
        self._routes = dict(routes or {})

    def _route(self, kind: Kind[Any]) -> Store:
        """
        Returns the backend serving this kind.

        Raises:
            LookupError: If the kind is unrouted and there is no default.
        """
        store = self._routes.get(kind.name, self._default)
        if store is None:
            raise LookupError(f"No store is registered for kind {kind.name!r}.")
        return store

    def _candidates(self, kind: Kind[T], scope: Scope) -> Sequence[Candidate[T]]:
        """Never called: every read is delegated to a routed backend."""
        raise NotImplementedError("CompositeStore delegates to its routed backends.")

    def resolve(
        self,
        kind: KindLike[T],
        *,
        scope: Scope | None = None,
        pick_kwargs: PickRequestKwargs | None = None,
    ) -> T:
        """Delegates to the routed backend, so its own presentation is used."""
        _kind = as_kind(kind)
        return self._route(_kind).resolve(_kind, scope=scope, pick_kwargs=pick_kwargs)

    def list(self, kind: KindLike[T], *, scope: Scope | None = None) -> Sequence[T]:
        """Delegates to the routed backend."""
        _kind = as_kind(kind)
        return self._route(_kind).list(_kind, scope=scope)

    def write(self, kind: KindLike[T], value: T, *, scope: Scope | None = None) -> None:
        """Delegates to the routed backend."""
        _kind = as_kind(kind)
        self._route(_kind).write(_kind, value, scope=scope)

    def scoped(self, **scope: str) -> "CompositeStore":
        """Returns a composite whose every backend is narrowed by the given scope."""
        return CompositeStore(
            default=self._default.scoped(**scope) if self._default is not None else None,
            routes={name: store.scoped(**scope) for name, store in self._routes.items()},
        )
