import logging
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import ClassVar, TypeVar

from confierge import Confierge
from pydantic import Field

from ..services import ServiceSettings
from ..utils import get_computer_name
from ._base import Candidate, Kind, KindLike, Scope, StoreBase, as_kind

T = TypeVar("T")
logger = logging.getLogger(__name__)

#: Translates clabe scope keys into the scope names ficus knows, e.g. ``{"computer_name": "hostname"}``.
#: Scope keys absent from the mapping are not sent to ficus.
ScopeMapping = Mapping[str, str]

#: The scopes ficus is keyed on today. Extend it by passing ``{**DEFAULT_SCOPE_MAPPING, "rig": "rig_id"}``
#: to :class:`ConfiergeStore`.
DEFAULT_SCOPE_MAPPING: ScopeMapping = MappingProxyType(
    {
        "computer_name": "hostname",
        "subject": "subject_id",
    }
)


class ConfiergeSettings(ServiceSettings):
    """Settings for :class:`ConfiergeStore`, read from the ``confierge`` section of clabe.yml."""

    __yml_section__: ClassVar[str | None] = "confierge"

    namespace: str = Field(
        description="The ficus namespace this store reads and writes, e.g. 'aind-behavior-vr-foraging'. Required.",
    )
    base_url: str | None = Field(
        default=None,
        description="Ficus' root URL. None defers to confierge: $FICUS_BASE_URL, else its built-in default.",
    )
    mode: str | None = Field(
        default=None,
        description="A ficus mode, an extra layer of overrides within each scope. None uses the default mode.",
    )
    overwrite_defaults: bool = Field(
        default=False,
        description="Whether a write may overwrite the 'defaults' layer.",
    )
    create_if_missing: bool = Field(
        default=True,
        description="Whether a write may create a layer that does not exist yet.",
    )
    append_new_fields_to_last_scope: bool = Field(
        default=True,
        description="Whether fields new to the config are written to the last (most specific) scope.",
    )


class ConfiergeStore(StoreBase):
    """
    A store over ficus, through the `confierge` client.

    This is a thin adapter: it translates the store API's scope into ficus' scopes (see
    :data:`DEFAULT_SCOPE_MAPPING`) and leaves merging, validation of scope names and the offline
    cache to ficus and confierge. Ficus owns the layering, so a read yields exactly one document and
    never prompts.

    A store addresses one ficus namespace, whatever the kind. To serve several kinds, give each its
    own store and route them with :class:`~clabe.stores.CompositeStore`.

    Example:
        ```python
        store = ConfiergeStore(ConfiergeSettings(namespace="aind-behavior-vr-foraging"))
        rig = store.scoped(subject="789907").resolve(Kind.from_rig(MyRig))
        ```
    """

    def __init__(
        self,
        settings: ConfiergeSettings,
        *,
        scope: Scope | None = None,
        scope_mapping: ScopeMapping | None = None,
        client: Confierge | None = None,
    ) -> None:
        """
        Args:
            settings: Which namespace and server to use, and how writes behave.
            scope: Initial scope. ``computer_name`` defaults to this machine's, as in
                :class:`~clabe.stores.LocalFileStore`.
            scope_mapping: Translates scope keys into ficus scope names. Defaults to
                :data:`DEFAULT_SCOPE_MAPPING`.
            client: A ready confierge client. Built from ``settings.base_url`` when omitted.
        """
        super().__init__(scope={"computer_name": get_computer_name(), **(scope or {})})
        self._settings = settings
        self._scope_mapping = dict(DEFAULT_SCOPE_MAPPING if scope_mapping is None else scope_mapping)
        self._client = client if client is not None else Confierge(base_url=settings.base_url)

    def __str__(self) -> str:
        return f"{type(self).__name__}({self._settings.namespace!r})"

    def _ficus_scopes(self, scope: Scope) -> dict[str, str]:
        """Translates a store scope into ficus scopes, dropping keys ficus has no scope for."""
        mapped = {self._scope_mapping[k]: v for k, v in scope.items() if v and k in self._scope_mapping}
        for key in scope.keys() - self._scope_mapping.keys():
            logger.debug("Scope key %r has no ficus scope and is not sent.", key)
        return mapped

    def _candidates(self, kind: Kind[T], scope: Scope) -> Sequence[Candidate[T]]:
        """Fetches the document ficus merges for this scope, falling back to confierge's cache offline."""
        data = self._client.get_config_safe(
            namespace=self._settings.namespace,
            mode=self._settings.mode,
            scopes=self._ficus_scopes(scope),
        )
        return [Candidate(self._settings.namespace, kind.adapter.validate_python(data))]

    def write(self, kind: KindLike[T], value: T, *, scope: Scope | None = None) -> None:
        """
        Posts a record to ficus, which decides which layer receives it from the scope.

        Raises:
            TypeError: If the record does not serialize to a JSON object.
        """
        _kind = as_kind(kind)
        payload = _kind.adapter.dump_python(value, mode="json")
        if not isinstance(payload, dict):
            raise TypeError(f"{_kind!r} must serialize to a JSON object to be written, got {type(payload).__name__}.")
        self._client.post_config_file(
            namespace=self._settings.namespace,
            config_data=payload,
            mode=self._settings.mode,
            scopes=self._ficus_scopes(self._merge_scope(scope)),
            overwrite_defaults=self._settings.overwrite_defaults,
            create_if_missing=self._settings.create_if_missing,
            append_new_fields_to_last_scope=self._settings.append_new_fields_to_last_scope,
        )
